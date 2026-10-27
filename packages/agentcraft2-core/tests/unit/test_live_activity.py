"""Operational activity: short, bounded status lines beside the draft, never model text."""

import asyncio
import json
from typing import Any

import pytest
from starlette.responses import JSONResponse

from agentcraft2_core.contracts import Principal, RequestContext
from agentcraft2_core.integrations.http.live import live_response
from agentcraft2_core.integrations.langgraph import report_activity
from agentcraft2_core.observability.live import (
    MAX_ACTIVITIES,
    LiveRun,
    bind_live_run,
    publish_activity,
)
from agentcraft2_core.pipeline import bind_request_context

CONTEXT = RequestContext(
    principal=Principal(subject="developer", tenant="local"),
    application="demo",
    thread_id="thread",
    request_id="request",
)
OTHER = RequestContext(
    principal=Principal(subject="someone-else", tenant="local"),
    application="demo",
    thread_id="thread",
    request_id="other-request",
)


async def no_drafts(call: int, text: str, *, reset: bool = False) -> None:
    pass


def run_with(seen: list[tuple[str, str, str | None]]) -> LiveRun:
    async def activity(kind: str, label: str, detail: str | None) -> None:
        seen.append((kind, label, detail))

    return LiveRun(CONTEXT, no_drafts, activity=activity)


# --- publishing -----------------------------------------------------------------------------------


async def test_activity_with_no_live_run_is_ignored() -> None:
    await publish_activity(CONTEXT, "searching", "Searching the memo")


async def test_a_live_run_without_an_activity_sink_ignores_activity() -> None:
    live = LiveRun(CONTEXT, no_drafts)
    with bind_live_run(live):
        await publish_activity(CONTEXT, "searching", "Searching the memo")


async def test_activity_reaches_the_sink_of_the_same_request() -> None:
    seen: list[tuple[str, str, str | None]] = []
    with bind_live_run(run_with(seen)):
        await publish_activity(CONTEXT, "reading", "Reading a source", "covenants")
        await publish_activity(CONTEXT, "checking", "Checking citations")
    assert seen == [
        ("reading", "Reading a source", "covenants"),
        ("checking", "Checking citations", None),
    ]


async def test_activity_of_another_request_is_never_delivered() -> None:
    seen: list[tuple[str, str, str | None]] = []
    with bind_live_run(run_with(seen)):
        await publish_activity(OTHER, "searching", "Searching the memo")
    assert seen == []


async def test_text_is_bounded_and_flattened() -> None:
    seen: list[tuple[str, str, str | None]] = []
    with bind_live_run(run_with(seen)):
        await publish_activity(CONTEXT, "reading", "L" * 500 + "\nsecond line", "D" * 500)
    ((_, label, detail),) = seen
    assert len(label) == 120
    assert "\n" not in label
    assert detail is not None
    assert len(detail) == 200


@pytest.mark.parametrize("kind", ["", "Has Spaces", "x" * 33, "tool:call", "UPPER"])
async def test_a_kind_that_is_not_a_short_lowercase_word_is_ignored(kind: str) -> None:
    seen: list[tuple[str, str, str | None]] = []
    with bind_live_run(run_with(seen)):
        await publish_activity(CONTEXT, kind, "Something")
    assert seen == []


async def test_one_request_publishes_a_bounded_number_of_activities() -> None:
    seen: list[tuple[str, str, str | None]] = []
    with bind_live_run(run_with(seen)):
        for number in range(MAX_ACTIVITIES + 20):
            await publish_activity(CONTEXT, "reading", f"Reading {number}")
    assert len(seen) == MAX_ACTIVITIES


async def test_a_failing_sink_never_fails_the_request() -> None:
    async def broken(kind: str, label: str, detail: str | None) -> None:
        raise RuntimeError("the browser went away")

    with bind_live_run(LiveRun(CONTEXT, no_drafts, activity=broken)):
        await publish_activity(CONTEXT, "reading", "Reading a source")


async def test_the_graph_helper_uses_the_context_of_the_current_request() -> None:
    seen: list[tuple[str, str, str | None]] = []
    with bind_live_run(run_with(seen)), bind_request_context(CONTEXT):
        await report_activity("calculating", "Calculating a ratio")
    with bind_live_run(run_with(seen)):
        await report_activity("calculating", "No request context is bound")
    assert seen == [("calculating", "Calculating a ratio", None)]


# --- on the wire ------------------------------------------------------------------------------


def frames(parts: list[str]) -> list[tuple[str, dict[str, Any]]]:
    found = []
    for part in parts:
        if part.startswith("event:"):
            name = part.split("\n", 1)[0].removeprefix("event: ")
            found.append((name, json.loads(part.split("data: ", 1)[1])))
    return found


async def test_activity_arrives_as_its_own_event_before_the_result() -> None:
    entered, release = asyncio.Event(), asyncio.Event()

    async def invoke() -> JSONResponse:
        await report_activity("searching", "Searching the memo", "covenants")
        await report_activity("answering", "Writing the answer")
        entered.set()
        await release.wait()
        return JSONResponse({"output": {"answer": "final"}})

    async def run() -> list[str]:
        parts: list[str] = []
        response = live_response(CONTEXT, _bound(invoke))
        async for chunk in response.body_iterator:
            parts.extend(str(chunk).split("\n\n"))
        return parts

    task = asyncio.create_task(run())
    await asyncio.wait_for(entered.wait(), timeout=2)
    release.set()
    found = frames(await asyncio.wait_for(task, timeout=2))
    assert [name for name, _ in found] == ["activity", "activity", "result"]
    first, second = found[0][1], found[1][1]
    assert (first["kind"], first["label"], first["detail"]) == (
        "searching",
        "Searching the memo",
        "covenants",
    )
    assert second["detail"] is None
    assert (first["seq"], second["seq"]) == (1, 2)
    for _, body in found:
        assert (body["thread_id"], body["request_id"]) == ("thread", "request")


def _bound(invoke: Any) -> Any:
    async def call() -> Any:
        with bind_request_context(CONTEXT):
            return await invoke()

    return call


async def test_a_slow_reader_never_stops_the_request_for_activity() -> None:
    """Activity is advisory: a request that publishes many never waits for a reader."""
    finished = asyncio.Event()

    async def invoke() -> JSONResponse:
        for number in range(MAX_ACTIVITIES):
            await report_activity("reading", f"Reading {number}")
        finished.set()
        return JSONResponse({"output": {"answer": "final"}})

    response = live_response(CONTEXT, _bound(invoke))
    iterator = aiter(response.body_iterator)
    assert await anext(iterator) == ": connected\n\n"
    await asyncio.wait_for(finished.wait(), timeout=2)  # nobody has read a frame yet
    names: list[str] = []
    async for chunk in iterator:
        names.extend(name for name, _ in frames(str(chunk).split("\n\n")))
    assert names[-1] == "result"
    assert names.count("activity") <= MAX_ACTIVITIES
