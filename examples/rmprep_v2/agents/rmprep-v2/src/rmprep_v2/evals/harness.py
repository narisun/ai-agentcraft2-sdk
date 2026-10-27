"""Run a scripted conversation through the real graph, data server and checkpoints.

Only the model's decisions and prose are scripted. Everything else is the local, real
implementation, so a test sees what a developer would: the model's requests, the saved
state, the checkpoint size and the evidence store.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from agentcraft2_core import ServiceContainer
from agentcraft2_core.pipeline import bind_request_context
from agentcraft2_core.testing import (
    calls_tool,
    load_test_config,
    scripted_model,
    scripted_providers,
)
from agentcraft2_core.testing.mcp import InProcessMcpConnector
from langchain_core.messages import AnyMessage

from rmprep_v2.evals.packs import WORKSPACE
from rmprep_v2.evidence_store import EvidenceStore
from rmprep_v2.graph import APPLICATION, answer, build_graph
from rmprep_v2_data import SERVER_ID, build_server

__all__ = [
    "CALL_MEMO_EVIDENCE",
    "COVENANT_REF",
    "MEMO_REQUEST",
    "Run",
    "checkpoint_bytes",
    "converse",
    "scope_of",
]

MEMO_REQUEST = "Prepare a meeting memo for JONE-0022 as of 2026-08-03."
COVENANT_REF = "covenants[facility_id=FAC000000130;name=DSCR]"
CALL_MEMO_EVIDENCE = calls_tool(
    "rmprep-v2-data.get_memo_evidence", uen="JONE-0022", meeting_date="2026-08-03"
)


@dataclass
class Run:
    """What one scripted conversation left behind."""

    messages: list[AnyMessage]
    memos: list[dict[str, Any]]
    prompts: list[list[Any]]
    checkpoint_bytes: int
    store: EvidenceStore | None
    thread: str


def checkpoint_bytes(database: Path) -> int:
    """Everything the checkpointer wrote for the thread, across every step."""
    with sqlite3.connect(database) as connection:
        total = 0
        for table, column in (("checkpoints", "checkpoint"), ("writes", "value")):
            row = connection.execute(
                f"SELECT COALESCE(SUM(LENGTH({column})), 0) FROM {table}"
            ).fetchone()
            total += int(row[0])
    return total


async def scope_of(state_dir: Path, thread: str) -> str:
    """The evidence-store scope of the local development caller on ``thread``."""
    config = load_test_config(
        WORKSPACE / "agents/rmprep-v2/.env.example", state_dir=state_dir / "scope"
    )
    async with ServiceContainer(config, scripted_providers("x")) as services:
        caller = await services.authenticate(None, application=APPLICATION, thread_id=thread)
    return caller.scope.key(thread)


async def converse(
    tmp_path: Path,
    *replies: Any,
    store: EvidenceStore | None,
    offload: bool = True,
    questions: tuple[str, ...] = (MEMO_REQUEST,),
    thread: str = "th-1",
) -> Run:
    """Ask each question in turn on one thread; the model answers from ``replies`` in order."""
    agent_config = replace(
        load_test_config(WORKSPACE / "agents/rmprep-v2/.env.example", state_dir=tmp_path / "agent"),
        model_trace_path=None,
    )
    server_config = load_test_config(
        WORKSPACE / "mcp-servers/rmprep-v2-data/.env.example", state_dir=tmp_path / "server"
    )
    async with ServiceContainer(server_config) as server_services:
        connector = InProcessMcpConnector({SERVER_ID: build_server(server_services)})
        async with ServiceContainer(
            agent_config, scripted_providers(*replies), mcp_connector=connector
        ) as services:
            tools = await services.mcp_tools(SERVER_ID)
            graph = build_graph(services, tools, evidence_store=store, offload_evidence=offload)
            caller = await services.authenticate(None, application=APPLICATION, thread_id=thread)
            for question in questions:
                await answer(graph, services, caller, question)
            with bind_request_context(caller):
                saved = await graph.aget_state(services.invocation(caller)["config"])
            prompts = [
                list(call) for model in scripted_model(services).models for call in model.calls
            ]
            database = next((tmp_path / "agent").rglob("*.sqlite"))
            return Run(
                messages=list(saved.values["messages"]),
                memos=list(saved.values.get("memos", [])),
                prompts=prompts,
                checkpoint_bytes=checkpoint_bytes(database),
                store=store,
                thread=thread,
            )
