"""The schema of a data model, written for a model that writes SQL.

A model asked for SQL needs to know what it may use and how the pieces fit: which tables exist
and what a row of each is, what each column holds, which columns identify a row, and how tables
join and whether a join can repeat rows. ``data_model.csv`` says all of it; this module reads it
and writes it down once, as Markdown to put in a prompt or as JSON for a program, for the whole
model or for the few tables a question needs.

Nothing here asks a model anything or runs SQL. With ``values`` it also reads the data files, to
list the few different values of a column that holds a short vocabulary (a segment, a status, a
currency), because a model that has seen ``'Retail'`` does not write ``'retail'``. That copies
values out of the data into the prompt, so it is off unless asked for, and it never lists a key.
"""

from __future__ import annotations

import difflib
import json
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agentcraft2_cli.data_model import Table as ModelTable
from agentcraft2_cli.data_model import parse_foreign_key, tables_of
from agentcraft2_cli.data_model_profile import read_table

__all__ = [
    "Join",
    "Reference",
    "Schema",
    "SchemaColumn",
    "SchemaTable",
    "build_schema",
    "estimate_tokens",
    "render_json",
    "render_markdown",
]

VALUE_LENGTH = 40
"""The longest a listed value is; a longer one is cut and ends in three dots."""
_NOTE = re.compile(r"\((?P<note>.*)\)\s*$")


@dataclass(frozen=True)
class Reference:
    """Where a column points: a table and, if the model says so, one of its columns.

    ``kind`` is ``reference`` (a key to join on), ``soft`` (a key, but some rows may match
    nothing) or ``relation`` (related, but not something to join on; see :attr:`Join.for_joining`).
    """

    table: str
    column: str | None
    kind: str
    note: str


@dataclass(frozen=True)
class SchemaColumn:
    """One column: what it holds, whether it may be empty and how it is used as a key."""

    name: str
    type: str
    nullable: bool
    primary_key: bool
    unique: bool
    references: Reference | None
    description: str
    values: tuple[str, ...] = ()


@dataclass(frozen=True)
class SchemaTable:
    """One table: what a row of it is, how rows are identified and its columns."""

    name: str
    domain: str
    description: str
    primary_key: tuple[str, ...]
    unique: tuple[str, ...]
    columns: tuple[SchemaColumn, ...]


@dataclass(frozen=True)
class Join:
    """A way to join two tables, from the table that points to the table it points at.

    Attributes:
        kind: ``reference`` (a column to a column), ``soft`` (the same, but the model says some
            rows may match nothing) or ``table`` (the tables relate, with no column to join on).
        cardinality: ``many-to-one``, ``one-to-one`` or ``unknown`` (the parent's column is not
            unique on its own, so a join can repeat rows).
        note: What the model says in brackets after the reference.
        warning: What to watch for when joining, or empty.
    """

    child_table: str
    child_column: str
    parent_table: str
    parent_column: str | None
    kind: str
    cardinality: str
    note: str
    warning: str

    @property
    def condition(self) -> str:
        """The join condition, or an empty text for tables that relate with no column."""
        if self.parent_column is None:
            return ""
        return f"{self.child_table}.{self.child_column} = {self.parent_table}.{self.parent_column}"

    @property
    def for_joining(self) -> bool:
        """Whether to join on it.

        A column that points at another table's key is a join. A soft reference to a column that
        is not a key is a relation the model notes (a reconciliation, an eligibility gate, a
        threshold): its values are measures, and an equality on them is not a join. A relation
        between tables with no column has no condition at all.
        """
        return self.parent_column is not None and not (
            self.kind == "soft" and self.cardinality == "unknown"
        )


@dataclass(frozen=True)
class Schema:
    """The tables and joins chosen from a model, and how they are meant to be read."""

    dialect: str
    tables: tuple[SchemaTable, ...]
    joins: tuple[Join, ...]
    tables_in_model: int
    values_from_data: bool


