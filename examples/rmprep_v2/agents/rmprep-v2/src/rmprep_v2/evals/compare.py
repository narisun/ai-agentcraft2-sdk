"""Run the same evaluation cases on rmprep and rmprep-v2 and compare what each one costs and does.

    python -m rmprep_v2.evals.compare --output compare.json

Each case becomes a scripted conversation on a fresh thread: its setup turns (memo requests and
earlier questions) and then its question, with the model's replies scripted. Only the model is
scripted. The graph, the data server, the policy, the validators and the checkpoints are the real
ones of each version, on the same data.

**What this measures.** For each case the model's final reply is the *gold answer*: the facts the
case expects and a citation of each record it requires. So the comparison asks whether each design
can support a correct, cited answer, and at what cost in model input, calls, tool calls and
checkpoint bytes. It does not measure how well any model writes, retrieves or abstains; that needs a
live model, human review and repeated runs, and has not been done.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import re
import statistics
import sys
import tempfile
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from agentcraft2_core import ServiceContainer, ValidationFailed
from agentcraft2_core.pipeline import bind_request_context
from agentcraft2_core.testing import (
    calls_tool,
    load_test_config,
    scripted_model,
    scripted_providers,
)
from agentcraft2_core.testing.mcp import InProcessMcpConnector
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage

from rmprep_v2.context import estimate_tokens
from rmprep_v2.evals.cases import Case, load_cases
from rmprep_v2.evals.harness import checkpoint_bytes
from rmprep_v2.evals.packs import WORKSPACE, load_packs, references
from rmprep_v2.settings import QnaLimits

__all__ = [
    "VARIANTS",
    "CaseResult",
    "Variant",
    "gold_answer",
    "memo_request",
    "run_cases",
    "section_of",
    "summarize",
]

_MEMO_REQUEST = re.compile(
    r"^(?:prepare|create|generate|refresh|update|write)\b.*\(([A-Z]{4}-\d{4})\).*as of (\d{4}-\d{2}-\d{2})",
    re.IGNORECASE,
)
_SECTIONS = {
    "accounts": "profile",
    "balances": "deposits",
    "transactions": "payment_totals",
    "payment_summaries": "payment_totals",
    "financial_facts": "financials",
    "product_profitability": "profitability",
    "credit_metrics": "credit",
}


@dataclass(frozen=True)
class Variant:
    """One version of the agent under comparison."""

    name: str
    workspace: Path
    package: str
    data_package: str
    server_id: str

    @property
    def tool_prefix(self) -> str:
        return self.server_id

    @property
    def agent_env(self) -> Path:
        return self.workspace / "agents" / self.name / ".env.example"

    @property
    def server_env(self) -> Path:
        return self.workspace / "mcp-servers" / f"{self.name}-data" / ".env.example"

    def source_paths(self) -> list[Path]:
        return [
            self.workspace / "agents" / self.name / "src",
            self.workspace / "mcp-servers" / f"{self.name}-data" / "src",
        ]


VARIANTS = (
    Variant("rmprep", WORKSPACE.parent / "rmprep", "rmprep", "rmprep_data", "rmprep-data"),
    Variant("rmprep-v2", WORKSPACE, "rmprep_v2", "rmprep_v2_data", "rmprep-v2-data"),
)


@dataclass
class CaseResult:
    """What one variant did on one case."""

    case_id: str
    family: str
    variant: str
    outcome: str
    """``accepted``, ``clarified`` (the question was answered with a question, no model called),
    ``limitation`` (an answer that says it could not finish) or ``rejected`` (the old design
    raising after its checks failed)."""
    detail: str
    model_calls: int
    tool_calls: int
    max_input_tokens: int
    checkpoint_bytes: int
    cited_unknown: list[str]
    """Cited references that name no record of the client's evidence."""
    warnings: int


def memo_request(message: str) -> tuple[str, str] | None:
    """The client and date of a memo request, or ``None`` when the message is not one."""
    found = _MEMO_REQUEST.match(message.strip())
    return (found.group(1), found.group(2)) if found else None


def section_of(reference: str) -> str:
    """The section a live read would use to fetch the record that ``reference`` names."""
    table = reference.split("[", 1)[0]
    return _SECTIONS.get(table, table)


