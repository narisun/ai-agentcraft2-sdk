"""Assemble one bounded Q&A model request from the conversation, the memo and the question.

Everything here is a local copy for one model call: nothing is written back to state. The
request holds the instructions, a directory of the memo's stored evidence, the selected memo
text, the newest complete exchanges that fit, and the current question with this question's
tool results. History shrinks first when the request is too large; older tool results of the
question become stubs next; a memo is never cut, so a memo that cannot fit is reported.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage, ToolMessage

from rmprep_v2.context import _relevant_evidence, _same_context, estimate_tokens
from rmprep_v2.memos import MemoVersion, turns
from rmprep_v2.response_review import evidence_references
from rmprep_v2.selection import ContextSelection
from rmprep_v2.settings import QnaLimits
from rmprep_v2.state import State

__all__ = ["QnaContext", "assemble"]

_STUB = (
    "Result omitted to save space (about {tokens} tokens). "
    "Call the tool again to read it; this note is not evidence."
)


@dataclass(frozen=True)
class QnaContext:
    """One model request and the facts about how it was built."""

    messages: list[AnyMessage]
    diagnostics: dict[str, Any]
    visible_refs: list[str]
    planned_input: int
    """Estimated tokens of the messages plus the tool definitions."""
    problem: str | None
    """``question_too_large`` when the question alone exceeds its allowance."""


def _find(messages: Sequence[AnyMessage], identity: str) -> AnyMessage | None:
    return next((m for m in messages if m.id == identity), None)


def _memo_messages(messages: Sequence[AnyMessage], memo: MemoVersion) -> list[AnyMessage]:
    """The memo as the model reads it: the request and the memo text, not the evidence exchange.

    A memo whose evidence is stored is read from the store when needed; one without stored
    evidence keeps its whole exchange, as it would have before the evidence store existed.
    """
    ids = memo["message_ids"]
    if not memo.get("source_ids"):
        return [m for m in messages if m.id in set(ids)]
    request, answer = _find(messages, ids[0]), _find(messages, ids[-1])
    return [
        request if request is not None else HumanMessage(f"Memo {memo['memo_id']}"),
        answer if answer is not None else AIMessage(memo["content"], id=ids[-1]),
    ]


def _system(
    instructions: str,
    selected: Sequence[Mapping[str, Any]],
    selection: ContextSelection,
    directory: Mapping[str, Any] | None,
    summary: str,
    output_budget: str,
) -> SystemMessage:
    """Instructions, then what stays put while a memo is discussed, then what moves."""
    stable = {
        "memo_ids": selection["memo_ids"],
        "uen": selection["uen"],
        "client_uens": selection.get("client_uens", []),
        "meeting_date": selection["meeting_date"],
        "retrieval_date": selection["retrieval_date"],
        "mode": selection["mode"],
        "memos": [
            {key: memo[key] for key in ("memo_id", "uen", "meeting_date")} for memo in selected
        ],
    }
    text = "Context selection: " + json.dumps(stable)
    if directory:
        text += (
            "\nEvidence directory (navigation only, not evidence; read records with "
            "get_evidence before citing): " + json.dumps(directory, ensure_ascii=False)
        )
    blocks: list[str | dict[str, Any]] = [
        {"type": "text", "text": instructions},
        {"type": "text", "text": text},
        {"type": "text", "text": "\n".join(part for part in (output_budget, summary) if part)},
    ]
    return SystemMessage(content=blocks, additional_kwargs={"agentcraft2_static_prefix": True})


def _stub_of(message: ToolMessage) -> ToolMessage:
    return message.model_copy(update={"content": _STUB.format(tokens=estimate_tokens([message]))})


def _last_batch(current: Sequence[AnyMessage]) -> set[str]:
    """IDs of the tool results that answer the newest tool request; the model has not yet used them."""
    batch: set[str] = set()
    for message in reversed(current):
        if isinstance(message, ToolMessage):
            batch.add(str(message.id))
        elif isinstance(message, AIMessage):
            break
    return batch


def _evict(
    current: list[AnyMessage], cap: int, evicted: list[str], *, force: bool = False
) -> list[AnyMessage]:
    """Replace the oldest tool results of this question by stubs until they fit ``cap``.

    The newest batch is kept. A stub keeps its call ID, so the request stays provider-valid.
    """
    protected = _last_batch(current)
    result = list(current)
    for index, message in enumerate(result):
        results = [m for m in result if isinstance(m, ToolMessage)]
        if not force and estimate_tokens(results) <= cap:
            break
        if (
            isinstance(message, ToolMessage)
            and str(message.id) not in protected
            and str(message.id) not in evicted
        ):
            result[index] = _stub_of(message)
            evicted.append(str(message.id))
    return result


def assemble(
    state: State,
    instructions: str,
    selection: ContextSelection,
    *,
    limits: QnaLimits,
    tool_tokens: int,
    directory: Mapping[str, Any] | None,
    output_budget: str = "",
) -> QnaContext:
    """Build the request for the current question. ``state`` may hold restored evidence."""
    groups = turns(state["messages"])
    current, prior = groups[-1], groups[:-1]
    memos = [m for m in state.get("memos", []) if m["memo_id"] in selection["memo_ids"]]
    if estimate_tokens(current[:1]) > limits.question_tokens:
        system = _system(instructions, memos, selection, None, "", output_budget)
        return QnaContext([system, *current[:1]], {}, [], 0, "question_too_large")

    question = str(current[0].content)
    pinned_message_ids = {message_id for memo in memos for message_id in memo["message_ids"]}
    other_pins = set(selection.get("lookup_message_ids", [])) | set(
        selection.get("evidence_message_ids", [])
    )
    pinned_groups = [
        g
        for g in prior
        if any(m.id in other_pins for m in g) and not any(m.id in pinned_message_ids for m in g)
    ]
    pinned_other = [_relevant_evidence(m, question) for g in pinned_groups for m in g]
    pinned_memo = [m for memo in memos for m in _memo_messages(state["messages"], memo)]

    legacy = not state.get("memos") and selection["uen"] in {"", state.get("active_client", "")}
    eligible = [
        [_relevant_evidence(m, question) for m in group]
        for group in prior
        if not any(m.id in pinned_message_ids | other_pins for m in group)
        and _same_context(group, selection, legacy=legacy)
        and selection["mode"] != "new"
    ]
    retained: list[list[AnyMessage]] = []
    for group in reversed(eligible):
        candidate = [*group, *(m for kept in retained for m in kept)]
        if estimate_tokens(candidate) > limits.recent_tokens:
            break
        retained.insert(0, group)

    evicted: list[str] = []
    working = _evict(list(current), limits.working_evidence_tokens, evicted)

    def summary() -> str:
        dropped = eligible[: len(eligible) - len(retained)]
        if not dropped:
            return ""
        topics = [str(g[0].content)[:160] for g in dropped[-3:]]
        return "Earlier discussion topics (user text, not evidence): " + json.dumps(topics)

    def compose(kept: list[list[AnyMessage]], now: list[AnyMessage]) -> list[AnyMessage]:
        return [
            _system(instructions, memos, selection, directory, summary(), output_budget),
            *pinned_memo,
            *pinned_other,
            *(m for group in kept for m in group),
            *now,
        ]

    messages = compose(retained, working)
    # Shrink toward the ceiling: older exchanges first, then older results of this question.
    while estimate_tokens(messages) + tool_tokens > limits.input_tokens and retained:
        retained = retained[1:]
        messages = compose(retained, working)
    if estimate_tokens(messages) + tool_tokens > limits.input_tokens:
        working = _evict(working, 0, evicted, force=True)
        messages = compose(retained, working)

    planned = estimate_tokens(messages) + tool_tokens
    refs = sorted(evidence_references(messages[1:])[0])
    pinned_turns = sum(
        any(m.id in pinned_message_ids | other_pins for m in group) for group in prior
    )
    omitted = max(0, len(prior) - len(retained) - pinned_turns)
    diagnostics: dict[str, Any] = {
        "memo_ids": selection["memo_ids"],
        "uen": selection["uen"],
        "client_uens": selection.get("client_uens", []),
        "meeting_date": selection["meeting_date"],
        "retrieval_date": selection["retrieval_date"],
        "mode": selection["mode"],
        "reason": selection["reason"],
        "omitted_turns": omitted,
        "evicted_tool_message_ids": evicted,
        "planned_input_tokens": planned,
        "input_token_ceiling": limits.input_tokens,
        "retained_recent_tokens": estimate_tokens([m for g in retained for m in g]),
        "included_message_ids": [m.id for m in messages[1:]],
        "visibility": {
            "memo_ids": selection["memo_ids"],
            "visible_refs": len(refs),
            "omitted_turns": omitted,
            "evicted": len(evicted),
        },
    }
    return QnaContext(messages, diagnostics, refs, planned, None)
