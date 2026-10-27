"""Warn about figures an answer states that none of the records it was shown support.

This is a heuristic, not a proof: it can say a figure is not among the records the answer could
see, never that it is wrong. So it never blocks an answer. It adds a visible caveat that travels
with the answer text, and names each figure so the reader can check it. A figure that is
derived (a change, a ratio) belongs in a calculation receipt; one that is not stated anywhere is
flagged. Dates, identifiers, references, ordinals, list numbers and small counts are not figures.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from langchain_core.messages import AnyMessage, ToolMessage

from rmprep_v2.citations import framed_payload
from rmprep_v2.receipts import number_of
from rmprep_v2.response_review import _REFERENCE, tool_payload
from rmprep_v2.tool_names import EVIDENCE_TOOL_NAMES

__all__ = ["Mention", "evidence_numbers", "mentions", "numeric_warnings", "warning_footer"]

MAX_WARNINGS = 5
_SMALL = Decimal(10)

_NOISE = re.compile(
    r"""
      \b\d{4}-\d{2}-\d{2}\b                      # an ISO date
    | \b(?:FY|Q)\s?\d{1,4}\b                     # FY2025, Q3
    | \b(?:[A-Za-z]+-)+\d+\b                     # an identifier such as JONE-0022 or memo-a12b
    | \bmemo-[a-z0-9]{12}\b
    | \b\d+(?:st|nd|rd|th)\b                     # an ordinal
    | \b[Vv]ersions?\s+\d+\b                     # version 2
    | ^\s{0,3}\d{1,3}[.)](?=\s)                  # a list marker at the start of a line
    """,
    re.VERBOSE | re.MULTILINE,
)
_FIGURE = re.compile(
    r"""
    (?<![A-Za-z0-9_.])
    (?P<currency>[$€£]\s?)?
    (?P<sign>-)?
    (?P<number>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)
    (?P<percent>\s?%)?
    (?P<suffix>(?:bn|billion|[kKmMbB]|x)\b)?
    """,
    re.VERBOSE,
)
_SCALE = {"k": Decimal(10) ** 3, "m": Decimal(10) ** 6, "b": Decimal(10) ** 9}


@dataclass(frozen=True)
class Mention:
    """A figure as written in an answer."""

    text: str
    value: Decimal
    context: str
    percent: bool


def _scale(suffix: str | None) -> Decimal:
    if not suffix or suffix == "x":
        return Decimal(1)
    return _SCALE[
        "b"
        if (suffix.startswith("b") and suffix != "b") or suffix in ("b", "bn")
        else suffix[0].lower()
    ]


def mentions(text: str) -> list[Mention]:
    """The figures of ``text`` in order, after leaving out dates, identifiers and references."""
    cleaned = _NOISE.sub(" ", _REFERENCE.sub(" ", text))
    found: list[Mention] = []
    for match in _FIGURE.finditer(cleaned):
        digits = match["number"].replace(",", "")
        try:
            value = Decimal(digits)
        except InvalidOperation:
            continue
        plain = not (match["currency"] or match["percent"] or match["suffix"] or "." in digits)
        if "," in match["number"]:
            plain = False
        if plain and abs(value) <= _SMALL:
            continue  # a count, not a measurement
        if plain and 1900 <= value <= 2100:
            continue  # a year
        value = value * _scale(match["suffix"])
        if match["sign"]:
            value = -value
        before = cleaned[: match.start()].split()[-2:]
        after = cleaned[match.end() :].split()[:2]
        shown = match.group(0).strip()
        found.append(
            Mention(shown, value, " ".join([*before, shown, *after]), bool(match["percent"]))
        )
    return found


def _collect(value: Any, into: set[Decimal]) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            # A field name can carry a threshold, such as the 90 of ar_over_90_pct.
            into.update(Decimal(digits) for digits in re.findall(r"\d+", str(key)))
            _collect(item, into)
    elif isinstance(value, list | tuple):
        for item in value:
            _collect(item, into)
    elif (number := number_of(value)) is not None:
        into.add(number)


def evidence_numbers(messages: Sequence[AnyMessage]) -> set[Decimal]:
    """Every number in the records the model was shown in full, calculations included."""
    numbers: set[Decimal] = set()
    for message in messages:
        if not isinstance(message, ToolMessage):
            continue
        payload = (
            framed_payload(message)
            if message.name in EVIDENCE_TOOL_NAMES
            else tool_payload(message)
        )
        _collect(payload, numbers)
    return numbers


def _tolerance(mention: Mention) -> Decimal:
    """Half a unit of the last digit written, so a rounded figure matches the value it rounds."""
    digits = re.sub(r"[^\d.]", "", mention.text.split("%")[0])
    places = len(digits.split(".")[1]) if "." in digits else 0
    suffix = re.search(r"(bn|billion|[kKmMbB])\b", mention.text)
    return Decimal(5) * Decimal(10) ** -(places + 1) * _scale(suffix.group(1) if suffix else None)


def _supported(mention: Mention, numbers: Iterable[Decimal]) -> bool:
    tolerance = _tolerance(mention)
    for number in numbers:
        if abs(number - mention.value) <= tolerance:
            return True
        if mention.percent and abs(number * 100 - mention.value) <= tolerance:
            return True
    return False


def numeric_warnings(
    text: str, messages: Sequence[AnyMessage], *, memo_texts: Iterable[str] = ()
) -> list[dict[str, str]]:
    """Figures of ``text`` that no record shown, calculation or memo statement supports."""
    numbers = evidence_numbers(messages)
    for memo in memo_texts:
        numbers.update(m.value for m in mentions(memo))
    warnings: list[dict[str, str]] = []
    seen: set[str] = set()
    for mention in mentions(text):
        if mention.text in seen or _supported(mention, numbers):
            continue
        seen.add(mention.text)
        warnings.append(
            {
                "code": "figure_not_in_evidence",
                "figure": mention.text,
                "context": mention.context,
                "advice": "State the records it comes from, or compute it with the calculate tool.",
            }
        )
        if len(warnings) == MAX_WARNINGS:
            break
    return warnings


def warning_footer(warnings: Sequence[Mapping[str, str]]) -> str:
    """The caveat appended to a released answer, so copying or exporting the text keeps it."""
    if not warnings:
        return ""
    items = "; ".join(f'{w["figure"]} ("{re.sub(r"[<>]", "", w["context"])}")' for w in warnings)
    return (
        "\n\nCheck before use: these figures are not stated in the records read for this answer: "
        + items
        + ". Verify each against its source."
    )
