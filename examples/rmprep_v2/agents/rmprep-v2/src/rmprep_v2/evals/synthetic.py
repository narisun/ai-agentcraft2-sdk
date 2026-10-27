"""A small made-up memo pack, so store and tool tests need no data server."""

from __future__ import annotations

from typing import Any

from rmprep_v2.cards import Card, Note, build_cards

__all__ = ["cards_of", "pack"]


def pack(meeting_date: str = "2026-08-03", dscr: float = 1.08, filler: int = 0) -> dict[str, Any]:
    """A pack with covenants, working capital and facilities, and one review finding.

    ``filler`` adds that many long records to a ``notes`` section, to test size limits.
    """
    sections: dict[str, Any] = {
        "covenants": {
            "note": "Covenant tests as recorded.",
            "records": [
                {"source_ref": "covenants[name=DSCR]", "name": "DSCR", "current_value": dscr},
                {"source_ref": "covenants[name=FCC]", "name": "Fixed Charge Coverage"},
            ],
        },
        "working_capital": {
            "note": "Days measures per period.",
            "records": [
                {
                    "source_ref": f"working_capital[period_end=2026-0{n}-30]",
                    "cash_conversion_cycle_days": 70 + n,
                }
                for n in (3, 6)
            ],
        },
        "facilities": {
            "note": "Facilities.",
            "records": [{"source_ref": "facilities[id=F1]", "facility_type": "Term Loan"}],
        },
    }
    if filler:
        sections["notes"] = {
            "note": "Long free text.",
            "records": [
                {"source_ref": f"notes[id={n}]", "text": ("covenant " * 360).strip()}
                for n in range(filler)
            ],
        }
    return {
        "uen": "JONE-0022",
        "meeting_date": meeting_date,
        "sections": sections,
        "review_findings": [
            {
                "code": "COVENANT_BREACH_RISK",
                "priority": "urgent",
                "message": "Recorded covenant verdict is not in compliance.",
                "source_refs": ["covenants[name=DSCR]"],
            }
        ],
        "missing_sections": [],
        "limitations": ["A supplied snapshot."],
    }


def cards_of(
    source: str, meeting_date: str = "2026-08-03", dscr: float = 1.08, filler: int = 0
) -> tuple[list[Card], list[Note]]:
    """Cards and notes of a synthetic pack stored as ``source``."""
    return build_cards(source, pack(meeting_date, dscr, filler))