def build_schema(
    rows: Sequence[Mapping[str, str]],
    *,
    data_dir: Path | None = None,
    tables: Sequence[str] | None = None,
    related: bool = False,
    exclude: Sequence[str] = (),
    values: int = 0,
    dialect: str = "redshift",
) -> Schema:
    """Build the schema of a model, or of the tables a question needs.

    Args:
        rows: The rows of ``data_model.csv``.
        data_dir: The folder of CSV files; only read when ``values`` is asked for.
        tables: Only these tables. By default every table.
        related: Also the tables one join away from the chosen ones, in either direction.
        exclude: Tables to leave out, such as one the services do not offer. Never added by
            ``related``, and no column says it points at one.
        values: List the different values of a text column that has at most this many. Keys never.
        dialect: The SQL dialect to write for; it is said in the schema, not checked.

    Raises:
        ValueError: If a chosen or excluded table is not in the model, a table is both, or
            ``values`` is asked for without the data folder.
    """
    if values and data_dir is None:
        raise ValueError("values come from the data files: give the data folder")
    whole = tables_of(rows)
    names = [t.name for t in whole]
    for name in (*(tables or ()), *exclude):
        _known(name, names)
    both = sorted(set(tables or ()) & set(exclude))
    if both:
        raise ValueError(f"a table cannot be both chosen and excluded: {', '.join(both)}")
    model = [t for t in whole if t.name not in set(exclude)]
    by_domain = {(t.domain, t.name): t for t in model}
    every_join = [j for t in model for j in _joins_of(t, by_domain)]
    chosen = _chosen([t.name for t in model], every_join, tables, related)
    kept = tuple(t for t in model if t.name in chosen)
    joins = tuple(j for j in every_join if j.child_table in chosen and j.parent_table in chosen)
    listed = {t.name: _values(t, data_dir, values) for t in kept} if values else {}
    # A column still says what it points at when that table is not in the choice, but not when the
    # table is excluded: an excluded table is not named anywhere.
    pointing = {(j.child_table, j.child_column): j for j in every_join}
    return Schema(
        dialect=dialect,
        tables=tuple(_table(t, pointing, listed) for t in kept),
        joins=joins,
        tables_in_model=len(whole),
        values_from_data=values > 0,
    )


def _known(name: str, names: Sequence[str]) -> None:
    if name not in names:
        close = difflib.get_close_matches(name, names, n=1)
        hint = f" Did you mean {close[0]}?" if close else f" the tables are: {', '.join(names)}"
        raise ValueError(f"there is no table called {name!r}.{hint}")


def _chosen(
    names: Sequence[str], joins: Sequence[Join], tables: Sequence[str] | None, related: bool
) -> set[str]:
    if not tables:
        return set(names)
    chosen = set(tables)
    if related:
        for join in joins:
            if join.child_table in tables:
                chosen.add(join.parent_table)
            if join.parent_table in tables:
                chosen.add(join.child_table)
    return chosen


def _joins_of(table: ModelTable, by_domain: Mapping[tuple[str, str], ModelTable]) -> list[Join]:
    found = []
    for column in table.columns:
        text = column["fk"].strip()
        parsed = parse_foreign_key(text) if text else None
        parent = by_domain.get((parsed[0], parsed[1])) if parsed else None
        if parsed is None or parent is None:
            continue  # the check of the model names it; a schema is not the place for it
        note = _NOTE.search(text)
        found.append(
            _join(table, column["column"], parent, parsed[2], note["note"].strip() if note else "")
        )
    return found


def _join(
    child: ModelTable, child_column: str, parent: ModelTable, parent_column: str | None, note: str
) -> Join:
    if parent_column is None:
        return Join(child.name, child_column, parent.name, None, "table", "unknown", note, "")
    unique = parent.primary_key == [parent_column] or any(
        c["column"] == parent_column and c["uk"] == "Y" for c in parent.columns
    )
    if not unique:
        whole = (
            f"join on its whole key ({', '.join(parent.primary_key)}) or "
            if (parent_column in parent.primary_key)
            else ""
        )
        warning = (
            f"{parent.name}.{parent_column} is not unique on its own; {whole}expect repeated rows"
        )
        cardinality = "unknown"
    else:
        warning = ""
        cardinality = "one-to-one" if child.primary_key == [child_column] else "many-to-one"
    kind = "soft" if note else "reference"
    if note:
        warning = "; ".join(part for part in (warning, f"may match nothing ({note})") if part)
    return Join(
        child.name, child_column, parent.name, parent_column, kind, cardinality, note, warning
    )


