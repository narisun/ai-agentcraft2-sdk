"""What a reader is told while a question is answered: fixed words, never drafts or evidence."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage

from rmprep_v2 import activity
from rmprep_v2.activity import tool_activity
from rmprep_v2.evals.harness import COVENANT_REF
from rmprep_v2.evidence_store import SqliteEvidenceStore
from rmprep_v2.settings import QnaLimits

from .test_qna_graph import (  # type: ignore[import-not-found]  # mypy cannot resolve a relative import inside the hyphenated agent folder
    BAD,
    GOOD,
    QUESTION,
    SEARCH,
    SECTION,
    ask,
    lim,
)

Line = tuple[str, str, str | None]


@pytest.fixture
async def store(tmp_path: Path) -> Any:
    async with SqliteEvidenceStore(tmp_path / "evidence.sqlite") as opened:
        yield opened


@pytest.fixture
def lines(monkeypatch: pytest.MonkeyPatch) -> list[Line]:
    """Every activity line the Q&A graph reports, in order."""
    seen: list[Line] = []

    async def record(kind: str, label: str, detail: str | None = None) -> None:
        seen.append((kind, label, detail))

    monkeypatch.setattr(activity, "report", record)
    return seen


def kinds(lines: list[Line]) -> list[str]:
    return [kind for kind, _, _ in lines]


# --- how a tool call is described ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("names", "kind", "label"),
    [
        (["search_evidence"], "searching", "Searching the memo"),
        (["get_evidence"], "reading", "Reading memo records"),
        (["get_memo_section"], "reading", "Reading memo records"),
        (["get_analysis_note"], "reading", "Reading memo records"),
        (["open_source"], "reading", "Reading memo records"),
        (["calculate"], "calculating", "Calculating a figure"),
        (["rmprep_v2_data.get_account"], "fetching", "Fetching current client data"),
        (["something_new"], "fetching", "Fetching current client data"),
    ],
)
def test_a_tool_call_is_described_by_what_it_does(names: list[str], kind: str, label: str) -> None:
    assert tool_activity(names)[:2] == (kind, label)


def test_several_calls_are_counted_not_listed() -> None:
    kind, label, detail = tool_activity(["get_evidence", "get_memo_section", "calculate"])
    assert (kind, detail) == ("reading", "3 lookups")
    assert label == "Reading memo records"
    assert tool_activity(["get_evidence"])[2] is None


# --- reporting never changes the answer ------------------------------------------------------------


async def test_a_failing_report_is_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    async def broken(kind: str, label: str, detail: str | None = None) -> None:
        raise RuntimeError("the browser went away")

    monkeypatch.setattr(activity, "report", broken)
    await activity.note("drafting", "Writing the answer")


async def test_an_sdk_without_activity_reporting_is_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import agentcraft2_core.integrations.langgraph as sdk

    monkeypatch.delattr(sdk, "report_activity", raising=False)
    await activity.report("drafting", "Writing the answer")


async def test_a_failing_report_does_not_stop_the_answer(
    tmp_path: Path, store: SqliteEvidenceStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken(kind: str, label: str, detail: str | None = None) -> None:
        raise RuntimeError("the browser went away")

    monkeypatch.setattr(activity, "report", broken)
    run = await ask(tmp_path, store, SECTION, GOOD)
    assert str(run.messages[-1].content) == GOOD


# --- what the graph reports ------------------------------------------------------------------------


async def test_a_plain_question_reports_choosing_writing_and_checking(
    tmp_path: Path, store: SqliteEvidenceStore, lines: list[Line]
) -> None:
    await ask(tmp_path, store, "The memo flags covenant pressure; verify the cure status.")
    assert lines == [
        ("selecting", "Choosing the memo", None),
        ("drafting", "Writing the answer", None),
        ("checking", "Checking citations and figures", None),
    ]


async def test_tool_rounds_are_reported_in_order(
    tmp_path: Path, store: SqliteEvidenceStore, lines: list[Line]
) -> None:
    await ask(tmp_path, store, SEARCH, SECTION, GOOD)
    assert kinds(lines) == [
        "selecting",
        "drafting",
        "searching",
        "drafting",
        "reading",
        "drafting",
        "checking",
    ]


async def test_a_revision_says_so(
    tmp_path: Path, store: SqliteEvidenceStore, lines: list[Line]
) -> None:
    await ask(tmp_path, store, SECTION, BAD, GOOD)
    assert kinds(lines) == [
        "selecting",
        "drafting",
        "reading",
        "drafting",
        "checking",
        "revising",
        "checking",
    ]
    assert ("revising", "Revising the answer", None) in lines


async def test_the_read_ahead_is_reported_with_a_count(
    tmp_path: Path, store: SqliteEvidenceStore, lines: list[Line]
) -> None:
    await ask(tmp_path, store, GOOD, limits=QnaLimits())
    reading = [line for line in lines if line[0] == "reading"]
    assert len(reading) == 1
    _, label, detail = reading[0]
    assert label == "Reading related records"
    assert detail is not None
    assert detail.endswith(" records")
    assert int(detail.split()[0]) >= 1


async def test_a_turn_that_ends_in_a_limit_reports_the_reason_code(
    tmp_path: Path, store: SqliteEvidenceStore, lines: list[Line]
) -> None:
    await ask(tmp_path, store, SECTION, BAD, BAD, BAD)
    assert lines[-1] == ("limited", "Ending with what could be verified", "validation_failed")


async def test_a_question_that_needs_a_clarification_reports_only_the_choice(
    tmp_path: Path, store: SqliteEvidenceStore, lines: list[Line]
) -> None:
    await ask(tmp_path, store, question="Explain memo-zzzzzzzzzzzz.")
    assert kinds(lines) == ["selecting"]


# --- nothing private is reported --------------------------------------------------------------------


async def test_activity_holds_no_question_draft_argument_or_record_text(
    tmp_path: Path, store: SqliteEvidenceStore, lines: list[Line]
) -> None:
    await ask(
        tmp_path,
        store,
        SEARCH,
        SECTION,
        BAD,
        GOOD,
        limits=lim(),
    )
    text = " ".join(" ".join(filter(None, line)) for line in lines)
    for private in ("MARKER12345", "DSCR", "covenant", COVENANT_REF, QUESTION, "memo-"):
        assert private not in text, private


# --- the stream a caller sees ------------------------------------------------------------------------


async def test_the_step_stream_shows_neither_a_rejected_draft_nor_the_models_prose_beside_a_call(
    tmp_path: Path, store: SqliteEvidenceStore
) -> None:
    thinking = AIMessage(
        "MARKERTHINK I will look at the covenants first.",
        tool_calls=[{"name": "get_memo_section", "args": {"section": "covenants"}, "id": "c1"}],
    )
    run = await ask(tmp_path, store, thinking, BAD, GOOD)
    shown = repr(run.log)
    assert "MARKERTHINK" not in shown
    assert "MARKER12345" not in shown, "a rejected draft is not streamed"
    assert GOOD in shown, "the accepted answer is"


async def test_the_step_stream_carries_no_evidence_values(
    tmp_path: Path, store: SqliteEvidenceStore
) -> None:
    run = await ask(tmp_path, store, SECTION, GOOD)
    tool_steps = [s for s in run.log if s["node"].endswith("/tools")]
    assert tool_steps
    for step in tool_steps:
        for message in step["messages"]:
            assert COVENANT_REF not in message["content"]
