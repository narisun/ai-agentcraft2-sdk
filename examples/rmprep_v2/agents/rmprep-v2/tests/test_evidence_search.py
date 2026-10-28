"""Cards are stored once, searched by keyword inside one scope and one memo's sources."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from rmprep_v2.evals.synthetic import cards_of
from rmprep_v2.evidence_store import EvidenceConflict, SqliteEvidenceStore, fts_query

SCOPE = "tenant/alice/rmprep-v2/thread-1"
OTHER = "tenant/alice/rmprep-v2/thread-2"


@pytest.fixture
async def store(tmp_path: Path) -> Any:
    async with SqliteEvidenceStore(tmp_path / "evidence.sqlite") as opened:
        for source, date, dscr in (("src-a", "2026-08-03", 1.08), ("src-b", "2026-07-03", 1.31)):
            cards, notes = cards_of(source, date, dscr)
            await opened.put_cards(SCOPE, source, cards, notes)
        yield opened


async def test_a_keyword_search_finds_the_matching_card_first(store: SqliteEvidenceStore) -> None:
    page = await store.search_cards(SCOPE, ["src-a"], "DSCR covenant")
    assert page.hits[0].card["source_ref"] == "covenants[name=DSCR]"
    assert page.total >= 1


async def test_a_search_only_sees_the_sources_it_is_given(store: SqliteEvidenceStore) -> None:
    only_a = await store.search_cards(SCOPE, ["src-a"], "DSCR")
    assert {hit.card["source_id"] for hit in only_a.hits} == {"src-a"}
    both = await store.search_cards(SCOPE, ["src-a", "src-b"], "DSCR")
    assert {hit.card["source_id"] for hit in both.hits} == {"src-a", "src-b"}
    assert (await store.search_cards(SCOPE, [], "DSCR")).hits == []


async def test_a_search_never_crosses_scopes(store: SqliteEvidenceStore) -> None:
    assert (await store.search_cards(OTHER, ["src-a"], "DSCR")).hits == []
    assert await store.get_cards(OTHER, ["src-a"], [cards_of("src-a")[0][0]["card_id"]]) == []


async def test_a_section_filter_narrows_the_search(store: SqliteEvidenceStore) -> None:
    page = await store.search_cards(SCOPE, ["src-a"], "cash conversion cycle", section="covenants")
    assert page.hits == []
    page = await store.search_cards(
        SCOPE, ["src-a"], "cash conversion cycle", section="working_capital"
    )
    assert {hit.card["section"] for hit in page.hits} == {"working_capital"}


async def test_paging_reports_the_total_and_continues(store: SqliteEvidenceStore) -> None:
    first = await store.search_cards(SCOPE, ["src-a"], "covenant working capital facility", limit=2)
    second = await store.search_cards(
        SCOPE, ["src-a"], "covenant working capital facility", limit=2, offset=2
    )
    assert len(first.hits) == 2
    assert first.total == second.total > 2
    assert not {h.card["card_id"] for h in first.hits} & {h.card["card_id"] for h in second.hits}


async def test_banking_synonyms_widen_a_query(store: SqliteEvidenceStore) -> None:
    page = await store.search_cards(SCOPE, ["src-a"], "debt service coverage")
    assert any(hit.card["source_ref"] == "covenants[name=DSCR]" for hit in page.hits)


@pytest.mark.parametrize(
    "query",
    ['"', "*", "NEAR(a b)", "a OR", "DSCR -", "(", "'; DROP TABLE cards; --", "col:val", "é中"],
)
async def test_odd_queries_are_data_not_syntax(store: SqliteEvidenceStore, query: str) -> None:
    page = await store.search_cards(SCOPE, ["src-a"], query)
    assert page.total >= 0


async def test_an_empty_query_returns_nothing_and_says_why(store: SqliteEvidenceStore) -> None:
    page = await store.search_cards(SCOPE, ["src-a"], "   ")
    assert page.hits == []
    assert page.total == 0


def test_the_fts_query_keeps_words_and_drops_syntax() -> None:
    assert fts_query("DSCR") == '"dscr"'
    assert fts_query('alpha "beta" OR gamma*') == '"alpha" OR "beta" OR "gamma"'
    assert fts_query("Which covenants need discussion for this client?") == '"covenants"'
    assert fts_query("") == ""
    assert '"dscr"' in fts_query("debt service coverage")


async def test_cards_come_back_in_the_order_asked(store: SqliteEvidenceStore) -> None:
    cards, _ = cards_of("src-a")
    wanted = [cards[2]["card_id"], cards[0]["card_id"]]
    got = await store.get_cards(SCOPE, ["src-a"], [*wanted, "c-missing"])
    assert [card["card_id"] for card in got] == wanted
    assert got[0] == cards[2]


async def test_a_card_of_another_source_is_not_returned(store: SqliteEvidenceStore) -> None:
    b_card = cards_of("src-b", "2026-07-03", 1.31)[0][0]["card_id"]
    assert await store.get_cards(SCOPE, ["src-a"], [b_card]) == []


async def test_storing_the_same_cards_again_changes_nothing(store: SqliteEvidenceStore) -> None:
    cards, notes = cards_of("src-a")
    await store.put_cards(SCOPE, "src-a", cards, notes)
    assert (await store.search_cards(SCOPE, ["src-a"], "DSCR")).total == 1


async def test_a_changed_card_under_the_same_id_is_refused(store: SqliteEvidenceStore) -> None:
    cards, notes = cards_of("src-a", dscr=9.99)
    with pytest.raises(EvidenceConflict):
        await store.put_cards(SCOPE, "src-a", cards, notes)


async def test_sections_notes_and_section_listing(store: SqliteEvidenceStore) -> None:
    assert await store.list_sections(SCOPE, ["src-a"]) == {
        "covenants": 2,
        "working_capital": 2,
        "facilities": 1,
    }
    notes = await store.list_notes(SCOPE, ["src-a"])
    assert [note["code"] for note in notes] == ["COVENANT_BREACH_RISK"]
    assert (await store.get_note(SCOPE, ["src-a"], notes[0]["note_id"])) == notes[0]
    assert await store.get_note(SCOPE, ["src-b"], notes[0]["note_id"]) is None
    page, total = await store.section_cards(SCOPE, ["src-a"], "working_capital", offset=1, limit=5)
    assert total == 2
    assert [card["source_ref"] for card in page] == ["working_capital[period_end=2026-06-30]"]


async def test_retiring_a_scope_removes_its_cards_and_index(store: SqliteEvidenceStore) -> None:
    await store.retire_scope(SCOPE)
    assert (await store.search_cards(SCOPE, ["src-a"], "DSCR")).hits == []
    assert await store.list_sections(SCOPE, ["src-a"]) == {}
