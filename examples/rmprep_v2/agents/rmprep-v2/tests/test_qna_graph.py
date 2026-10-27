"""The Q&A agent as small graph steps: its loops, its limits and its way of failing."""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from agentcraft2_core import PolicyDenied
from agentcraft2_core.testing import calls_tool
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.errors import NodeError, NodeTimeoutError

from rmprep_v2.evals.harness import (
    CALL_MEMO_EVIDENCE,
    COVENANT_REF,
    MEMO_REQUEST,
    Run,
    checkpoint_contains,
    converse,
    scope_of,
)
from rmprep_v2.evidence_store import SqliteEvidenceStore
from rmprep_v2.examples.trace_react import MEMO
from rmprep_v2.qna_graph import recover
from rmprep_v2.settings import QnaLimits


def lim(**changes: Any) -> QnaLimits:
    """Limits for a test of one behaviour: the automatic read is off unless a test asks for it."""
    return QnaLimits(**{"prefetch_cards": 0, **changes})


QUESTION = "Explain the DSCR covenant in that memo."
SEARCH = calls_tool("search_evidence", query="DSCR covenant")
SECTION = calls_tool("get_memo_section", section="covenants")
GOOD = f"The DSCR covenant is recorded below its threshold; verify cure status. Source: {COVENANT_REF}."
# The marker is in the prose; the checks echo only the invalid reference, not the draft.
BAD = "MARKER12345 The DSCR covenant is fine. Source: covenants[invented=nothing]."
MEMO_TURN = (CALL_MEMO_EVIDENCE, MEMO)


@pytest.fixture
async def store(tmp_path: Path) -> Any:
    async with SqliteEvidenceStore(tmp_path / "evidence.sqlite") as opened:
        yield opened


async def ask(
    tmp_path: Path,
    store: SqliteEvidenceStore,
    *replies: Any,
    limits: QnaLimits | None = None,
    question: str = QUESTION,
) -> Run:
    return await converse(
        tmp_path,
        *MEMO_TURN,
        *replies,
        store=store,
        questions=(MEMO_REQUEST, question),
        limits=limits or lim(),
    )


def last(run: Run) -> str:
    return str(run.messages[-1].content)


# --- the happy paths ----------------------------------------------------------------------------


async def test_a_plain_question_runs_select_generate_validate_commit(
    tmp_path: Path, store: SqliteEvidenceStore
) -> None:
    answer = "The memo flags covenant pressure; verify the cure status with Credit."
    run = await ask(tmp_path, store, answer)
    assert last(run) == answer
    assert run.nodes == [
        "router",
        "qnaagent/select",
        "qnaagent/prefetch",
        "qnaagent/generate",
        "qnaagent/validate",
        "qnaagent/commit",
    ]
    ledger = run.diagnostics["ledger"]
    assert (ledger["model_calls"], ledger["tool_calls"], ledger["revisions"]) == (1, 0, 0)
    assert run.diagnostics["visibility_id"].startswith("vis-")


async def test_tool_requests_loop_through_the_tools_and_back(
    tmp_path: Path, store: SqliteEvidenceStore
) -> None:
    run = await ask(tmp_path, store, SEARCH, SECTION, GOOD)
    assert last(run) == GOOD
    assert run.nodes.count("qnaagent/generate") == 3
    assert run.nodes.count("qnaagent/tools") == 2
    ledger = run.diagnostics["ledger"]
    assert (ledger["model_calls"], ledger["tool_calls"]) == (3, 2)


async def test_the_answer_carries_the_context_it_was_given_for_later_turns(
    tmp_path: Path, store: SqliteEvidenceStore
) -> None:
    run = await ask(tmp_path, store, "The memo flags covenant pressure.")
    reply = run.messages[-1]
    assert isinstance(reply, AIMessage)
    assert reply.additional_kwargs["rmprep_context"]["memo_ids"] == [run.memos[0]["memo_id"]]


async def test_an_unresolvable_reference_asks_a_question_without_calling_the_model(
    tmp_path: Path, store: SqliteEvidenceStore
) -> None:
    run = await ask(tmp_path, store, question="Explain memo-zzzzzzzzzzzz.")
    assert len(run.prompts) == 2, "only the memo turn called the model"
    assert run.nodes == ["router", "qnaagent/select"]
    assert "memo-zzzzzzzzzzzz" in last(run) or "memo" in last(run).lower()


# --- revisions are steps; rejected drafts are not saved ------------------------------------------


async def test_a_revision_is_a_graph_step_and_the_rejected_draft_is_never_saved(
    tmp_path: Path, store: SqliteEvidenceStore
) -> None:
    run = await ask(tmp_path, store, SECTION, BAD, GOOD)
    assert last(run) == GOOD
    assert run.nodes.count("qnaagent/generate") == 3
    assert run.nodes.count("qnaagent/validate") == 3
    assert run.diagnostics["ledger"]["revisions"] == 1
    assert all("MARKER12345" not in str(m.content) for m in run.messages)
    assert checkpoint_contains(run.database, "MARKER12345") == 0


