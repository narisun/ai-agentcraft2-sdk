"""Evidence leaves the checkpoint but not the model's view, and every answer still checks out."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import pytest
from agentcraft2_core import ServiceContainer
from agentcraft2_core.pipeline import bind_request_context
from agentcraft2_core.testing import (
    calls_tool,
    load_test_config,
    scripted_model,
    scripted_providers,
)
from agentcraft2_core.testing.mcp import InProcessMcpConnector
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, ToolMessage

from rmprep_v2.evidence_store import SqliteEvidenceStore, source_id
from rmprep_v2.examples.trace_react import FOLLOW_UP, MEMO
from rmprep_v2.graph import APPLICATION, answer, build_graph
from rmprep_v2.offload import hydrate_messages, stable_id
from rmprep_v2.response_review import tool_payload
from rmprep_v2_data import SERVER_ID, build_server

WORKSPACE = Path(__file__).resolve().parents[3]
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
    store: SqliteEvidenceStore
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


async def converse(
    tmp_path: Path,
    *questions_and_replies: Any,
    store: SqliteEvidenceStore | None,
    offload: bool = True,
    questions: tuple[str, ...] = (MEMO_REQUEST,),
    thread: str = "th-1",
) -> Run:
    agent_config = load_test_config(
        WORKSPACE / "agents/rmprep-v2/.env.example", state_dir=tmp_path / "agent"
    )
    server_config = load_test_config(
        WORKSPACE / "mcp-servers/rmprep-v2-data/.env.example", state_dir=tmp_path / "server"
    )
    agent_config = replace(agent_config, model_trace_path=None)
    async with ServiceContainer(server_config) as server_services:
        connector = InProcessMcpConnector({SERVER_ID: build_server(server_services)})
        async with ServiceContainer(
            agent_config, scripted_providers(*questions_and_replies), mcp_connector=connector
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
                store=store,  # type: ignore[arg-type]
                thread=thread,
            )


@pytest.fixture
async def store(tmp_path: Path) -> Any:
    async with SqliteEvidenceStore(tmp_path / "evidence.sqlite") as opened:
        yield opened


def evidence_messages(messages: list[AnyMessage]) -> list[ToolMessage]:
    return [
        m for m in messages if isinstance(m, ToolMessage) and "get_memo_evidence" in (m.name or "")
    ]


async def test_the_checkpoint_holds_a_reference_not_the_evidence(
    tmp_path: Path, store: SqliteEvidenceStore
) -> None:
    run = await converse(tmp_path, CALL_MEMO_EVIDENCE, MEMO, store=store)
    (message,) = evidence_messages(run.messages)
    assert len(str(message.content)) < 3_000
    assert "evidence_reference" in str(message.content)
    assert COVENANT_REF not in str(message.content)
    assert message.additional_kwargs["evidence_source_id"].startswith("src-")


async def test_the_model_still_sees_the_whole_evidence(
    tmp_path: Path, store: SqliteEvidenceStore
) -> None:
    run = await converse(tmp_path, CALL_MEMO_EVIDENCE, MEMO, store=store)
    final_prompt = run.prompts[-1]
    shown = [m for m in final_prompt if isinstance(m, ToolMessage)]
    assert shown
    assert COVENANT_REF in str(shown[0].content)
    assert len(str(shown[0].content)) > 40_000
    assert "evidence_reference" not in str(shown[0].content)
    # The text is the framed result the validators already read.
    assert tool_payload(shown[0])["uen"] == "JONE-0022"


async def test_the_original_result_is_stored_exactly(
    tmp_path: Path, store: SqliteEvidenceStore
) -> None:
    run = await converse(tmp_path, CALL_MEMO_EVIDENCE, MEMO, store=store)
    (message,) = evidence_messages(run.messages)
    scope = await scope_of(tmp_path, run.thread)
    stored = await store.get_source(scope, message.additional_kwargs["evidence_source_id"])
    assert stored is not None
    assert stored.tool.endswith("get_memo_evidence")
    assert stored.arguments == {"uen": "JONE-0022", "meeting_date": "2026-08-03"}
    shown = tool_payload(next(m for m in run.prompts[-1] if isinstance(m, ToolMessage)))
    assert stored.payload == shown
    assert source_id(stored.tool, stored.arguments, stored.payload) == stored.artifact_id


async def scope_of(tmp_path: Path, thread: str) -> str:
    """The scope of the local development caller, as the store keys it."""
    config = load_test_config(
        WORKSPACE / "agents/rmprep-v2/.env.example", state_dir=tmp_path / "scope"
    )
    async with ServiceContainer(config, scripted_providers("x")) as services:
        caller = await services.authenticate(None, application=APPLICATION, thread_id="th-1")
    return caller.scope.key(thread)


async def test_an_accepted_memo_points_at_a_published_manifest(
    tmp_path: Path, store: SqliteEvidenceStore
) -> None:
    run = await converse(tmp_path, CALL_MEMO_EVIDENCE, MEMO, store=store)
    (memo,) = run.memos
    (message,) = evidence_messages(run.messages)
    assert memo["source_ids"] == [message.additional_kwargs["evidence_source_id"]]
    scope = await scope_of(tmp_path, run.thread)
    manifest = await store.get_manifest(scope, memo["manifest_id"])
    assert manifest is not None
    assert manifest["memo_id"] == memo["memo_id"]
    assert manifest["source_ids"] == memo["source_ids"]
    assert manifest["source_refs"] == memo["source_refs"]


async def test_checkpoint_storage_no_longer_grows_with_the_evidence(
    tmp_path: Path, store: SqliteEvidenceStore
) -> None:
    before = await converse(
        tmp_path / "before", CALL_MEMO_EVIDENCE, MEMO, store=None, offload=False
    )
    after = await converse(tmp_path / "after", CALL_MEMO_EVIDENCE, MEMO, store=store)
    assert after.checkpoint_bytes * 5 < before.checkpoint_bytes


async def test_a_follow_up_is_answered_from_the_stored_evidence(
    tmp_path: Path, store: SqliteEvidenceStore
) -> None:
    run = await converse(
        tmp_path,
        CALL_MEMO_EVIDENCE,
        MEMO,
        FOLLOW_UP,
        store=store,
        questions=(MEMO_REQUEST, "Explain the covenant breaches in that memo."),
    )
    assert str(run.messages[-1].content) == FOLLOW_UP
    follow_up_prompt = run.prompts[-1]
    assert any(
        COVENANT_REF in str(m.content) for m in follow_up_prompt if isinstance(m, ToolMessage)
    )
    (message,) = evidence_messages(run.messages)
    assert "evidence_reference" in str(message.content)


async def test_lost_evidence_is_reported_to_the_model_not_hidden(
    tmp_path: Path, store: SqliteEvidenceStore
) -> None:
    run = await converse(tmp_path, CALL_MEMO_EVIDENCE, MEMO, store=store)
    scope = await scope_of(tmp_path, run.thread)
    assert await store.retire_scope(scope) >= 1
    hydrated = await hydrate_messages(run.messages, store, scope)
    (message,) = evidence_messages(hydrated)
    assert json.loads(str(message.content).split(">\n", 1)[1].rsplit("\n<", 1)[0]) == {
        "evidence_unavailable": True,
        "source_id": run.messages[
            run.messages.index(evidence_messages(run.messages)[0])
        ].additional_kwargs["evidence_source_id"],
        "reason": "The stored evidence is missing. Retrieve it again before answering from it.",
    }


async def test_another_thread_cannot_hydrate_this_threads_evidence(
    tmp_path: Path, store: SqliteEvidenceStore
) -> None:
    run = await converse(tmp_path, CALL_MEMO_EVIDENCE, MEMO, store=store)
    own = await scope_of(tmp_path, run.thread)
    other = await scope_of(tmp_path, "th-other")
    own_view = await hydrate_messages(run.messages, store, own)
    other_view = await hydrate_messages(run.messages, store, other)
    assert COVENANT_REF in str(evidence_messages(own_view)[0].content)
    assert COVENANT_REF not in str(evidence_messages(other_view)[0].content)


async def test_messages_without_references_are_left_alone(store: SqliteEvidenceStore) -> None:
    messages: list[AnyMessage] = [
        HumanMessage("hello", id="h1"),
        AIMessage("hi", id="a1"),
        ToolMessage("plain", tool_call_id="t", name="other", id="t1"),
    ]
    assert await hydrate_messages(messages, store, "scope") == messages


def test_a_replayed_reply_gets_the_same_id_and_a_changed_one_does_not() -> None:
    first = stable_id("msg-1", 3, "answer text", [])
    assert stable_id("msg-1", 3, "answer text", []) == first
    assert stable_id("msg-1", 3, "other text", []) != first
    assert stable_id("msg-2", 3, "answer text", []) != first
    assert stable_id("msg-1", 3, "", [{"name": "tool", "args": {"a": 1}, "id": "c"}]) != first
    assert first.startswith("ai-")
