"""Run SQL the way the data service would, and compare results by value.

The service transpiles queries from the Redshift dialect to DuckDB and loads the CSV files with
``read_csv``; this does the same, and switches external access off once the files are loaded.
"""

from __future__ import annotations

import threading
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import duckdb
import sqlglot
from sqlglot import exp

from .config import DATA, SKIP_TABLES

ROW_LIMIT = 5000
SECONDS = 20
Rows = list[tuple[Any, ...]]


class Engine:
    """The CSV files of the workspace, loaded into an in-memory DuckDB."""

    def __init__(self) -> None:
        self.connection = duckdb.connect(":memory:")
        for path in sorted(DATA.glob("*.csv")):
            if path.stem in ("data_model", *SKIP_TABLES):
                continue
            self.connection.read_csv(str(path)).create(path.stem)
        self.connection.execute("SET enable_external_access = false")

    def run(self, sql: str, dialect: str = "redshift") -> tuple[list[str], Rows, str | None]:
        """Return ``(columns, rows, error)``; the error is ``None`` when the statement ran."""
        try:
            statements = [s for s in sqlglot.parse(sql, read=dialect) if s]
            if len(statements) != 1:
                return [], [], "not exactly one statement"
            text = statements[0].sql(dialect="duckdb")
        except Exception as error:  # the SQL of a model may be anything
            return [], [], f"parse: {str(error)[:200]}"
        cursor = self.connection.cursor()
        timer = threading.Timer(SECONDS, cursor.interrupt)
        timer.start()
        try:
            cursor.execute(text)
            columns = [str(c[0]) for c in cursor.description or ()]
            rows = cursor.fetchmany(ROW_LIMIT + 1)
        except Exception as error:
            return [], [], f"{type(error).__name__}: {str(error).splitlines()[0][:200]}"
        finally:
            timer.cancel()
            cursor.close()
        if len(rows) > ROW_LIMIT:
            return columns, rows[:ROW_LIMIT], "truncated"
        return columns, rows, None

    def tables_in(self, sql: str, dialect: str = "redshift") -> set[str]:
        """The names of the tables a statement reads."""
        try:
            return {
                t.name for s in sqlglot.parse(sql, read=dialect) if s for t in s.find_all(exp.Table)
            }
        except Exception:
            return set()


def _norm(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, bool):
        return bool(value)
    if isinstance(value, int | float | Decimal):
        return round(float(value), 2)
    if isinstance(value, date | datetime):
        return value.isoformat()[:10]
    return str(value).strip()


def normalize(rows: Rows) -> Rows:
    """Rows with numbers rounded to cents and dates as text, in a fixed order."""
    return sorted((tuple(_norm(v) for v in row) for row in rows), key=repr)


def compare(
    gold_columns: list[str], gold_rows: Rows, columns: list[str], rows: Rows
) -> tuple[bool, float, float, str]:
    """How a result compares with the gold result.

    Returns ``(passed, recall, precision, how)``. The same number of columns is compared by position;
    a result with more columns is compared on the columns whose names match the gold names.
    """
    gold = normalize(gold_rows)
    candidate_rows = rows
    how = "positional"
    if len(columns) != len(gold_columns):
        wanted = [g.lower() for g in gold_columns]
        have = [c.lower() for c in columns]
        if (
            len(columns) > len(gold_columns)
            and all(w in have for w in wanted)
            and len(set(wanted)) == len(wanted)
        ):
            picks = [have.index(w) for w in wanted]
            candidate_rows = [tuple(row[i] for i in picks) for row in rows]
            how = "by name"
        else:
            return False, 0.0, 0.0, f"{len(columns)} columns, wanted {len(gold_columns)}"
    got = normalize(candidate_rows)
    left = list(gold)
    hits = 0
    for row in got:
        if row in left:
            left.remove(row)
            hits += 1
    recall = hits / len(gold) if gold else 1.0
    precision = hits / len(got) if got else (1.0 if not gold else 0.0)
    return got == gold, recall, precision, how
