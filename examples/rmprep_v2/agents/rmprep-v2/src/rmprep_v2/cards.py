"""Evidence cards: one exact, small view of one source record, built by code, not by a model.

A card keeps the record's values untouched, its ``source_ref`` (the citation key rmprep
already uses), and the limits of its section. Nothing is summarized, so a card cannot change
a number. A record larger than the target is flagged, not cut. Review findings become
analysis notes that name the cards behind them. The directory is a short map of a memo's
evidence: it names sections and notes and carries no facts to cite.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from typing import Any, NotRequired, TypedDict

__all__ = [
    "CARD_TOKEN_TARGET",
    "DIRECTORY_TOKEN_CAP",
    "NOTE_TOKEN_CAP",
    "Card",
    "Note",
    "build_cards",
    "card_id",
    "directory",
    "json_tokens",
]

CARD_TOKEN_TARGET = 500
"""A target, not a license to cut: a larger record is kept whole and flagged ``oversized``."""
NOTE_TOKEN_CAP = 1_500
DIRECTORY_TOKEN_CAP = 2_000


class Card(TypedDict):
    """One record of one stored source."""

    card_id: str
    source_id: str
    section: str
    source_ref: str
    fields: dict[str, Any]
    limits: list[str]
    as_of: str
    tokens: int
    oversized: NotRequired[bool]


class Note(TypedDict):
    """A review finding with the cards that support it. An interpretation, not a fact."""

    note_id: str
    source_id: str
    code: str
    priority: str
    conclusion: str
    source_refs: list[str]
    supporting_card_ids: list[str]
    limitations: list[str]
    tokens: int


def json_tokens(value: object) -> int:
    """Estimated tokens of a value as the model sees it: the agent's conservative estimate."""
    return 16 + math.ceil(len(json.dumps(value, ensure_ascii=False).encode("utf-8")) / 3)


def _short(*parts: object) -> str:
    return hashlib.sha256("|".join(str(part) for part in parts).encode("utf-8")).hexdigest()


def card_id(source_id: str, source_ref: str) -> str:
    """The ID of the card for one record of one source; replaying gives the same ID."""
    return "c-" + _short(source_id, source_ref)[:12]


def _limits(page: Mapping[str, Any]) -> list[str]:
    limits: list[str] = []
    if page.get("note"):
        limits.append(str(page["note"]))
    evidence_limits = page.get("evidence_limits")
    if isinstance(evidence_limits, dict):
        limits += [f"{key}: {value}" for key, value in evidence_limits.items()]
    if page.get("truncated"):
        limits.append("This section is truncated; later records exist that this card set lacks.")
    return limits


def _pages(payload: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    sections = payload.get("sections")
    if isinstance(sections, dict):
        return dict(sections)
    if isinstance(payload.get("records"), list):
        return {str(payload.get("section", "records")): payload}
    return {}


def build_cards(source_id: str, payload: Mapping[str, Any]) -> tuple[list[Card], list[Note]]:
    """Cards for every record of a stored source, and notes for its review findings."""
    cards: list[Card] = []
    seen: dict[str, int] = {}
    for section, page in _pages(payload).items():
        as_of = str(payload.get("meeting_date") or page.get("end_date") or "")
        limits = _limits(page)
        for record in page.get("records", []):
            ref = record.get("source_ref")
            if not isinstance(ref, str):
                continue
            # Two records with one reference would collide; the second gets its own ID.
            seen[ref] = seen.get(ref, 0) + 1
            identity = card_id(source_id, ref if seen[ref] == 1 else f"{ref}#{seen[ref]}")
            card: Card = {
                "card_id": identity,
                "source_id": source_id,
                "section": section,
                "source_ref": ref,
                "fields": {key: value for key, value in record.items() if key != "source_ref"},
                "limits": limits,
                "as_of": as_of,
                "tokens": 0,
            }
            card["tokens"] = json_tokens({k: v for k, v in card.items() if k != "tokens"})
            if card["tokens"] > CARD_TOKEN_TARGET:
                card["oversized"] = True
            cards.append(card)
    by_ref = {card["source_ref"]: card["card_id"] for card in cards}
    notes: list[Note] = []
    for finding in payload.get("review_findings", []):
        refs = [str(ref) for ref in finding.get("source_refs", [])]
        note: Note = {
            "note_id": "n-"
            + _short(source_id, finding["code"], finding["message"], *sorted(refs))[:12],
            "source_id": source_id,
            "code": str(finding["code"]),
            "priority": str(finding.get("priority", "")),
            "conclusion": str(finding["message"]),
            "source_refs": refs,
            "supporting_card_ids": [by_ref[ref] for ref in refs if ref in by_ref],
            "limitations": [
                "An interpretation by the review rules. Read the supporting records before "
                "stating any fact from it."
            ],
            "tokens": 0,
        }
        note["tokens"] = json_tokens({k: v for k, v in note.items() if k != "tokens"})
        notes.append(note)
    return cards, notes


def directory(
    cards: Sequence[Card], notes: Sequence[Note], payload: Mapping[str, Any]
) -> dict[str, Any]:
    """A short map of one memo's evidence: sections, counts, notes and the evidence limits."""
    counts: dict[str, int] = {}
    for card in cards:
        counts[card["section"]] = counts.get(card["section"], 0) + 1
    shown: dict[str, Any] = {
        "as_of": str(payload.get("meeting_date", "")),
        "sections": {name: {"cards": count} for name, count in counts.items()},
        "missing_sections": list(payload.get("missing_sections", [])),
        "notes": [
            {"note_id": note["note_id"], "code": note["code"], "priority": note["priority"]}
            for note in notes
        ],
        "limitations": list(payload.get("limitations", [])),
    }
    omitted = 0
    while json_tokens(shown) > DIRECTORY_TOKEN_CAP and shown["notes"]:
        shown["notes"].pop()
        omitted += 1
        shown["notes_omitted"] = omitted
    return shown
