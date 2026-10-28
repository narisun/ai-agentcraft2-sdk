"""Where the CSV files of a folder disagree with the ``data_model.csv`` that describes them.

The files are read the same way a draft is made from them, so a model drafted from a folder has
no differences with it. A *difference* is something the model says that the data contradicts, or
a table or column present on only one side. A *note* is something a reviewer may want to know that
does not contradict the model: a column typed as text that holds numbers, a key that is larger
than it needs to be, a join the data shows and the model does not state.

Findings count values and never quote them, so the output is safe to paste into a ticket.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from agentcraft2_cli.data_model import Table as ModelTable
from agentcraft2_cli.data_model import parse_foreign_key, tables_of
from agentcraft2_cli.data_model_profile import (
    Table,
    find_foreign_keys,
    find_key,
    infer_type,
    read_folder,
)

__all__ = ["Difference", "compare_model"]

Level = Literal["difference", "note"]


@dataclass(frozen=True)
class Difference:
    """One finding: its level, a short kind to filter on, where it is and what it says."""

    level: Level
    kind: str
    where: str
    message: str

    def __str__(self) -> str:
        return f"{self.level}: {self.kind}: {self.where}: {self.message}"


def _difference(kind: str, where: str, message: str) -> Difference:
    return Difference("difference", kind, where, message)


def _note(kind: str, where: str, message: str) -> Difference:
    return Difference("note", kind, where, message)


def compare_model(rows: Sequence[Mapping[str, str]], data_dir: Path) -> list[Difference]:
    """Compare a model with the CSV files in ``data_dir``."""
    files = read_folder(data_dir)
    described = {t.name: t for t in tables_of(rows)}
    found = [
        _difference("table_not_in_model", name, f"{name}.csv has no table in the model")
        for name in sorted(set(files) - set(described))
    ]
    found += [
        _difference(
            "table_not_in_files", name, f"the model describes {name} but there is no {name}.csv"
        )
        for name in described
        if name not in files
    ]
    both = [name for name in described if name in files]
    types = {
        name: {c: infer_type(files[name].values(c)) for c in files[name].columns} for name in files
    }
    for name in both:
        found += _columns(described[name], files[name])
        found += _nullable(described[name], files[name])
        found += _datatypes(described[name], files[name], types[name])
        found += _keys(described[name], files[name], types[name])
        found += _unique(described[name], files[name])
    found += _joins(described, files, both, types)
    return found


def _columns(model: ModelTable, table: Table) -> list[Difference]:
    wanted = [c["column"] for c in model.columns]
    found = [
        _difference(
            "column_not_in_model",
            f"{table.name}.{c}",
            "the file has this column; the model does not",
        )
        for c in table.columns
        if c not in wanted
    ]
    found += [
        _difference(
            "column_not_in_files",
            f"{table.name}.{c}",
            "the model has this column; the file does not",
        )
        for c in wanted
        if c not in table.columns
    ]
    in_file = [c for c in table.columns if c in wanted]
    in_model = [c for c in wanted if c in table.columns]
    if in_file != in_model:
        found.append(
            _difference(
                "column_order",
                table.name,
                f"the columns are in another order: file {', '.join(in_file)}; "
                f"model {', '.join(in_model)}",
            )
        )
    return found


def _accepts(declared: str, seen: str, values: list[str]) -> bool:
    """Whether a column declared as ``declared`` can hold values that look like ``seen``."""
    if declared in (seen, "string"):
        return True
    if declared == "decimal":
        return seen == "integer"
    if declared == "timestamp":
        return seen == "date"
    if declared == "boolean":
        return seen == "integer" and set(values) <= {"0", "1"}
    return False


def _datatypes(model: ModelTable, table: Table, types: Mapping[str, str]) -> list[Difference]:
    found: list[Difference] = []
    for column in model.columns:
        name = column["column"]
        values = table.values(name) if name in table.cells else []
        if not values:
            continue
        declared, seen, where = column["datatype"], types[name], f"{table.name}.{name}"
        if not _accepts(declared, seen, values):
            found.append(
                _difference(
                    "datatype", where, f"the model says {declared}; the values look like {seen}"
                )
            )
        elif declared == "string" and seen not in ("string", "integer"):
            # Whole-number codes (an industry number) are text on purpose, so they are not news.
            found.append(
                _note("datatype", where, f"the model says string; every value looks like {seen}")
            )
    return found


def _nullable(model: ModelTable, table: Table) -> list[Difference]:
    found: list[Difference] = []
    for column in model.columns:
        name = column["column"]
        if name not in table.cells or not table.rows:
            continue
        empty, where = table.empties(name), f"{table.name}.{name}"
        if column["nullable"] == "N" and empty:
            found.append(
                _difference(
                    "nullable",
                    where,
                    f"the model says the column is never empty; {empty} of {table.rows} values are",
                )
            )
    return found


def _keys(model: ModelTable, table: Table, types: Mapping[str, str]) -> list[Difference]:
    key = model.primary_key
    if not key or any(c not in table.cells for c in key) or not table.rows:
        return []
    found: list[Difference] = []
    label = f"the primary key ({', '.join(key)})"
    empty = sum(table.empties(c) for c in key)
    if empty:
        found.append(_difference("primary_key", table.name, f"{label} has {empty} empty values"))
    repeated = table.rows - len(set(zip(*(table.cells[c] for c in key), strict=True)))
    inferred = find_key(table, dict(types))
    if repeated:
        hint = f"; {', '.join(inferred)} would be unique" if inferred else ""
        found.append(
            _difference(
                "primary_key", table.name, f"{label} repeats in {repeated} rows (duplicates){hint}"
            )
        )
    elif inferred and set(inferred) < set(key):
        found.append(
            _note(
                "primary_key",
                table.name,
                f"{label} is unique, and so is {', '.join(inferred)} alone",
            )
        )
    return found


def _unique(model: ModelTable, table: Table) -> list[Difference]:
    found: list[Difference] = []
    for column in model.columns:
        name = column["column"]
        if column["uk"] != "Y" or name not in table.cells:
            continue
        values = table.values(name)
        repeated = len(values) - len(set(values))
        if repeated:
            found.append(
                _difference(
                    "unique",
                    f"{table.name}.{name}",
                    f"the model says the column is unique; {repeated} values repeat an earlier one",
                )
            )
    return found


def _orphans(
    described: Mapping[str, ModelTable], files: Mapping[str, Table], name: str
) -> list[Difference]:
    """The model's foreign keys of one table whose values have no match in the target."""
    by_domain = {(t.domain, t.name): t.name for t in described.values()}
    found: list[Difference] = []
    for column in described[name].columns:
        parsed = parse_foreign_key(column["fk"]) if column["fk"] else None
        if parsed is None or parsed[2] is None:
            continue
        target = by_domain.get((parsed[0], parsed[1]))
        if target is None or target not in files or parsed[2] not in files[target].cells:
            continue
        if column["column"] not in files[name].cells:
            continue
        known = set(files[target].cells[parsed[2]])
        missing = {v for v in files[name].values(column["column"]) if v not in known}
        if missing:
            noun = "value has" if len(missing) == 1 else "values have"
            # A join the model qualifies in brackets (soft reference, reconciliation) is
            # not promised to match, so unmatched values are worth knowing, not a contradiction.
            report = _note if "(" in column["fk"] else _difference
            found.append(
                report(
                    "foreign_key",
                    f"{name}.{column['column']}",
                    f"{len(missing)} distinct {noun} no match in {target}.{parsed[2]}",
                )
            )
    return found


def _joins(
    described: Mapping[str, ModelTable],
    files: Mapping[str, Table],
    both: Sequence[str],
    types: Mapping[str, Mapping[str, str]],
) -> list[Difference]:
    found: list[Difference] = []
    for name in both:
        found += _orphans(described, files, name)
    keys = {
        name: described[name].primary_key
        if name in described and described[name].primary_key
        else find_key(files[name], dict(types[name]))
        for name in files
    }
    for (name, column), (target, target_column) in sorted(
        find_foreign_keys(dict(files), keys).items()
    ):
        row = (
            next((c for c in described[name].columns if c["column"] == column), None)
            if name in described
            else None
        )
        if row is not None and not row["fk"]:
            found.append(
                _note(
                    "foreign_key_candidate",
                    f"{name}.{column}",
                    f"every value exists in {target}.{target_column}, and the model states no fk",
                )
            )
    return found
