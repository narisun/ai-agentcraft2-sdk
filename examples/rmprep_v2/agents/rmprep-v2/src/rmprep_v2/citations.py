"""Decide whether each record an answer cites was in front of the model when it wrote.

An answer cites a record by its ``source_ref``. The record is released as a citation only if the
model was shown the record itself, which is what the visibility record of the final model call
says. A search hit is a lead, an analysis note is an interpretation, the memo's own citations are
claims, and a result that was dropped from the request to save space is no longer in front of the
model. Each of those gets its own finding, so the repair instruction can say exactly what to do.
"""

from __future__ import annotations

import json
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from langchain_core.messages import AnyMessage, ToolMessage

from rmprep_v2.cards import card_id
from rmprep_v2.evidence_store import EvidenceStore
from rmprep_v2.response_review import _REFERENCE, tool_payload
from rmprep_v2.tool_names import EVIDENCE_TOOL_NAMES

__all__ = [
    "CitationBasis",
    "Finding",
    "basis_from_messages",
    "citation_issues",
    "framed_payload",
    "other_version_refs",
    "refs_of",
    "resolve",
]

Status = Literal[
    "valid",
    "evicted",
    "discovery_only",
    "note_only",
    "memo_only",
    "other_version",
    "unknown",
]
Kind = Literal["visible", "discovery", "notes", "none"]

_GUIDANCE: dict[str, str] = {
    "unknown": "These citations match no record read for this answer: {refs}. Copy the full "
    "source_ref of a record you read; do not abbreviate or invent one.",
    "discovery_only": "These records were only seen in search results: {refs}. Read each with "
    "get_evidence before citing it.",
    "evicted": "These records were read earlier but are no longer in front of you: {refs}. Read "
    "each again with get_evidence before citing it.",
    "note_only": "These records are named by an analysis note, which is an interpretation: "
    "{refs}. Read each with get_evidence before citing it.",
    "memo_only": "These records are cited in the memo text but were not read for this answer: "
    "{refs}. Read each with get_evidence before citing it.",
    "other_version": "These records belong to a memo version that is not selected for this "
    "question: {refs}. Cite only records of the selected memo, or ask for a comparison of versions.",
}
_ORDER = ("unknown", "discovery_only", "evicted", "note_only", "memo_only", "other_version")


@dataclass(frozen=True)
class CitationBasis:
    """Which records the model could cite, and which weaker claims to a record exist."""

    visible: frozenset[str]
    """Records read in full: cards, section reads, calculations and their operands, data results."""
    discovery: frozenset[str]
    """Records seen only as search hits."""
    notes: frozenset[str]
    """Records named by an analysis note."""
    memo_text: frozenset[str]
    """Records the memo's own text cites."""
    evicted: frozenset[str]
    """Records read in this question and then dropped from the request."""
    other_versions: frozenset[str]
    """Records that exist only in a memo version that is not selected."""

    def to_record(self) -> dict[str, list[str]]:
        """Plain, sorted lists, for the visibility record kept in the evidence store."""
        return {name: sorted(getattr(self, name)) for name in self.__dataclass_fields__}

    @classmethod
    def from_record(cls, record: Mapping[str, Iterable[str]]) -> CitationBasis:
        """Rebuild a basis from :meth:`to_record`; a missing set is empty."""
        return cls(**{name: frozenset(record.get(name, ())) for name in cls.__dataclass_fields__})

    def with_other_versions(self, refs: Collection[str]) -> CitationBasis:
        return CitationBasis(
            self.visible, self.discovery, self.notes, self.memo_text, self.evicted, frozenset(refs)
        )


@dataclass(frozen=True)
class Finding:
    """One distinct reference an answer cites, and what the model could actually see of it."""

    ref: str
    status: Status


def resolve(text: str, basis: CitationBasis) -> list[Finding]:
    """Judge each distinct reference of ``text``, in the order the answer first cites it."""
    findings: list[Finding] = []
    for ref in dict.fromkeys(_REFERENCE.findall(text)):
        if ref in basis.visible:
            status: Status = "valid"
        elif ref in basis.evicted:
            status = "evicted"
        elif ref in basis.discovery:
            status = "discovery_only"
        elif ref in basis.notes:
            status = "note_only"
        elif ref in basis.memo_text:
            status = "memo_only"
        elif ref in basis.other_versions:
            status = "other_version"
        else:
            status = "unknown"
        findings.append(Finding(ref, status))
    return findings


