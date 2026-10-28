"""Calculation receipts: arithmetic done by code on named records, with every input shown.

A model may not invent a derived figure and have it pass for evidence. It asks for the
calculation, names the records and fields, and gets a receipt: the operation, the formula, each
operand with its record, field and exact value, the unrounded result and the rounded one with the
rule used. Arithmetic is decimal, never floating point. Operands that cannot be compared (a
different currency, a different metric, a value that is not a number) are refused with a reason
instead of being combined.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from decimal import ROUND_HALF_UP, Context, Decimal, InvalidOperation
from typing import Any, Final

__all__ = [
    "MAX_OPERANDS",
    "MAX_PLACES",
    "OPERATIONS",
    "Refusal",
    "compute",
    "number_of",
    "receipt_id",
]

OPERATIONS: Final = {
    "difference": ("b - a", 2, 2),
    "percent_change": ("(b - a) / a * 100", 2, 2),
    "ratio": ("a / b", 2, 2),
    "sum": ("a + b + c ...", 2, 10),
}
"""Each operation: how its formula reads, and the fewest and most operands it takes."""
MAX_OPERANDS = 10
MAX_PLACES = 6
_PRECISION = Context(prec=28)
_LETTERS = "abcdefghij"


class Refusal(Exception):
    """A calculation that must not be done; the status says why."""

    def __init__(self, status: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def number_of(value: object) -> Decimal | None:
    """A field's value as an exact number, or ``None`` when it is not one.

    A null, a boolean or a word is not zero. Text that is a plain number, such as the
    ``"13000000"`` a CSV source gives, is read exactly.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value).strip())
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def _plain(value: Decimal) -> str:
    text = format(value.normalize(), "f")
    return "0" if text in {"-0", ""} else text


def receipt_id(operation: str, operands: Sequence[tuple[str, str, str]], places: int) -> str:
    """The same calculation on the same values always has the same ID, wherever it is run."""
    body = json.dumps([operation, [list(o) for o in operands], places], ensure_ascii=False)
    return "calc-" + hashlib.sha256(body.encode("utf-8")).hexdigest()[:12]


def compute(
    operation: str,
    records: Sequence[Mapping[str, Any]],
    fields: Sequence[str],
    *,
    places: int,
) -> dict[str, Any]:
    """Do one calculation. ``records`` are cards (``fields`` holds their values); raises Refusal."""
    if operation not in OPERATIONS:
        raise Refusal("bad_request", f"operation must be one of: {', '.join(OPERATIONS)}")
    formula, fewest, most = OPERATIONS[operation]
    if not fewest <= len(records) <= most:
        raise Refusal("bad_request", f"{operation} takes {fewest} to {most} operands")
    if not 0 <= places <= MAX_PLACES:
        raise Refusal("bad_request", f"places must be 0 to {MAX_PLACES}")

    numbers: list[Decimal] = []
    shown: list[dict[str, Any]] = []
    for label, record, field in zip(_LETTERS, records, fields, strict=False):
        values = record["fields"]
        if field not in values:
            raise Refusal("field_missing", f"{record['source_ref']} has no field {field!r}")
        number = number_of(values[field])
        if number is None:
            raise Refusal("not_numeric", f"{record['source_ref']} {field} is not a number")
        numbers.append(number)
        entry: dict[str, Any] = {
            "label": label,
            "source_ref": record["source_ref"],
            "field": field,
            "value": _plain(number),
            "as_of": record.get("as_of", ""),
        }
        if values.get("currency"):
            entry["currency"] = values["currency"]
        shown.append(entry)

    currencies = {entry["currency"] for entry in shown if "currency" in entry}
    if len(currencies) > 1:
        raise Refusal("currency_mismatch", "the operands are in different currencies")
    if operation != "ratio" and len(set(fields)) > 1:
        raise Refusal("metric_mismatch", f"{operation} needs the same field in every operand")

    a, b = numbers[0], numbers[-1]
    if operation == "difference":
        exact = _PRECISION.subtract(b, a)
    elif operation == "percent_change":
        if a == 0:
            raise Refusal("division_by_zero", "the earlier value is zero")
        exact = _PRECISION.multiply(_PRECISION.divide(_PRECISION.subtract(b, a), a), Decimal(100))
    elif operation == "ratio":
        if b == 0:
            raise Refusal("division_by_zero", "the divisor is zero")
        exact = _PRECISION.divide(a, b)
    else:
        exact = Decimal(0)
        for number in numbers:
            exact = _PRECISION.add(exact, number)
    rounded = exact.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    letters = list(_LETTERS[: len(numbers)])
    return {
        "receipt_id": receipt_id(
            operation, [(e["source_ref"], e["field"], e["value"]) for e in shown], places
        ),
        "operation": operation,
        "formula": " + ".join(letters) if operation == "sum" else formula,
        "operands": shown,
        "exact": _plain(exact),
        "result": _plain(rounded),
        "places": places,
        "rounding": "half-up",
    }
