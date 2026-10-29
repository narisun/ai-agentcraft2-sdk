"""Execute the saved answers and score them. Costs nothing: no model is called.

python -m evals.sql_schema.score answers.jsonl            # a summary
python -m evals.sql_schema.score answers.jsonl --detail   # and one line per answer
"""

from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from typing import Any

from .config import CASES, RESULTS
from .executor import Engine, Rows, compare, normalize
from .prompts import SQL_BLOCK
from .questions import QUESTIONS

REF = re.compile(r"^(?P<table>[a-z_]+)\[(?P<keys>.*)\]$")


def parse_ref(ref: str) -> tuple[str, dict[str, str]] | None:
    """Split ``table[key=value;key=value]`` into the table and its key values."""
    match = REF.match(ref)
    if not match:
        return None
    keys: dict[str, str] = {}
    for part in match["keys"].split(";"):
        name, _, value = part.partition("=")
        keys[name.strip().lower()] = value.strip()
    return match["table"], keys


def blocks(text: str) -> list[str]:
    """The SQL statements in an answer: its code blocks, or the whole text if it has none."""
    found = [b.strip().rstrip(";").strip() for b in SQL_BLOCK.findall(text)]
    return [b for b in found if b] or ([text.strip().rstrip(";")] if text.strip() else [])


def cell(value: Any) -> str:
    """A value as the text a source reference would hold."""
    if value is None:
        return ""
    return str(normalize([(value,)])[0][0])


def score_set1(engine: Engine, answer: dict[str, Any], case: dict[str, Any]) -> dict[str, Any]:
    """Set 1: whether the rows a case names are among the rows the statements return."""
    statements = blocks(answer["text"])
    ran = 0
    tables: set[str] = set()
    results: list[tuple[list[str], Rows]] = []
    for sql in statements[:3]:
        columns, rows, error = engine.run(sql)
        tables |= engine.tables_in(sql)
        if error is None or error == "truncated":
            ran += 1
            results.append((columns, rows))
    parsed = [parse_ref(r) for r in case["required_refs"]]
    wanted = [w for w in parsed if w]
    table_hit = sum(1 for table, _ in wanted if table in tables)
    verifiable = found = 0
    for _, keys in wanted:
        for columns, rows in results:
            have = {c.lower(): i for i, c in enumerate(columns)}
            if not all(k in have for k in keys):
                continue  # the key columns are not in this result: the row cannot be checked here
            verifiable += 1
            if any(all(cell(row[have[k]]) == v for k, v in keys.items()) for row in rows):
                found += 1
            break
    return {
        "ran": bool(statements) and ran == len(statements[:3]),
        "statements": len(statements),
        "refs": len(wanted),
        "tables_hit": table_hit,
        "refs_verifiable": verifiable,
        "refs_found": found,
    }


def score_set2(
    engine: Engine, answer: dict[str, Any], gold: tuple[list[str], Rows]
) -> dict[str, Any]:
    """Set 2: whether the one statement returns the gold result."""
    statements = blocks(answer["text"])
    if len(statements) != 1:
        return {"ran": False, "passed": False, "note": f"{len(statements)} statements"}
    columns, rows, error = engine.run(statements[0])
    if error is not None and error != "truncated":
        return {"ran": False, "passed": False, "note": error[:90]}
    passed, recall, precision, how = compare(gold[0], gold[1], columns, rows)
    return {
        "ran": True,
        "passed": passed,
        "recall": round(recall, 2),
        "precision": round(precision, 2),
        "note": how,
    }


def main() -> None:
    name = sys.argv[1]
    detail = "--detail" in sys.argv
    answers = [
        json.loads(line)
        for line in (RESULTS / name).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    engine = Engine()
    cases: dict[str, dict[str, Any]] = {}
    with CASES.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                case = json.loads(line)
                cases[case["id"]] = case
    gold: dict[str, tuple[list[str], Rows]] = {}
    for q in QUESTIONS:
        columns, rows, _ = engine.run(q["gold"])
        gold[q["id"]] = (columns, rows)
    tests = {q["id"]: q["tests"] for q in QUESTIONS}

    scored: list[dict[str, Any]] = []
    for a in answers:
        if a.get("error") or not a["text"]:
            scored.append({**a, "ran": False, "passed": False, "note": a.get("error", "empty")})
        elif a["set"] == 1:
            scored.append({**a, **score_set1(engine, a, cases[a["id"]])})
        else:
            scored.append({**a, **score_set2(engine, a, gold[a["id"]])})
    (RESULTS / f"{name}.scored.json").write_text(
        json.dumps(scored, indent=1, ensure_ascii=False, default=str), encoding="utf-8"
    )

    groups: dict[tuple[str, int, str], list[dict[str, Any]]] = defaultdict(list)
    for s in scored:
        groups[(s["model"], s["set"], s["condition"])].append(s)
    print("SET 2: results equal to the gold results (and statements that ran)")
    for (model, set_no, condition), rows_ in sorted(groups.items()):
        if set_no == 2:
            passed = sum(r["passed"] for r in rows_)
            ran = sum(r["ran"] for r in rows_)
            cost = sum(r["cost"] for r in rows_)
            print(
                f"  {model:<18} {condition:<9} correct {passed:>2}/{len(rows_)}   ran {ran:>2}/{len(rows_)}   ${cost:.3f}"
            )
    print("SET 1: rows each case needs (found / checkable / required), and tables named")
    for (model, set_no, condition), rows_ in sorted(groups.items()):
        if set_no == 1:
            refs = sum(r.get("refs", 0) for r in rows_)
            found = sum(r.get("refs_found", 0) for r in rows_)
            checkable = sum(r.get("refs_verifiable", 0) for r in rows_)
            tables = sum(r.get("tables_hit", 0) for r in rows_)
            ran = sum(r["ran"] for r in rows_)
            print(
                f"  {model:<18} {condition:<9} rows {found:>2}/{checkable:>2}/{refs:>2}   tables {tables:>2}/{refs:>2}   ran {ran:>2}/{len(rows_)}"
            )
    if detail:
        print()
        for s in sorted(scored, key=lambda r: (r["set"], r["id"], r["model"], r["condition"])):
            mark = "PASS" if s.get("passed") else ("ok  " if s["ran"] else "FAIL")
            if s["set"] == 2:
                extra = s.get("note", "")
            else:
                extra = f"found {s.get('refs_found')}/{s.get('refs_verifiable')}/{s.get('refs')}"
            print(
                f"  {mark} {s['id']} {tests.get(s['id'], ''):<3} {s['model'][7:]:<10} {s['condition']:<9} {extra}"
            )


if __name__ == "__main__":
    main()
