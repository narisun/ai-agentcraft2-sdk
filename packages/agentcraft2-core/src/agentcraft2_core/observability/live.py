"""Request-scoped draft delivery and usage totals, independent of HTTP and models."""

import logging
import re
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Protocol

from agentcraft2_core.contracts import RequestContext
from agentcraft2_core.contracts.diagnostics import TokenUsage

_LOG = logging.getLogger(__name__)

#: At most this many activity lines are sent for one request; later ones are dropped.
MAX_ACTIVITIES = 64
_KIND = re.compile(r"[a-z][a-z0-9_]{0,31}")


class DraftSink(Protocol):
    """Deliver provisional text after configured model checks; application checks may remain."""

    async def __call__(self, call: int, text: str, *, reset: bool = False) -> None:
        """Start a provider attempt or append its public text (never reasoning/tools)."""
        ...


class ActivitySink(Protocol):
    """Deliver one short status line about what the application is doing."""

    async def __call__(self, kind: str, label: str, detail: str | None) -> None:
        """Show ``label`` (at most 120 characters) and an optional ``detail`` (200)."""
        ...


@dataclass
class LiveRun:
    """Collect every provider attempt, including revisions and rejected replies."""

    context: RequestContext
    sink: DraftSink
    model_calls: int = 0
    usages: list[TokenUsage] = field(default_factory=list)
    activity: ActivitySink | None = None
    activities: int = 0

    async def begin(self) -> int:
        """Assign a request-local attempt number before asking the provider."""
        self.model_calls += 1
        call = self.model_calls
        await self.sink(call, "", reset=True)
        return call

    def usage(self) -> TokenUsage:
        """Sum complete reported fields; a missing attempt makes that total unknown."""
        if not self.model_calls or len(self.usages) != self.model_calls:
            return TokenUsage()
        counts: dict[str, int | None] = {}
        for name in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens"):
            values = [getattr(usage, name) for usage in self.usages]
            counts[name] = sum(values) if all(value is not None for value in values) else None
        kinds = {usage.kind for usage in self.usages}
        return TokenUsage(
            source="langchain",
            kind=self.usages[0].kind if len(kinds) == 1 else "unavailable",
            **counts,
        )


def _line(text: str, limit: int) -> str:
    """One line of at most ``limit`` characters: no line breaks, no model or tool content."""
    return " ".join(text.split())[:limit]


async def publish_activity(
    context: RequestContext | None, kind: str, label: str, detail: str | None = None
) -> None:
    """Tell the browser what the application is doing, for the request that owns the live run.

    Activity is operational: a fixed kind word, a short label and an optional detail written
    by the application. It is never model text, tool arguments or tool results, and it is
    advisory: a missing run, another request's run, an invalid kind, a request past
    ``MAX_ACTIVITIES`` or a sink that fails changes nothing for the request.
    """
    active = live_run(context)
    if active is None or active.activity is None or not _KIND.fullmatch(kind):
        return
    if active.activities >= MAX_ACTIVITIES:
        return
    active.activities += 1
    try:
        await active.activity(
            kind, _line(label, 120), None if detail is None else _line(detail, 200)
        )
    except Exception:  # noqa: BLE001 - a display failure never fails the request
        _LOG.warning("activity could not be delivered", exc_info=True)


_LIVE: ContextVar[LiveRun | None] = ContextVar("live_model_run", default=None)


def live_run(context: RequestContext | None) -> LiveRun | None:
    """Never deliver a model call from a different authenticated request."""
    active = _LIVE.get()
    return active if active is not None and active.context == context else None


@contextmanager
def bind_live_run(run: LiveRun) -> Iterator[None]:
    """Let graph tasks inherit this invocation's draft sink and usage accumulator."""
    token = _LIVE.set(run)
    try:
        yield
    finally:
        _LIVE.reset(token)