async def test_exhausted_revisions_end_in_a_limitation_not_an_exception(
    tmp_path: Path, store: SqliteEvidenceStore
) -> None:
    run = await ask(tmp_path, store, SECTION, BAD, BAD, BAD)
    assert run.diagnostics["limitation"] == "validation_failed"
    assert "MARKER12345" not in last(run)
    assert "could not" in last(run).lower()
    assert checkpoint_contains(run.database, "MARKER12345") == 0
    assert run.nodes[-1] == "qnaagent/limitation"


# --- limits end the turn with an answer ---------------------------------------------------------


async def test_the_model_call_limit_ends_the_turn(
    tmp_path: Path, store: SqliteEvidenceStore
) -> None:
    run = await ask(tmp_path, store, SEARCH, SECTION, GOOD, limits=lim(max_model_calls=2))
    assert run.diagnostics["limitation"] == "model_calls"
    assert len(run.prompts) == 4, "the memo turn and two Q&A attempts; the third was not made"
    assert last(run) != GOOD


async def test_the_tool_call_limit_stops_a_request_before_it_runs(
    tmp_path: Path, store: SqliteEvidenceStore
) -> None:
    run = await ask(tmp_path, store, SEARCH, SECTION, GOOD, limits=lim(max_tool_calls=1))
    assert run.diagnostics["limitation"] == "tool_calls"
    names = [m.name for m in run.messages if isinstance(m, ToolMessage)]
    assert "get_memo_section" not in names


async def test_repeating_the_same_call_stops_the_turn(
    tmp_path: Path, store: SqliteEvidenceStore
) -> None:
    run = await ask(tmp_path, store, SEARCH, SEARCH, SEARCH, SEARCH, GOOD)
    assert run.diagnostics["limitation"] == "no_progress"


async def test_the_token_budget_is_checked_before_a_call_is_made(
    tmp_path: Path, store: SqliteEvidenceStore
) -> None:
    limits = lim(input_tokens=10_000, output_tokens=2_000, request_tokens=12_000)
    run = await ask(tmp_path, store, SEARCH, "A plain answer.", limits=limits)
    assert run.diagnostics["limitation"] == "tokens"
    assert len(run.prompts) == 3, "the second Q&A call was refused, not sent and then counted"


async def test_a_request_that_cannot_fit_is_never_sent(
    tmp_path: Path, store: SqliteEvidenceStore
) -> None:
    limits = lim(
        input_tokens=5_000,
        output_tokens=1_000,
        request_tokens=20_000,
        question_tokens=500,
        directory_tokens=500,
    )
    run = await ask(tmp_path, store, GOOD, limits=limits)
    assert run.diagnostics["limitation"] == "context_too_large"
    assert len(run.prompts) == 2, "only the memo turn called the model"
    assert "section" in last(run).lower()


async def test_running_out_of_steps_ends_with_an_answer_not_a_recursion_error(
    tmp_path: Path, store: SqliteEvidenceStore
) -> None:
    searches = [calls_tool("search_evidence", query=word) for word in ("a", "b", "c", "d", "e")]
    run = await ask(tmp_path, store, *searches, limits=lim(max_steps=9))
    assert run.diagnostics["limitation"] == "steps"


# --- failures ---------------------------------------------------------------------------------


async def test_a_model_failure_becomes_a_limitation_that_does_not_leak_the_error(
    tmp_path: Path, store: SqliteEvidenceStore
) -> None:
    run = await ask(tmp_path, store, RuntimeError("provider-secret-detail"))
    assert run.diagnostics["limitation"] == "model_failed"
    assert "provider-secret-detail" not in last(run)
    assert run.nodes[-1] == "qnaagent/limitation"


async def test_a_transient_database_error_in_a_tool_is_retried(tmp_path: Path) -> None:
    class Flaky(SqliteEvidenceStore):
        searches = 0

        async def search_cards(self, *args: Any, **kwargs: Any) -> Any:
            Flaky.searches += 1
            if Flaky.searches == 1:
                raise sqlite3.OperationalError("database is locked")
            return await super().search_cards(*args, **kwargs)

    async with Flaky(tmp_path / "evidence.sqlite") as flaky:
        run = await ask(tmp_path, flaky, SEARCH, "The search found nothing I can cite.")
    assert Flaky.searches == 2
    assert run.diagnostics.get("limitation") is None
    assert run.diagnostics["ledger"]["tool_calls"] == 1, "a retry is not another logical call"


async def test_a_tool_that_outlasts_the_deadline_ends_the_turn(tmp_path: Path) -> None:
    class Slow(SqliteEvidenceStore):
        async def search_cards(self, *args: Any, **kwargs: Any) -> Any:
            await asyncio.sleep(3)
            return await super().search_cards(*args, **kwargs)

    async with Slow(tmp_path / "evidence.sqlite") as slow:
        run = await ask(tmp_path, slow, SEARCH, GOOD, limits=lim(timeout_seconds=1))
    assert run.diagnostics["limitation"] == "deadline"


