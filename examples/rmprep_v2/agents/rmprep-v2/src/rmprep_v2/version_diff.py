"""What changed between two versions of a memo, found by code from their stored cards.

A comparison question ("what is new in version 2?") cannot be answered by searching for its words:
the records that are new are exactly the ones the words do not describe. Each card carries its
record's ``source_ref``, so the same record in two versions can be paired and its values compared
exactly. Nothing is summarized and no model is involved.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from rmprep_v2.evidence_store import EvidenceStore

__all__ = ["VersionDiff", "comparable", "compare_cards", "memo_cards", "version_read_ahead"]

_PAGE = 50


def _record(card: Mapping[str, Any]) -> str:
    """The part of a card that is the record: its values and the limits of its section.

    The card ID, source ID, date and token estimate belong to the version, not to the record.
    """
    return json.dumps(
        {"fields": card["fields"], "limits": card["limits"]}, sort_keys=True, ensure_ascii=False
    )


@dataclass(frozen=True)
class VersionDiff:
    """The records only in the newer version, only in the older, and in both with other values."""

    added: list[dict[str, Any]] = field(default_factory=list)
    removed: list[dict[str, Any]] = field(default_factory=list)
    changed: list[tuple[dict[str, Any], dict[str, Any]]] = field(default_factory=list)
    """Each change as the older card and the newer card of one record."""
    unchanged: int = 0

    def summary(self) -> dict[str, int]:
        """How many records fall in each class: counts only, no value."""
        return {
            "added": len(self.added),
            "removed": len(self.removed),
            "changed": len(self.changed),
            "unchanged": self.unchanged,
        }

    def read_ahead(self, cap: int) -> list[str]:
        """The cards to read first: the newer side of what is new or changed, then the older side
        of what changed, then what was removed. At most ``cap``, each once."""
        wanted = [c["card_id"] for c in self.added]
        wanted += [after["card_id"] for _, after in self.changed]
        wanted += [before["card_id"] for before, _ in self.changed]
        wanted += [c["card_id"] for c in self.removed]
        return list(dict.fromkeys(wanted))[: max(0, cap)]


def compare_cards(
    older: Sequence[Mapping[str, Any]], newer: Sequence[Mapping[str, Any]]
) -> VersionDiff:
    """Pair the cards of two versions by record and say what was added, removed or changed."""
    before = {c["source_ref"]: dict(c) for c in older}
    after = {c["source_ref"]: dict(c) for c in newer}
    added = [dict(c) for ref, c in after.items() if ref not in before]
    removed = [dict(c) for ref, c in before.items() if ref not in after]
    changed = [
        (before[ref], card)
        for ref, card in after.items()
        if ref in before and _record(before[ref]) != _record(card)
    ]
    same = sum(1 for ref in after if ref in before) - len(changed)
    return VersionDiff(added=added, removed=removed, changed=changed, unchanged=same)


async def memo_cards(
    store: EvidenceStore, scope: str, source_ids: Sequence[str]
) -> list[dict[str, Any]]:
    """Every card of these sources, section by section in the order they were built."""
    cards: list[dict[str, Any]] = []
    for section in await store.list_sections(scope, source_ids):
        offset = 0
        while True:
            page, total = await store.section_cards(
                scope, source_ids, section, offset=offset, limit=_PAGE
            )
            cards += page
            offset += len(page)
            if not page or offset >= total:
                break
    return cards


def comparable(
    memos: Sequence[Mapping[str, Any]],
) -> tuple[Mapping[str, Any], Mapping[str, Any]] | None:
    """The older and the newer of two different versions of one client's memo, else ``None``.

    Two clients, a version without stored sources, or any other number of memos is not a
    comparison of versions, and nothing is paired.
    """
    if len(memos) != 2 or memos[0]["memo_id"] == memos[1]["memo_id"]:
        return None
    if memos[0]["uen"] != memos[1]["uen"]:
        return None
    if not all(memo.get("source_ids") for memo in memos):
        return None
    older, newer = sorted(memos, key=lambda memo: (memo["meeting_date"], memo["version"]))
    return older, newer


async def version_read_ahead(
    store: EvidenceStore, scope: str, memos: Sequence[Mapping[str, Any]], cap: int
) -> list[str]:
    """The cards to read first for a question about two versions of a memo; none for any other
    selection."""
    pair = comparable(memos)
    if pair is None:
        return []
    older, newer = pair
    diff = compare_cards(
        await memo_cards(store, scope, older["source_ids"]),
        await memo_cards(store, scope, newer["source_ids"]),
    )
    return diff.read_ahead(cap)
