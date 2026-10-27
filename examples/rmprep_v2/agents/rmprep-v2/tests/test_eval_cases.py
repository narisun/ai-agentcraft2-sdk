"""The evaluation cases are well formed, cover every family and cite records that exist."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from rmprep_v2.evals import baseline
from rmprep_v2.evals.cases import FAMILIES, Case, coverage, load_cases, problems
from rmprep_v2.evals.packs import load_packs, references

_REFERENCE = re.compile(r"[a-z_]+\[[^\]]+\]")


@pytest.fixture(scope="module")
def cases() -> list[Case]:
    return load_cases()


def test_the_cases_are_well_formed(cases: list[Case]) -> None:
    assert problems(cases) == []


def test_every_family_has_cases(cases: list[Case]) -> None:
    counts = coverage(cases)
    assert [family for family in FAMILIES if counts[family] == 0] == []
    assert len(cases) >= 30


def test_problems_names_what_is_wrong(cases: list[Case]) -> None:
    broken = {
        **cases[0],
        "expected_outcome": "maybe",
        "required_refs": [],
        "required_facts": [],
        "prohibited": [],
    }
    duplicate = {**cases[1], "id": cases[0]["id"]}
    found = problems([broken, duplicate])  # type: ignore[list-item]
    assert any("expected_outcome must be one of" in text for text in found)
    assert any("duplicate id" in text for text in found)
    assert any("say what a good answer states" in text for text in found)


async def test_required_references_exist_in_the_evidence(cases: list[Case]) -> None:
    """A case cannot ask for a record the data server never returns for that client and date."""
    wanted = {(case["uen"], case["as_of"]) for case in cases}
    packs = await load_packs(wanted)
    missing: list[str] = []
    for case in cases:
        available = set(references(packs[(case["uen"], case["as_of"])]))
        missing += [f"{case['id']}: {ref}" for ref in case["required_refs"] if ref not in available]
    assert missing == []


def test_cases_name_only_references_in_their_own_text(cases: list[Case]) -> None:
    """A reference typed into a fact or a prohibition is a deliberate forgery, so mark it."""
    for case in cases:
        typed = {
            ref
            for text in [*case["required_facts"], *case["prohibited"]]
            for ref in _REFERENCE.findall(text)
        }
        assert typed <= {"credit_exceptions[exception_id=CE-FAKE-999]"}, case["id"]


async def test_the_baseline_report_measures_a_memo_and_follow_ups(tmp_path: Path) -> None:
    report = await baseline.measure(tmp_path)
    assert report["tool_definitions"]["count"] == 4
    packs = [p for p in report["evidence_packs"] if p["status"] == "found"]
    assert len(packs) == len(report["evidence_packs"]) > 40
    routes = [turn["route"] for turn in report["turns"]]
    assert routes[0] == "memoagent"
    assert set(routes[1:]) == {"qnaagent"}
    # The memo's evidence is in the checkpoint once, not once per question.
    first, last = report["turns"][0], report["turns"][-1]
    assert last["checkpoint_message_bytes"] < 2 * first["checkpoint_message_bytes"]