def gold_answer(case: Case) -> str:
    """The reply a correct model would give: the expected facts, citing each required record."""
    facts = " ".join(case["required_facts"]) or "The evidence does not establish this."
    refs = case["required_refs"]
    return facts + (" Sources: " + "; ".join(refs) + "." if refs else "")


def _memo_text(uen: str, date: str) -> str:
    return (
        f"Meeting memo for {uen} as of {date}. Review the recorded items with the client. "
        f"Source: accounts[uen={uen}]."
    )


def _script(variant: Variant, case: Case) -> tuple[list[str], list[Any]]:
    questions: list[str] = []
    replies: list[Any] = []
    for message in case["setup"]:
        questions.append(message)
        if (parsed := memo_request(message)) is not None:
            uen, date = parsed
            replies += [
                calls_tool(f"{variant.tool_prefix}.get_memo_evidence", uen=uen, meeting_date=date),
                _memo_text(uen, date),
            ]
        else:
            replies.append("Noted from the memo.")
    questions.append(case["question"])
    if case["expects_retrieval"]:
        replies.append(
            calls_tool(
                f"{variant.tool_prefix}.get_client_records",
                uen=case["uen"],
                section=section_of(case["required_refs"][0]),
                # A refresh reads today's evidence, not the memo's date.
                end_date=datetime.now(UTC).date().isoformat(),
            )
        )
    replies.append(gold_answer(case))
    return questions, replies


def _load(variant: Variant) -> tuple[Any, Any]:
    for path in variant.source_paths():
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))
    return (
        importlib.import_module(f"{variant.package}.graph"),
        importlib.import_module(variant.data_package),
    )


async def _run(
    variant: Variant, case: Case, folder: Path, real: set[str], prefetch: int | None = None
) -> CaseResult:
    graph_module, data_module = _load(variant)
    questions, replies = _script(variant, case)
    agent_config = replace(
        load_test_config(variant.agent_env, state_dir=folder / "agent", roles=["analyst"]),
        model_trace_path=None,
    )
    server_config = load_test_config(variant.server_env, state_dir=folder / "server")
    detail, outcome = "", "accepted"
    async with ServiceContainer(server_config) as server_services:
        connector = InProcessMcpConnector(
            {data_module.SERVER_ID: data_module.build_server(server_services)}
        )
        async with ServiceContainer(
            agent_config, scripted_providers(*replies), mcp_connector=connector
        ) as services:
            tools = await services.mcp_tools(data_module.SERVER_ID)
            options: dict[str, Any] = {}
            if prefetch is not None and variant.name == "rmprep-v2":
                options["qna_limits"] = QnaLimits(prefetch_cards=prefetch)
            graph = graph_module.build_graph(services, tools, **options)
            caller = await services.authenticate(
                None, application=graph_module.APPLICATION, thread_id="compare"
            )
            before = 0
            for index, question in enumerate(questions):
                if index == len(questions) - 1:
                    before = sum(len(model.calls) for model in scripted_model(services).models)
                try:
                    await graph_module.answer(graph, services, caller, question)
                except ValidationFailed as error:
                    outcome, detail = "rejected", str(error)[:120]
                    break
            calls = [c for m in scripted_model(services).models for c in m.calls][before:]
            with bind_request_context(caller):
                state = await graph.aget_state(services.invocation(caller)["config"])
    messages = list(state.values.get("messages", []))
    last_human = max((i for i, m in enumerate(messages) if isinstance(m, HumanMessage)), default=0)
    turn = messages[last_human:]
    final = turn[-1] if turn else None
    if (
        outcome == "accepted"
        and isinstance(final, AIMessage)
        and final.additional_kwargs.get("rmprep_limitation")
    ):
        outcome, detail = "limitation", str(final.additional_kwargs["rmprep_limitation"])
    text = str(final.content) if isinstance(final, AIMessage) else ""
    if outcome == "accepted" and not calls:
        outcome, detail = "clarified", text[:120]
    cited = set(re.findall(r"\b[a-z_][a-z_0-9]*\[[^\]\n]+\]", text))
    unknown = sorted(c for c in cited if not c.startswith("calculation[") and c not in real)
    definitions = estimate_tokens([HumanMessage(json.dumps([t.name for t in tools]))])
    return CaseResult(
        case_id=case["id"],
        family=case["family"],
        variant=variant.name,
        outcome=outcome,
        detail=detail,
        model_calls=len(calls),
        tool_calls=sum(len(m.tool_calls) for m in turn if isinstance(m, AIMessage)),
        max_input_tokens=max(
            (estimate_tokens(cast(list[AnyMessage], list(c))) for c in calls), default=0
        )
        + definitions,
        checkpoint_bytes=checkpoint_bytes(next((folder / "agent").rglob("checkpoints.sqlite"))),
        cited_unknown=unknown if outcome == "accepted" else [],
        warnings=int(state.values.get("context_diagnostics", {}).get("warnings", 0) or 0),
    )


