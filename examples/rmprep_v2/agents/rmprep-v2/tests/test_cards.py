"""Evidence cards are exact, small views of single records, and the directory stays small."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from rmprep_v2.cards import (
    CARD_TOKEN_TARGET,
    build_cards,
    card_id,
    directory,
    json_tokens,
)
from rmprep_v2.evals.packs import load_packs, references

SOURCE = "src-0123456789abcdef01234567"
DSCR = "covenants[facility_id=FAC000000130;name=DSCR]"


@pytest.fixture(scope="module")
def pack() -> dict[str, Any]:
    packs = asyncio.run(load_packs([("JONE-0022", "2026-08-03")]))
    return packs[("JONE-0022", "2026-08-03")]


def test_every_record_becomes_one_card_with_its_own_reference(pack: dict[str, Any]) -> None:
    cards, _ = build_cards(SOURCE, pack)
    assert sorted(card["source_ref"] for card in cards) == sorted(references(pack))
    assert len({card["card_id"] for card in cards}) == len(cards)
    assert all(card["source_id"] == SOURCE for card in cards)


def test_a_card_keeps_the_exact_values_of_its_record(pack: dict[str, Any]) -> None:
    cards, _ = build_cards(SOURCE, pack)
    dscr = next(card for card in cards if card["source_ref"] == DSCR)
    record = next(r for r in pack["sections"]["covenants"]["records"] if r["source_ref"] == DSCR)
    assert dscr["section"] == "covenants"
    assert dscr["fields"] == {k: v for k, v in record.items() if k != "source_ref"}
    assert dscr["as_of"] == "2026-08-03"


def test_card_ids_are_stable_and_depend_on_the_source(pack: dict[str, Any]) -> None:
    first, _ = build_cards(SOURCE, pack)
    again, _ = build_cards(SOURCE, pack)
    other, _ = build_cards("src-other", pack)
    assert [c["card_id"] for c in first] == [c["card_id"] for c in again]
    assert {c["card_id"] for c in first}.isdisjoint(c["card_id"] for c in other)
    assert card_id(SOURCE, DSCR) == next(c["card_id"] for c in first if c["source_ref"] == DSCR)


def test_cards_stay_within_the_token_target(pack: dict[str, Any]) -> None:
    cards, _ = build_cards(SOURCE, pack)
    assert max(card["tokens"] for card in cards) <= CARD_TOKEN_TARGET
    assert not any(card.get("oversized") for card in cards)


def test_a_card_carries_the_limits_of_its_section(pack: dict[str, Any]) -> None:
    cards, _ = build_cards(SOURCE, pack)
    covenant = next(c for c in cards if c["section"] == "covenants")
    assert covenant["limits"], "the section's own note travels with every card"
    page = pack["sections"]["covenants"]
    assert covenant["limits"][0] == page["note"]
    for key, value in page["evidence_limits"].items():
        assert f"{key}: {value}" in covenant["limits"]


def test_a_record_larger_than_the_target_is_flagged_not_cut() -> None:
    big = {
        "uen": "X-1",
        "sections": {
            "notes": {
                "records": [{"source_ref": "notes[id=1]", "text": "word " * 3_000}],
                "note": "free text",
            }
        },
    }
    (card,), _ = build_cards(SOURCE, big)
    assert card["oversized"] is True
    assert card["fields"]["text"] == "word " * 3_000
    assert card["tokens"] > CARD_TOKEN_TARGET


def test_notes_are_the_review_findings_with_the_cards_behind_them(
    pack: dict[str, Any],
) -> None:
    cards, notes = build_cards(SOURCE, pack)
    by_ref = {card["source_ref"]: card["card_id"] for card in cards}
    assert len(notes) == len(pack["review_findings"])
    first = notes[0]
    assert first["code"] == pack["review_findings"][0]["code"]
    assert first["conclusion"] == pack["review_findings"][0]["message"]
    assert first["supporting_card_ids"] == [by_ref[r] for r in first["source_refs"]]
    assert len({note["note_id"] for note in notes}) == len(notes)
    assert all(note["tokens"] <= 1_500 for note in notes)


def test_the_directory_is_a_small_map_not_the_evidence(pack: dict[str, Any]) -> None:
    cards, notes = build_cards(SOURCE, pack)
    shown = directory(cards, notes, pack)
    assert json_tokens(shown) <= 2_000
    assert shown["sections"]["covenants"] == {"cards": 2}
    assert "borrowing_base" not in shown["sections"], "an empty section is reported as missing"
    assert "borrowing_base" in shown["missing_sections"]
    assert shown["notes"][0]["code"] == notes[0]["code"]
    assert shown["notes"][0]["note_id"] == notes[0]["note_id"]
    assert shown["as_of"] == "2026-08-03"
    assert shown["limitations"] == pack["limitations"]
    # It names evidence and says nothing a reader could cite as a fact.
    assert DSCR not in str(shown)
