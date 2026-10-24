"""Behaviors found wrong in review of core: each test fails on the code it replaced."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal
from io import StringIO
from pathlib import Path
from typing import Any

import pytest

from agentcraft2_core.adapters.audit_jsonl import JsonlAuditOptions, JsonlAuditSink
from agentcraft2_core.contracts import AgentEntry, AgentSnapshot
from agentcraft2_core.observability.errors import _is_theirs
from agentcraft2_core.observability.logs import configure_logging
from agentcraft2_core.pipeline import ToolCall
from agentcraft2_core.pipeline.guardrails import FramingInterceptor
from agentcraft2_core.testing.contracts import make_audit_record


def test_an_agent_whose_client_id_is_its_own_id_is_one_registration() -> None:
    # A Keycloak client named like the agent it fronts is ordinary.
    agent = AgentEntry(id="accounts-agent", owner="o", version="1", client_id="accounts-agent")
    assert AgentSnapshot([agent]).get("accounts-agent") is agent


def test_a_client_id_that_is_another_agents_id_is_still_refused() -> None:
    first = AgentEntry(id="one", owner="o", version="1")
    second = AgentEntry(id="two", owner="o", version="1", client_id="one")
    with pytest.raises(ValueError, match="registered more than once"):
        AgentSnapshot([first, second])


async def test_a_tool_result_holding_a_date_or_a_decimal_is_still_framed() -> None:
    async def tool(request: ToolCall) -> Any:
        return {"as_of": datetime(2026, 10, 24, tzinfo=UTC), "balance": Decimal("12.50")}

    request = ToolCall(context=None, tool="accounts.lookup")
    framed = await FramingInterceptor[Any]()(request, tool)
    assert isinstance(framed, str)
    assert "2026-10-24" in framed
    assert "12.50" in framed
    assert request.evidence.facts()["result_framed"] is True


def test_a_bad_log_level_leaves_logging_as_it_was_and_names_may_be_any_case() -> None:
    root = logging.getLogger()
    before = (list(root.handlers), root.level)
    try:
        with pytest.raises(ValueError, match="not a log level"):
            configure_logging("svc", level="loud", stream=StringIO())
        assert list(root.handlers) == before[0]  # nothing was installed before it failed
        assert root.level == before[1]
        handler = configure_logging("svc", level="info", stream=StringIO())
        assert handler in root.handlers
    finally:
        for handler in list(root.handlers):
            if handler not in before[0]:
                root.removeHandler(handler)
        root.setLevel(before[1])


def test_the_ui_package_is_the_librarys_code_not_the_developers() -> None:
    assert _is_theirs("/repo/packages/agentcraft2-ui/src/agentcraft2_ui/integrations/chat.py")
    assert not _is_theirs("/work/my-service/src/my_service/graph.py")


async def test_the_audit_file_holds_exactly_the_bytes_that_were_written(tmp_path: Path) -> None:
    sink = JsonlAuditSink(JsonlAuditOptions(path=tmp_path / "audit.jsonl"))
    await sink.write(make_audit_record(1))
    await sink.aclose()
    raw = sink.path.read_bytes()
    # On Windows a file opened without O_BINARY turns each newline into CR LF.
    assert b"\r" not in raw
    assert raw.endswith(b"\n")


async def test_an_audit_file_that_cannot_be_written_says_why_in_the_error(tmp_path: Path) -> None:
    from agentcraft2_core.contracts import ConfigurationError

    # A folder where the file should be: the operating system's reason is the useful part.
    (tmp_path / "audit.jsonl").mkdir()
    sink = JsonlAuditSink(JsonlAuditOptions(path=tmp_path / "audit.jsonl"))
    with pytest.raises(ConfigurationError) as raised:
        await sink.validate()
    assert "OSError" in raised.value.message or "Error" in raised.value.message
    assert str(tmp_path / "audit.jsonl") in raised.value.message


async def test_closing_the_audit_file_waits_for_a_write_whose_caller_was_cancelled(
    tmp_path: Path,
) -> None:
    import asyncio
    import threading

    sink = JsonlAuditSink(JsonlAuditOptions(path=tmp_path / "audit.jsonl", fsync=False))
    started, release = threading.Event(), threading.Event()
    real_append = sink._append

    def slow_append(data: bytes) -> None:
        started.set()
        release.wait(5)
        real_append(data)

    sink._append = slow_append  # type: ignore[method-assign]
    writer = asyncio.create_task(sink.write(make_audit_record(1)))
    while not started.is_set():
        await asyncio.sleep(0.01)
    writer.cancel()  # shutdown cancels the request while its record is still being written
    with pytest.raises(asyncio.CancelledError):
        await writer

    closing = asyncio.create_task(sink.aclose())
    await asyncio.sleep(0.1)
    assert not closing.done(), "the file was closed under a write still in progress"
    release.set()
    await closing
    # The record was not lost to the close.
    assert sink.path.read_bytes().endswith(b"\n")
