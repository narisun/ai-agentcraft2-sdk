"""The Q&A limits load from settings, and the request ledger stops work at the first one hit."""

from __future__ import annotations

from collections.abc import Mapping

import pytest
from agentcraft2_core import ConfigurationError
from agentcraft2_core.contracts import ConfigSource

from rmprep_v2.qna_ledger import (
    model_gate,
    new_ledger,
    note_tool_calls,
    reconcile,
    reserve,
    tool_gate,
)
from rmprep_v2.settings import QnaLimits, load_qna_limits


class Settings(ConfigSource):
    def __init__(self, values: Mapping[str, str]) -> None:
        self._values = dict(values)

    def get(self, name: str) -> str | None:
        return self._values.get(name)

    def names(self) -> list[str]:
        return list(self._values)


def test_the_defaults_are_the_reviewed_proposal() -> None:
    limits = QnaLimits()
    assert (limits.input_tokens, limits.output_tokens, limits.request_tokens) == (
        40_000,
        4_000,
        200_000,
    )
    assert (limits.recent_tokens, limits.question_tokens) == (8_000, 2_000)
    assert (limits.directory_tokens, limits.working_evidence_tokens) == (2_000, 10_000)
    assert (limits.max_tool_calls, limits.max_model_calls, limits.max_revisions) == (6, 8, 2)
    assert (limits.timeout_seconds, limits.max_no_progress_repeats) == (60, 2)


def test_settings_override_the_defaults() -> None:
    limits = load_qna_limits(
        source=Settings({"RMPREP_QNA_MAX_MODEL_CALLS": "5", "RMPREP_QNA_REQUEST_TOKENS": "90000"})
    )
    assert limits.max_model_calls == 5
    assert limits.request_tokens == 90_000
    assert limits.max_tool_calls == 6


def test_an_unknown_or_malformed_setting_fails_before_anything_starts() -> None:
    with pytest.raises(ConfigurationError, match="Unknown rmprep Q&A setting"):
        load_qna_limits(source=Settings({"RMPREP_QNA_MAX_MODEL_CALL": "5"}))
    for bad in ("0", "-1", "ten", "1.5", ""):
        with pytest.raises(ConfigurationError, match="RMPREP_QNA_INPUT_TOKENS"):
            load_qna_limits(source=Settings({"RMPREP_QNA_INPUT_TOKENS": bad}))


def test_limits_that_cannot_work_together_are_refused() -> None:
    with pytest.raises(ConfigurationError, match="output"):
        load_qna_limits(
            source=Settings(
                {"RMPREP_QNA_OUTPUT_TOKENS": "50000", "RMPREP_QNA_INPUT_TOKENS": "40000"}
            )
        )
    with pytest.raises(ConfigurationError, match="request"):
        load_qna_limits(
            source=Settings(
                {"RMPREP_QNA_REQUEST_TOKENS": "1000", "RMPREP_QNA_INPUT_TOKENS": "40000"}
            )
        )
    with pytest.raises(ValueError, match="positive"):
        QnaLimits(max_model_calls=0)


def test_the_step_limit_grows_with_the_work_allowed() -> None:
    small = QnaLimits(max_model_calls=2, max_tool_calls=1)
    large = QnaLimits(max_model_calls=8, max_tool_calls=6)
    assert small.recursion_limit < large.recursion_limit
    # Every model attempt can take a generate, a validate and a tool step.
    assert large.recursion_limit >= 3 * large.max_model_calls + large.max_tool_calls


# --- the ledger -----------------------------------------------------------------------------

LIMITS = QnaLimits(max_model_calls=3, max_tool_calls=2, request_tokens=50_000)


def test_a_fresh_ledger_allows_a_model_call() -> None:
    ledger = new_ledger(100.0)
    assert model_gate(ledger, LIMITS, 101.0, planned_input=10_000) is None


def test_reserving_counts_the_call_and_the_planned_tokens() -> None:
    ledger = reserve(new_ledger(0.0), LIMITS, planned_input=10_000)
    assert ledger["model_calls"] == 1
    assert ledger["reserved_tokens"] == 10_000 + LIMITS.output_tokens


def test_reported_usage_replaces_the_reservation() -> None:
    ledger = reserve(new_ledger(0.0), LIMITS, planned_input=10_000)
    ledger = reconcile(
        ledger, LIMITS, planned_input=10_000, usage={"input_tokens": 7_000, "output_tokens": 300}
    )
    assert ledger["reserved_tokens"] == 7_300


def test_missing_usage_keeps_the_conservative_reservation() -> None:
    ledger = reserve(new_ledger(0.0), LIMITS, planned_input=10_000)
    ledger = reconcile(ledger, LIMITS, planned_input=10_000, usage=None)
    assert ledger["reserved_tokens"] == 10_000 + LIMITS.output_tokens


