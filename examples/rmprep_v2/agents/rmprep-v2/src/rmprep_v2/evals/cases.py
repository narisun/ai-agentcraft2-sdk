"""The Q&A evaluation cases: their shape, how to load them and how to check them.

A case is one question asked of rmprep-v2 after some setup turns. It records what a
good outcome is (which records must support the answer, which facts it must state,
what it must not claim), so old and new implementations are scored against the same
frozen expectations. Cases start as drafts; an RM domain reviewer sets ``review_status``.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any, Literal, NotRequired, TypedDict

__all__ = ["CASES_FILE", "FAMILIES", "OUTCOMES", "Case", "load_cases", "problems"]

CASES_FILE = Path(__file__).with_name("cases.jsonl")

Family = Literal[
    "memo_explanation",
    "omitted_detail",
    "derivation",
    "cross_section",
    "comparison",
    "follow_up",
    "refresh",
    "insufficient",
    "adversarial",
]
FAMILIES: tuple[str, ...] = (
    "memo_explanation",
    "omitted_detail",
    "derivation",
    "cross_section",
    "comparison",
    "follow_up",
    "refresh",
    "insufficient",
    "adversarial",
)
OUTCOMES = ("answer", "answer_with_limits", "abstain", "clarify", "refuse")
MODES = ("snapshot", "refresh", "comparison")
REVIEW_STATUSES = ("draft", "reviewed", "needs-policy-decision")


class Case(TypedDict):
    """One evaluation case. Reference strings are ``source_ref`` values of the evidence."""

    id: str
    family: Family
    scenario: str
    uen: str
    as_of: str
    mode: Literal["snapshot", "refresh", "comparison"]
    role: str
    setup: list[str]
    """User messages sent first, in order, in the same thread. They create memos and context."""
    question: str
    expected_outcome: Literal["answer", "answer_with_limits", "abstain", "clarify", "refuse"]
    required_refs: list[str]
    """Records an acceptable answer must cite."""
    required_facts: list[str]
    """Statements an acceptable answer must contain, written for a human or judge to check."""
    prohibited: list[str]
    """Claims or disclosures that make an answer unacceptable."""
    expects_retrieval: bool
    """True when the question needs data the memo does not hold, so a tool call is expected."""
    review_status: Literal["draft", "reviewed", "needs-policy-decision"]
    notes: NotRequired[str]


_REQUIRED = {
    "id": str,
    "family": str,
    "scenario": str,
    "uen": str,
    "as_of": str,
    "mode": str,
    "role": str,
    "setup": list,
    "question": str,
    "expected_outcome": str,
    "required_refs": list,
    "required_facts": list,
    "prohibited": list,
    "expects_retrieval": bool,
    "review_status": str,
}


def load_cases(path: Path = CASES_FILE) -> list[Case]:
    """Read the cases file; one JSON object per line."""
    cases: list[Any] = [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    return cases


def problems(cases: Sequence[Case]) -> list[str]:
    """Structural problems in a set of cases; an empty list means the set is well formed."""
    found: list[str] = []
    seen: set[str] = set()
    for case in cases:
        label = str(case.get("id", "?"))
        for field, kind in _REQUIRED.items():
            if not isinstance(case.get(field), kind):
                found.append(f"{label}: {field} must be a {kind.__name__}")
        if label in seen:
            found.append(f"{label}: duplicate id")
        seen.add(label)
        for field, allowed in (
            ("family", FAMILIES),
            ("expected_outcome", OUTCOMES),
            ("mode", MODES),
            ("review_status", REVIEW_STATUSES),
        ):
            if case.get(field) not in allowed:
                found.append(f"{label}: {field} must be one of {', '.join(allowed)}")
        outcome = case.get("expected_outcome")
        if outcome in ("answer", "answer_with_limits") and not (
            case.get("required_refs") or case.get("expects_retrieval")
        ):
            found.append(f"{label}: an answer needs required_refs")
        if not case.get("required_facts") and not case.get("prohibited"):
            found.append(f"{label}: say what a good answer states or must not claim")
        if not str(case.get("question", "")).strip():
            found.append(f"{label}: the question is empty")
    return found


def coverage(cases: Iterable[Case]) -> dict[str, int]:
    """How many cases each family has."""
    counts = dict.fromkeys(FAMILIES, 0)
    for case in cases:
        counts[case["family"]] += 1
    return counts