def _table(
    table: ModelTable,
    pointing: Mapping[tuple[str, str], Join],
    listed: Mapping[str, Mapping[str, tuple[str, ...]]],
) -> SchemaTable:
    columns = []
    for column in table.columns:
        join = pointing.get((table.name, column["column"]))
        reference = None
        if join is not None:
            kind = join.kind if join.for_joining else "relation"
            reference = Reference(join.parent_table, join.parent_column, kind, join.note)
        columns.append(
            SchemaColumn(
                name=column["column"],
                type=column["datatype"],
                nullable=column["nullable"].strip().upper() == "Y",
                primary_key=column["pk"] == "Y",
                unique=column["uk"] == "Y",
                references=reference,
                description=column["description"].strip(),
                values=listed.get(table.name, {}).get(column["column"], ()),
            )
        )
    return SchemaTable(
        name=table.name,
        domain=table.domain,
        description=table.description,
        primary_key=tuple(table.primary_key),
        unique=tuple(c["column"] for c in table.columns if c["uk"] == "Y"),
        columns=tuple(columns),
    )


def _values(table: ModelTable, data_dir: Path | None, limit: int) -> dict[str, tuple[str, ...]]:
    """The different values of each short text column that is not a key, most common first."""
    assert data_dir is not None  # noqa: S101 - build_schema checked it
    path = data_dir / f"{table.name}.csv"
    if not path.is_file():
        return {}
    data = read_table(path)
    found: dict[str, tuple[str, ...]] = {}
    for column in table.columns:
        name = column["column"]
        if (
            column["datatype"] != "string"
            or column["pk"] == "Y"
            or column["uk"] == "Y"
            or column["fk"].strip()
            or name not in data.cells
        ):
            continue
        counts = Counter(v for v in data.values(name) if "\n" not in v and "\r" not in v)
        if 1 <= len(counts) <= limit:
            ordered = sorted(counts, key=lambda value: (-counts[value], value))
            found[name] = tuple(_short(value) for value in ordered)
    return found


def _short(value: str) -> str:
    return value if len(value) <= VALUE_LENGTH else value[: VALUE_LENGTH - 3] + "..."


# --- writing it -------------------------------------------------------------------------------


def _cell(text: str) -> str:
    """Text for one cell of a Markdown table: one line, and no bar that would end the cell."""
    return " ".join(text.replace("|", "/").split())


def _join_line(join: Join) -> str:
    flavour = join.cardinality + (", soft" if join.kind == "soft" else "")
    line = f"- {join.condition} ({flavour})"
    return f"{line}: {join.warning}" if join.warning else line


def _relation_lines(joins: Sequence[Join]) -> list[str]:
    """One line for each relation that is not a join; those that say the same are one line."""
    groups: dict[tuple[str, str, str | None, str], list[str]] = {}
    for join in joins:
        key = (join.child_table, join.parent_table, join.parent_column, join.note)
        groups.setdefault(key, []).append(f"{join.child_table}.{join.child_column}")
    lines = []
    for (_, parent, column, note), children in groups.items():
        target = parent + (f".{column}" if column else "")
        lines.append(
            f"- {', '.join(children)} relates to {target}" + (f" ({note})" if note else "")
        )
    return lines


def _key(column: SchemaColumn) -> str:
    parts = []
    if column.primary_key:
        parts.append("pk")
    if column.unique:
        parts.append("uk")
    if column.references is not None:
        reference = column.references
        target = reference.table + (f".{reference.column}" if reference.column else "")
        parts.append(
            {"reference": "fk ", "soft": "soft fk ", "relation": "relates "}[reference.kind]
            + target
        )
    return ", ".join(parts)


