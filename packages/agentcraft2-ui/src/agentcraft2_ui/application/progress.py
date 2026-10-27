"""Ephemeral draft snapshots; a slow or disconnected browser cannot stop dispatch."""

import asyncio
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True)
class Draft:
    """Public text from the latest provider attempt, not an accepted answer."""

    call: int
    text: str


ProgressCallback = Callable[[Draft], Awaitable[None]]

#: The agent sends at most this many activity lines per request; the feed keeps no more.
MAX_ACTIVITIES = 64


@dataclass(frozen=True)
class Activity:
    """One operational status line from the agent: what it is doing, never model text."""

    seq: int
    kind: str
    label: str
    detail: str | None


ActivityCallback = Callable[[tuple[Activity, ...]], Awaitable[None]]


class ProgressFeed:
    """Keep one bounded snapshot per listener, coalescing rapid token updates."""

    def __init__(self) -> None:
        self.current = Draft(0, "")
        self.listeners: set[asyncio.Queue[Draft]] = set()
        self.activities: list[Activity] = []

    def add_activity(self, activity: Activity) -> None:
        """Keep lines in sequence order; a repeated or older number is ignored."""
        last = self.activities[-1].seq if self.activities else 0
        if activity.seq > last and len(self.activities) < MAX_ACTIVITIES:
            self.activities.append(activity)

    def publish(self, call: int, text: str, *, reset: bool) -> None:
        """Retain at most 65,536 draft characters, separate from transport byte limits."""
        if call < self.current.call:
            return
        previous = "" if reset or call != self.current.call else self.current.text
        self.current = Draft(call, (previous + text)[-65_536:])
        for queue in self.listeners:
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(self.current)

    @contextmanager
    def listen(self) -> Iterator[asyncio.Queue[Draft]]:
        """A browser owns only its subscription, never the request's lifetime."""
        queue: asyncio.Queue[Draft] = asyncio.Queue(maxsize=1)
        if self.current.call:
            queue.put_nowait(self.current)
        self.listeners.add(queue)
        try:
            yield queue
        finally:
            self.listeners.discard(queue)


_FEED: ContextVar[ProgressFeed | None] = ContextVar("ui_progress", default=None)


@contextmanager
def bind_progress(feed: ProgressFeed) -> Iterator[None]:
    """Bind the HTTP adapter's updates to the application-owned invocation task."""
    token = _FEED.set(feed)
    try:
        yield
    finally:
        _FEED.reset(token)


def publish_draft(call: int, text: str, *, reset: bool) -> None:
    """Offer draft progress without coupling the transport to a browser framework."""
    feed = _FEED.get()
    if feed is not None:
        feed.publish(call, text, reset=reset)


def publish_activity(activity: Activity) -> None:
    """Offer an activity line without coupling the transport to a browser framework."""
    feed = _FEED.get()
    if feed is not None:
        feed.add_activity(activity)


async def wait_with_progress(
    task: asyncio.Task[object],
    feed: ProgressFeed,
    callback: ProgressCallback,
    on_activity: ActivityCallback | None = None,
) -> None:
    """Render at most twenty snapshots a second; completion never waits on a token queue."""
    shown = 0
    with feed.listen() as queue:
        while not task.done():
            if on_activity is not None and len(feed.activities) > shown:
                shown = len(feed.activities)
                async with asyncio.timeout(2):
                    await on_activity(tuple(feed.activities))
            try:
                draft = await asyncio.wait_for(queue.get(), timeout=0.1)
            except TimeoutError:
                continue
            async with asyncio.timeout(2):
                await callback(draft)
            await asyncio.sleep(0.05)
