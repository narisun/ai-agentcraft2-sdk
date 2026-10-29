"""Reading a data model and the query files that go with it, to make tools from them.

The data model says what each table holds and what each column means; a query file says which
rows a tool returns. Put together they give a tool what a person would write by hand: a description
that says what a row is, and for each argument what it is, taken from the column it is compared
with. This module does the reading and the checking; :func:`agentcraft2_cli.readers.from_model`
makes the proposal from it.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from sqlglot import exp

from agentcraft2_cli.data_model import (
    MODEL_FILE,
    Table,
    check_data_model,
    read_data_model,
    tables_of,
)
from agentcraft2_cli.data_model_profile import read_table
from agentcraft2_cli.errors import CliError
from agentcraft2_core.contracts import ParameterType

__all__ = [
    "MIN_DESCRIPTION",
    "Model",
    "Use",
    "analyse",
    "example_value",
    "has_classification",
    "load_model",
    "model_type",
]

MIN_DESCRIPTION = 20
"""The shortest a query's description may be: a model reads it to choose the tool."""

_COMPARISONS = (exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE, exp.Like, exp.ILike)
_MODEL_TYPES: Mapping[str, ParameterType] = {
    "string": ParameterType.STRING,
    "integer": ParameterType.INTEGER,
    "decimal": ParameterType.NUMBER,
    "boolean": ParameterType.BOOLEAN,
    "date": ParameterType.DATE,
    "timestamp": ParameterType.TIMESTAMP,
}
_STAND_INS: Mapping[ParameterType, str | int | float | bool] = {
    ParameterType.STRING: "example",
    ParameterType.INTEGER: 1,
    ParameterType.NUMBER: 1.0,
    ParameterType.BOOLEAN: True,
    ParameterType.DATE: "2026-01-01",
    ParameterType.TIMESTAMP: "2026-01-01 00:00:00",
}
_CLASSIFICATION_LINE = re.compile(r"^--\s*classification\s*:", re.IGNORECASE | re.MULTILINE)


@dataclass(frozen=True)
class Model:
    """A data model read for making tools: its tables, and where its files are."""

    path: Path
    data_dir: Path
    tables: Mapping[str, Table]

    def column(self, table: str, column: str) -> dict[str, str] | None:
        """The model's row for a column, or ``None`` if the table or the column is not there."""
        found = self.tables.get(table)
        if found is None:
            return None
        return next((c for c in found.columns if c["column"] == column), None)


@dataclass(frozen=True)
class Use:
    """What a query uses of the model.

    Attributes:
        tables: The tables of the model it reads, in the order they appear.
        unknown_tables: Tables it reads that the model does not describe.
        unknown_columns: Columns it uses that no table it reads has, and that it does not
            define itself.
        arguments: For each parameter, the table and column it is compared with, or ``None``
            when it is not compared with a column.
    """

    tables: tuple[str, ...] = ()
    unknown_tables: tuple[str, ...] = ()
    unknown_columns: tuple[str, ...] = ()
    arguments: Mapping[str, tuple[str, str] | None] = field(default_factory=dict)


def load_model(data_dir: Path) -> Model:
    """Read ``data_model.csv`` in ``data_dir``, and refuse a model that breaks the standard.

    Raises:
        CliError: If there is no model, it is not in the standard's form, or it has errors. What
            reaches a tool is what the model says, so what is wrong must be fixed first.
    """
    path = data_dir / f"{MODEL_FILE}.csv"
    if not path.is_file():
        raise CliError(
            f"there is no data model at {path}",
            fix=f"draft one from the files with: ac2 data model draft {data_dir}",
        )
    try:
        rows = read_data_model(path)
    except ValueError as error:
        raise CliError(
            str(error), fix="use the standard's columns; see ac2 data model draft"
        ) from error
    errors = [issue for issue in check_data_model(rows, data_dir) if issue.level == "error"]
    if errors:
        count = len(errors)
        raise CliError(
            f"the data model does not follow the standard: {count} "
            f"{'problem' if count == 1 else 'problems'} would reach the tools",
            expected="a model with every description and type filled in, that matches the files",
            actual="; ".join(str(issue) for issue in errors[:3]),
            fix=f"fix them; ac2 data model check {data_dir} lists every one",
        )
    return Model(path, data_dir, {table.name: table for table in tables_of(rows)})


def has_classification(text: str) -> bool:
    """Whether a query file says how sensitive its result is."""
    return bool(_CLASSIFICATION_LINE.search(text))