def render_markdown(schema: Schema) -> str:
    """Write the schema as Markdown: the rules, the index, the joins, then a card per table."""
    domains = len({t.domain for t in schema.tables})
    chosen = len(schema.tables)
    size = (
        f"{chosen} tables"
        if chosen == schema.tables_in_model
        else f"{chosen} of {schema.tables_in_model} tables (the rest of the model is left out)"
    )
    rules = (
        f"SQL dialect: {schema.dialect}. Use only the tables, columns and joins listed here. "
        "Join only as the join list says; do not guess a join. Dates are written YYYY-MM-DD and "
        "timestamps in ISO 8601."
    )
    if any(c.type == "boolean" for t in schema.tables for c in t.columns):
        # A description may say "Yes/No" for what a file holds; the engine reads a boolean.
        rules += (
            " Boolean columns hold true or false: compare them with TRUE or FALSE, not with text."
        )
    lines = [
        "# Data schema",
        "",
        rules,
        "",
        f"{size}, in {domains} {'domain' if domains == 1 else 'domains'}.",
    ]
    if schema.values_from_data:
        lines += [
            "",
            "Values were read from the data files. They show how a column is written; they are "
            "not a complete list.",
        ]
    lines += ["", "## Tables", "", "| table | domain | what it holds | one row per |"]
    lines.append("| --- | --- | --- | --- |")
    for table in schema.tables:
        key = ", ".join(table.primary_key) or "(no primary key)"
        lines.append(
            f"| {table.name} | {_cell(table.domain)} | {_cell(table.description)} | {_cell(key)} |"
        )
    lines += ["", "## Joins", ""]
    real = [join for join in schema.joins if join.for_joining]
    lines += [_join_line(join) for join in real] or ["None between these tables."]
    others = [join for join in schema.joins if not join.for_joining]
    if others:
        lines += [
            "",
            "## Other relations",
            "",
            "These relate tables or columns, but are not keys: do not join on them as written; "
            "the note in brackets says how they relate.",
            "",
            *_relation_lines(others),
        ]
    for table in schema.tables:
        lines += ["", f"## {table.name}", "", _cell(table.description), ""]
        key = (
            f"Primary key: {', '.join(table.primary_key)}."
            if table.primary_key
            else "No primary key."
        )
        unique = f" Unique: {', '.join(table.unique)}." if table.unique else ""
        lines += [f"Domain: {_cell(table.domain)}. {key}{unique}", ""]
        lines += ["| column | type | null | key | description |", "| --- | --- | --- | --- | --- |"]
        for column in table.columns:
            null = "yes" if column.nullable else "no"
            lines.append(
                f"| {column.name} | {column.type} | {null} | {_key(column) or '-'} | "
                f"{_cell(column.description)} |"
            )
        for column in table.columns:
            if column.values:
                lines += ["", f"Values of {column.name}: {' | '.join(column.values)}"]
    return "\n".join(line.rstrip() for line in lines) + "\n"


def _as_dict(schema: Schema) -> dict[str, Any]:
    def reference(value: Reference | None) -> dict[str, Any] | None:
        if value is None:
            return None
        return {
            "table": value.table,
            "column": value.column,
            "kind": value.kind,
            "note": value.note,
        }

    return {
        "dialect": schema.dialect,
        "values_from_data": schema.values_from_data,
        "tables": [
            {
                "name": t.name,
                "domain": t.domain,
                "description": t.description,
                "primary_key": list(t.primary_key),
                "unique": list(t.unique),
                "columns": [
                    {
                        "name": c.name,
                        "type": c.type,
                        "nullable": c.nullable,
                        "primary_key": c.primary_key,
                        "unique": c.unique,
                        "references": reference(c.references),
                        "description": c.description,
                        "values": list(c.values),
                    }
                    for c in t.columns
                ],
            }
            for t in schema.tables
        ],
        "joins": [
            {
                "child_table": j.child_table,
                "child_column": j.child_column,
                "parent_table": j.parent_table,
                "parent_column": j.parent_column,
                "kind": j.kind,
                "cardinality": j.cardinality,
                "for_joining": j.for_joining,
                "condition": j.condition,
                "note": j.note,
                "warning": j.warning,
            }
            for j in schema.joins
        ],
    }


def render_json(schema: Schema) -> str:
    """Write the schema as JSON, the same facts as the Markdown, for a program to read."""
    return json.dumps(_as_dict(schema), indent=2, ensure_ascii=False) + "\n"


def estimate_tokens(text: str) -> int:
    """About how many tokens a model will count in ``text``: a quarter of its characters."""
    return max(1, (len(text) + 3) // 4)
