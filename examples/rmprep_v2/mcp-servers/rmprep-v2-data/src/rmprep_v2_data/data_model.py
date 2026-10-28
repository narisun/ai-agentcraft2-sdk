"""The data model standard: what ``data/data_model.csv`` must say about every table and column.

The model is the one place that describes the data: what each table holds, how its rows are
identified, how tables join and what each column means. A person reads it to learn the data and a
model reads it to write queries, so a missing description is a missing fact. This module reads the
file, groups it into tables and checks it against the standard in ``docs/data-model-standard.md``.

An *error* is a fact that is missing or wrong. A *warning* is something a reviewer should look at
but that the file may legitimately say.
"""

from __future__ import annotations

import csv
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

__all__ = [
    "COLUMNS",
    "DATATYPES",
    "Issue",
    "Table",
    "check_data_model",
    "read_data_model",
    "tables_of",
]

COLUMNS = (
    "domain",
    "table",
    "table_description",
    "column",
    "datatype",
    "nullable",
    "pk",
    "uk",
    "fk",
    "description",
)
"""The columns of the file, in order. One row describes one column of one table."""

DATATYPES = ("string", "integer", "decimal", "boolean", "date", "timestamp")

MODEL_FILE = "data_model"
TABLE_DESCRIPTION = (20, 300)
"""The shortest and the longest a table description may be, in characters."""
SHORT_DESCRIPTION = 15

_NAME = re.compile(r"[a-z][a-z0-9_]*")
_REQUIRED = (
    "domain",
    "table",
    "table_description",
    "column",
    "datatype",
    "nullable",
    "description",
)
_FOREIGN_KEY = re.compile(
    r"(?P<domain>[a-z][a-z0-9_]*)\.(?P<table>[a-z][a-z0-9_]*)(?:\.(?P<column>[a-z][a-z0-9_]*))?"
    r"(?:\s*\((?P<note>.*)\))?\s*"
)

Level = Literal["error", "warning"]


@dataclass(frozen=True)
class Issue:
    """One thing a reviewer should read: where it is and what it says."""

    level: Level
    where: str
    message: str

    def __str__(self) -> str:
        return f"{self.level}: {self.where}: {self.message}"


@dataclass
class Table:
    """The rows of the model that describe one table."""

    domain: str
    name: str
    description: str
    columns: list[dict[str, str]] = field(default_factory=list)

    @property
    def primary_key(self) -> list[str]:
        """The columns whose values identify a row, in file order."""
        return [c["column"] for c in self.columns if c["pk"] == "Y"]


def read_data_model(path: Path) -> list[dict[str, str]]:
    """Read the model's rows. A file with other columns is not the standard and is refused."""
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        header = tuple(reader.fieldnames or ())
        if header != COLUMNS:
            raise ValueError(
                f"{path.name} must have exactly these columns, in this order: {', '.join(COLUMNS)} "
                f"(it has: {', '.join(header)})"
            )
        return [{name: (value or "") for name, value in record.items()} for record in reader]


def tables_of(rows: Sequence[Mapping[str, str]]) -> list[Table]:
    """Group rows into tables, in the order the tables first appear."""
    tables: dict[str, Table] = {}
    for record in rows:
        table = tables.setdefault(
            record["table"],
            Table(record["domain"], record["table"], record["table_description"].strip()),
        )
        table.columns.append(dict(record))
    return list(tables.values())


def check_data_model(
    rows: Sequence[Mapping[str, str]], data_dir: Path | None = None
) -> list[Issue]:
    """Check the model against the standard, and against the CSV files in ``data_dir`` if given."""
    issues: list[Issue] = []
    for number, record in enumerate(rows, start=2):  # row 1 of the file is the header
        issues += _row_issues(record, f"row {number}")
    tables = tables_of(rows)
    for table in tables:
        issues += _table_issues(table, rows)
    index = {(t.domain, t.name): t for t in tables}
    for record in rows:
        issues += _foreign_key_issues(record, index)
    if data_dir is not None:
        issues += _folder_issues(tables, data_dir)
    return issues


def _row_issues(record: Mapping[str, str], where: str) -> list[Issue]:
    issues = [
        Issue("error", where, f"{name} is required and is empty")
        for name in _REQUIRED
        if not record[name].strip()
    ]
    for name in ("domain", "table", "column"):
        if record[name].strip() and not _NAME.fullmatch(record[name]):
            issues.append(
                Issue(
                    "error",
                    where,
                    f"{name} '{record[name]}' must be lower case words joined by underscores",
                )
            )
    if record["datatype"].strip() and record["datatype"] not in DATATYPES:
        issues.append(
            Issue(
                "error",
                where,
                f"datatype '{record['datatype']}' is not one of {', '.join(DATATYPES)}",
            )
        )
    if record["nullable"].strip() and record["nullable"] not in ("Y", "N"):
        issues.append(Issue("error", where, f"nullable '{record['nullable']}' must be Y or N"))
    for name in ("pk", "uk"):
        if record[name] not in ("", "Y"):
            issues.append(Issue("error", where, f"{name} '{record[name]}' must be Y or blank"))
    return issues


