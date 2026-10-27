"""A calculation is done by code, from named records, and leaves a receipt that can be checked."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from rmprep_v2.cards import build_cards, json_tokens
from rmprep_v2.evidence_store import SqliteEvidenceStore
from rmprep_v2.receipts import OPERATIONS, receipt_id
from rmprep_v2.retrieval import calculate

SCOPE = "tenant/alice/rmprep-v2/thread-1"
MEMO: Any = {
    "memo_id": "memo-aaaaaaaaaaaa",
    "version": 1,
    "meeting_date": "2026-08-03",
    "source_ids": ["src-a"],
}


def wc(period: str, ccc: Any, **more: Any) -> dict[str, Any]:
    return {
        "source_ref": f"working_capital[uen=X;period_end={period}]",
        "period_end": period,
        "cash_conversion_cycle_days": ccc,
        "net_working_capital": more.pop("nwc", None),
        "currency": more.pop("currency", "USD"),
        **more,
    }


def pack(*records: dict[str, Any]) -> dict[str, Any]:
    return {
        "uen": "X",
        "meeting_date": "2026-08-03",
        "sections": {"working_capital": {"note": "Days per period.", "records": list(records)}},
    }


REFS = {
    "mar": "working_capital[uen=X;period_end=2026-03-31]",
    "jun": "working_capital[uen=X;period_end=2026-06-30]",
    "dec": "working_capital[uen=X;period_end=2025-12-31]",
    "eur": "working_capital[uen=X;period_end=2026-01-31]",
    "text": "working_capital[uen=X;period_end=2026-02-28]",
}


@pytest.fixture
async def store(tmp_path: Path) -> Any:
    records = [
        wc("2026-03-31", 87, nwc="13000000"),
        wc("2026-06-30", 106, nwc="6000000"),
        wc("2025-12-31", "74", nwc=18000000),
        wc("2026-01-31", 90, nwc="1000", currency="EUR"),
        wc("2026-02-28", "n/a", nwc=None),
    ]
    async with SqliteEvidenceStore(tmp_path / "evidence.sqlite") as opened:
        cards, notes = build_cards("src-a", pack(*records))
        await opened.put_cards(SCOPE, "src-a", cards, notes)
        yield opened


def operands(*pairs: tuple[str, str]) -> list[dict[str, str]]:
    return [{"source_ref": REFS[key], "field": field} for key, field in pairs]


CCC = "cash_conversion_cycle_days"
NWC = "net_working_capital"


async def run(
    store: SqliteEvidenceStore, operation: str, *pairs: tuple[str, str], **kw: Any
) -> dict[str, Any]:
    return await calculate(store, SCOPE, [MEMO], operation, operands(*pairs), **kw)


# --- the arithmetic ---------------------------------------------------------------------------------


async def test_a_difference_is_the_later_minus_the_earlier_and_shows_its_inputs(
    store: SqliteEvidenceStore,
) -> None:
    result = await run(store, "difference", ("mar", CCC), ("jun", CCC))
    assert result["status"] == "ok"
    receipt = result["receipt"]
    assert receipt["result"] == "19"
    assert receipt["operation"] == "difference"
    assert receipt["formula"] == "b - a"
    assert [(o["label"], o["source_ref"], o["field"], o["value"]) for o in receipt["operands"]] == [
        ("a", REFS["mar"], CCC, "87"),
        ("b", REFS["jun"], CCC, "106"),
    ]
    assert receipt["operands"][0]["as_of"] == "2026-08-03"
    assert receipt["ref"] == f"calculation[{receipt['receipt_id']}]"


async def test_a_percent_change_is_exact_and_rounded_by_a_stated_rule(
    store: SqliteEvidenceStore,
) -> None:
    receipt = (await run(store, "percent_change", ("mar", CCC), ("jun", CCC)))["receipt"]
    assert receipt["exact"].startswith("21.83908045977")
    assert receipt["result"] == "21.84"
    assert receipt["places"] == 2
    assert receipt["rounding"] == "half-up"
    one = (await run(store, "percent_change", ("mar", CCC), ("jun", CCC), places=0))["receipt"]
    assert one["result"] == "22"


async def test_rounding_is_decimal_half_up_not_floating_point(store: SqliteEvidenceStore) -> None:
    # 1000 / 400 = 2.5 and 13000000 / 6000000 = 2.1666...; half-up must hold at the edge.
    receipt = (await run(store, "ratio", ("mar", NWC), ("jun", NWC), places=1))["receipt"]
    assert receipt["exact"].startswith("2.16666666")
    assert receipt["result"] == "2.2"


async def test_a_sum_adds_numeric_strings_and_numbers_alike(store: SqliteEvidenceStore) -> None:
    receipt = (await run(store, "sum", ("mar", NWC), ("jun", NWC), ("dec", NWC)))["receipt"]
    assert receipt["result"] == "37000000"
    assert [o["value"] for o in receipt["operands"]] == ["13000000", "6000000", "18000000"]
    assert receipt["formula"] == "a + b + c"


async def test_the_same_inputs_give_the_same_receipt_everywhere(store: SqliteEvidenceStore) -> None:
    first = (await run(store, "difference", ("mar", CCC), ("jun", CCC)))["receipt"]
    again = (await run(store, "difference", ("mar", CCC), ("jun", CCC)))["receipt"]
    assert first == again
    assert first["receipt_id"] == receipt_id(
        "difference", [(REFS["mar"], CCC, "87"), (REFS["jun"], CCC, "106")], 2
    )
    other = (await run(store, "difference", ("jun", CCC), ("mar", CCC)))["receipt"]
    assert other["receipt_id"] != first["receipt_id"]
    assert other["result"] == "-19"


async def test_a_receipt_is_small(store: SqliteEvidenceStore) -> None:
    result = await run(store, "sum", ("mar", NWC), ("jun", NWC), ("dec", NWC))
    assert json_tokens(result) <= 1_000


# --- what is refused ----------------------------------------------------------------------------------


async def test_a_record_the_memo_does_not_hold_is_not_a_valid_operand(
    store: SqliteEvidenceStore,
) -> None:
    result = await calculate(
        store,
        SCOPE,
        [MEMO],
        "difference",
        [
            {"source_ref": "working_capital[uen=X;period_end=1999-01-01]", "field": CCC},
            *operands(("jun", CCC)),
        ],
    )
    assert result["status"] == "operand_not_found"
    assert "1999-01-01" in result["detail"]


async def test_another_scope_cannot_be_used_for_a_calculation(store: SqliteEvidenceStore) -> None:
    result = await calculate(
        store,
        "tenant/alice/rmprep-v2/other",
        [MEMO],
        "difference",
        operands(("mar", CCC), ("jun", CCC)),
    )
    assert result["status"] == "operand_not_found"


@pytest.mark.parametrize(
    ("pairs", "status"),
    [
        ((("mar", "no_such_field"), ("jun", CCC)), "field_missing"),
        ((("mar", CCC), ("text", CCC)), "not_numeric"),
        ((("mar", NWC), ("eur", NWC)), "currency_mismatch"),
        ((("mar", CCC), ("jun", NWC)), "metric_mismatch"),
    ],
)
async def test_operands_that_cannot_be_compared_are_refused_with_a_reason(
    store: SqliteEvidenceStore, pairs: tuple[tuple[str, str], ...], status: str
) -> None:
    result = await run(store, "difference", *pairs)
    assert result["status"] == status
    assert "receipt" not in result


async def test_a_ratio_may_compare_two_different_fields_of_one_currency(
    store: SqliteEvidenceStore,
) -> None:
    result = await run(store, "ratio", ("mar", NWC), ("jun", NWC))
    assert result["status"] == "ok"


async def test_a_null_value_is_not_zero(store: SqliteEvidenceStore) -> None:
    result = await run(store, "difference", ("text", NWC), ("jun", NWC))
    assert result["status"] == "not_numeric"


async def test_division_by_zero_is_reported(tmp_path: Path) -> None:
    async with SqliteEvidenceStore(tmp_path / "z.sqlite") as store:
        cards, notes = build_cards("src-a", pack(wc("2026-03-31", 0), wc("2026-06-30", 5)))
        await store.put_cards(SCOPE, "src-a", cards, notes)
        zero = {"source_ref": REFS["mar"], "field": CCC}
        five = {"source_ref": REFS["jun"], "field": CCC}
        assert (await calculate(store, SCOPE, [MEMO], "ratio", [five, zero]))[
            "status"
        ] == "division_by_zero"
        assert (await calculate(store, SCOPE, [MEMO], "percent_change", [zero, five]))[
            "status"
        ] == "division_by_zero"


async def test_the_operation_and_the_operand_count_are_checked(store: SqliteEvidenceStore) -> None:
    assert (await run(store, "multiply", ("mar", CCC), ("jun", CCC)))["status"] == "bad_request"
    assert (await run(store, "difference", ("mar", CCC)))["status"] == "bad_request"
    assert (await run(store, "difference", ("mar", CCC), ("jun", CCC), ("dec", CCC)))[
        "status"
    ] == "bad_request"
    many = [("mar", NWC)] * 11
    assert (await run(store, "sum", *many))["status"] == "bad_request"
    assert (await run(store, "difference", ("mar", CCC), ("jun", CCC), places=9))[
        "status"
    ] == "bad_request"
    assert set(OPERATIONS) == {"difference", "percent_change", "ratio", "sum"}


async def test_without_a_selected_memo_there_is_nothing_to_calculate_from(
    store: SqliteEvidenceStore,
) -> None:
    result = await calculate(store, SCOPE, [], "difference", operands(("mar", CCC), ("jun", CCC)))
    assert result["status"] == "no_memo"
