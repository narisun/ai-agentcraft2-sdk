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
