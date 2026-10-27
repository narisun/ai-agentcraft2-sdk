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

## Next: V1

The evidence store and the offloading tool node. First failing tests: a stored pack is returned
byte for byte, a second write with different content is refused, and no checkpoint written during
the memo turn contains more than the reference and the small summary.
