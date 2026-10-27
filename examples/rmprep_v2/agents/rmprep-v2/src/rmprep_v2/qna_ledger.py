"""The request ledger: what one question has used, and which limit it reached first.

Every model attempt is reserved against the request's token budget before it is sent, so a
call that would cross a limit is never made. Reported usage replaces the reservation after the
call. When a provider reports no usage, or the call is cut off, the reservation stays: an
estimate is conservative, not exact, and is never trusted to be a billing figure. The ledger is
plain data in the graph state, so a resumed turn keeps what it had already spent.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any, TypedDict

from rmprep_v2.settings import QnaLimits

__all__ = [
    "Ledger",
    "model_gate",
    "new_ledger",
    "note_tool_calls",
    "reconcile",
    "reserve",
    "tool_gate",
]


class Ledger(TypedDict):
    """Counters for one question. They start at zero for each new question."""

    started_at: float
    model_calls: int
    tool_calls: int
    revisions: int
    reserved_tokens: int
    repeats: int
    last_calls: list[str]


def new_ledger(started_at: float) -> Ledger:
    """An empty ledger for a question that started at ``started_at`` (Unix seconds)."""
    return {
        "started_at": started_at,
        "model_calls": 0,
        "tool_calls": 0,
        "revisions": 0,
        "reserved_tokens": 0,
        "repeats": 0,
        "last_calls": [],
    }


def model_gate(ledger: Ledger, limits: QnaLimits, now: float, *, planned_input: int) -> str | None:
    """The reason a model call must not be made now, or ``None``.

    Checked in a fixed order, so when several limits are reached the first wins: the deadline,
    the number of model attempts, the size of one request, then the cumulative token budget.
    """
    if now - ledger["started_at"] > limits.timeout_seconds:
        return "deadline"
    if ledger["model_calls"] >= limits.max_model_calls:
        return "model_calls"
    if planned_input > limits.input_tokens:
        return "context_too_large"
    if ledger["reserved_tokens"] + planned_input + limits.output_tokens > limits.request_tokens:
        return "tokens"
    return None


def tool_gate(ledger: Ledger, limits: QnaLimits, *, requested: int) -> str | None:
    """The reason a model's tool requests must not run, or ``None``.

    Logical calls count whether or not a result is served from a cache, and automatic reads
    count like the model's own.
    """
    if ledger["repeats"] >= limits.max_no_progress_repeats:
        return "no_progress"
    if ledger["tool_calls"] + requested > limits.max_tool_calls:
        return "tool_calls"
    return None


def reserve(ledger: Ledger, limits: QnaLimits, planned_input: int, *, attempts: int = 1) -> Ledger:
    """Count ``attempts`` model calls and reserve their planned input and maximum output."""
    return {
        **ledger,
        "model_calls": ledger["model_calls"] + attempts,
        "reserved_tokens": ledger["reserved_tokens"]
        + attempts * (planned_input + limits.output_tokens),
    }


def reconcile(
    ledger: Ledger,
    limits: QnaLimits,
    *,
    planned_input: int,
    usage: Mapping[str, Any] | None,
) -> Ledger:
    """Swap the last call's reservation for what the provider reported, if it reported anything.

    A provider's input figure already includes cached tokens, so they are not added again.
    """
    if not usage or "input_tokens" not in usage:
        return ledger
    reserved = planned_input + limits.output_tokens
    actual = int(usage["input_tokens"]) + int(usage.get("output_tokens", 0))
    return {**ledger, "reserved_tokens": ledger["reserved_tokens"] - reserved + actual}


def _signature(name: str, arguments: Mapping[str, Any]) -> str:
    return name + ":" + json.dumps(arguments, sort_keys=True, ensure_ascii=False, default=str)


def note_tool_calls(ledger: Ledger, calls: Sequence[tuple[str, Mapping[str, Any]]]) -> Ledger:
    """Count tool calls and track a run of the same call repeated without anything new."""
    repeats = ledger["repeats"]
    last = list(ledger["last_calls"])
    for name, arguments in calls:
        signature = _signature(name, arguments)
        repeats = repeats + 1 if signature in last else 0
        last = [signature]
    return {
        **ledger,
        "tool_calls": ledger["tool_calls"] + len(calls),
        "repeats": repeats,
        "last_calls": last,
    }
