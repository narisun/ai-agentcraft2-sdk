"""Measure what the current memo and Q&A flow sends to the model and keeps in the checkpoint.

This is phase V0 of the retrieval-first Q&A work: numbers to compare the new design
against. Only the model's decisions and prose are scripted. MCP queries, identity,
policy, tool-result framing and SQLite checkpoints are the real local ones.

    python -m rmprep_v2.evals.baseline --output agents/rmprep-v2/src/rmprep_v2/evals/baseline.json

Token figures use the agent's own estimator (``rmprep_v2.context.estimate_tokens``), which
is deliberately conservative (UTF-8 bytes / 3). They are planning numbers, not provider counts.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import tempfile
from dataclasses import replace
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
from langchain_core.messages import AnyMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.utils.function_calling import convert_to_openai_tool

from rmprep_v2.context import estimate_tokens
from rmprep_v2.evals.packs import WORKSPACE, unframe
from rmprep_v2.evidence_store import SqliteEvidenceStore
from rmprep_v2.examples.trace_react import FOLLOW_UP, MEMO
from rmprep_v2.graph import APPLICATION, answer, build_graph
from rmprep_v2.retrieval import retrieval_tools
from rmprep_v2_data import SERVER_ID, build_server

MEETING_DATE = "2026-08-03"
JONES = "JONE-0022"

# A mix of Q&A questions: some name a section the evidence projection recognises, some
# do not (so the whole evidence pack is sent), and a follow-up that refers back.
QUESTIONS = (
    "Explain the covenant breaches in that memo. Use the saved evidence.",
    "How did the cash conversion cycle change, and why?",
    "Is the recorded waiver proof that it was executed?",
    "What is the single biggest risk to raise with the client?",
    "Which of the open actions has been outstanding longest?",
    "Summarise the memo in three sentences for my manager.",
)
ROUNDS = 2  # the six questions are asked this many times to show growth in a long thread

_DSCR = "covenants[facility_id=FAC000000130;name=DSCR]"
_EXCEPTIONS = (
    "credit_exceptions[exception_id=CE-JONE-002] and credit_exceptions[exception_id=CE-JONE-001]"
)
# One scripted answer per question. Each cites source_ref values that exist in the evidence,
# so the answer passes the agent's own checks and the run measures normal turns.
ANSWERS = (
    FOLLOW_UP,
    "The working-capital records give the cash conversion cycle for each period; the evidence "
    "does not establish why it changed. Sources: "
    "working_capital[uen=JONE-0022;period_end=2026-06-30] and "
    "working_capital[uen=JONE-0022;period_end=2026-03-31].",
    "The recorded exception dates do not prove that a waiver was executed. Verify with Credit. "
    "Source: credit_exceptions[exception_id=CE-JONE-001].",
    f"The most urgent item is the recorded covenant verdict: verify cure status. Source: {_DSCR}.",
    f"Two credit exceptions are past their recorded due dates; confirm owners. Sources: {_EXCEPTIONS}.",
    f"The memo flags covenant failures and overdue items. Verify cure status with Credit. Source: {_DSCR}.",
)


def _tokens(messages: list[AnyMessage]) -> int:
    return estimate_tokens(messages) if messages else 0


def _classify(prompt: list[AnyMessage]) -> dict[str, int]:
    """Split one model request into its parts, in estimated tokens."""
    last_human = max((i for i, m in enumerate(prompt) if isinstance(m, HumanMessage)), default=-1)
    parts: dict[str, list[AnyMessage]] = {
        "system": [],
        "evidence": [],
        "history": [],
        "question_and_loop": [],
    }
    for index, message in enumerate(prompt):
        if isinstance(message, SystemMessage):
            parts["system"].append(message)
        elif index >= last_human >= 0:
            parts["question_and_loop"].append(message)
        elif isinstance(message, ToolMessage):
            parts["evidence"].append(message)
        else:
            parts["history"].append(message)
    return {name: _tokens(messages) for name, messages in parts.items()}


def _checkpoint_bytes(messages: list[AnyMessage]) -> int:
    return len(
        json.dumps([m.model_dump(mode="json") for m in messages], ensure_ascii=False).encode()
    )


async def _pack_sizes(
    services: ServiceContainer, caller: Any, tools: dict[str, Any], uens: list[str]
) -> list[dict[str, Any]]:
    """Size the memo evidence pack the server returns for each client."""
    rows: list[dict[str, Any]] = []
    tool = next(t for name, t in tools.items() if name.endswith("get_memo_evidence"))
    for uen in uens:
        with bind_request_context(caller):
            raw = await tool.ainvoke({"uen": uen, "meeting_date": MEETING_DATE})
        text = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)
        message = ToolMessage(content=text, tool_call_id="x", name=tool.name)
        payload = unframe(text)
        sections = payload.get("sections", {}) if isinstance(payload, dict) else {}
        rows.append(
            {
                "uen": uen,
                "status": payload.get("status") if isinstance(payload, dict) else None,
                "chars": len(text),
                "estimated_tokens": estimate_tokens([message]),
                "records": sum(len(p.get("records", [])) for p in sections.values()),
                "truncated_sections": sorted(n for n, p in sections.items() if p.get("truncated")),
                "section_tokens": {
                    name: estimate_tokens([ToolMessage(content=json.dumps(page), tool_call_id="x")])
                    for name, page in sections.items()
                },
            }
        )
    return rows


async def measure(state_dir: Path, *, offload: bool = False) -> dict[str, Any]:
    """Run the scripted conversation. ``offload`` keeps evidence in the evidence store."""
    agent_config = replace(
        load_test_config(
            WORKSPACE / "agents/rmprep-v2/.env.example", state_dir=state_dir / "agent"
        ),
        model_trace_path=state_dir / "model-trace.jsonl",
    )
    server_config = load_test_config(
        WORKSPACE / "mcp-servers/rmprep-v2-data/.env.example", state_dir=state_dir / "server"
    )
    with (WORKSPACE / "data/company.csv").open(encoding="utf-8", newline="") as handle:
        uens = [row["uen"] for row in csv.DictReader(handle)]

    replies = [
        calls_tool("rmprep-v2-data.get_memo_evidence", uen=JONES, meeting_date=MEETING_DATE),
        MEMO,
        *(ANSWERS * ROUNDS),
    ]
    report: dict[str, Any] = {
        "meeting_date": MEETING_DATE,
        "estimator": "utf8_bytes/3 + 16",
        "evidence_offloaded": offload,
    }
    async with ServiceContainer(server_config) as server_services:
        connector = InProcessMcpConnector({SERVER_ID: build_server(server_services)})
        async with ServiceContainer(
            agent_config, scripted_providers(*replies), mcp_connector=connector
        ) as services:
            await services.validate()
            tools = await services.mcp_tools(SERVER_ID)
            graph = build_graph(services, tools, offload_evidence=offload)
            caller = await services.authenticate(
                None, application=APPLICATION, thread_id="baseline"
            )
            schema_text = json.dumps([convert_to_openai_tool(t) for t in tools])
            own = retrieval_tools(SqliteEvidenceStore(":memory:"))
            own_text = json.dumps([convert_to_openai_tool(t) for t in own])
            report["retrieval_tool_definitions"] = {
                "count": len(own),
                "chars": len(own_text),
                "estimated_tokens": -(-len(own_text.encode()) // 3),
                "offered_to_the_model": offload,
            }
            report["tool_definitions"] = {
                "count": len(tools),
                "chars": len(schema_text),
                "estimated_tokens": -(-len(schema_text.encode()) // 3),
            }
            report["evidence_packs"] = await _pack_sizes(
                services, caller, {t.name: t for t in tools}, uens
            )

            seen_calls = 0
            turns: list[dict[str, Any]] = []
            asked = [
                f"Prepare a meeting memo for {JONES} as of {MEETING_DATE}.",
                *(QUESTIONS * ROUNDS),
            ]
            for number, question in enumerate(asked, start=1):
                try:
                    await answer(graph, services, caller, question)
                except ValidationFailed as error:
                    raise SystemExit(
                        f"turn {number} ({question!r}) failed its checks: {error}"
                    ) from None
                with bind_request_context(caller):
                    saved = await graph.aget_state(services.invocation(caller)["config"])
                calls = [c for model in scripted_model(services).models for c in model.calls]
                new = calls[seen_calls:]
                seen_calls = len(calls)
                per_call = [_classify(cast(list[AnyMessage], list(prompt))) for prompt in new]
                messages = list(saved.values["messages"])
                turns.append(
                    {
                        "turn": number,
                        "route": saved.values.get("route"),
                        "question": question,
                        "model_calls": len(new),
                        "input_tokens_per_call": [sum(c.values()) for c in per_call],
                        "largest_call": max(per_call, key=lambda c: sum(c.values()), default={}),
                        "checkpoint_messages": len(messages),
                        "checkpoint_message_bytes": _checkpoint_bytes(messages),
                        "reported_estimate": saved.values.get("context_diagnostics", {}).get(
                            "estimated_input_tokens"
                        ),
                    }
                )
            report["turns"] = turns
    sqlite_files = sorted(state_dir.rglob("*.sqlite"))
    report["checkpoint_sqlite_bytes"] = {
        str(path.relative_to(state_dir)): path.stat().st_size for path in sqlite_files
    }
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="write the report as JSON here")
    parser.add_argument(
        "--offload", action="store_true", help="keep evidence in the evidence store (the V1 design)"
    )
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="rmprep-v2-baseline-") as folder:
        report = asyncio.run(measure(Path(folder), offload=args.offload))
    text = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    packs = [p for p in report["evidence_packs"] if p["status"] == "found"]
    print(f"tool definitions: {report['tool_definitions']}")
    if packs:
        sizes = sorted(p["estimated_tokens"] for p in packs)
        print(
            f"evidence packs ({len(packs)} clients): min {sizes[0]}, "
            f"median {sizes[len(sizes) // 2]}, max {sizes[-1]} estimated tokens"
        )
    for turn in report["turns"]:
        print(
            f"turn {turn['turn']:>2} {turn['route']:<9} calls={turn['model_calls']} "
            f"input={max(turn['input_tokens_per_call'], default=0):>6} "
            f"checkpoint={turn['checkpoint_message_bytes']:>8} B  {turn['question'][:50]}"
        )


if __name__ == "__main__":
    main()
