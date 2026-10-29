"""The evaluation's own checks: its gold SQL runs, its scoring is fair, its meter stops a run.

None of this calls a model or the network:  ``pytest evals/sql_schema`` from the workspace root.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from evals.sql_schema.config import CASES, Meter
from evals.sql_schema.executor import Engine, compare, normalize
from evals.sql_schema.prompts import bare_text, question_prompt
from evals.sql_schema.questions import QUESTIONS
from evals.sql_schema.score import blocks, cell, parse_ref


@pytest.fixture(scope="module")
def engine() -> Engine:
    return Engine()


# --- the questions and their gold SQL -------------------------------------------------------


def test_the_questions_have_distinct_ids_and_say_what_they_test() -> None:
    ids = [q["id"] for q in QUESTIONS]
    assert len(ids) == len(set(ids)) == 21
    assert all(set(q["tests"]) <= set("VJRTL") and q["tests"] for q in QUESTIONS)


def test_every_gold_query_runs_and_returns_rows(engine: Engine) -> None:
    for q in QUESTIONS:
        columns, rows, error = engine.run(q["gold"])
        assert error is None, f"{q['id']}: {error}"
        assert rows, f"{q['id']} returns nothing, so any empty answer would pass it"
        assert columns


def test_a_gold_result_is_not_the_result_of_the_wrong_spelling(engine: Engine) -> None:
    gold = engine.run("SELECT COUNT(*) FROM accounts WHERE segment = 'Upper Mid-Market'")
    guess = engine.run("SELECT COUNT(*) FROM accounts WHERE segment = 'Upper Middle'")
    assert not compare(gold[0], gold[1], guess[0], guess[1])[0]


def test_the_existing_cases_that_name_rows_are_found() -> None:
    cases = [
        json.loads(line) for line in CASES.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    assert len(cases) == 38
    named = [c for c in cases if c["required_refs"]]
    assert len(named) == 31
    assert sum(len(c["required_refs"]) for c in named) == 55


def test_the_scenario_answer_keys_are_not_loaded(engine: Engine) -> None:
    _, _, error = engine.run("SELECT * FROM rm_scenarios")
    assert error is not None
    assert "rm_scenarios" not in bare_text()


# --- scoring ---------------------------------------------------------------------------------


def test_the_same_rows_in_another_order_pass() -> None:
    rows = [(1, "a"), (2, "b")]
    assert compare(["n", "s"], rows, ["x", "y"], [(2, "b"), (1, "a")])[0]


def test_amounts_are_compared_to_the_cent_and_dates_as_dates() -> None:
    assert normalize([(1.004, None)]) == normalize([(1.0, None)])
    assert compare(["d"], [("2026-01-02",)], ["d"], [(__import__("datetime").date(2026, 1, 2),)])[0]


def test_extra_columns_pass_when_the_wanted_ones_are_there_by_name() -> None:
    passed, _, _, how = compare(["uen", "n"], [("A", 1)], ["uen", "extra", "n"], [("A", "x", 1)])
    assert passed
    assert how == "by name"


def test_a_wrong_number_of_columns_fails() -> None:
    passed, recall, _, how = compare(["a", "b"], [(1, 2)], ["a"], [(1,)])
    assert not passed
    assert recall == 0.0
    assert "wanted 2" in how


def test_a_missing_row_fails_and_a_partial_answer_has_a_recall() -> None:
    passed, recall, precision, _ = compare(["a"], [(1,), (2,)], ["a"], [(1,)])
    assert not passed
    assert (recall, precision) == (0.5, 1.0)


def test_a_reference_is_split_into_its_table_and_key_values() -> None:
    assert parse_ref("covenants[facility_id=FAC1;name=DSCR]") == (
        "covenants",
        {"facility_id": "FAC1", "name": "DSCR"},
    )
    assert parse_ref("not a reference") is None


def test_the_sql_of_an_answer_is_its_code_blocks() -> None:
    assert blocks("```sql\nSELECT 1;\n```\n") == ["SELECT 1"]
    assert blocks("```sql\nSELECT 1\n```\ntext\n```sql\nSELECT 2\n```") == ["SELECT 1", "SELECT 2"]
    assert blocks("SELECT 3") == ["SELECT 3"]
    assert blocks("") == []


def test_a_cell_is_the_text_a_reference_holds() -> None:
    assert cell(None) == ""
    assert cell(12.0) == "12.0"
    assert cell("  x ") == "x"


# --- the prompts and the meter ----------------------------------------------------------------


def test_the_question_says_the_date_and_the_client_when_it_has_them() -> None:
    text = question_prompt("What?", "ABC-1", "2026-08-03")
    assert text.splitlines() == [
        "Today's date for this question is 2026-08-03.",
        "The client in question is ABC-1.",
        "Question: What?",
    ]
    assert question_prompt("What?") == "Question: What?"


def test_the_meter_adds_up_cost_and_stops_a_run_past_its_ceiling() -> None:
    usage = SimpleNamespace(
        input_tokens=1_000_000,
        output_tokens=0,
        cache_read_input_tokens=0,
        cache_creation_input_tokens=0,
    )
    meter = Meter(ceiling=0.15)
    assert meter.add("claude-haiku-5-5", usage) == pytest.approx(0.10)
    with pytest.raises(SystemExit, match="over the ceiling"):
        meter.add("claude-haiku-5-5", usage)
    assert meter.calls == 2


def test_cached_tokens_are_priced_below_ordinary_input() -> None:
    cached = SimpleNamespace(
        input_tokens=0,
        output_tokens=0,
        cache_read_input_tokens=1_000_000,
        cache_creation_input_tokens=0,
    )
    assert Meter(ceiling=1.0).add("claude-sonnet-5-5", cached) == pytest.approx(0.20)