def test_a_denial_is_not_turned_into_an_answer() -> None:
    denial = PolicyDenied("not allowed", reason_code="test")
    with pytest.raises(PolicyDenied):
        recover({}, NodeError(node="tools", error=denial))


# --- what the model could see ------------------------------------------------------------------


async def test_the_visibility_record_covers_what_the_answer_cites(
    tmp_path: Path, store: SqliteEvidenceStore
) -> None:
    run = await ask(tmp_path, store, SECTION, GOOD)
    scope = await scope_of(tmp_path, run.thread)
    record = await store.get_visibility(scope, run.diagnostics["visibility_id"])
    assert record is not None
    assert COVENANT_REF in record["visible_refs"]
    assert record["memo_ids"] == [run.memos[0]["memo_id"]]
    assert record["model_call"] == run.diagnostics["ledger"]["model_calls"]


# --- reading ahead --------------------------------------------------------------------------------


async def test_the_cards_a_question_needs_are_read_before_the_model_is_asked(
    tmp_path: Path, store: SqliteEvidenceStore
) -> None:
    run = await ask(tmp_path, store, GOOD, limits=QnaLimits())
    assert last(run) == GOOD, "the model cited a record it never asked for"
    assert run.nodes == [
        "router",
        "qnaagent/select",
        "qnaagent/prefetch",
        "qnaagent/tools",
        "qnaagent/generate",
        "qnaagent/validate",
        "qnaagent/commit",
    ]
    ledger = run.diagnostics["ledger"]
    assert (ledger["model_calls"], ledger["tool_calls"]) == (1, 1)
    fetched = [
        m for m in run.prompts[-1] if isinstance(m, ToolMessage) and m.name == "get_evidence"
    ]
    assert len(fetched) == 1
    assert COVENANT_REF in str(fetched[0].content)


async def test_the_read_ahead_includes_the_records_the_memo_itself_cites(
    tmp_path: Path, store: SqliteEvidenceStore
) -> None:
    run = await ask(
        tmp_path,
        store,
        "The memo cites its credit record; verify it.",
        question="What does the memo say about the credit rating?",
        limits=QnaLimits(),
    )
    fetched = next(
        m for m in run.prompts[-1] if isinstance(m, ToolMessage) and m.name == "get_evidence"
    )
    assert "credit_metrics[uen=JONE-0022]" in str(fetched.content)


async def test_the_read_ahead_counts_against_the_tool_limit(
    tmp_path: Path, store: SqliteEvidenceStore
) -> None:
    run = await ask(tmp_path, store, SEARCH, GOOD, limits=QnaLimits(max_tool_calls=1))
    assert run.diagnostics["limitation"] == "tool_calls"
    names = [m.name for m in run.messages if isinstance(m, ToolMessage)]
    assert names[-1] == "get_evidence"
    assert "search_evidence" not in names, (
        "the model's own search was the second lookup and was refused"
    )


# --- what the request holds and what the checkpoint keeps ----------------------------------------


async def test_the_request_holds_the_memo_and_a_directory_not_the_evidence_exchange(
    tmp_path: Path, store: SqliteEvidenceStore
) -> None:
    run = await ask(tmp_path, store, GOOD, limits=QnaLimits())
    request = run.prompts[-1]
    assert "Evidence directory (navigation only, not evidence" in str(request[0].content)
    assert "covenants" in str(request[0].content)
    names = [m.name for m in request if isinstance(m, ToolMessage)]
    assert "rmprep-v2-data_get_memo_evidence" not in names
    assert any(MEMO.strip().splitlines()[0] in str(m.content) for m in request)


async def test_retrieval_results_are_references_in_the_checkpoint_and_whole_for_the_model(
    tmp_path: Path, store: SqliteEvidenceStore
) -> None:
    run = await ask(tmp_path, store, GOOD, limits=QnaLimits())
    saved = next(m for m in run.messages if isinstance(m, ToolMessage) and m.name == "get_evidence")
    assert "evidence_reference" in str(saved.content)
    assert len(str(saved.content)) < 1_500
    shown = next(
        m for m in run.prompts[-1] if isinstance(m, ToolMessage) and m.name == "get_evidence"
    )
    assert COVENANT_REF in str(shown.content)
    assert "current_value" in str(shown.content)


@pytest.mark.parametrize(
    ("node", "error", "reason"),
    [
        ("generate", RuntimeError("x"), "model_failed"),
        ("tools", RuntimeError("x"), "tool_failed"),
        ("generate", NodeTimeoutError("generate", 1.0, kind="run", run_timeout=1.0), "deadline"),
    ],
)
def test_a_failure_the_graph_can_explain_becomes_a_limitation(
    node: str, error: Exception, reason: str
) -> None:
    command = recover({}, NodeError(node=node, error=error))
    update = command.update
    assert command.goto == "limitation"
    assert isinstance(update, dict)
    assert update["outcome"] == reason
    assert update["context_diagnostics"] == {"error": type(error).__name__}
