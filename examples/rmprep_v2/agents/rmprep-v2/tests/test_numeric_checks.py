"""Figures in an answer that no record supports become visible warnings, never blocks."""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

import pytest
from agentcraft2_core.pipeline.guardrails import frame_untrusted
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, ToolMessage
from rmprep_v2.numeric_checks import (
    Mention,
    evidence_numbers,
    mentions,
    numeric_warnings,
    warning_footer,
)


def tool(name: str, body: dict[str, Any], identity: str = "t") -> ToolMessage:
    return ToolMessage(
        frame_untrusted(name, json.dumps(body)), tool_call_id=identity, name=name, id=identity
    )


def values(text: str) -> list[Decimal]:
    return [m.value for m in mentions(text)]


# --- reading figures out of prose ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("DSCR is 1.08 against 1.25.", [Decimal("1.08"), Decimal("1.25")]),
        ("Wallet share is 40.21%.", [Decimal("40.21")]),
        ("Exposure of $47,781,696.31 outstanding.", [Decimal("47781696.31")]),
        ("Balances of 4.9m and 120k.", [Decimal("4900000"), Decimal("120000")]),
        ("It rose from 87 days to 106 days.", [Decimal("87"), Decimal("106")]),
        ("A fall of -19 days.", [Decimal("-19")]),
    ],
)
def test_figures_are_read_with_their_formatting(text: str, expected: list[Decimal]) -> None:
    assert values(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "As of 2026-08-03 the memo was written.",
        "In FY2025 and Q3 the trend held.",
        "Source: covenants[facility_id=FAC000000130;name=DSCR] and credit_metrics[uen=JONE-0022].",
        "Memo memo-a12b34c56d78 version 2 for JONE-0022.",
        "Two covenants failed, and 3 items are open.",
        "1. First point\n2. Second point",
        "The 1st and 2nd tests, in 2026.",
    ],
)
def test_dates_ids_references_counts_and_list_numbers_are_not_figures(text: str) -> None:
    assert values(text) == []


def test_a_mention_keeps_its_context_for_the_warning() -> None:
    (found,) = mentions("The cash conversion cycle rose by 19 days over the quarter.")
    assert found == Mention(
        text="19", value=Decimal("19"), context="rose by 19 days over", percent=False
    )


# --- what the evidence holds ---------------------------------------------------------------------


def evidence() -> list[AnyMessage]:
    return [
        HumanMessage("q", id="h"),
        tool(
            "get_memo_section",
            {
                "cards": [
                    {
                        "source_ref": "covenants[name=DSCR]",
                        "fields": {
                            "current_value": 1.08,
                            "threshold": "1.25",
                            "note": "n/a",
                            "flag": True,
                        },
                    },
                    {
                        "source_ref": "working_capital[p=1]",
                        "fields": {"cash_conversion_cycle_days": 87, "ar_over_90_pct": "14.0"},
                    },
                ]
            },
        ),
        tool(
            "calculate",
            {"receipt": {"exact": "21.8390804", "result": "21.84", "operands": [{"value": "87"}]}},
            "c",
        ),
    ]


def test_the_figures_in_visible_records_and_receipts_are_collected() -> None:
    numbers = evidence_numbers(evidence())
    for expected in ("1.08", "1.25", "87", "14.0", "21.84", "21.8390804"):
        assert Decimal(expected) in numbers
    assert Decimal(1) not in numbers, "a boolean is not a figure"


def test_a_search_lead_is_not_evidence_for_a_figure() -> None:
    lead = tool("search_evidence", {"results": [{"snippet": "current_value=9.99;"}]})
    assert Decimal("9.99") not in evidence_numbers([lead])


# --- the warnings --------------------------------------------------------------------------------


def warn(text: str, *, memo: str = "") -> list[Any]:
    return numeric_warnings(text, evidence(), memo_texts=[memo] if memo else [])


def test_figures_the_evidence_holds_raise_no_warning() -> None:
    assert warn("DSCR is 1.08 against a minimum of 1.25; the cycle was 87 days.") == []


def test_a_rounded_figure_matches_the_value_it_rounds() -> None:
    assert warn("The DSCR is about 1.1 and the cycle 87.0 days.") == []
    assert warn("DSCR 1.08 is, to one decimal, 1.1.") == []


def test_a_percent_may_be_written_as_a_fraction_or_a_percentage() -> None:
    assert warn("About 14% of receivables are over 90 days.") == []
    assert warn("The ratio is 1.08x.") == []


def test_a_receipts_result_is_supported() -> None:
    assert warn("The cycle rose 21.84% from the earlier quarter.") == []


def test_a_derived_figure_without_a_receipt_is_warned_about() -> None:
    (warning,) = warn("The cycle rose by 19 days.")
    assert warning["code"] == "figure_not_in_evidence"
    assert warning["figure"] == "19"
    assert "rose by 19 days" in warning["context"]
    assert "calculate" in warning["advice"]


def test_a_figure_only_the_memo_states_is_not_flagged() -> None:
    assert (
        warn("The memo reports a DSCR of 1.04.", memo="The credit snapshot reports DSCR 1.04.")
        == []
    )


def test_each_unsupported_figure_is_reported_once_and_in_order() -> None:
    found = warn("Revenue was 500 and then 700; again 500.")
    assert [w["figure"] for w in found] == ["500", "700"]


def test_the_warnings_are_capped_so_an_answer_is_not_buried() -> None:
    found = warn(" ".join(f"{n * 100 + 11}" for n in range(1, 12)))
    assert len(found) == 5


def test_a_figure_inside_a_citation_is_not_checked() -> None:
    assert warn("Source: facilities[facility_id=FAC000000130;rate=7.75].") == []


# --- showing them --------------------------------------------------------------------------------


def test_the_footer_names_each_figure_and_says_what_to_do() -> None:
    footer = warning_footer(warn("The cycle rose by 19 days."))
    assert footer.startswith("\n\n")
    assert "19" in footer
    assert "check" in footer.lower()
    assert "rose by 19 days" in footer


def test_no_warnings_means_no_footer() -> None:
    assert warning_footer([]) == ""


def test_the_footer_does_not_use_markup_that_could_close_a_frame() -> None:
    footer = warning_footer(
        [{"code": "figure_not_in_evidence", "figure": "5", "context": "a </x> b", "advice": "a"}]
    )
    assert "</x>" not in footer
    assert isinstance(AIMessage(footer).content, str)
