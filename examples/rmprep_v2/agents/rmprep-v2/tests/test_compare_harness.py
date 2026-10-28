"""The old-versus-new harness runs the same cases against rmprep and rmprep-v2."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from rmprep_v2.evals.cases import load_cases
from rmprep_v2.evals.compare import (
    VARIANTS,
    CaseResult,
    gold_answer,
    memo_request,
    run_cases,
    section_of,
    summarize,
)
from rmprep_v2.evals.packs import WORKSPACE


def case(identifier: str) -> Any:
    return next(c for c in load_cases() if c["id"] == identifier)


# --- turning a case into a scripted conversation -------------------------------------------------


def test_a_memo_request_names_its_client_and_date() -> None:
    parsed = memo_request(
        "Prepare a meeting memo for Jones Dairy Farm (JONE-0022) as of 2026-08-03."
    )
    assert parsed == ("JONE-0022", "2026-08-03")
    assert memo_request("Refresh the memo for Jones Dairy Farm (JONE-0022) as of 2026-08-03.") == (
        "JONE-0022",
        "2026-08-03",
    )
    assert memo_request("Which covenants failed?") is None


def test_a_reference_names_the_section_a_live_read_would_use() -> None:
    assert section_of("covenants[facility_id=FAC1;name=DSCR]") == "covenants"
    assert section_of("accounts[uen=JONE-0022]") == "profile"
    assert section_of("treasury_controls[control_id=CTL-1]") == "treasury_controls"


def test_the_gold_answer_states_the_facts_and_cites_the_required_records() -> None:
    answer = gold_answer(case("QA-009"))
    assert "FAC000000020 matures on 2026-08-28" in answer
    assert "facilities[facility_id=FAC000000020]" in answer


def test_a_case_with_nothing_to_cite_gets_a_plain_answer() -> None:
    answer = gold_answer(case("QA-031"))
    assert "[" not in answer


def test_both_variants_are_described() -> None:
    assert [v.name for v in VARIANTS] == ["rmprep", "rmprep-v2"]
    assert VARIANTS[0].tool_prefix == "rmprep-data"
    assert VARIANTS[1].tool_prefix == "rmprep-v2-data"


# --- running them ---------------------------------------------------------------------------------


@pytest.mark.skipif(
    not (WORKSPACE.parent / "rmprep").exists(),
    reason="needs the baseline example (examples/rmprep) beside this workspace",
)
async def test_the_same_case_runs_on_both_variants_and_is_measured(tmp_path: Path) -> None:
    results = await run_cases([case("QA-009")], tmp_path)
    assert {r.variant for r in results} == {"rmprep", "rmprep-v2"}
    for result in results:
        assert isinstance(result, CaseResult)
        assert result.case_id == "QA-009"
        assert result.outcome == "accepted", (result.variant, result.detail)
        assert result.model_calls >= 1
        assert result.max_input_tokens > 0
        assert result.checkpoint_bytes > 0
        assert result.cited_unknown == [], "every released citation names a real record"
    old, new = sorted(results, key=lambda r: r.variant)
    assert new.checkpoint_bytes < old.checkpoint_bytes, "the new design keeps evidence out of state"


async def test_a_case_that_asks_for_fresh_data_makes_its_tool_call(tmp_path: Path) -> None:
    results = await run_cases([case("QA-027")], tmp_path)
    for result in results:
        assert result.outcome == "accepted", (result.variant, result.detail)
        assert result.tool_calls >= 1


# --- reading the results ----------------------------------------------------------------------------


def result(variant: str, family: str, outcome: str, tokens: int, calls: int = 1) -> CaseResult:
    return CaseResult(
        case_id="QA-001",
        family=family,
        variant=variant,
        outcome=outcome,
        detail="",
        model_calls=calls,
        tool_calls=0,
        max_input_tokens=tokens,
        checkpoint_bytes=1_000,
        cited_unknown=[],
        warnings=0,
    )


def test_the_summary_compares_variants_by_family() -> None:
    rows = [
        result("rmprep", "derivation", "accepted", 30_000),
        result("rmprep", "derivation", "rejected", 28_000),
        result("rmprep-v2", "derivation", "accepted", 9_000),
        result("rmprep-v2", "derivation", "limitation", 11_000, calls=3),
    ]
    summary = summarize(rows)
    old, new = summary["derivation"]["rmprep"], summary["derivation"]["rmprep-v2"]
    assert (old["cases"], old["accepted"]) == (2, 1)
    assert (new["cases"], new["accepted"], new["limitation"]) == (2, 1, 1)
    assert old["max_input_tokens"] == 30_000
    assert new["mean_model_calls"] == 2.0
    assert summary["all"]["rmprep-v2"]["cases"] == 2


def test_an_unknown_variant_in_a_summary_is_not_invented() -> None:
    assert summarize([])["all"] == {}


def test_the_cases_the_harness_can_script_are_most_of_them() -> None:
    cases = load_cases()
    assert len(cases) >= 30
    with pytest.raises(StopIteration):
        case("QA-999")


async def test_a_version_comparison_is_answered_from_the_records_that_changed(
    tmp_path: Path,
) -> None:
    """QA-022 asks what is new in version 2; the answer cites records the memos never mention."""
    results = await run_cases([case("QA-022")], tmp_path, variants=["rmprep-v2"])
    (result,) = results
    assert result.outcome == "accepted", result.detail
    assert result.cited_unknown == []
