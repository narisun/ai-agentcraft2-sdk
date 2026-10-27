"""How often does keyword search over the cards put the required records in the top ten?

    python -m rmprep_v2.evals.retrieval_recall

For each evaluation case that names required references, the question text is used as the
search query over that client's cards, as a model's first search would be. The score is the
share of the required records found in the first ten hits (recall at 10). The questions are
used as written, so a follow-up such as "Explain the second one." scores zero: resolving it
needs the conversation, not the index.
"""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from typing import Any

from rmprep_v2.cards import build_cards
from rmprep_v2.evals.cases import Case, load_cases
from rmprep_v2.evals.packs import load_packs
from rmprep_v2.evidence_store import SqliteEvidenceStore

__all__ = ["measure"]

TOP = 10


async def measure(cases: list[Case] | None = None) -> dict[str, Any]:
    """Recall at ten for every case that names required references."""
    chosen = [case for case in (cases or load_cases()) if case["required_refs"]]
    packs = await load_packs({(case["uen"], case["as_of"]) for case in chosen})
    rows: list[dict[str, Any]] = []
    async with SqliteEvidenceStore(":memory:") as store:
        for (uen, as_of), pack in packs.items():
            source = f"src-{uen}-{as_of}"
            cards, notes = build_cards(source, pack)
            await store.put_cards("eval", source, cards, notes)
        for case in chosen:
            source = f"src-{case['uen']}-{case['as_of']}"
            page = await store.search_cards("eval", [source], case["question"], limit=TOP)
            found = {hit.card["source_ref"] for hit in page.hits}
            required = set(case["required_refs"])
            rows.append(
                {
                    "id": case["id"],
                    "family": case["family"],
                    "required": len(required),
                    "found": len(required & found),
                    "recall": len(required & found) / len(required),
                    "complete": required <= found,
                }
            )
    by_family: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_family[row["family"]].append(row)
    return {
        "top": TOP,
        "cases": len(rows),
        "mean_recall": sum(row["recall"] for row in rows) / len(rows),
        "complete": sum(row["complete"] for row in rows),
        "by_family": {
            family: {
                "cases": len(group),
                "mean_recall": round(sum(r["recall"] for r in group) / len(group), 3),
                "complete": sum(r["complete"] for r in group),
            }
            for family, group in sorted(by_family.items())
        },
        "rows": rows,
    }


def main() -> None:
    report = asyncio.run(measure())
    print(
        f"recall@{report['top']}: mean {report['mean_recall']:.3f} over {report['cases']} cases; "
        f"all required records found in {report['complete']}"
    )
    for family, numbers in report["by_family"].items():
        print(f"  {family:<17} {numbers}")
    print(json.dumps([row for row in report["rows"] if row["recall"] < 1], indent=1)[:1500])


if __name__ == "__main__":
    main()
