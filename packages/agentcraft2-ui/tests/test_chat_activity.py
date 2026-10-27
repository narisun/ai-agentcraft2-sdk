"""Operational activity from the agent: parsed strictly, shown while the run works, never saved."""

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest

from agentcraft2_ui.adapters.catalog import FileCatalog, LocalAccess
from agentcraft2_ui.adapters.events import terminal_body
from agentcraft2_ui.adapters.http import HttpDispatcher
from agentcraft2_ui.adapters.sqlite import SqliteThreads
from agentcraft2_ui.application.chat import ChatService, view_for
from agentcraft2_ui.application.progress import (
    MAX_ACTIVITIES,
    Activity,
    ProgressFeed,
    bind_progress,
    publish_activity,
)
from agentcraft2_ui.config.settings import Settings
from agentcraft2_ui.contracts.queries import ContractError

IDENTITY = {"thread_id": "thread", "request_id": "request"}


def frame(name: str, **value: object) -> bytes:
    return f"event: {name}\ndata: {json.dumps({**IDENTITY, **value})}\n\n".encode()


def activity(seq: object = 1, **changes: object) -> bytes:
    """One activity frame; ``changes`` may replace any field, including ``seq``."""
    value: dict[str, object] = {
        "seq": seq,
        "kind": "searching",
        "label": "Searching the memo",
        "detail": None,
    }
    return frame("activity", **{**value, **changes})


RESULT = frame("result", output={"answer": "Accepted"}, model_calls=1, usage={})


async def parse(body: bytes, feed: ProgressFeed | None = None) -> None:
    response = httpx.Response(200, content=body)
    with bind_progress(feed or ProgressFeed()):
        await terminal_body(response, 100_000, thread_id="thread", request_id="request")


# --- the feed -------------------------------------------------------------------------------------


def test_activity_is_kept_in_order_on_the_feed() -> None:
    feed = ProgressFeed()
    with bind_progress(feed):
        publish_activity(Activity(1, "searching", "Searching the memo", None))
        publish_activity(Activity(2, "reading", "Reading a source", "covenants"))
    assert [a.label for a in feed.activities] == ["Searching the memo", "Reading a source"]


def test_the_feed_keeps_a_bounded_number_and_ignores_a_repeat() -> None:
    feed = ProgressFeed()
    with bind_progress(feed):
        for number in range(1, MAX_ACTIVITIES + 10):
            publish_activity(Activity(number, "reading", f"Reading {number}", None))
        publish_activity(Activity(3, "reading", "Reading again", None))
    assert len(feed.activities) == MAX_ACTIVITIES
    assert [a.seq for a in feed.activities] == sorted({a.seq for a in feed.activities})


def test_publishing_with_no_feed_bound_is_ignored() -> None:
    publish_activity(Activity(1, "searching", "Searching the memo", None))


# --- the parser -----------------------------------------------------------------------------------


async def test_activity_frames_are_delivered_to_the_bound_feed() -> None:
    feed = ProgressFeed()
    await parse(
        activity(1) + activity(2, kind="reading", label="Reading", detail="x") + RESULT, feed
    )
    assert [(a.seq, a.kind, a.detail) for a in feed.activities] == [
        (1, "searching", None),
        (2, "reading", "x"),
    ]


@pytest.mark.parametrize(
    "bad",
    [
        {"seq": 0},
        {"seq": True},
        {"seq": "1"},
        {"kind": "Has Space"},
        {"kind": 7},
        {"label": ""},
        {"label": "x" * 121},
        {"label": 5},
        {"detail": "x" * 201},
        {"detail": 5},
        {"thread_id": "someone-else"},
        {"request_id": "someone-else"},
    ],
)
async def test_a_malformed_or_foreign_activity_frame_is_a_contract_error(
    bad: dict[str, object],
) -> None:
    with pytest.raises(ContractError, match="invalid_response"):
        await parse(activity(**bad) + RESULT)


async def test_an_agent_that_sends_no_activity_still_works() -> None:
    feed = ProgressFeed()
    await parse(RESULT, feed)
    assert feed.activities == []


# --- through the service ----------------------------------------------------------------------


class Stream(httpx.AsyncByteStream):
    def __init__(self, request: httpx.Request, release: asyncio.Event) -> None:
        self.request, self.release = request, release

    async def __aiter__(self) -> AsyncIterator[bytes]:
        identity = {
            "thread_id": json.loads(self.request.content)["thread_id"],
            "request_id": self.request.headers["x-request-id"],
        }

        def event(name: str, **value: object) -> bytes:
            return f"event: {name}\ndata: {json.dumps({**identity, **value})}\n\n".encode()

        yield event("activity", seq=1, kind="searching", label="Searching the memo", detail=None)
        yield event("activity", seq=2, kind="reading", label="Reading a source", detail="covenants")
        await self.release.wait()
        yield event(
            "result",
            output={"answer": "Accepted answer"},
            model_calls=1,
            usage={"input_tokens": 1, "output_tokens": 1, "source": "langchain"},
        )


async def test_activity_is_shown_while_the_run_works_and_is_not_saved(
    settings: Settings, tmp_path: Path
) -> None:
    release, shown = asyncio.Event(), asyncio.Event()
    seen: list[tuple[Activity, ...]] = []

    async def agent(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(404)
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=Stream(request, release),
        )

    async def display_activity(activities: tuple[Activity, ...]) -> None:
        seen.append(activities)
        if len(activities) == 2:
            shown.set()

    async def ignore_draft(draft: object) -> None:
        pass

    access = LocalAccess(settings)
    store = SqliteThreads(tmp_path / "activity.sqlite", access, b"key")
    principal = settings.identity.principal()
    async with httpx.AsyncClient(transport=httpx.MockTransport(agent)) as client:
        service = ChatService(store, FileCatalog(settings, access), HttpDispatcher(client, 3))
        thread = (await service.create(principal, "memo")).thread.thread_id
        browser = asyncio.create_task(
            service.submit(
                principal,
                thread,
                "send",
                "question",
                on_progress=ignore_draft,
                on_activity=display_activity,
            )
        )
        await asyncio.wait_for(shown.wait(), timeout=2)
        assert not browser.done(), "the activity is visible before the answer"
        release.set()
        run = await asyncio.wait_for(browser, timeout=2)
        assert run.answer == "Accepted answer"
        assert [a.label for a in seen[-1]] == ["Searching the memo", "Reading a source"]
        history = await store.history(view_for(principal, "memo"), thread)
        assert "Searching the memo" not in repr(history), "activity is not part of the transcript"
        await service.close()
