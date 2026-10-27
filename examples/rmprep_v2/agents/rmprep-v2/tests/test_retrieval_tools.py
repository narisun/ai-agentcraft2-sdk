"""The retrieval tools answer from the selected memo's stored evidence, within size limits."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
from agentcraft2_core.pipeline.guardrails import frame_untrusted
from agentcraft2_core.testing import calls_tool
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from rmprep_v2.cards import json_tokens
from rmprep_v2.context_tools import tool_context_issues
from rmprep_v2.evals.harness import CALL_MEMO_EVIDENCE, COVENANT_REF, MEMO_REQUEST, converse
from rmprep_v2.evals.synthetic import cards_of
from rmprep_v2.evidence_store import SqliteEvidenceStore
from rmprep_v2.examples.trace_react import MEMO
from rmprep_v2.response_review import response_issues
from rmprep_v2.retrieval import (
    RETRIEVAL_TOOL_NAMES,
    get_analysis_note,
    get_evidence,
    get_memo_section,
    search_evidence,
    selected_memos,
)
from rmprep_v2.selection import ContextSelection

SCOPE = "tenant/alice/rmprep-v2/thread-1"
OTHER = "tenant/alice/rmprep-v2/thread-2"
DSCR = "covenants[name=DSCR]"


def memo(memo_id: str, source: str, date: str, version: int = 1) -> dict[str, Any]:
    return {
        "memo_id": memo_id,
        "version": version,
        "uen": "JONE-0022",
        "meeting_date": date,
        "source_ids": [source],
    }


A: Any = memo("memo-aaaaaaaaaaaa", "src-a", "2026-08-03", 2)
B: Any = memo("memo-bbbbbbbbbbbb", "src-b", "2026-07-03", 1)


@pytest.fixture
async def store(tmp_path: Path) -> Any:
    async with SqliteEvidenceStore(tmp_path / "evidence.sqlite") as opened:
        for source, date, dscr in (("src-a", "2026-08-03", 1.08), ("src-b", "2026-07-03", 1.31)):
            cards, notes = cards_of(source, date, dscr)
            await opened.put_cards(SCOPE, source, cards, notes)
        cards, notes = cards_of("src-big", filler=10)
        await opened.put_cards(SCOPE, "src-big", cards, notes)
        yield opened


def ids_of(source: str, section: str | None = None, **kwargs: Any) -> list[str]:
    cards, _ = cards_of(source, **kwargs)
    return [c["card_id"] for c in cards if section is None or c["section"] == section]


# --- search -----------------------------------------------------------------------------


async def test_search_returns_discovery_results_within_the_cap(store: SqliteEvidenceStore) -> None:
    result = await search_evidence(store, SCOPE, [A], "DSCR covenant")
    assert result["status"] == "ok"
    first = result["results"][0]
    assert first["source_ref"] == DSCR
    assert first["memo_id"] == A["memo_id"]
    assert first["as_of"] == "2026-08-03"
    assert len(first["snippet"].split()) <= 60
    assert "current_value" in first["snippet"]
    assert json_tokens(result) <= 2_000
    assert "discovery" in result["note"].lower()
    assert result["total_matches"] >= 1
    assert result["coverage"]["cards_in_memo"] == 5


async def test_search_pages_and_clamps_its_limit(store: SqliteEvidenceStore) -> None:
    page = await search_evidence(store, SCOPE, [A], "covenant working capital", limit=1000)
    assert page["returned"] <= 10
    small = await search_evidence(store, SCOPE, [A], "covenant working capital", limit=1)
    assert small["returned"] == 1
    assert small["next_offset"] == 1
    last = await search_evidence(
        store, SCOPE, [A], "covenant working capital", limit=1, offset=small["total_matches"] - 1
    )
    assert last["next_offset"] is None


async def test_search_shortens_a_result_page_that_would_exceed_its_cap(
    store: SqliteEvidenceStore,
) -> None:
    sources = {
        "memo_id": "memo-cccccccccccc",
        "version": 1,
        "meeting_date": "x",
        "source_ids": ["src-big"],
    }
    result = await search_evidence(store, SCOPE, [sources], "covenant", limit=10)
    assert json_tokens(result) <= 2_000
    assert result["truncated"] is True
    assert result["returned"] < 10
    assert result["next_offset"] == result["returned"]


async def test_search_with_two_memos_labels_each_hit_with_its_own_memo(
    store: SqliteEvidenceStore,
) -> None:
    result = await search_evidence(store, SCOPE, [A, B], "DSCR")
    labels = {(hit["memo_id"], hit["as_of"]) for hit in result["results"]}
    assert labels == {(A["memo_id"], "2026-08-03"), (B["memo_id"], "2026-07-03")}


async def test_search_only_covers_the_selected_memos(store: SqliteEvidenceStore) -> None:
    result = await search_evidence(store, SCOPE, [A], "DSCR")
    assert {hit["memo_id"] for hit in result["results"]} == {A["memo_id"]}


async def test_search_with_no_selected_memo_says_so(store: SqliteEvidenceStore) -> None:
    result = await search_evidence(store, SCOPE, [], "DSCR")
    assert result["status"] == "no_memo"
    assert "get_client_records" in result["message"]


async def test_a_memo_without_stored_evidence_is_reported(store: SqliteEvidenceStore) -> None:
    legacy = {"memo_id": "memo-dddddddddddd", "version": 1, "meeting_date": "x"}
    result = await search_evidence(store, SCOPE, [legacy], "DSCR")
    assert result["status"] == "no_stored_evidence"


async def test_another_scope_finds_nothing(store: SqliteEvidenceStore) -> None:
    result = await search_evidence(store, OTHER, [A], "DSCR")
    assert result["status"] == "ok"
    assert result["results"] == []
    assert result["total_matches"] == 0
    assert "does not show" in result["note"]


# --- get_evidence -----------------------------------------------------------------------


async def test_get_evidence_returns_whole_cards_with_their_memo(store: SqliteEvidenceStore) -> None:
    wanted = ids_of("src-a", "covenants")
    result = await get_evidence(store, SCOPE, [A], wanted)
    assert result["status"] == "ok"
    assert [c["card_id"] for c in result["cards"]] == wanted
    dscr = result["cards"][0]
    assert dscr["source_ref"] == DSCR
    assert dscr["fields"] == {"name": "DSCR", "current_value": 1.08}
    assert dscr["memo_id"] == A["memo_id"]
    assert dscr["limits"] == ["Covenant tests as recorded."]
    assert result["not_found"] == []


async def test_get_evidence_does_not_return_another_memos_card(store: SqliteEvidenceStore) -> None:
    other_version = ids_of("src-b", "covenants", meeting_date="2026-07-03", dscr=1.31)
    result = await get_evidence(store, SCOPE, [A], other_version)
    assert result["cards"] == []
    assert result["not_found"] == other_version


async def test_get_evidence_reports_unknown_ids_and_never_guesses(
    store: SqliteEvidenceStore,
) -> None:
    result = await get_evidence(store, SCOPE, [A], ["c-nonexistent"])
    assert result["cards"] == []
    assert result["not_found"] == ["c-nonexistent"]


async def test_get_evidence_stops_at_its_cap_and_lists_what_it_left(
    store: SqliteEvidenceStore,
) -> None:
    big = {
        "memo_id": "memo-cccccccccccc",
        "version": 1,
        "meeting_date": "x",
        "source_ids": ["src-big"],
    }
    wanted = ids_of("src-big", "notes", filler=10)
    result = await get_evidence(store, SCOPE, [big], wanted)
    assert json_tokens(result) <= 3_000
    assert 1 <= len(result["cards"]) < 10
    left = [c["card_id"] for c in result["cards"]]
    assert result["omitted_ids"] == wanted[len(left) : 10]
    assert result["truncated"] is True


async def test_get_evidence_takes_at_most_ten_ids(store: SqliteEvidenceStore) -> None:
    result = await get_evidence(store, SCOPE, [A], [f"c-{n}" for n in range(25)])
    assert len(result["not_found"]) == 10
    assert result["skipped_ids"] == [f"c-{n}" for n in range(10, 25)]


async def test_get_evidence_in_another_scope_finds_nothing(store: SqliteEvidenceStore) -> None:
    result = await get_evidence(store, OTHER, [A], ids_of("src-a", "covenants"))
    assert result["cards"] == []


# --- get_memo_section --------------------------------------------------------------------


async def test_a_memo_section_lists_its_cards_in_order(store: SqliteEvidenceStore) -> None:
    result = await get_memo_section(store, SCOPE, [A], "working_capital")
    assert result["status"] == "ok"
    assert result["memo_id"] == A["memo_id"]
    assert [c["source_ref"] for c in result["cards"]] == [
        "working_capital[period_end=2026-03-30]",
        "working_capital[period_end=2026-06-30]",
    ]
    assert result["total_cards"] == 2
    assert result["next_offset"] is None


async def test_a_memo_section_pages_when_it_would_exceed_the_cap(
    store: SqliteEvidenceStore,
) -> None:
    big = {
        "memo_id": "memo-cccccccccccc",
        "version": 1,
        "meeting_date": "x",
        "source_ids": ["src-big"],
    }
    first = await get_memo_section(store, SCOPE, [big], "notes")
    assert json_tokens(first) <= 3_000
    assert first["truncated"] is True
    second = await get_memo_section(store, SCOPE, [big], "notes", offset=first["next_offset"])
    assert second["cards"][0]["source_ref"] != first["cards"][0]["source_ref"]


async def test_an_unknown_section_lists_the_ones_that_exist(store: SqliteEvidenceStore) -> None:
    result = await get_memo_section(store, SCOPE, [A], "weather")
    assert result["status"] == "unknown_section"
    assert result["available"] == {"covenants": 2, "working_capital": 2, "facilities": 1}


async def test_two_selected_memos_need_a_memo_to_be_named(store: SqliteEvidenceStore) -> None:
    result = await get_memo_section(store, SCOPE, [A, B], "covenants")
    assert result["status"] == "memo_required"
    assert result["memos"] == [A["memo_id"], B["memo_id"]]
    named = await get_memo_section(store, SCOPE, [A, B], "covenants", memo_id=B["memo_id"])
    assert named["status"] == "ok"
    assert named["meeting_date"] == "2026-07-03"
    assert named["cards"][0]["fields"]["current_value"] == 1.31


async def test_a_memo_that_is_not_selected_is_refused(store: SqliteEvidenceStore) -> None:
    result = await get_memo_section(store, SCOPE, [A], "covenants", memo_id=B["memo_id"])
    assert result["status"] == "memo_not_selected"
    assert "cards" not in result


# --- notes --------------------------------------------------------------------------------


async def test_an_analysis_note_names_its_supporting_cards(store: SqliteEvidenceStore) -> None:
    _, notes = cards_of("src-a")
    result = await get_analysis_note(store, SCOPE, [A], notes[0]["note_id"])
    assert result["status"] == "ok"
    note = result["note"]
    assert note["code"] == "COVENANT_BREACH_RISK"
    assert note["supporting_card_ids"] == ids_of("src-a", "covenants")[:1]
    assert "interpretation" in " ".join(note["limitations"]).lower()
    assert json_tokens(result) <= 1_500


async def test_an_unknown_or_foreign_note_is_not_found(store: SqliteEvidenceStore) -> None:
    _, notes = cards_of("src-a")
    assert (await get_analysis_note(store, SCOPE, [A], "n-nothing"))["status"] == "not_found"
    assert (await get_analysis_note(store, SCOPE, [B], notes[0]["note_id"]))[
        "status"
    ] == "not_found"
    assert (await get_analysis_note(store, OTHER, [A], notes[0]["note_id"]))[
        "status"
    ] == "not_found"


# --- which memos are selected -------------------------------------------------------------


def test_the_selected_memos_come_from_the_applications_selection_not_the_model() -> None:
    state = {
        "memos": [A, B, memo("memo-eeeeeeeeeeee", "src-e", "2026-05-01")],
        "selection": {"memo_ids": [B["memo_id"], A["memo_id"]]},
    }
    assert [m["memo_id"] for m in selected_memos(state)] == [B["memo_id"], A["memo_id"]]
    assert selected_memos({"memos": [A]}) == []
    assert selected_memos({"memos": [A], "selection": {"memo_ids": ["memo-unknown"]}}) == []


# --- guards ------------------------------------------------------------------------------


SELECTION: ContextSelection = {
    "memo_ids": [A["memo_id"]],
    "uen": "JONE-0022",
    "client_uens": ["JONE-0022"],
    "meeting_date": "2026-08-03",
    "retrieval_date": "",
    "mode": "snapshot",
    "reason": "test",
    "clarification": "",
}


def call(name: str, **args: Any) -> AIMessage:
    return AIMessage("", tool_calls=[{"name": name, "args": args, "id": "c1"}])


def test_an_id_tool_needs_no_date_argument() -> None:
    for name, args in (
        ("search_evidence", {"query": "DSCR"}),
        ("get_evidence", {"card_ids": ["c-1"]}),
        ("get_analysis_note", {"note_id": "n-1"}),
        ("get_memo_section", {"section": "covenants"}),
    ):
        assert tool_context_issues(call(name, **args), SELECTION, [A], "2026-10-27") == []


def test_a_retrieval_call_without_a_selected_memo_is_refused_before_it_runs() -> None:
    nothing: ContextSelection = {**SELECTION, "memo_ids": []}
    issues = tool_context_issues(call("search_evidence", query="x"), nothing, [A], "2026-10-27")
    assert any("saved memo" in issue for issue in issues)


def test_a_section_call_naming_an_unselected_memo_is_refused_before_it_runs() -> None:
    issues = tool_context_issues(
        call("get_memo_section", section="covenants", memo_id=B["memo_id"]),
        SELECTION,
        [A, B],
        "2026-10-27",
    )
    assert any("selected memo" in issue for issue in issues)


def test_the_tool_names_are_the_four_retrieval_tools() -> None:
    assert RETRIEVAL_TOOL_NAMES == (
        "search_evidence",
        "get_evidence",
        "get_memo_section",
        "get_analysis_note",
    )


# --- citations --------------------------------------------------------------------------


def framed(name: str, body: dict[str, Any]) -> ToolMessage:
    return ToolMessage(
        frame_untrusted(name, json.dumps(body)), tool_call_id="t1", name=name, id=f"m-{name}"
    )


ANSWER = AIMessage(f"The DSCR is below its threshold. Source: {DSCR}.")


def test_a_search_hit_alone_does_not_make_a_reference_citable() -> None:
    search = framed("search_evidence", {"results": [{"source_ref": DSCR}]})
    issues = response_issues(ANSWER, [HumanMessage("q", id="h"), search], 350)
    assert any("do not exactly match" in issue for issue in issues)


def test_a_fetched_card_makes_its_reference_citable() -> None:
    fetched = framed("get_evidence", {"cards": [{"source_ref": DSCR, "fields": {}}]})
    assert response_issues(ANSWER, [HumanMessage("q", id="h"), fetched], 350) == []


def test_a_note_does_not_make_references_citable() -> None:
    note = framed("get_analysis_note", {"note": {"source_refs": [DSCR]}})
    issues = response_issues(ANSWER, [HumanMessage("q", id="h"), note], 350)
    assert any("do not exactly match" in issue for issue in issues)


# --- through the graph ------------------------------------------------------------------


def body_of(message: Any) -> dict[str, Any]:
    text = str(message.content)
    return cast(
        dict[str, Any],
        json.loads(text.split(">\n", 1)[1].rsplit("\n</untrusted_tool_result>", 1)[0]),
    )


async def test_the_model_searches_reads_a_section_and_cites_a_stored_card(tmp_path: Path) -> None:
    final = (
        "The DSCR covenant is recorded below its threshold; verify cure status with Credit. "
        f"Source: {COVENANT_REF}."
    )
    async with SqliteEvidenceStore(tmp_path / "evidence.sqlite") as store:
        run = await converse(
            tmp_path,
            CALL_MEMO_EVIDENCE,
            MEMO,
            calls_tool("search_evidence", query="DSCR covenant"),
            calls_tool("get_memo_section", section="covenants"),
            final,
            store=store,
            questions=(MEMO_REQUEST, "Explain the DSCR covenant in that memo."),
        )
    assert str(run.messages[-1].content) == final
    tools = [m for m in run.messages if isinstance(m, ToolMessage)]
    assert [m.name for m in tools[-2:]] == ["search_evidence", "get_memo_section"]
    found, section = body_of(tools[-2]), body_of(tools[-1])
    assert found["status"] == "ok"
    assert found["results"][0]["memo_id"] == run.memos[0]["memo_id"]
    assert section["cards"][0]["source_ref"] == COVENANT_REF
    assert "current_value" in section["cards"][0]["fields"]
    assert section["memo_id"] == run.memos[0]["memo_id"]


async def test_a_card_id_the_memo_does_not_hold_is_reported_not_guessed(tmp_path: Path) -> None:
    async with SqliteEvidenceStore(tmp_path / "evidence.sqlite") as store:
        run = await converse(
            tmp_path,
            CALL_MEMO_EVIDENCE,
            MEMO,
            calls_tool("get_evidence", card_ids=["c-invented"]),
            "The stored evidence does not include that record, so I cannot answer from it.",
            store=store,
            questions=(MEMO_REQUEST, "Explain the DSCR covenant in that memo."),
        )
    result = body_of([m for m in run.messages if isinstance(m, ToolMessage)][-1])
    assert result["cards"] == []
    assert result["not_found"] == ["c-invented"]


def test_a_model_is_shown_no_scope_state_or_date_arguments() -> None:
    from langchain_core.utils.function_calling import convert_to_openai_tool

    from rmprep_v2.evidence_store import SqliteEvidenceStore as Store
    from rmprep_v2.retrieval import retrieval_tools

    shown = {
        tool.name: set(convert_to_openai_tool(tool)["function"]["parameters"]["properties"])
        for tool in retrieval_tools(Store(":memory:"))
    }
    assert shown == {
        "search_evidence": {"query", "section", "limit", "offset"},
        "get_evidence": {"card_ids"},
        "get_memo_section": {"section", "memo_id", "offset"},
        "get_analysis_note": {"note_id"},
    }
