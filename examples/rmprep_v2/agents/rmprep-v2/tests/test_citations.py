"""A citation is released only when the record behind it was in front of the model."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from agentcraft2_core.pipeline.guardrails import frame_untrusted
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, ToolMessage

from rmprep_v2.citations import (
    CitationBasis,
    basis_from_messages,
    citation_issues,
    other_version_refs,
    refs_of,
    resolve,
)
from rmprep_v2.evals.synthetic import cards_of
from rmprep_v2.evidence_store import SqliteEvidenceStore

DSCR = "covenants[name=DSCR]"
FCC = "covenants[name=FCC]"
WC = "working_capital[period_end=2026-06-30]"
FAC = "facilities[id=F1]"


def tool(name: str, body: dict[str, Any], identity: str = "t") -> ToolMessage:
    return ToolMessage(
        frame_untrusted(name, json.dumps(body)), tool_call_id=identity, name=name, id=identity
    )


def basis(**kwargs: Any) -> CitationBasis:
    fields: dict[str, Any] = {name: frozenset() for name in CitationBasis.__dataclass_fields__}
    fields.update({key: frozenset(value) for key, value in kwargs.items()})
    return CitationBasis(**fields)


# --- classifying what an answer cites ------------------------------------------------------------


def test_a_record_that_was_read_is_valid() -> None:
    findings = resolve(f"The DSCR is low. Source: {DSCR}.", basis(visible={DSCR}))
    assert [(f.ref, f.status) for f in findings] == [(DSCR, "valid")]
    assert citation_issues(findings, needs_citation=True) == []


@pytest.mark.parametrize(
    ("sets", "status"),
    [
        ({}, "unknown"),
        ({"discovery": {DSCR}}, "discovery_only"),
        ({"notes": {DSCR}}, "note_only"),
        ({"memo_text": {DSCR}}, "memo_only"),
        ({"evicted": {DSCR}}, "evicted"),
        ({"other_versions": {DSCR}}, "other_version"),
    ],
)
def test_each_way_a_reference_can_fall_short_has_its_own_status(
    sets: dict[str, set[str]], status: str
) -> None:
    findings = resolve(f"Source: {DSCR}.", basis(**sets))
    assert findings[0].status == status
    assert citation_issues(findings, needs_citation=False)


def test_reading_a_record_wins_over_every_weaker_claim_to_it() -> None:
    everything = basis(
        visible={DSCR}, discovery={DSCR}, notes={DSCR}, memo_text={DSCR}, evicted={DSCR}
    )
    assert resolve(f"Source: {DSCR}.", everything)[0].status == "valid"


def test_a_reference_that_was_read_and_then_dropped_is_evicted_not_valid() -> None:
    findings = resolve(f"Source: {DSCR}.", basis(evicted={DSCR}, discovery={DSCR}))
    assert findings[0].status == "evicted"


def test_every_distinct_reference_is_judged_once_in_order() -> None:
    text = f"A {FCC}. B {DSCR}. C {FCC}. D covenants[invented=1]."
    findings = resolve(text, basis(visible={DSCR, FCC}))
    assert [(f.ref, f.status) for f in findings] == [
        (FCC, "valid"),
        (DSCR, "valid"),
        ("covenants[invented=1]", "unknown"),
    ]


def test_the_guidance_says_what_to_do_for_each_status() -> None:
    text = f"{DSCR} {FCC} {WC} {FAC} covenants[x=1]"
    findings = resolve(
        text,
        basis(discovery={DSCR}, notes={FCC}, evicted={WC}, other_versions={FAC}),
    )
    guidance = " ".join(citation_issues(findings, needs_citation=False))
    assert "only seen in search results" in guidance
    assert "analysis note" in guidance
    assert "no longer in front of you" in guidance
    assert "not selected" in guidance
    assert "match no record" in guidance
    assert "get_evidence" in guidance
    for ref in (DSCR, FCC, WC, FAC, "covenants[x=1]"):
        assert ref in guidance


def test_an_answer_that_read_evidence_must_cite_something() -> None:
    nothing = resolve("A plain answer with no source.", basis(visible={DSCR}))
    assert citation_issues(nothing, needs_citation=True) == [
        "Cite at least one full source_ref from the evidence just retrieved."
    ]
    assert citation_issues(nothing, needs_citation=False) == []


def test_a_bad_citation_does_not_satisfy_the_duty_to_cite() -> None:
    findings = resolve("Source: covenants[invented=1].", basis(visible={DSCR}))
    issues = citation_issues(findings, needs_citation=True)
    assert len(issues) == 1 and "match no record" in issues[0]


# --- what the call could see ------------------------------------------------------------------------


def test_the_basis_separates_records_read_from_leads() -> None:
    messages: list[AnyMessage] = [
        HumanMessage("q", id="h"),
        tool("get_evidence", {"cards": [{"source_ref": DSCR}]}, "a"),
        tool("search_evidence", {"results": [{"source_ref": FCC}]}, "b"),
        tool("get_analysis_note", {"note": {"source_refs": [WC]}}, "c"),
    ]
    found = basis_from_messages(messages, memo_texts=[f"The memo cites {FAC}."])
    assert found.visible == {DSCR}
    assert found.discovery == {FCC}
    assert found.notes == {WC}
    assert found.memo_text == {FAC}


def test_a_search_hit_for_a_record_that_was_also_read_is_not_only_a_lead() -> None:
    messages: list[AnyMessage] = [
        tool("search_evidence", {"results": [{"source_ref": DSCR}]}, "b"),
        tool("get_memo_section", {"cards": [{"source_ref": DSCR}]}, "a"),
    ]
    found = basis_from_messages(messages)
    assert resolve(f"Source: {DSCR}.", found)[0].status == "valid"


def test_a_calculation_makes_its_own_reference_and_its_operands_citable() -> None:
    receipt = {
        "receipt": {
            "ref": "calculation[calc-abc123]",
            "operands": [{"source_ref": DSCR}, {"source_ref": FCC}],
        }
    }
    found = basis_from_messages([tool("calculate", receipt)])
    assert found.visible == {"calculation[calc-abc123]", DSCR, FCC}


def test_the_basis_round_trips_through_the_stored_record() -> None:
    original = basis(
        visible={DSCR}, discovery={FCC}, notes={WC}, memo_text={FAC}, evicted={"x[a=1]"}
    )
    assert CitationBasis.from_record(original.to_record()) == original
    assert json.dumps(original.to_record())  # plain JSON, sorted for a stable hash
    assert original.to_record()["visible"] == [DSCR]


def test_refs_of_one_message_names_which_kind_it_holds() -> None:
    assert refs_of(tool("get_evidence", {"cards": [{"source_ref": DSCR}]})) == ("visible", {DSCR})
    assert refs_of(tool("search_evidence", {"results": [{"source_ref": DSCR}]})) == (
        "discovery",
        {DSCR},
    )
    assert refs_of(tool("get_analysis_note", {"note": {"source_refs": [DSCR]}})) == (
        "notes",
        {DSCR},
    )
    assert refs_of(AIMessage("x")) == ("none", set())
    assert refs_of(ToolMessage("plain", tool_call_id="t", name="other")) == ("none", set())


# --- records of a version that is not selected -------------------------------------------------------


@pytest.fixture
async def store(tmp_path: Path) -> Any:
    async with SqliteEvidenceStore(tmp_path / "evidence.sqlite") as opened:
        for source, date in (("src-a", "2026-08-03"), ("src-b", "2026-07-03")):
            cards, notes = cards_of(source, date)
            await opened.put_cards("scope", source, cards, notes)
        yield opened


async def test_a_reference_of_an_unselected_memo_is_recognized(store: SqliteEvidenceStore) -> None:
    memos = [
        {"memo_id": "memo-a", "source_ids": ["src-a"]},
        {"memo_id": "memo-b", "source_ids": ["src-b"]},
    ]
    other = await other_version_refs(store, "scope", memos, ["memo-a"], {DSCR, "covenants[x=1]"})
    assert other == {DSCR}, "the unknown reference is not claimed by the other memo"


async def test_nothing_is_claimed_when_every_memo_is_selected(store: SqliteEvidenceStore) -> None:
    memos = [{"memo_id": "memo-a", "source_ids": ["src-a"]}]
    assert await other_version_refs(store, "scope", memos, ["memo-a"], {DSCR}) == set()
    assert await other_version_refs(store, "scope", memos, ["memo-a"], set()) == set()
