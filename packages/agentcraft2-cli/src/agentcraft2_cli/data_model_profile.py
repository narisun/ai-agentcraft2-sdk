"""What the CSV files of a folder say about themselves: types, empties, keys and joins.

A draft model and a comparison both start here. Everything is read from the files and nothing is
sent anywhere. The findings are *inferences*: a column that is unique in these rows may not be
unique in the next delivery, so the callers present them as drafts and notes, never as facts.
"""

from __future__ import annotations

import csv
import itertools
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from agentcraft2_cli.data_model import MODEL_FILE
from agentcraft2_cli.errors import CliError

__all__ = [
    "KEY_COLUMN_LIMIT",
    "KEY_SIZE_LIMIT",
    "Table",
    "find_foreign_keys",
    "find_key",
    "infer_type",
    "read_folder",
    "read_table",
]

KEY_SIZE_LIMIT = 6
"""The most columns a primary key inferred from data may have."""
KEY_COLUMN_LIMIT = 12
"""How many columns of a table are tried in combination when looking for a key."""

_INTEGER = re.compile(r"[+-]?(0|[1-9][0-9]{0,17})")
_DECIMAL = re.compile(r"[+-]?(0|[1-9][0-9]*)?\.[0-9]+|[+-]?(0|[1-9][0-9]*)\.[0-9]*")
_BOOLEANS = frozenset({"true", "false", "yes", "no", "y", "n"})
_IDENTIFIER_NAME = re.compile(r"id|.*_id")


@dataclass
class Table:
    """One CSV file read whole: its columns in order and the stripped cells of each."""

    name: str
    columns: list[str]
    cells: dict[str, list[str]]
    rows: int

    def values(self, column: str) -> list[str]:
        """The cells of a column that are not empty."""
        return [v for v in self.cells[column] if v]

    def empties(self, column: str) -> int:
        """How many cells of a column are empty."""
        return sum(1 for v in self.cells[column] if not v)


def read_table(path: Path) -> Table:
    """Read one CSV file. Cells are stripped; a short row is padded with empty cells."""
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.reader(handle)
        header = [name.strip() for name in next(reader, [])]
        if not header:
            raise CliError(f"{path.name} is empty", fix="give it a header row naming its columns")
        if len(set(header)) != len(header):
            raise CliError(
                f"{path.name} has two columns with the same name",
                fix="rename one so that every column name is unique",
            )
        cells: dict[str, list[str]] = {name: [] for name in header}
        rows = 0
        for record in reader:
            if not any(cell.strip() for cell in record):
                continue  # a blank line is not a row
            rows += 1
            for index, name in enumerate(header):
                cells[name].append(record[index].strip() if index < len(record) else "")
    return Table(path.stem, header, cells, rows)


def read_folder(data_dir: Path) -> dict[str, Table]:
    """Read every CSV file of a folder except the model itself, by table name."""
    if not data_dir.is_dir():
        raise CliError(
            f"{data_dir} is not a folder",
            expected="a folder of CSV files, one per table",
            fix="give the path of the data folder",
        )
    paths = sorted(p for p in data_dir.glob("*.csv") if p.stem != MODEL_FILE)
    if not paths:
        raise CliError(
            f"there are no CSV files in {data_dir}",
            expected="one CSV file per table, with a header row",
            fix="point at the folder that holds the data",
        )
    return {path.stem: read_table(path) for path in paths}


def infer_type(values: list[str]) -> str:
    """The narrowest type that every value fits: boolean, integer, decimal, date or timestamp.

    A number with a leading zero is an identifier, not a number. Anything else is a string,
    and so is a column with no values.
    """
    if not values:
        return "string"
    if all(v.lower() in _BOOLEANS for v in values):
        return "boolean"
    if all(_INTEGER.fullmatch(v) for v in values):
        return "integer"
    if all(_INTEGER.fullmatch(v) or _DECIMAL.fullmatch(v) for v in values):
        return "decimal"
    if all(_is_date(v) for v in values):
        return "date"
    if all(_is_timestamp(v) for v in values):
        return "timestamp"
    return "string"


def _is_date(value: str) -> bool:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _is_timestamp(value: str) -> bool:
    if not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?", value
    ):
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def find_key(table: Table, types: dict[str, str]) -> list[str]:
    """The smallest set of columns whose values are unique in every row; empty if none is.

    Columns with empty cells, decimals (measures) and booleans are never part of a key. A single
    column named like an identifier wins; otherwise the first unique column; otherwise the first
    unique combination, trying fewer columns before more and earlier columns before later ones.
    """
    if table.rows == 0:
        return []
    eligible = [
        c for c in table.columns if table.empties(c) == 0 and types[c] not in ("decimal", "boolean")
    ]
    unique = [c for c in eligible if len(set(table.cells[c])) == table.rows]
    named = [c for c in unique if _IDENTIFIER_NAME.fullmatch(c)]
    if named or unique:
        return [(named or unique)[0]]
    for size in range(2, KEY_SIZE_LIMIT + 1):
        for combination in itertools.combinations(eligible[:KEY_COLUMN_LIMIT], size):
            if len(set(zip(*(table.cells[c] for c in combination), strict=True))) == table.rows:
                return list(combination)
    return []


def find_foreign_keys(
    tables: dict[str, Table], keys: dict[str, list[str]]
) -> dict[tuple[str, str], tuple[str, str]]:
    """Columns whose every value is a value of another table's single-column key, same name.

    The result maps (table, column) to the (table, column) it points at. A key column can point
    at another table's key too (a one-to-one extension of it). When several tables could be the
    parent, the widest wins and a tie in width goes to the name that sorts first. Two tables whose
    key values are exactly the same could each point at the other, so only the one that ranks
    below (narrower, or later by name) points at the other.
    """
    parents = {name: key[0] for name, key in keys.items() if len(key) == 1}
    found: dict[tuple[str, str], tuple[str, str]] = {}
    for name, table in tables.items():
        for column in table.columns:
            candidates = [
                parent
                for parent, key in parents.items()
                if parent != name
                and key == column
                and _points_at(table, column, tables[parent], key)
                and (
                    set(table.values(column)) != set(tables[parent].cells[key])
                    or _ranks_above(tables[parent], table)
                )
            ]
            if candidates:
                best = min(candidates, key=lambda t: (-len(tables[t].columns), t))
                found[(name, column)] = (best, column)
    return found


def _points_at(child: Table, column: str, parent: Table, key: str) -> bool:
    values = set(child.values(column))
    return bool(values) and values <= set(parent.cells[key])


def _ranks_above(parent: Table, child: Table) -> bool:
    """Whether ``parent`` is wider than ``child``, or as wide and first by name."""
    return (len(parent.columns), child.name) > (len(child.columns), parent.name)
