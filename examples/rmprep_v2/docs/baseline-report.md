# Baseline: what rmprep sends to the model today

Phase V0 of the retrieval-first Q&A work. These are the numbers the new design is compared
against. rmprep-v2 starts as a copy of rmprep, so they describe both.

Reproduce them with `python -m rmprep_v2.evals.baseline` from this workspace. The raw report is
[baseline.json](../agents/rmprep-v2/src/rmprep_v2/evals/baseline.json).

## How it was measured

- A scripted model drives the real graph for one client, Jones Dairy Farm (JONE-0022) as of
  2026-08-03: one memo, then six different questions asked twice (13 turns in one thread).
  MCP queries, identity, policy, tool-result framing and SQLite checkpoints are the real local ones.
- Token figures use the agent's own estimator (UTF-8 bytes / 3, plus 16 per message). It is
  conservative: provider counts are likely lower, so read these as planning numbers.
- The evidence-pack survey calls `get_memo_evidence` for all 46 clients in the data.
- Not measured: provider token counts, latency, cache reads, a real model's answer length, the
  larger "10-page memo" stress workload. The scripted answers are short, so history growth is a
  lower bound.

## Findings

| What | Measured | Design assumption it tests |
| --- | --- | --- |
| Evidence pack for one client | 17.6k to 27.5k estimated tokens, median 21.4k; 52 to 116 records; about 72 KB for Jones | "50 to 200k evidence" is not true for this data: a pack is about 20k |
| Largest pack sections (Jones) | financials 4.7k, working capital 1.9k, payment totals 1.6k, activities 1.1k | Cards by record are small; financials is the one to page |
| Truncated sections in any pack | None of the 46 | Truncation handling matters for other tools, not the memo pack |
| Memo turn, model input | 4.6k before the tool call, 28.2k after it | Fits the proposed 32k target and 40k ceiling, with little room |
| Q&A turn whose question names a section | 7.1k to 8.6k | `_relevant_evidence` projection works when a keyword matches |
| Q&A turn with no section keyword | 29.1k to 30.2k | The projection falls back to the whole pack: about four times larger |
| System prompt, every call | 4.6k to 4.7k | The plan allots 0.8k typical and 1k ceiling: unrealistic as written |
| Tool definitions (4 tools) | 1.1k | Within the 2k typical allowance |
| History added per Q&A turn | about 0.1k (scripted answers) | Real answers (up to 350 words) will add more |
| Checkpoint state after the memo | 72.7 KB, of which the evidence is nearly all | The memo's evidence is stored once, not once per question |
| Checkpoint state after 13 turns | 85.7 KB, 28 messages | State growth per turn is small; per memo it is about 72 KB |
| Checkpoint database after 13 turns | 9.7 MB | Every step stores the whole state again: about 0.75 MB per turn |

## What this changes in the plan

1. **The fallback in `_relevant_evidence` is the first problem to fix.** Half of the questions
   in this run (6 of 12) sent the whole pack. Retrieval by section, record and search replaces the keyword
   alias table and the fallback.
2. **Active state is not the storage problem; history is.** The 512 KiB active-checkpoint limit
   would allow about seven memos before it is reached, but the database already holds 9.7 MB for
   one memo and twelve questions. Offloading evidence to a store before the tool node returns
   attacks the larger number. Revision 2 asks for retained-history storage to be reported
   separately; this is that report.
3. **The system prompt needs its own allowance.** At about 4.7k it is the biggest fixed cost of
   a retrieval-answered turn. Either trim it (the memo, Q&A and common prompts are concatenated)
   or set the component allowance near 5k. The 40k ceiling still holds.
4. **The 32k target is met by today's flows and is not the problem.** The goal for V3 is to make
   the common case look like the keyword-matched turns (about 8k) for every question, and to keep
   a memo turn from needing the whole pack in one call.
5. **A typical question should cost about 8k, not 22k.** That is the figure to beat, and the
   proposed allocation table (22k typical) overstates it for this data.

## Evaluation cases

[cases.jsonl](../agents/rmprep-v2/src/rmprep_v2/evals/cases.jsonl) has 38 draft cases across the
nine families of the design (memo explanation, omitted detail, derivation, cross-section, version
comparison, follow-up, refresh, insufficient evidence, adversarial). They use eight of the ten
supplied business scenarios and Jones's July and August memos.

- Every reference and figure is read from the data server's evidence by
  `python -m rmprep_v2.evals.draft_cases`, and a test checks that each required reference exists
  for its client and date. Question wording and expected facts are drafts.
- Each case states the expected outcome (answer, answer with limits, abstain, clarify, refuse), the
  records an answer must cite, facts it must state and claims it must not make.
- Not yet covered: role-based masking (the supplied data masks no column), prompt injection inside
  tool results and long-thread cases. One case, the raw-evidence dump, is marked
  `needs-policy-decision`.
- No case is reviewed. An RM domain reviewer sets `review_status` and freezes a holdout split
  before any tuning against them.

## How rmprep's graph compares with LangGraph practice

A review of the graph code (`graph.py`, `specialist.py`, `state.py`, `router.py`, `context.py`),
not a benchmark. The foundations are sound. These parts follow good practice and v2 keeps them:
typed state with reducers (including one that refuses to change an accepted memo version);
per-request identity in `context_schema` rather than state; thread-scoped checkpointing; the
standard `ToolNode` and `tools_condition` loop; model input assembled locally so trimming never
deletes saved state; history kept in whole exchanges so a tool call is never split from its
result; one atomic commit of an accepted answer; a deterministic router; and tests that use a
scripted model against real checkpoints.

