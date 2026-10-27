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

These parts depart from it, and each has a fix with a phase and a test:

| Departure in rmprep | Why it matters | v2 fix | Phase |
| --- | --- | --- | --- |
| Evidence packs live in `ToolMessage`s in state | State and the checkpoint database grow with data (9.7 MB above) | Store the original outside the graph; the tool node returns a reference and a small summary | V1 |
| `MemoVersion.message_ids` point at raw messages | Trimming or compacting messages would break a memo | A memo points at a manifest ID in the evidence store | V1 |
| `uuid4()` and `now()` called inside the node | A replay produces different IDs and times | IDs derive from the request, tool-call and message IDs; the clock stays injected | V1 |
| One ~130-line `call_model` node does selection, context, generation, validation and commit | Hard to test or observe step by step | Nodes `select`, `build_context`, `generate`, `validate`, `commit`; each one small and tested alone | V3 |
| The 3-attempt revision loop is a Python `for` inside the node | No checkpoint between attempts; a resume repeats the model calls | Revision is a `validate` to `generate` edge, counted in the request ledger | V3 |
| Rejected drafts are kept out of state only because the loop is in one node | Moving the loop into the graph would otherwise checkpoint them | Spike first: hold drafts in an untracked or ephemeral channel and confirm no checkpoint contains one | V3 |
| Both specialists share the parent `State`; `steps()` filters duplicate messages | No private scratch space and noisy streams | The Q&A subgraph gets its own state schema with explicit input and output | V3 |
| `ValidationFailed` is raised after the retries | The run ends in an error, not an answer | A `limitation` node publishes a safe answer and ends the run normally | V3 |
| No `recursion_limit` or `RetryPolicy` | Only the SDK budgets (50 model, 100 tool calls) bound the loop | An explicit `recursion_limit` and a `RetryPolicy` on the model and tool nodes, both tested; the 6 tool and 8 model call limits map onto the SDK budget | V3 |
| Validation is part of `call_model` | A failed check is invisible as a step | `validate` is a node that returns issues; citation and numeric checks plug into it | V4 |

Decisions this review leaves open:

- **Hand-built loop.** v2 keeps the explicit graph rather than a prebuilt agent loop, because the
  validation, budgets and visibility manifests do not fit one. `langchain` is not installed in
  this environment, so a prebuilt `create_agent` was not evaluated.
- **Memo agent.** The nodes above are written for Q&A first. The memo agent adopts the same nodes
  once Q&A shows they hold, so memo behaviour stays comparable with rmprep until then.

## Plan for V1 to V4, with the first failing tests

| Phase | Work | First failing tests |
| --- | --- | --- |
| V1 | Evidence store protocol with a SQLite and FTS5 implementation; the offloading tool node; memo manifests; deterministic IDs | A stored pack is returned byte for byte; a second write with different content is refused; no checkpoint written during the memo turn holds more than a reference and a small summary; replaying a turn gives the same IDs |
| V2 | Evidence cards, the section directory, `search_evidence`, `get_evidence`, `get_memo_section`, `get_analysis_note`; scope and date guards for tools that take only an ID | A card resolves only inside its client, version and thread; a search hit never discloses another client; an ID tool rejects a date that differs from the memo's |
| V3 | The Q&A graph as small nodes with its own schema; the context assembler with the Q&A allocations; visibility manifest; request ledger; `limitation` node; `recursion_limit` and `RetryPolicy` | Every model request stays under the ceiling; a revision is a graph step and a resumed run keeps its counts; no checkpoint holds a rejected draft; exhausting a budget ends with a limitation answer, not an exception |
| V4 | Citation resolver against the visibility manifest; calculation receipts; numeric warnings in `validate` | A directory-only or unseen citation fails; a derived figure cites its operands; a warning survives reload and copy |

Baseline to beat: a typical Q&A turn near 8k estimated tokens for every question (today 7k to 9k
only when a keyword matches, 29k to 30k otherwise), and no checkpoint database growth beyond the
reference and summary per turn.
