"""Scoring recorded production exchanges, off the request path."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentcraft2_core.contracts import ValidationFailed
from agentcraft2_core.evaluation import (
    EvalCase,
    Interaction,
    load_interactions,
    sampled,
    score_interactions,
    scored_by,
    write_interactions,
)
from agentcraft2_core.evaluation.scorers import Score, Scorer


def _interaction(number: int, verdict: str = "") -> Interaction:
    return Interaction(
        request_id=f"r-{number}",
        thread_id="t-1",
        question=f"question {number}",
        answer=f"answer {number}",
        application="helper",
        verdict=verdict,
    )


def test_interactions_round_trip_through_jsonl(tmp_path: Path) -> None:
    path = tmp_path / "interactions.jsonl"
    given = [_interaction(1), _interaction(2, verdict="down")]
    write_interactions(path, given)
    assert load_interactions(path) == given


def test_sampling_is_deterministic_and_rate_bound() -> None:
    interactions = [_interaction(n) for n in range(200)]
    first = sampled(interactions, rate=0.25, salt="week-42")
    second = sampled(interactions, rate=0.25, salt="week-42")
    assert first == second
    assert 20 <= len(first) <= 80  # a quarter, within loose bounds
    assert sampled(interactions, rate=1.0) == interactions
    assert sampled(interactions, rate=0.0) == []


async def test_scoring_judges_the_recorded_answers() -> None:
    class AnswerLength:
        name = "short"

        async def __call__(self, case: EvalCase, output: object) -> Score:
            return Score("short", 1.0 if len(str(output)) < 20 else 0.0)

    scorer: Scorer = AnswerLength()
    report = await score_interactions(
        [_interaction(1), _interaction(2, verdict="down")],
        [scorer],
        metadata={"source": "test"},
    )
    assert [r.case.id for r in report.results] == ["r-1", "r-2"]
    assert report.means == {"short": 1.0}
    assert report.by_tag("down") == {"short": 1.0}
    assert report.metadata == {"source": "test"}


def test_a_missing_interactions_file_is_an_explained_error_not_a_traceback(tmp_path: Path) -> None:
    with pytest.raises(ValidationFailed) as raised:
        load_interactions(tmp_path / "nowhere.jsonl")
    assert "cannot be read" in raised.value.message
    assert "FileNotFoundError" in str(raised.value.actual)


def test_a_repeated_or_missing_request_id_is_refused_with_its_line(tmp_path: Path) -> None:
    path = tmp_path / "interactions.jsonl"
    write_interactions(path, [_interaction(1), _interaction(1)])
    with pytest.raises(ValidationFailed, match=r"line 2 repeats request_id 'r-1'") as raised:
        load_interactions(path)
    assert "line 1" in str(raised.value.actual)
    path.write_text(
        '{"schema": "agentcraft2.interaction/v1", "thread_id": "t", "question": "q",'
        ' "answer": "a"}\n',
        encoding="utf-8",
    )
    with pytest.raises(ValidationFailed, match="line 1 has no request_id"):
        load_interactions(path)


async def test_scoring_refuses_interactions_that_share_a_request_id() -> None:
    twice = [_interaction(1), Interaction("r-1", "t-1", "question 2", "answer 2")]

    with pytest.raises(ValidationFailed, match="cannot be told apart"):
        await score_interactions(twice, [scored_by("any", lambda case, output: True)])
