"""What changed between two versions of a memo, found by code from their stored cards."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from rmprep_v2.cards import card_id
from rmprep_v2.evidence_store import SqliteEvidenceStore
from rmprep_v2.version_diff import comparable, compare_cards, memo_cards, version_read_ahead

OLD, NEW = "src-old", "src-new"


def card(source: str, ref: str, *, value: Any = 1, as_of: str = "2026-07-03", **more: Any) -> Any:
    return {
        "card_id": card_id(source, ref),
        "source_id": source,
        "section": ref.split("[", 1)[0],
        "source_ref": ref,
        "fields": {"value": value, **more.pop("fields", {})},
        "limits": more.pop("limits", []),
        "as_of": as_of,
        "tokens": 40,
        **more,
    }


def memo(
    identity: str, version: int, date: str, *, uen: str = "JONE-0022", sources: Any = None
) -> Any:
    return {
        "memo_id": identity,
        "version": version,
        "uen": uen,
        "meeting_date": date,
        "source_ids": ["src-" + identity] if sources is None else sources,
    }


# --- comparing two sets of cards ---------------------------------------------------------------------


def test_records_only_in_the_newer_version_are_added_and_only_in_the_older_are_removed() -> None:
    older = [card(OLD, "a[id=1]"), card(OLD, "b[id=1]")]
    newer = [card(NEW, "a[id=1]"), card(NEW, "c[id=1]")]
    diff = compare_cards(older, newer)
    assert [c["source_ref"] for c in diff.added] == ["c[id=1]"]
    assert [c["source_ref"] for c in diff.removed] == ["b[id=1]"]
    assert diff.changed == []
    assert diff.unchanged == 1


def test_a_record_whose_values_differ_is_changed_and_keeps_both_sides() -> None:
    older = [card(OLD, "a[id=1]", value=1)]
    newer = [card(NEW, "a[id=1]", value=2)]
    (before, after), *rest = compare_cards(older, newer).changed
    assert rest == []
    assert (before["fields"]["value"], after["fields"]["value"]) == (1, 2)
    assert (before["source_id"], after["source_id"]) == (OLD, NEW)


def test_a_change_in_a_limit_is_a_change() -> None:
    older = [card(OLD, "a[id=1]", limits=[])]
    newer = [card(NEW, "a[id=1]", limits=["The source was incomplete."])]
    assert len(compare_cards(older, newer).changed) == 1


def test_only_the_record_decides_what_is_the_same() -> None:
    """Another source id, card id, date or token estimate is not a change to the record."""
    older = [card(OLD, "a[id=1]", as_of="2026-07-03")]
    newer = [card(NEW, "a[id=1]", as_of="2026-08-03", tokens=99)]
    diff = compare_cards(older, newer)
    assert (diff.added, diff.removed, diff.changed, diff.unchanged) == ([], [], [], 1)


def test_the_order_of_keys_inside_a_record_does_not_matter() -> None:
    older = [card(OLD, "a[id=1]", fields={"x": 1, "y": 2})]
    newer = [card(NEW, "a[id=1]", fields={"y": 2, "x": 1})]
    assert compare_cards(older, newer).changed == []


def test_two_identical_versions_differ_in_nothing() -> None:
    cards = [card(OLD, "a[id=1]"), card(OLD, "a[id=2]")]
    same = [card(NEW, "a[id=1]"), card(NEW, "a[id=2]")]
    diff = compare_cards(cards, same)
    assert not (diff.added or diff.removed or diff.changed)
    assert diff.read_ahead(10) == []


def test_the_diff_keeps_the_order_the_cards_were_built_in() -> None:
    newer = [card(NEW, f"s[id={n}]") for n in (3, 1, 2)]
    assert [c["source_ref"] for c in compare_cards([], newer).added] == [
        "s[id=3]",
        "s[id=1]",
        "s[id=2]",
    ]


# --- what to read first -------------------------------------------------------------------------------


def test_read_ahead_puts_the_newer_side_first_then_the_older_side_of_changes_then_removals() -> (
    None
):
    older = [card(OLD, "k[id=1]", value=1), card(OLD, "g[id=1]")]
    newer = [card(NEW, "k[id=1]", value=2), card(NEW, "n[id=1]")]
    assert compare_cards(older, newer).read_ahead(10) == [
        card_id(NEW, "n[id=1]"),
        card_id(NEW, "k[id=1]"),
        card_id(OLD, "k[id=1]"),
        card_id(OLD, "g[id=1]"),
    ]


def test_read_ahead_respects_its_cap_and_never_repeats_a_card() -> None:
    older = [card(OLD, f"k[id={n}]", value=0) for n in range(8)]
    newer = [card(NEW, f"k[id={n}]", value=1) for n in range(8)]
    ids = compare_cards(older, newer).read_ahead(10)
    assert len(ids) == 10
    assert len(set(ids)) == 10
    assert ids[:8] == [card_id(NEW, f"k[id={n}]") for n in range(8)]


def test_a_summary_counts_without_naming_a_value() -> None:
    older = [card(OLD, "k[id=1]", value="SECRET-OLD"), card(OLD, "g[id=1]")]
    newer = [card(NEW, "k[id=1]", value="SECRET-NEW"), card(NEW, "n[id=1]")]
    summary = compare_cards(older, newer).summary()
    assert summary == {"added": 1, "removed": 1, "changed": 1, "unchanged": 0}


# --- which memos can be compared ----------------------------------------------------------------------


def test_two_versions_of_one_client_are_compared_oldest_first() -> None:
    later = memo("m2", 2, "2026-08-03")
    earlier = memo("m1", 1, "2026-07-03")
    assert comparable([later, earlier]) == (earlier, later)


def test_the_same_meeting_date_is_ordered_by_version() -> None:
    first, second = memo("m1", 1, "2026-08-03"), memo("m2", 2, "2026-08-03")
    assert comparable([second, first]) == (first, second)


@pytest.mark.parametrize(
    "memos",
    [
        [],
        [memo("m1", 1, "2026-07-03")],
        [memo("m1", 1, "2026-07-03"), memo("m2", 2, "2026-08-03"), memo("m3", 3, "2026-09-03")],
        [memo("m1", 1, "2026-07-03"), memo("m2", 1, "2026-07-03", uen="CANA-0010")],
        [memo("m1", 1, "2026-07-03", sources=[]), memo("m2", 2, "2026-08-03")],
        [memo("m1", 1, "2026-07-03"), memo("m1", 1, "2026-07-03")],
    ],
)
def test_other_selections_are_not_a_version_comparison(memos: list[Any]) -> None:
    assert comparable(memos) is None


# --- reading the cards of a memo from the store -----------------------------------------------------------


async def test_every_card_of_a_memo_is_loaded_across_sections_in_build_order(
    tmp_path: Path,
) -> None:
    cards = [card("s1", f"{section}[id={n}]") for section in ("alpha", "beta") for n in range(3)]
    async with SqliteEvidenceStore(tmp_path / "evidence.sqlite") as store:
        await store.put_cards("scope", "s1", cards, [])
        await store.put_cards("scope", "s2", [card("s2", "alpha[id=9]")], [])
        loaded = await memo_cards(store, "scope", ["s1"])
        assert [c["source_ref"] for c in loaded] == [c["source_ref"] for c in cards]
        both = await memo_cards(store, "scope", ["s1", "s2"])
        assert len(both) == 7


async def test_a_section_larger_than_one_page_is_loaded_whole(tmp_path: Path) -> None:
    cards = [card("s1", f"big[id={n}]") for n in range(130)]
    async with SqliteEvidenceStore(tmp_path / "evidence.sqlite") as store:
        await store.put_cards("scope", "s1", cards, [])
        assert len(await memo_cards(store, "scope", ["s1"])) == 130


# --- what the Q&A graph reads ahead for a comparison ---------------------------------------------------


async def test_a_version_comparison_reads_ahead_what_differs(tmp_path: Path) -> None:
    async with SqliteEvidenceStore(tmp_path / "evidence.sqlite") as store:
        await store.put_cards(
            "scope", "src-m1", [card("src-m1", "a[id=1]"), card("src-m1", "b[id=1]")], []
        )
        await store.put_cards(
            "scope",
            "src-m2",
            [
                card("src-m2", "a[id=1]"),
                card("src-m2", "b[id=1]", value=2),
                card("src-m2", "c[id=1]"),
            ],
            [],
        )
        memos = [memo("m2", 2, "2026-08-03"), memo("m1", 1, "2026-07-03")]
        assert await version_read_ahead(store, "scope", memos, 10) == [
            card_id("src-m2", "c[id=1]"),
            card_id("src-m2", "b[id=1]"),
            card_id("src-m1", "b[id=1]"),
        ]


async def test_nothing_is_read_ahead_for_a_selection_that_is_not_a_version_comparison(
    tmp_path: Path,
) -> None:
    async with SqliteEvidenceStore(tmp_path / "evidence.sqlite") as store:
        await store.put_cards("scope", "src-m1", [card("src-m1", "a[id=1]")], [])
        assert await version_read_ahead(store, "scope", [memo("m1", 1, "2026-07-03")], 10) == []
