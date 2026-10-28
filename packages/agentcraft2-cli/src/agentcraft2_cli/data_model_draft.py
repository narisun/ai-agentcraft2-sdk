"""A draft ``data_model.csv`` inferred from a folder of CSV files.

The draft states what the files show: each table's columns in order, a type, whether the column
was ever empty, a primary key, and joins to other tables' keys. What only a person knows is a
placeholder that the standard's check reports until it is replaced: the domain and every
description. For a column with a few repeated values the placeholder lists them, which is how
most descriptions already name their allowed values.
"""

from __future__ import annotations

import csv
from pathlib import Path

from agentcraft2_cli.data_model import COLUMNS, PLACEHOLDER_DOMAIN, PLACEHOLDER_TEXT
from agentcraft2_cli.data_model_profile import (
    Table,
    find_foreign_keys,
    find_key,
    infer_type,
    read_folder,
)

__all__ = ["draft_model", "write_model"]

VALUES_SHOWN = 8
"""A column with at most this many different values, some repeated, lists them."""
VALUE_LENGTH = 24


def draft_model(data_dir: Path) -> list[dict[str, str]]:
    """The rows of a draft model for every CSV file in ``data_dir``, in file-name order."""
    tables = read_folder(data_dir)
    types = {
        name: {c: infer_type(table.values(c)) for c in table.columns}
        for name, table in tables.items()
    }
    keys = {name: find_key(table, types[name]) for name, table in tables.items()}
    joins = find_foreign_keys(tables, keys)
    rows: list[dict[str, str]] = []
    for name, table in tables.items():
        about = (
            f"{PLACEHOLDER_TEXT}: describe this table. "
            f"It has {table.rows} rows and {len(table.columns)} columns."
        )
        for column in table.columns:
            target = joins.get((name, column))
            values = [
                PLACEHOLDER_DOMAIN,
                name,
                about,
                column,
                types[name][column],
                # Only a key claims to be never empty: a column empty in no row of this
                # delivery may still be allowed to be empty in the next.
                "N" if column in keys[name] else "Y",
                "Y" if column in keys[name] else "",
                "",
                f"{PLACEHOLDER_DOMAIN}.{target[0]}.{target[1]}" if target else "",
                _description(table, column, types[name][column], column in keys[name]),
            ]
            rows.append(dict(zip(COLUMNS, values, strict=True)))
    return rows


def _description(table: Table, column: str, kind: str, in_key: bool) -> str:
    text = f"{PLACEHOLDER_TEXT}: describe this column."
    values = table.values(column)
    seen = sorted(set(values))
    if (
        kind in ("string", "boolean")
        and 0 < len(seen) <= VALUES_SHOWN
        and len(seen) < len(values)
        and all(len(v) <= VALUE_LENGTH for v in seen)
    ):
        text += f" Values seen: {' | '.join(seen)}."
    if table.rows and not in_key and table.empties(column) == 0:
        text += f" Never empty in the {table.rows} rows."
    return text


def write_model(rows: list[dict[str, str]], path: Path) -> None:
    """Write rows as a ``data_model.csv``, creating the folder if it does not exist."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS, lineterminator="\r\n")
        writer.writeheader()
        writer.writerows(rows)