def citation_issues(findings: Sequence[Finding], *, needs_citation: bool) -> list[str]:
    """Blocking repair instructions, one per kind of shortfall, naming the references."""
    issues = []
    for status in _ORDER:
        refs = [finding.ref for finding in findings if finding.status == status]
        if refs:
            issues.append(_GUIDANCE[status].format(refs=", ".join(refs)))
    if needs_citation and not findings:
        issues.append("Cite at least one full source_ref from the evidence just retrieved.")
    return issues


def _references_in(payload: Mapping[str, Any]) -> set[str]:
    """Every ``source_ref`` of the records, cards and calculation operands of one result."""
    found: set[str] = set()
    sections = payload.get("sections")
    pages = sections.values() if isinstance(sections, dict) else [payload]
    for page in pages:
        for record in page.get("records", []):
            if isinstance(record, dict) and isinstance(record.get("source_ref"), str):
                found.add(record["source_ref"])
    for card in payload.get("cards", []):
        if isinstance(card, dict) and isinstance(card.get("source_ref"), str):
            found.add(card["source_ref"])
    receipt = payload.get("receipt")
    if isinstance(receipt, dict):
        if isinstance(receipt.get("ref"), str):
            found.add(receipt["ref"])
        found.update(
            operand["source_ref"]
            for operand in receipt.get("operands", [])
            if isinstance(operand, dict) and isinstance(operand.get("source_ref"), str)
        )
    return found


def framed_payload(message: ToolMessage) -> dict[str, Any]:
    content = message.content
    if not isinstance(content, str) or not content.startswith("<untrusted_tool_result"):
        return {}
    try:
        value = json.loads(content.split(">\n", 1)[1].rsplit("\n</untrusted_tool_result>", 1)[0])
    except (ValueError, IndexError):
        return {}
    return value if isinstance(value, dict) else {}


def refs_of(message: AnyMessage) -> tuple[Kind, set[str]]:
    """The records one message shows, and whether it shows them or only points at them."""
    if not isinstance(message, ToolMessage):
        return "none", set()
    if message.name == "search_evidence":
        results = framed_payload(message).get("results", [])
        return "discovery", {
            r["source_ref"] for r in results if isinstance(r, dict) and "source_ref" in r
        }
    if message.name == "get_analysis_note":
        note = framed_payload(message).get("note")
        refs = note.get("source_refs", []) if isinstance(note, dict) else []
        return "notes", {ref for ref in refs if isinstance(ref, str)}
    if message.name in EVIDENCE_TOOL_NAMES:
        return "visible", _references_in(framed_payload(message))
    found = _references_in(tool_payload(message))
    return ("visible", found) if found else ("none", set())


def basis_from_messages(
    messages: Sequence[AnyMessage],
    *,
    memo_texts: Iterable[str] = (),
    evicted_refs: Iterable[str] = (),
) -> CitationBasis:
    """What the model could cite, from the messages it was sent (results already restored)."""
    visible: set[str] = set()
    discovery: set[str] = set()
    notes: set[str] = set()
    for message in messages:
        kind, refs = refs_of(message)
        if kind == "visible":
            visible |= refs
        elif kind == "discovery":
            discovery |= refs
        elif kind == "notes":
            notes |= refs
    memo_refs = {ref for text in memo_texts for ref in _REFERENCE.findall(text)}
    return CitationBasis(
        frozenset(visible),
        frozenset(discovery - visible),
        frozenset(notes - visible),
        frozenset(memo_refs - visible),
        frozenset(set(evicted_refs) - visible),
        frozenset(),
    )


async def other_version_refs(
    store: EvidenceStore,
    scope: str,
    memos: Sequence[Mapping[str, Any]],
    selected_ids: Sequence[str],
    refs: Collection[str],
) -> set[str]:
    """Which of ``refs`` are records of a saved memo that is not selected for this question."""
    if not refs:
        return set()
    sources = [
        source
        for memo in memos
        if memo["memo_id"] not in selected_ids
        for source in memo.get("source_ids", [])
    ]
    if not sources:
        return set()
    wanted = {card_id(source, ref): ref for source in sources for ref in refs}
    found = await store.get_cards(scope, sources, list(wanted))
    return {wanted[card["card_id"]] for card in found if card["card_id"] in wanted}