def model_type(datatype: str) -> ParameterType:
    """The parameter type that fits a column of the model's datatype."""
    return _MODEL_TYPES[datatype]


def analyse(expression: exp.Query, model: Model) -> Use:
    """Find the tables, columns and arguments of a parsed query, against the model."""
    ctes = {cte.alias for cte in expression.find_all(exp.CTE)}
    aliases, read, unknown_tables = _tables(expression, ctes, model)
    unknown = _unknown_columns(expression, ctes, aliases, read, bool(unknown_tables), model)
    arguments: dict[str, tuple[str, str] | None] = {}
    for placeholder in expression.find_all(exp.Placeholder):
        name = placeholder.name
        if name and arguments.get(name) is None:
            arguments[name] = _compared_with(placeholder, aliases, read, model)
    return Use(tuple(read), tuple(unknown_tables), tuple(unknown), arguments)


def _tables(
    expression: exp.Query, ctes: set[str], model: Model
) -> tuple[dict[str, str], list[str], list[str]]:
    """The tables a query reads: by the name it calls each, the model's, and those it lacks."""
    aliases: dict[str, str] = {}
    read: list[str] = []
    unknown: list[str] = []
    for node in expression.find_all(exp.Table):
        if node.name in ctes:
            continue
        aliases[node.alias_or_name] = node.name
        if node.name not in model.tables:
            _add(unknown, node.name)
        else:
            _add(read, node.name)
    return aliases, read, unknown


def _unknown_columns(
    expression: exp.Query,
    ctes: set[str],
    aliases: Mapping[str, str],
    read: Sequence[str],
    lacks_a_table: bool,
    model: Model,
) -> list[str]:
    """The columns a query uses that the model does not describe.

    A name the query gives something itself (an alias, a column of a ``WITH`` query) is not the
    model's to know, and nor is a column of a table the model lacks: that is reported on its own.
    """
    defined = {alias.alias for alias in expression.find_all(exp.Alias)}
    derived = ctes | {sub.alias for sub in expression.find_all(exp.Subquery) if sub.alias}
    available = {c["column"] for name in read for c in model.tables[name].columns}
    unknown: list[str] = []
    for column in expression.find_all(exp.Column):
        if column.name in defined:
            continue
        if column.table:
            table = aliases.get(column.table)
            if (
                table is not None
                and column.table not in derived
                and table in model.tables
                and model.column(table, column.name) is None
            ):
                _add(unknown, f"{column.table}.{column.name}")
        elif column.name not in available and (read or not lacks_a_table):
            _add(unknown, column.name)
    return unknown


def _add(found: list[str], name: str) -> None:
    if name not in found:
        found.append(name)


def _compared_with(
    placeholder: exp.Placeholder, aliases: Mapping[str, str], read: Sequence[str], model: Model
) -> tuple[str, str] | None:
    """The table and column a placeholder is compared with, if it is compared with one."""
    parent = placeholder.parent
    column: exp.Expression | None = None
    if isinstance(parent, _COMPARISONS):
        left, right = parent.args.get("this"), parent.args.get("expression")
        column = right if left is placeholder else left
    elif isinstance(parent, exp.Between):
        column = parent.args.get("this")
    if not isinstance(column, exp.Column):
        return None
    if column.table:
        table = aliases.get(column.table)
        if table is not None and model.column(table, column.name) is not None:
            return table, column.name
        return None
    owners = [t for t in read if model.column(t, column.name) is not None]
    return (owners[0], column.name) if len(owners) == 1 else None


def example_value(
    model: Model, table: str, column: str, kind: ParameterType
) -> str | int | float | bool:
    """A value for a tool's test call: the first one the column holds, or a stand-in of the type.

    Taken from the data so that the call returns rows.
    """
    try:
        values = read_table(model.data_dir / f"{table}.csv").values(column)
    except (CliError, KeyError, OSError):
        values = []
    return _coerce(values[0], kind) if values else _STAND_INS[kind]


def stand_in(kind: ParameterType) -> str | int | float | bool:
    """A value of the type, for an argument that is not compared with a column."""
    return _STAND_INS[kind]


def _coerce(value: str, kind: ParameterType) -> str | int | float | bool:
    try:
        if kind is ParameterType.INTEGER:
            return int(value)
        if kind is ParameterType.NUMBER:
            return float(value)
    except ValueError:
        return _STAND_INS[kind]
    if kind is ParameterType.BOOLEAN:
        return value.strip().lower() in ("true", "yes", "y", "1")
    return value
