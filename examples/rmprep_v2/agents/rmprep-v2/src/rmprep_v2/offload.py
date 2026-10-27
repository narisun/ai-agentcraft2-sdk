"""Keep large tool results out of the checkpoint and put them back in front of the model.

The tool node stores each evidence result in the evidence store and returns a small
reference message, so the checkpoint holds the reference and a summary. Before the model
or a validator reads the conversation, :func:`hydrate_messages` turns each reference back
into the framed result. The hydrated copy is local to one call and is never returned as a
state update.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

from agentcraft2_core import RequestContext
from agentcraft2_core.integrations.langgraph import current_request_context
from agentcraft2_core.pipeline.guardrails import frame_untrusted
from langchain_core.messages import AIMessage, AnyMessage, ToolMessage
from langchain_core.runnables import Runnable, RunnableConfig

from rmprep_v2.evidence_store import EvidenceStore, canonical_json, source_id
from rmprep_v2.response_review import EVIDENCE_TOOLS, tool_payload
from rmprep_v2.state import State

__all__ = [
    "SOURCE_KEY",
    "evidence_scope",
    "hydrate_messages",
    "offloading",
    "stable_id",
    "summarize",
]

SOURCE_KEY = "evidence_source_id"
"""The ``additional_kwargs`` key that names the stored source a reference message stands for."""

_FRAME = re.compile(r'<untrusted_tool_result tool="([^"]*)">')

_UNAVAILABLE = "The stored evidence is missing. Retrieve it again before answering from it."


def evidence_scope(context: RequestContext | None = None) -> str:
    """The store key of the current request's thread; nothing outside it is readable."""
    found = context or current_request_context()
    if found is None:
        raise RuntimeError("evidence is read and written on behalf of a request")
    return found.scope.key(found.thread_id)


def stable_id(
    last_message_id: str, position: int, text: str, tool_calls: Sequence[Mapping[str, Any]]
) -> str:
    """An ID for a model reply that replaying the same turn reproduces.

    It depends on the message the reply follows and on the reply itself, so a replay with
    identical output gets the same ID, and a different draft cannot take another's.
    """
    body = canonical_json([last_message_id, position, text, list(tool_calls)])
    return "ai-" + hashlib.sha256(body.encode("utf-8")).hexdigest()[:24]


def summarize(payload: Mapping[str, Any]) -> dict[str, Any]:
    """The few facts a reader of the checkpoint needs without the data itself."""
    summary: dict[str, Any] = {
        key: payload[key]
        for key in (
            "status",
            "section",
            "meeting_date",
            "start_date",
            "end_date",
            "truncated",
            "next_offset",
            "missing_sections",
        )
        if key in payload
    }
    sections = payload.get("sections")
    if isinstance(sections, dict):
        summary["records_by_section"] = {
            name: len(page.get("records", [])) for name, page in sections.items()
        }
        summary["truncated_sections"] = [
            name for name, page in sections.items() if page.get("truncated")
        ]
    if isinstance(payload.get("records"), list):
        summary["records"] = len(payload["records"])
    return summary


def _label(message: ToolMessage) -> str:
    """The tool name the framing carries: the registered name, not the model-safe one."""
    found = _FRAME.match(message.content) if isinstance(message.content, str) else None
    return found.group(1) if found else str(message.name or "")


def _reference(
    message: ToolMessage, payload: Mapping[str, Any], identity: str, label: str
) -> ToolMessage:
    body: dict[str, Any] = {
        "evidence_reference": {"source_id": identity},
        "summary": summarize(payload),
    }
    if isinstance(payload.get("uen"), str):
        body["uen"] = payload["uen"]
    content = frame_untrusted(label, json.dumps(body, ensure_ascii=False))
    return message.model_copy(
        update={
            "content": content,
            "additional_kwargs": {**message.additional_kwargs, SOURCE_KEY: identity},
        }
    )


def offloading(
    tool_node: Runnable[Any, Any], store: EvidenceStore
) -> Callable[[State, RunnableConfig], Awaitable[dict[str, Any]]]:
    """Wrap a tool node: store each evidence result and return a reference in its place."""

    async def run(state: State, config: RunnableConfig) -> dict[str, Any]:
        result = await tool_node.ainvoke(state, config)
        if not isinstance(result, dict):
            return dict(result)
        scope = evidence_scope()
        asked = next(
            (
                {call["id"]: call for call in message.tool_calls}
                for message in reversed(state["messages"])
                if isinstance(message, AIMessage) and message.tool_calls
            ),
            {},
        )
        kept: list[AnyMessage] = []
        for message in result.get("messages", []):
            payload = tool_payload(message) if isinstance(message, ToolMessage) else {}
            call = asked.get(getattr(message, "tool_call_id", None))
            if (
                call is None
                or message.name not in EVIDENCE_TOOLS
                or not ("sections" in payload or "records" in payload)
            ):
                kept.append(message)
                continue
            label = _label(message)
            identity = source_id(label, call["args"], payload)
            await store.put_source(scope, identity, payload, tool=label, arguments=call["args"])
            kept.append(_reference(message, payload, identity, label))
        return {**result, "messages": kept}

    return run


async def hydrate_messages(
    messages: Sequence[AnyMessage], store: EvidenceStore, scope: str
) -> list[AnyMessage]:
    """Replace each reference message by the framed result it stands for, for one model call."""
    hydrated: list[AnyMessage] = []
    for message in messages:
        identity = message.additional_kwargs.get(SOURCE_KEY)
        if not isinstance(message, ToolMessage) or not isinstance(identity, str):
            hydrated.append(message)
            continue
        stored = await store.get_source(scope, identity)
        body: dict[str, Any] = (
            stored.payload
            if stored is not None
            else {"evidence_unavailable": True, "source_id": identity, "reason": _UNAVAILABLE}
        )
        label = stored.tool if stored is not None else _label(message)
        hydrated.append(
            message.model_copy(
                update={"content": frame_untrusted(label, json.dumps(body, ensure_ascii=False))}
            )
        )
    return hydrated


__all__ += ["source_id"]