def test_cached_input_counts_once() -> None:
    ledger = reserve(new_ledger(0.0), LIMITS, planned_input=10_000)
    usage = {
        "input_tokens": 9_000,
        "output_tokens": 100,
        "input_token_details": {"cache_read": 6_000, "cache_creation": 1_000},
    }
    # The provider's input figure already includes cached tokens.
    assert reconcile(ledger, LIMITS, planned_input=10_000, usage=usage)["reserved_tokens"] == 9_100


def test_a_retried_attempt_costs_another_reservation() -> None:
    ledger = reserve(new_ledger(0.0), LIMITS, planned_input=5_000, attempts=2)
    assert ledger["model_calls"] == 2
    assert ledger["reserved_tokens"] == 2 * (5_000 + LIMITS.output_tokens)


def test_the_model_gate_names_the_first_limit_reached() -> None:
    spent = new_ledger(0.0)
    for _ in range(3):
        spent = reserve(spent, LIMITS, planned_input=1_000)
    assert model_gate(spent, LIMITS, 1.0, planned_input=1_000) == "model_calls"
    assert model_gate(new_ledger(0.0), LIMITS, 61.0, planned_input=1_000) == "deadline"
    assert model_gate(new_ledger(0.0), LIMITS, 1.0, planned_input=45_000) == "context_too_large"
    nearly = reserve(new_ledger(0.0), LIMITS, planned_input=39_000)
    assert model_gate(nearly, LIMITS, 1.0, planned_input=10_000) == "tokens"
    # Two limits reached at once: the one earlier in the order wins.
    both = reserve(reserve(reserve(new_ledger(0.0), LIMITS, 1_000), LIMITS, 1_000), LIMITS, 1_000)
    assert model_gate(both, LIMITS, 61.0, planned_input=1_000) == "deadline"


def test_the_tool_gate_counts_logical_calls_including_repeats() -> None:
    ledger = new_ledger(0.0)
    assert tool_gate(ledger, LIMITS, requested=2) is None
    assert tool_gate(ledger, LIMITS, requested=3) == "tool_calls"
    ledger = note_tool_calls(ledger, [("search_evidence", {"query": "dscr"})] * 2)
    assert ledger["tool_calls"] == 2
    assert tool_gate(ledger, LIMITS, requested=1) == "tool_calls"


def test_the_same_call_again_without_new_evidence_is_a_repeat() -> None:
    ledger = new_ledger(0.0)
    call = ("search_evidence", {"query": "dscr", "limit": 5})
    ledger = note_tool_calls(ledger, [call])
    assert ledger["repeats"] == 0
    ledger = note_tool_calls(ledger, [("search_evidence", {"limit": 5, "query": "dscr"})])
    assert ledger["repeats"] == 1, "argument order is not a difference"
    ledger = note_tool_calls(ledger, [call])
    assert ledger["repeats"] == 2
    # A different call is progress and clears the run of repeats.
    assert note_tool_calls(ledger, [("get_evidence", {"card_ids": ["c-1"]})])["repeats"] == 0


def test_repeating_past_the_allowance_stops_the_turn() -> None:
    ledger = new_ledger(0.0)
    call = ("search_evidence", {"query": "dscr"})
    for _ in range(4):
        ledger = note_tool_calls(ledger, [call])
    assert ledger["repeats"] == 3
    assert tool_gate(ledger, QnaLimits(max_tool_calls=10), requested=1) == "no_progress"


def test_the_read_ahead_can_be_turned_off_but_no_other_limit_can_be_zero() -> None:
    assert QnaLimits().prefetch_cards == 8
    assert load_qna_limits(source=Settings({"RMPREP_QNA_PREFETCH_CARDS": "0"})).prefetch_cards == 0
    with pytest.raises(ConfigurationError, match="RMPREP_QNA_MAX_TOOL_CALLS"):
        load_qna_limits(source=Settings({"RMPREP_QNA_MAX_TOOL_CALLS": "0"}))
    with pytest.raises(ConfigurationError, match="RMPREP_QNA_PREFETCH_CARDS"):
        load_qna_limits(source=Settings({"RMPREP_QNA_PREFETCH_CARDS": "-1"}))


# --- retries the platform did inside one call ------------------------------------------------------


def test_retries_inside_one_call_cost_model_calls_and_a_reservation_each() -> None:
    from rmprep_v2.qna_ledger import charge_retries

    ledger = reserve(new_ledger(0.0), LIMITS, planned_input=5_000)
    charged = charge_retries(ledger, LIMITS, planned_input=5_000, attempts=3)
    assert charged["model_calls"] == 3
    assert charged["reserved_tokens"] == 3 * (5_000 + LIMITS.output_tokens)


def test_a_single_attempt_or_an_unknown_count_costs_nothing_extra() -> None:
    from rmprep_v2.qna_ledger import charge_retries

    ledger = reserve(new_ledger(0.0), LIMITS, planned_input=5_000)
    for attempts in (1, 0, None, True, "3"):
        assert charge_retries(ledger, LIMITS, planned_input=5_000, attempts=attempts) == ledger