def _table_issues(table: Table, rows: Sequence[Mapping[str, str]]) -> list[Issue]:
    name = table.name
    issues: list[Issue] = []
    mine = [r for r in rows if r["table"] == name]
    if len({r["domain"] for r in mine}) > 1:
        issues.append(Issue("error", name, "a table belongs to one domain; its rows name several"))
    if len({r["table_description"].strip() for r in mine}) > 1:
        issues.append(
            Issue("error", name, "every row of a table must carry the same table_description")
        )
    issues += _description_issues(table)
    seen: set[str] = set()
    for column in table.columns:
        if column["column"] in seen:
            issues.append(Issue("error", name, f"column {column['column']} appears more than once"))
        seen.add(column["column"])
        if len(column["description"].strip()) < SHORT_DESCRIPTION and column["description"].strip():
            issues.append(
                Issue(
                    "warning", f"{name}.{column['column']}", "the column description is very short"
                )
            )
    key = [c for c in table.columns if c["pk"] == "Y"]
    if not key:
        issues.append(Issue("error", name, "a table needs a primary key: mark pk=Y on its columns"))
    for column in key:
        where = f"{name}.{column['column']}"
        if column["nullable"] == "Y":
            issues.append(Issue("error", where, "a primary key column cannot be nullable"))
        if column["datatype"] == "decimal":
            issues.append(
                Issue(
                    "warning",
                    where,
                    "the primary key includes a decimal column, which is usually a measure; "
                    "check that it identifies the row",
                )
            )
    return issues


def _description_issues(table: Table) -> list[Issue]:
    text, name = table.description, table.name
    if not text:
        return [Issue("error", name, "table_description is required and is empty")]
    low, high = TABLE_DESCRIPTION
    issues: list[Issue] = []
    if len(text) < low:
        issues.append(Issue("error", name, f"table_description must be at least {low} characters"))
    if len(text) > high:
        issues.append(Issue("error", name, f"table_description must be at most {high} characters"))
    if not text.endswith("."):
        issues.append(Issue("error", name, "table_description must end with a full stop"))
    words = set(re.findall(r"[a-z0-9]+", text.lower())) - set(
        re.findall(r"[a-z0-9]+", name.lower())
    )
    if len(words) < 3:
        issues.append(
            Issue("error", name, "table_description must say more than the table name repeated")
        )
    return issues


def _foreign_key_issues(
    record: Mapping[str, str], index: Mapping[tuple[str, str], Table]
) -> list[Issue]:
    text = record["fk"].strip()
    if not text:
        return []
    where = f"{record['table']}.{record['column']}"
    match = _FOREIGN_KEY.fullmatch(text)
    if match is None:
        return [
            Issue(
                "error",
                where,
                f"fk '{text}' must read domain.table.column, then an optional (note)",
            )
        ]
    target = index.get((match["domain"], match["table"]))
    if target is None:
        return [Issue("error", where, f"fk '{text}' names a table the model does not describe")]
    if match["column"] is None:
        return [
            Issue(
                "warning",
                where,
                f"fk '{text}' names a table and no column; say which column it joins, "
                "or put the explanation in the description",
            )
        ]
    if match["column"] not in {c["column"] for c in target.columns}:
        return [
            Issue("error", where, f"fk '{text}' names a column that {target.name} does not have")
        ]
    return []


def _folder_issues(tables: Sequence[Table], data_dir: Path) -> list[Issue]:
    issues: list[Issue] = []
    files = {p.stem: p for p in data_dir.glob("*.csv") if p.stem != MODEL_FILE}
    for table in tables:
        path = files.get(table.name)
        if path is None:
            issues.append(Issue("error", table.name, f"there is no {table.name}.csv in {data_dir}"))
            continue
        with path.open(newline="", encoding="utf-8") as handle:
            header = next(csv.reader(handle), [])
        expected = [c["column"] for c in table.columns]
        if header != expected:
            issues.append(
                Issue(
                    "error",
                    table.name,
                    f"the model and the {path.name} header differ: model has "
                    f"{', '.join(expected)}; file has {', '.join(header)}",
                )
            )
    described = {t.name for t in tables}
    for name in sorted(set(files) - described):
        issues.append(Issue("error", name, f"{name}.csv has no table in the model"))
    return issues