async def run_cases(
    cases: Sequence[Case],
    folder: Path | None = None,
    *,
    variants: Sequence[str] | None = None,
    prefetch: int | None = None,
) -> list[CaseResult]:
    """Run each case on every available variant, each on a fresh thread and fresh state.

    ``variants`` limits the run to those names; ``prefetch`` sets how many cards rmprep-v2
    reads ahead for each question (its default when omitted).
    """
    packs = await load_packs(
        {(c["uen"], c["as_of"]) for c in cases} | {(c["uen"], "2026-07-03") for c in cases}
    )
    real = {ref for pack in packs.values() for ref in references(pack)}
    root = folder or Path(tempfile.mkdtemp(prefix="rmprep-compare-"))
    results: list[CaseResult] = []
    for case in cases:
        for variant in VARIANTS:
            if not variant.workspace.exists() or (variants and variant.name not in variants):
                continue
            results.append(
                await _run(variant, case, root / variant.name / case["id"], real, prefetch)
            )
    return results


def _row(rows: Sequence[CaseResult]) -> dict[str, Any]:
    return {
        "cases": len(rows),
        "accepted": sum(r.outcome == "accepted" for r in rows),
        "clarified": sum(r.outcome == "clarified" for r in rows),
        "limitation": sum(r.outcome == "limitation" for r in rows),
        "rejected": sum(r.outcome == "rejected" for r in rows),
        "max_input_tokens": max(r.max_input_tokens for r in rows),
        "median_input_tokens": int(statistics.median(r.max_input_tokens for r in rows)),
        "mean_model_calls": round(statistics.fmean(r.model_calls for r in rows), 2),
        "mean_tool_calls": round(statistics.fmean(r.tool_calls for r in rows), 2),
        "mean_checkpoint_bytes": int(statistics.fmean(r.checkpoint_bytes for r in rows)),
        "unknown_citations": sum(len(r.cited_unknown) for r in rows),
        "warnings": sum(r.warnings for r in rows),
    }


def summarize(results: Sequence[CaseResult]) -> dict[str, dict[str, Any]]:
    """Per family and overall, one row per variant."""
    grouped: dict[str, dict[str, list[CaseResult]]] = defaultdict(lambda: defaultdict(list))
    grouped["all"]  # present even with no results
    for result in results:
        grouped[result.family][result.variant].append(result)
        grouped["all"][result.variant].append(result)
    return {
        family: {variant: _row(rows) for variant, rows in sorted(by_variant.items())}
        for family, by_variant in sorted(
            grouped.items(), key=lambda item: (item[0] == "all", item[0])
        )
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="write the results as JSON here")
    parser.add_argument("--ids", nargs="*", help="run only these case IDs")
    parser.add_argument("--variants", nargs="*", help="run only these variants")
    parser.add_argument("--prefetch", type=int, help="cards rmprep-v2 reads ahead per question")
    args = parser.parse_args()
    cases = [c for c in load_cases() if not args.ids or c["id"] in args.ids]
    results = asyncio.run(run_cases(cases, variants=args.variants, prefetch=args.prefetch))
    summary = summarize(results)
    if args.output:
        args.output.write_text(
            json.dumps({"summary": summary, "results": [asdict(r) for r in results]}, indent=2),
            encoding="utf-8",
        )
    for family, by_variant in summary.items():
        for variant, row in by_variant.items():
            print(
                f"{family:<17} {variant:<10} accepted {row['accepted']:>2}/{row['cases']:<2} "
                f"clar {row['clarified']} limit {row['limitation']} rej {row['rejected']} "
                f"input max {row['max_input_tokens']:>6} calls {row['mean_model_calls']:>4} "
                f"tools {row['mean_tool_calls']:>4} ckpt {row['mean_checkpoint_bytes']:>7}"
            )


if __name__ == "__main__":
    main()