These parts depart from it, or are worth changing. Each has a fix with a phase and a test. A
second pass against the LangGraph documentation (see Sources) corrected some of the first review's
ratings; the notes say where.

| In rmprep | What LangGraph's documentation says | v2 fix | Phase |
| --- | --- | --- | --- |
| Evidence packs live in `ToolMessage`s in state | State is raw data for one thread; cross-thread and cross-subgraph data belongs in a `Store`. Checkpoints accumulate and the docs advise pruning on a schedule (there is no built-in TTL) | Keep the original outside the graph. The tool node returns a reference and a small summary. Tools reach the store by injection (`InjectedStore`), not a global | V1 |
| `MemoVersion.message_ids` point at raw messages | State should hold what later nodes need, not pointers into history that may be trimmed | A memo points at a manifest ID in the evidence store | V1 |
| `uuid4()` and `now()` inside the node | A retry or resume re-runs the node from its start, so side effects and IDs must be idempotent | IDs derive from request, tool-call and message IDs; the clock stays injected | V1 |
| One ~130-line `call_model` node | One job per node; checkpoints are at node boundaries, so small nodes repeat less work after a failure; external calls get their own nodes and policies | Nodes `select`, `build_context`, `generate`, `validate`, `commit` | V3 |
| A `for` loop of 3 attempts inside the node | Correcting an LLM mistake is an accepted loop: write the error into state and route back with `Command`. The docs do not require the loop to be graph edges | Keep revision, but make it visible: `validate` writes the issues to state (cleared with `Overwrite`) and returns `Command(goto="generate")`. Count each attempt in the request ledger. This is a smaller change than the first review said | V3 |
| Drafts stay out of state only because the loop is one node | `UntrackedValue` is excluded from checkpoints and reset on resume; the docs show it in TypeScript only | Spike in Python first: hold drafts in an untracked channel and confirm no checkpoint holds one and a resume starts clean | V3 |
| Both specialists share the parent `State`; `steps()` filters duplicates | Sharing keys and adding the compiled subgraph as a node is a documented pattern. To isolate, wrap the subgraph in a node that maps parent state to its own schema. Input and output schemas limit `invoke` results but do not hide channels from `stream` | Q&A runs behind a wrapper node with its own schema. Do not rely on schemas to hide drafts from streams | V3 |
| `ValidationFailed` raised after the retries | Unexpected errors may raise; but a failure after retries is expected here. LangGraph 1.2 has a per-node `error_handler` that routes to a recovery branch, and `RemainingSteps` lets a node route to a fallback before the step limit | A `limitation` node publishes a safe answer; route to it from `error_handler` and when `RemainingSteps` runs low | V3 |
| No `recursion_limit` or `RetryPolicy` | `recursion_limit` is a top-level config key (default 1000 from 1.0.6); exceeding it raises `GraphRecursionError`. `RetryPolicy` and `timeout` are set per node, and `StateGraph.set_node_defaults` applies them to many | Set `recursion_limit` from the call budget, a `RetryPolicy` and `timeout` on the model and tool nodes through `set_node_defaults`, and test each. The 6 tool and 8 model call limits map onto the SDK budget | V3 |
| Validation is part of `call_model` | Routing decisions belong in nodes with `Command[Literal[...]]` so the flow stays traceable | `validate` is a node; citation and numeric checks plug into it | V4 |
| `astream(..., subgraphs=True, stream_mode="updates")` | The docs now recommend `stream_events(version="v3")` and `stream.subgraphs` | Keep `astream` for now. Check `stream_events` v3 against LangGraph 1.2.17 when the activity stream is built | V6 |

Where v1 already matches the documentation: state is kept raw and prompts are formatted in the node
that needs them ("keep state raw, format prompts on demand"); runtime values go in `context_schema`;
a compiled subgraph with shared keys is added as a node; `ToolNode` runs tool calls in parallel.

Verified in the installed LangGraph 1.2.17: `StateGraph.set_node_defaults`, per-node
`retry_policy`, `timeout` and `error_handler` on `add_node`, `RemainingSteps`, `Overwrite`,
`UntrackedValue`, `EphemeralValue`, `Command`, `stream_events`, and the SQLite store module.
Not verified: that `stream_events` supports `version="v3"` here, and the behaviour of an untracked
channel in Python.

Decisions this review leaves open:

- **Hand-built loop.** v2 keeps the explicit graph rather than a prebuilt agent loop, because the
  validation, budgets and visibility manifests do not fit one. `langchain` is not installed in
  this environment, so a prebuilt `create_agent` and its middleware (for example summarization or
  tool-call limits) were not evaluated. Evaluate them before building the conversation summary.
- **Evidence store backing.** V1 starts with a spike: can LangGraph's own `Store` (namespaced
  items with filtered search) hold cards and answer section and reference lookups? Keyword search
  likely still needs a SQLite FTS5 index of our own. The plan keeps a narrow `EvidenceStore`
  protocol so either backs it, and tools receive it by injection.
- **Clarifications.** rmprep answers an ambiguous memo reference with a clarifying message and
  ends the turn. The docs offer `interrupt()` for user-fixable errors. A chat turn that ends with a
  question is simpler for Agent Lab, so v2 keeps it.
- **Memo agent.** The nodes above are written for Q&A first. The memo agent adopts the same nodes
  once Q&A shows they hold, so memo behaviour stays comparable with rmprep until then.

