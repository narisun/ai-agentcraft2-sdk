"""Negotiate live drafts around the same governed invocation and final envelope."""

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from anyio import CancelScope
from starlette.responses import Response, StreamingResponse

from agentcraft2_core.contracts import RequestContext
from agentcraft2_core.integrations.http.streaming import ClosingStreamingResponse
from agentcraft2_core.observability.live import LiveRun, bind_live_run

_LOG = logging.getLogger(__name__)


def _offer(queue: asyncio.Queue[tuple[str, dict[str, object]] | None], item: Any) -> None:
    with contextlib.suppress(asyncio.QueueFull):
        queue.put_nowait(item)


def live_response(
    context: RequestContext, invoke: Callable[[], Awaitable[Response]]
) -> StreamingResponse:
    """Stream bounded draft events; only a successful result contains an accepted answer."""
    return ClosingStreamingResponse(
        _events(context, invoke),
        media_type="text/event-stream",
        headers={
            "x-request-id": context.request_id,
            "cache-control": "no-cache, no-transform",
            "x-accel-buffering": "no",
        },
    )


async def _events(
    context: RequestContext, invoke: Callable[[], Awaitable[Response]]
) -> AsyncIterator[str]:
    queue: asyncio.Queue[tuple[str, dict[str, object]] | None] = asyncio.Queue(maxsize=128)
    identity = {"thread_id": context.thread_id, "request_id": context.request_id}
    owner_loop = asyncio.get_running_loop()

    async def draft(call: int, text: str, *, reset: bool = False) -> None:
        item = ("draft", {**identity, "call": call, "text": text, "reset": reset})
        if asyncio.get_running_loop() is owner_loop:
            await queue.put(item)
        else:
            # Synchronous graph nodes can invoke a model through LoopBridge.
            await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(queue.put(item), owner_loop))

    sequence = 0

    async def activity(kind: str, label: str, detail: str | None) -> None:
        nonlocal sequence
        sequence += 1
        body: dict[str, object] = {
            **identity,
            "seq": sequence,
            "kind": kind,
            "label": label,
            "detail": detail,
        }
        item = ("activity", body)
        # Advisory: never make the request wait for a reader that is behind.
        if asyncio.get_running_loop() is owner_loop:
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(item)
        else:
            owner_loop.call_soon_threadsafe(_offer, queue, item)

    live = LiveRun(context, draft, activity=activity)

    async def execute() -> None:
        try:
            with bind_live_run(live):
                response = await invoke()
            body = json.loads(bytes(response.body))
            body.update(usage=live.usage().model_dump(), model_calls=live.model_calls)
            name = "result" if response.status_code == 200 else "error"
            await queue.put((name, {**body, **identity}))
        except Exception:  # noqa: BLE001 - no exception text belongs in browser events
            # The browser hears "internal"; the log says what failed and for which request.
            _LOG.exception("the live run failed unexpectedly", extra={**identity, "route": "live"})
            await queue.put(("error", {**identity, "error": "internal"}))
        finally:
            # A cancelled consumer cancels this task too; do not block putting a sentinel.
            current = asyncio.current_task()
            if current is not None and not current.cancelling():
                await queue.put(None)

    task = asyncio.create_task(execute())
    try:
        yield ": connected\n\n"
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=10)
            except TimeoutError:
                yield ": heartbeat\n\n"
                continue
            if item is None:
                break
            name, body = item
            yield f"event: {name}\ndata: {json.dumps(body)}\n\n"
    finally:
        with CancelScope(shield=True):
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