Sources (LangGraph documentation, read 2026-10-27):
[persistence](https://docs.langchain.com/oss/python/langgraph/persistence),
[subgraphs](https://docs.langchain.com/oss/python/langgraph/use-subgraphs),
[thinking in LangGraph](https://docs.langchain.com/oss/python/langgraph/thinking-in-langgraph),
[graph API](https://docs.langchain.com/oss/python/langgraph/graph-api).

## Plan for V1 to V4, with the first failing tests

| Phase | Work | First failing tests |
| --- | --- | --- |
| V1 | Spike on LangGraph's `Store`, then an evidence store protocol with a SQLite and FTS5 implementation, injected into tools; the offloading tool node; memo manifests; deterministic IDs | A stored pack is returned byte for byte; a second write with different content is refused; no checkpoint written during the memo turn holds more than a reference and a small summary; replaying a turn gives the same IDs |
| V2 | Evidence cards, the section directory, `search_evidence`, `get_evidence`, `get_memo_section`, `get_analysis_note`; scope and date guards for tools that take only an ID | A card resolves only inside its client, version and thread; a search hit never discloses another client; an ID tool rejects a date that differs from the memo's |
| V3 | The Q&A graph as small nodes behind a wrapper with its own schema; the context assembler with the Q&A allocations; visibility manifest; request ledger; `limitation` node; `recursion_limit`, `RetryPolicy`, `timeout` and `error_handler` | Every model request stays under the ceiling; each revision is counted and visible as a step, and a resumed run keeps its counts; no checkpoint holds a rejected draft; exhausting a budget, or running low on steps, ends with a limitation answer, not an exception |
| V4 | Citation resolver against the visibility manifest; calculation receipts; numeric warnings in `validate` | A directory-only or unseen citation fails; a derived figure cites its operands; a warning survives reload and copy |

Baseline to beat: a typical Q&A turn near 8k estimated tokens for every question (today 7k to 9k
only when a keyword matches, 29k to 30k otherwise), and no checkpoint database growth beyond the
reference and summary per turn.

## V1 results: evidence store and offloading tool node

### LangGraph Store spike

Can LangGraph's own `Store` (`AsyncSqliteStore`, 1.2.17) hold the evidence? Tried in a scratch
script on 2026-10-27. **No**, for these reasons:

| Need | Result |
| --- | --- |
| Namespaced items with equality filters | Works; a namespace is isolated from a sibling |
| A source never changes | Not provided: a second `put` with different content silently overwrites |
| Search by text | Not provided: without an embedding index the `query` argument is ignored (a query that matches nothing still returned every item) |
| Stay inside one thread | A prefix search crosses threads, so every caller must scope; there is no delete for a namespace |
| Publish a manifest after checking its sources | No transaction across items |

The store stays our own small protocol on SQLite. It is a good fit for LangGraph's Store later
if an enterprise deployment wants that backend, because the protocol is six methods.

### What was built

- [evidence_store.py](../agents/rmprep-v2/src/rmprep_v2/evidence_store.py): `EvidenceStore`
  protocol and `SqliteEvidenceStore`. A source is stored once as canonical JSON with its hash;
  the same ID and content again is a no-op, other content under the same ID raises
  `EvidenceConflict`. Every call takes a scope (`RequestContext.scope.key(thread_id)`), and nothing
  reads across scopes. A manifest is published only when every source it names exists in the
  scope, and a memo cannot have two manifests.
- [offload.py](../agents/rmprep-v2/src/rmprep_v2/offload.py): `offloading` wraps `ToolNode`, stores
  each evidence result and returns a small reference message with a summary; `hydrate_messages`
  restores the framed result for one model call and is never returned as a state update.
  A missing source reaches the model as an explicit "evidence unavailable, retrieve it again"
  message, not as silence.
- The specialist hydrates once at the top of `call_model`, so context building, the citation and
  waiver checks, memo registration and focus all read the same messages they always did.
- An accepted memo publishes a manifest (its sources, source references and body hash) and keeps
  `manifest_id` and `source_ids` beside its message IDs.
- Reply IDs derive from the message they follow and the reply itself, so replaying a turn with the
  same output reproduces its IDs, and a different draft cannot take another's.
- The store is on by default: a file beside the checkpoints (`evidence.sqlite` in the service's
  state folder). `build_graph(..., offload_evidence=False)` restores rmprep's behaviour for a
  comparison.

### Measured, same scripted conversation as the baseline

| | rmprep behaviour | V1 |
| --- | --- | --- |
| Checkpoint state after the memo | 72.7 KB | 3.4 KB |
| Checkpoint state after 13 turns | 85.1 KB | 15.8 KB |
| Checkpoint database after 13 turns | 9.6 MB | 2.5 MB |
| Memo turn, model input | 28.2k | 22.9k |
| Q&A turn that falls back to the whole pack | 29.1k to 30.1k | 23.7k to 24.8k |
| Q&A turn with a matching section keyword | 7.1k to 8.6k | 7.1k to 8.6k (unchanged) |

- The input drop is not from offloading. The restored result is written as compact JSON, with no
  spaces after separators, and the estimator counts bytes. A real tokenizer will save less than
  the estimator shows. Measure it with a provider's counter in V5 before relying on it.
- State growth is now about 1 KB per turn, and the database about 190 KB per turn. What remains is
  the conversation itself and the per-turn diagnostics written into state, and every step still
  stores the whole state again. Slimming the diagnostics and the pruning policy are next candidates.

### Not done in V1

- Evidence cards and full-text search are V2, where they are first needed; the SQLite FTS5 index
  belongs with them (FTS5 is available in the installed SQLite 3.45).
- A manifest is published before the checkpoint commits. A replay that produces a different memo
  leaves the first manifest unreferenced. Cleaning those up needs the retention policy (D-06).
- Crash tests around the publication boundary, and authorization of derived views, are not written.
- Tracing and inspection tools show the reference message, which is what the checkpoint holds.
  That is intended, but anyone who reads checkpoints to see the evidence must read the store.

## V2 results: cards, search and the retrieval tools

### What was built

- [cards.py](../agents/rmprep-v2/src/rmprep_v2/cards.py): one card per source record, built by
  code. A card keeps the record's values untouched, its `source_ref` (the citation key rmprep already
  uses), its section's limits and its as-of date. A record larger than the 500-token target is
  flagged `oversized`, never cut; the largest real record is about 350 tokens. Review findings
  become analysis notes that name their supporting cards. `directory()` is a short map (sections,
  counts, notes, limitations) held under 2,000 tokens.
- The store gained cards, notes and a SQLite FTS5 keyword index (porter stemming), searched inside
  one scope and the sources of the selected memos only. Queries are turned into quoted words, so
  odd input is data, not syntax. A small controlled list of banking synonyms (for example "debt
  service coverage" also looks for "DSCR") widens a query.
- [retrieval.py](../agents/rmprep-v2/src/rmprep_v2/retrieval.py): four tools for the Q&A agent,
  `search_evidence`, `get_evidence`, `get_memo_section` and `get_analysis_note`. Their results are
  capped at 2k, 3k, 3k and 1.5k estimated tokens and say what they left out (`truncated`,
  `next_offset`, `omitted_ids`, `skipped_ids`, `not_found`). The memo agent keeps the data tools only.
- **The application, not the model, chooses the memo.** The tools read the memo or memos in the
  turn's `selection`; there is no scope, client or date argument for a model to widen. With two
  memos selected, `get_memo_section` makes the model name one, and an unselected memo is refused.
- **Guards before a tool runs.** `tool_context_issues` exempts these tools from the data tools'
  date argument, refuses a retrieval call when no memo is selected, and refuses a section call that
  names an unselected memo. The tools repeat the checks, so a missed guard still returns nothing.
- **Citations.** `get_evidence` and `get_memo_section` make a reference citable, because they return
  the records. A search hit and an analysis note do not: a note is an interpretation, and a hit is a
  lead. This is a first step toward the visibility-manifest rule of V4.
- Governance: the tools run through `services.tools(...)`, so they get the policy, audit and framing
  stages. The policy file gains a rule for the four names. A native tool has no declared data
  classification, so the rule sets no ceiling; the thread's evidence store is the boundary.
- The Q&A prompt is version 4: it explains search before fetch, that a note is not evidence, and that
  an empty search does not show the evidence is absent. The harness that runs a scripted
  conversation moved into `rmprep_v2.evals.harness` for reuse.

### Measured

| | V1 | V2 |
| --- | --- | --- |
| Q&A turn, model input (section keyword matches) | 7.1k to 8.6k | 7.4k to 8.9k |
| Tool definitions the Q&A model carries | 1.1k | 1.1k plus 0.7k for the four new tools |
| Keyword search, recall at 10 on the evaluation cases that name records | not available | 0.82 before the synonym list, 0.90 after (28 of 31 cases find every required record) |

- Each Q&A call grows by about 250 tokens (the longer prompt), plus 700 for the tool definitions.
  The saving comes when a question no longer sends the whole pack, which is V3's job.
- The 31 cases are drafts, and the synonym list was added after seeing which ones missed (for
  example "refinancing" and "service issue"). The 0.90 is therefore optimistic. Freeze a holdout
  before tuning further. The misses left are the two follow-ups (the question alone says nothing
  without the conversation) and the two-memo comparison (the measure indexes one memo).
- A retrieved source carries a `source.retrieved_at` timestamp, so each retrieval of the same data
  has its own source ID and its own card IDs. That is correct for an observation, but a scripted
  test cannot compute a card ID in advance. The graph test therefore reads a whole section instead,
  and the fetch-by-ID path is covered by direct tests on the tool functions.

### Not done in V2

- The directory is built and tested but not yet put in the prompt; V3's context assembler places it.
- Role-based masking is not implemented: the supplied data masks no column, so there is no case to
  test it against.
- With `offload_evidence=False` the Q&A prompt still mentions tools that are not bound. Use it for
  measuring storage, not for comparing answers.
- Follow-ups such as "Explain the second one." need the conversation to form a query; that is
  V3's typed focus and query rewriting.

## V3 results: the Q&A agent as small graph steps

### Spike: private keys, untracked drafts and handlers (LangGraph 1.2.17)

A scratch graph confirmed what the plan needed before any code was written:

| Question | Result |
| --- | --- |
| Can a subgraph keep keys the parent never sees? | Yes. A key that exists only in the subgraph's schema is absent from the parent's result and state, so no wrapper node is needed. This corrects the earlier plan, which asked for a wrapper |
| Does an `UntrackedValue` keep a draft out of checkpoints? | Yes. After a rejected draft and a retry, no checkpoint or write row held the draft's text |
| Does `Command(goto=...)` with a cleared issues list work for a revision loop? | Yes |
| Does `RemainingSteps` let a node route to a fallback instead of raising? | Yes |
| Does a node `error_handler` route a failure to a recovery node? | Yes |

### What was built

- [qna_graph.py](../agents/rmprep-v2/src/rmprep_v2/qna_graph.py): `select`, `prefetch`, `generate`,
  `validate`, `tools`, `commit` and `limitation`, built as one node of the agent graph with its
  own schema. The draft and the request it came from sit in an untracked channel. A revision is a
  `validate` to `generate` step with the issues in state; a tool request is a `validate` to
  `tools` step. The memo agent is unchanged.
- **A limit ends in an answer.** `limitation` returns a fixed text for the reason (`validation_failed`,
  `model_calls`, `tool_calls`, `tokens`, `deadline`, `context_too_large`, `question_too_large`,
  `no_progress`, `steps`, `model_failed`, `tool_failed`). It repeats no draft and no error message.
  A node `error_handler` routes model and tool failures there, and `RemainingSteps` routes there
  before the step limit. A policy denial, a platform budget error, a credentials error or a
  configuration error is re-raised, so the caller still sees a refusal as a refusal.
- [qna_ledger.py](../agents/rmprep-v2/src/rmprep_v2/qna_ledger.py): a per-question ledger in graph
  state. Every model attempt is reserved (planned input plus maximum output) before it is sent, so a
  call over a limit is never made. Reported usage replaces the reservation; missing usage keeps it.
  Limits are checked in a fixed order, so the first reached wins: deadline, model attempts, request
  size, cumulative tokens; tool calls and repeats are checked before a tool runs.
- [qna_context.py](../agents/rmprep-v2/src/rmprep_v2/qna_context.py): the assembler. The request
  holds the instructions, a block of stable context (the memo selection and the evidence directory),
  a block that moves (output budget, older-topic summary), the memo request and text, recent complete
  exchanges within their budget, and the question with this question's tool results. History shrinks
  first when the request is too large; older tool results of the question become stubs next; a memo is
  never cut, so one that cannot fit stops the turn. Each model call writes a visibility record (the
  references the model could see) to the evidence store.
- **Reading ahead.** `prefetch` reads the search hits for the question and the records the memo
  itself cites, through the governed `get_evidence` tool, before the model is asked. Without it a
  follow-up could not cite anything it had not asked for, because the evidence exchange no longer sits
  in the prompt. The read is authorized, audited and counted against the tool limit, and it does not
  oblige the answer to cite (only a lookup the model chose does).
- The agent's own retrieval results are kept as references in the checkpoint too, like the data
  results, so reading ahead does not add them to every later checkpoint.
- Settings: `RMPREP_QNA_*` for each limit in [settings.py](../agents/rmprep-v2/src/rmprep_v2/settings.py),
  validated at startup (a misspelled or malformed name stops the service), with the defaults of the
  reviewed proposal. `RMPREP_QNA_PREFETCH_CARDS=0` turns the read-ahead off.

### Decisions the documentation and the SDK forced

- **No LangGraph `RetryPolicy` on the model node.** The SDK's model and tool pipelines already retry
  a transient failure at their innermost stage, so a node retry would multiply attempts. A
  `RetryPolicy` applies only to the `tools` node, for `sqlite3.OperationalError` (a locked evidence
  database). The node `timeout` is the request's time limit.
- **Gap: the SDK's physical retries are invisible to the ledger.** They happen inside one governed
  call, so a retried model call counts once. Closing this needs the SDK's retry count in the call
  result or a limit on it; it is not done here.
- **The step limit lives on the Q&A graph.** `with_config(recursion_limit=...)` on the subgraph limits
  it without changing the memo agent's loop. A test shows a small limit ends in a `steps` limitation.

### Measured, same scripted conversation (offloaded)

| | rmprep behaviour | V1 | V3 |
| --- | --- | --- | --- |
| Q&A turn, model input | 7.4k to 30.4k | 7.1k to 24.8k | 7.8k to 17.9k |
| Checkpoint state after 13 turns | 85.1 KB | 15.8 KB | 28.9 KB |
| Checkpoint database after 13 turns | 9.6 MB | 2.5 MB | 4.9 MB |
| Questions that send the whole evidence pack | 6 of 12 | 6 of 12 | none |

- The worst case fell by 40% and no question sends the whole pack, but a typical turn is not 8k: it
  settles near 16k to 18k. The first Q&A turn is 7.8k; the rest carry the earlier turns' cards in
  recent history (about 7k to 8k of the 8k allowance) on top of a 5.9k system block (the instructions
  plus the directory). The next candidate is to stub the results of earlier exchanges, which can be
  read again.
- The database is twice V1's because small nodes checkpoint more often: each step stores the whole
  state again. State growth is about 2 KB a turn. Pruning and retention are still open (D-06).

### Findings along the way

- The copy script had turned the `rmprep_context` metadata key into `rmprep-v2@@context`. It was used
  consistently, so nothing failed, but it would have been written into checkpoints. Fixed.
- Tests shared one evidence file in the workspace folder, because the test configuration redirects the
  checkpoint database but not the service's state folder. The store now sits beside the checkpoint
  database, wherever that is, and tests no longer write into the workspace.
- The first search ranked free-text activity notes above the covenant cards for "Which covenants need
  discussion for this client?". Words such as "discussion" and "client" are now stop words, and a match
  on a label (section, reference, field name) is weighted over the same word in a value.
- Several earlier tests encoded rmprep's behaviour: the follow-up prompt held the whole evidence
  exchange, a failed answer raised, and the Q&A budget was the memo context budget. They now assert the
  new behaviour: cards read ahead, a limitation answer, and `RMPREP_QNA_*` limits. The memo agent still
  uses `RMPREP_CONTEXT_*`.

### Not done in V3

- A resume in the middle of a turn keeping its counts. The ledger is in checkpointed graph state and
  the draft is deliberately not, so `validate` asks again when it finds none; no test crashes a turn to
  prove it.
- Typed focus and query rewriting. A follow-up such as "Explain the second one." searches on its own
  words and reads ahead only what the memo cites.
- Stubbing old tool results in history, the memo agent's move to the same nodes, and the answer
  validators' use of the visibility record (V4).

## V4 results: citations, calculations and numeric warnings

### What was built

- [citations.py](../agents/rmprep-v2/src/rmprep_v2/citations.py): the resolver. An answer is judged
  against the record of what the final model call was shown, which V3 writes to the evidence store
  for each call; `validate` reads that record back, so the stored record, not a recomputation, is the
  authority. If the record is missing the turn ends with an `unverifiable` limitation: it fails closed.
  Each distinct reference an answer cites gets one finding:

  | Status | Meaning | Repair instruction names |
  | --- | --- | --- |
  | `valid` | The record itself was read: a card, a section read, a data result, or a calculation and its operands | |
  | `evicted` | Read earlier in the question, then dropped from the request to save space | read it again with `get_evidence` |
  | `discovery_only` | Seen only as a search hit | read it with `get_evidence` |
  | `note_only` | Named by an analysis note, which is an interpretation | read it with `get_evidence` |
  | `memo_only` | Cited by the memo's own text but not read for this answer | read it with `get_evidence` |
  | `other_version` | A record of a saved memo that is not selected for this question | cite the selected memo, or compare versions |
  | `unknown` | Matches nothing read in this thread | copy a full `source_ref`; do not invent one |

  Reading a record wins over every weaker claim to it. Each finding is blocking; a failed answer is
  revised at most twice and then ends in the `validation_failed` limitation. The old citation check
  stays for the memo agent; `response_issues(citations=False)` lets the Q&A graph use this one.
- [receipts.py](../agents/rmprep-v2/src/rmprep_v2/receipts.py) and the `calculate` tool: `difference`,
  `percent_change`, `ratio` and `sum`, in decimal arithmetic, never floating point. The model names
  each operand by the `source_ref` of a record of the selected memo and a field. The receipt shows the
  operation, the formula, every operand with its record, field, exact value and as-of date, the exact
  result, the rounded result and the rule (`half-up`, 0 to 6 places). Its ID depends only on the inputs,
  so the same calculation has the same receipt anywhere. Operands that cannot be compared are refused
  with a reason instead of combined: a different currency, a different metric, a value that is null,
  boolean or text, a missing field, a division by zero, a record outside the selected memo. A receipt
  is citable as `calculation[calc-...]`, and so are its operands.
- [numeric_checks.py](../agents/rmprep-v2/src/rmprep_v2/numeric_checks.py): figures an answer states
  that no record shown, no receipt and no memo statement supports become warnings, at most five. A
  rounded figure matches the value it rounds (to half a unit of its last digit), a percent matches a
  fraction, and a threshold named in a field (the 90 of `ar_over_90_pct`) counts. Dates, identifiers,
  references, ordinals, list numbers, years and counts of ten or fewer are not figures. A warning
  never blocks and never asks the model to revise.
- **Where a warning shows.** The commit step appends a "Check before use" line to the released answer,
  written by code, not the model, after the checks, so it is not counted in the word limit. Because it
  is part of the text, copying or exporting the answer keeps it. The same facts are in the answer's
  metadata (`rmprep_warnings`), and the checkpoint holds both, so a reload shows them.
- **Where a citation leads.** The answer's metadata lists each citation released (`rmprep_citations`):
  its reference, kind (record or calculation), the memo version and the card it points at.
  `open_source` opens a card as the memo version it came from saw it, inside the thread: an older
  answer's link shows the older value, not the latest version's.
- The Q&A prompt is version 5: a figure that is not stated must come from `calculate`, and a figure no
  record supports is shown to the reader as unverified.

### Measured

| | V3 | V4 |
| --- | --- | --- |
| Q&A turn, model input | 7.8k to 17.9k | 7.9k to 18.1k |
| Tool definitions the Q&A model carries | 0.7k | 1.05k (five tools) |
| Checkpoint state after 13 turns | 28.9 KB | 32.3 KB |

The extra 0.3k per call is prompt version 5; the state growth is the citation metadata of each answer.
Behaviour is shown by tests rather than a rate: the workspace's 648 tests pass.

### Not done in V4, and not measured

- **The warning heuristic has no measured error rate.** There is no corpus of real model answers yet,
  so its false positives (a supported figure flagged) and misses (an unsupported figure that happens to
  equal an unrelated number in the evidence) are unknown. Its tolerance of half a unit of the last digit
  is a choice, not a calibrated one.
- A "proven arithmetic inconsistency" check, for an answer whose stated result differs from the
  receipt it cites, is not built. Today such an answer is released with its citation valid.
- A claim is not checked against its citation: a right record cited for the wrong metric, period or
  currency passes. That is a semantic judgement for the evaluation corpus and a human reviewer.
- Agent Lab is unchanged: it shows the answer text, so the caveat is visible, but it has no source
  panel, activity log or warning styling for the new metadata.
- The memo agent keeps the old checks, so a memo's citations are still judged against the messages.
- Role masking of cards and an authorized-view version in the visibility record remain open (D-04).

## V5 results: the old-versus-new harness, retry accounting and Bedrock caching

### What was built

- **Harness** (`evals/compare.py`, `python -m rmprep_v2.evals.compare`). It runs every case in
  `cases.jsonl` against both workspaces, `rmprep` and `rmprep-v2`, in one process, on the same data and
  the same scripted conversation: build the memo, then ask the case's question. The model is scripted:
  it answers with the case's gold answer, citing the records the case requires, and calls the tools a
  careful model would (a fresh read for a refresh case). Each run is classified `accepted`, `clarified`
  (the selection rules asked the user to choose a memo), `limitation` or `rejected`, and records model
  calls, tool calls, the largest model input (estimator tokens), checkpoint bytes, citations that name
  no known record, and warnings.
- **Retry accounting.** The SDK change (branch `feat/model-attempts-in-reply`) puts the number of
  physical attempts a governed model call took into the reply's `response_metadata`
  (`agentcraft2_attempts`). In `qna_ledger.charge_retries` the Q&A graph reserves the extra attempts
  against the model-call and input-token limits, so a call retried inside the SDK counts as the several
  calls it was.
- **Bedrock Converse caching** (branch `feat/bedrock-converse-caching`, a separate SDK change). Options
  `prompt_caching` and `cache_ttl` (`5m` or `1h`) in `AC2_MODEL_OPTIONS`. The provider puts a cache
  point after the static prefix of the system prompt (`static_system_prompt`) and asks langchain-aws to
  cache the conversation's tail; a five-minute cache never names its ttl. Cached tokens are added to the
  input total once, so audit and the ledger see the full input.
- **Case wording.** Running the cases showed that several drafts asked something the selection rules
  treat as a different request (the word "compare" with a memo, "latest" as a refresh), so QA-010,
  QA-026 and QA-027 were reworded to name their client or date. The read-ahead default rose from 5 to 8
  cards (below).

### Measured, 38 draft cases, scripted model

| | rmprep | rmprep-v2 |
| --- | --- | --- |
| Accepted | 34 of 38 | 35 of 38 |
| Rejected after revisions | 3 (QA-020, QA-021, QA-037) | 0 |
| Ended in a limitation answer | 0 | 2 (QA-022, QA-037) |
| Asked the user to choose a memo | 1 (QA-023) | 1 (QA-023) |
| Largest model input | 50.7k | 10.6k |
| Mean model calls per case | 1.18 | 1.13 |
| Mean tool calls per case | 0.05 | 0.92 |
| Mean checkpoint size | 1.18 MB | 0.27 MB |

The cross-section family went from 1 of 3 to 3 of 3 accepted with the read-ahead; the comparison family
is 1 of 2 against 0 of 2 (QA-022 ends in a limitation, QA-023 asks for a selection in both). Every other
family is equal. The new design's largest input is about a fifth of the old one's and its checkpoints
about a quarter of the size, which is the effect of keeping evidence out of state and out of the prompt.

Read-ahead sweep, new design only: 5 cards gave 34 of 38, 8 gave 35, 10 gave 35. Eight is the default.
Two full runs with the same code gave different counts (rmprep 33 and 34 of 38, rmprep-v2 33 and 35),
so a difference of one or two cases is within run-to-run variation; the cause was not isolated. Only the
input and checkpoint differences are large enough to read as an effect.

### What this does not show

- **No live model was used.** The scripted model returns the gold answer, so "accepted" means the
  design let a right answer through and kept its evidence reachable, not that a model finds the right
  answer. Answer quality, tool-use judgement and cost need a live run.
- **The cases are drafts.** They have not been reviewed by an RM and there is no frozen holdout, so
  tuning the read-ahead on them (5 to 8) is optimistic. A live evaluation needs a reviewed, frozen set,
  at least three repetitions per case, and approved credentials.
- **QA-037** asks for a memo by explicit date, which the selection rules do not accept; it fails in
  both. Whether an explicit date should select a memo is a product decision.
- **QA-022 and QA-023** are comparison cases; QA-023 is ambiguous in both designs and QA-022 is
  not recovered by the new one.
- Token figures are the estimator's (bytes divided by three), conservative against a real tokenizer.

### Bedrock caching: facts and limits

- Cache points are processed tools, then system, then messages; at most four per request; a minimum
  prefix applies per model (1,024 tokens for Sonnet 4.5, 4,096 for Opus 4.5 and Haiku 4.5), below which
  the point is ignored without an error. The v2 static prefix is about 4.7k estimator tokens, so on the
  4,096-token models the real count must be checked.
- Bedrock's `inputTokens` excludes cached tokens. The provider reports
  `inputTokens + cacheRead + cacheWrite` as the input total, with the split kept in
  `input_token_details`; a test pins that the governed model audits it once.
- Wire behaviour is tested against a recording client and the botocore stubber. No call to Bedrock was
  made, so hit rates and savings are unmeasured. The tests need `langchain_aws`, which is not in the
  global environment.
- **Counting before a call is not wired.** Bedrock's `CountTokens` could replace the estimator for the
  ledger's reservation, but the SDK has no governed counting path (policy, audit, resilience). Adding
  one is a policy decision, so the ledger still reserves with the estimator.

### Not done in V5

- The live evaluation, calibration of the estimator against real token counts, and cache hit rates.
- Input tokens of a failed physical attempt are unknown, so a retry is charged at the planned input.
- The two SDK branches are local and unpushed; the generated docs and the guide were regenerated for
  the new Bedrock options.

## V6 results: the activity stream, and why there is no fan-out

### What was built

- **SDK change, separate branch** `feat/live-activity-events` (from `main`, local, unpushed). A graph
  node calls `await report_activity(kind, label, detail)`. The live `/invoke` response sends it as an
  `activity` event beside the drafts, with the request's `thread_id` and `request_id` and a sequence
  number. Agent Lab lists the lines in an expandable "Activity" step while the agent works, collapses
  it to "Activity · N steps" when the answer is ready, and does not save it: reopening the
  conversation shows the answers only (the data layer stores no steps).
- **Bounds.** A lowercase `kind` word of at most 32 characters, a label of at most 120, a detail of at
  most 200, one line each (line breaks flattened), at most 64 lines per request. Activity is advisory:
  a missing live run, another request's run, an invalid kind, a full queue or a failing sink changes
  nothing for the request, and the request never waits for a reader that is behind. A malformed or
  foreign `activity` event from an agent is an invalid-response error in the UI, like any other
  malformed event.
- **rmprep-v2** (`activity.py`). The Q&A graph reports a fixed label at each stage: choosing the memo,
  reading related records (with a count), writing the answer, searching or reading or calculating or
  fetching (by what the tool does, counted rather than listed), checking citations and figures,
  revising, and, when a limit ends the turn, the reason code. Nothing else is reported: not the
  question, a draft, a tool argument or result, or a record's text. A test pins this with markers in
  the draft, the question and the arguments. The import of the SDK function is tolerant, so an SDK
  without it simply shows no activity.
- **Step stream.** `steps()` (the `/invoke/stream` events) used to carry the model's prose beside a
  tool call as message content. It now sends that content empty; a rejected draft was already absent
  (it lives in an untracked channel), and a test pins both.

### Tested

- SDK: 18 tests for the feed, parser and service in the UI package, 15 for publishing and the wire in
  core, and a browser-protocol test through a real Chainlit server (the step opens with the lines,
  collapses with the final name, and a resumed conversation holds no tool step or activity text).
  Core unit suite 1,725 passed; the one failure needs the AWS package, which the scratch environment
  lacks. UI suite 386 passed.
- rmprep-v2: 21 activity tests, plus one through the real service and the live response that checks the
  sequence on the wire. That test is skipped when the installed SDK has no `report_activity`.

### Not done, and why

- **`Send` fan-out analysts: not built.** Three reasons. (1) Nothing here can measure whether it helps:
  the harness scripts the model, so it can show only that a design lets a right answer through, not
  that parallel analysts reason better. (2) The request ledger is one dict without a reducer; parallel
  branches that each reserve model calls and tokens would need an atomic, order-independent merge, and
  the gates in fixed order (a reservation before every call) would no longer hold. (3) The one case
  fan-out would target, comparison, fails for a retrieval reason, shown below.
- **The comparison finding (QA-022).** In rmprep-v2 the read-ahead is chosen by searching for the
  question's words, so for "What is new in version 2?" it does not find the records that are new, and
  the answer cites records the model was never shown; the citation check correctly refuses it (the
  old design passes it only because it sends the whole evidence pack, 50.7k tokens). Raising the
  read-ahead from 8 to 30 changed nothing, because one `get_evidence` call is capped at 10 cards. The
  scripted memo here cites a single record, so a real memo may cite more; that is unmeasured. The
  simpler remedy, built afterwards (next section), is a deterministic comparison of the two versions' cards (records added,
  removed or changed between the sources), read ahead for a comparison question. Try that before any
  fan-out.
- **`stream_events(version="v3")` was not adopted.** `astream` is kept. Activity is published from the
  nodes, so it does not depend on the streaming API.
- **The memo agent reports no activity.** Memo generation is the longest wait, but its graph is
  rmprep's unchanged; adding the same labels is small and was left for when the memo agent adopts the
  new nodes.
- **No measured latency.** The design's gate (activity visible within one second) was not measured:
  there was no live model, and the first line is sent as the graph's first node starts, before any
  model call.
- **Activity is not durable.** It is shown while the agent works and collapsed afterwards, in that
  browser only. The design's "persistent activity" would need the data layer to store it.
- **Live drafts remain.** Agent Lab's existing provisional draft panel streams model text while the
  agent works; the design asks that reasoning drafts not be exposed. That behaviour belongs to the
  SDK's live drafts (`live_updates`), not to this change, and is a decision for the owner of the agent.

### Follow-up: reading ahead what differs between two versions

- **What was built.** `version_diff.py` pairs the cards of two versions of one client's memo by
  `source_ref` and classes each record as added, removed, changed (its values or the limits of its
  section differ) or unchanged. The card ID, source ID, date and token estimate belong to the version,
  not the record, so they never count as a change, and the order of keys inside a record does not
  matter. For a question about two versions (`comparable`: two different memos of the same client,
  both with stored sources), `prefetch` reads the differing cards first, the newer side of what is new
  or changed, then the older side of what changed, then what was removed, capped at the ten cards of
  one `get_evidence` call; the search hits and the memo's own citations fill what is left. Two clients,
  one memo, more than two memos, or a memo without stored sources get the read-ahead they had before.
  No model is involved and nothing is summarized.
- **Measured, same 38 draft cases, scripted model.** rmprep-v2 accepts 36 of 38 (35 before); the
  comparison family is 1 of 2 (0 of 2 before), the same as rmprep. QA-022 is now accepted, with its
  six cited records read ahead because they are the records that differ. Largest model input is 10.5k
  tokens against 50.7k for rmprep, and the mean checkpoint is 0.27 MB against 1.18 MB. The two cases
  left are QA-023, which both designs answer with a question about which memo, and QA-037, which asks
  for a memo by explicit date and ends in a limitation answer; the selection-rule decision on QA-037
  is still open.
- **Limits of that result.** One case moved, so this shows the mechanism works on the case that
  motivated it, not a rate. The case set is the unreviewed draft set. The scripted memo cites a single
  record, so a real memo that cites more would also shrink the problem; that was not measured.
  When more than ten records differ, only the first ten are read ahead and the model is not told how
  many were left out; telling it (a count from `VersionDiff.summary()`) is not built.
