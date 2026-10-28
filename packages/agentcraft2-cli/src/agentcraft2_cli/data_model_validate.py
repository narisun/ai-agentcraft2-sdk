"""Cell-level validation of CSV files against a ``data_model.csv``, run through DuckDB.

``diff`` asks whether the model describes the files. This asks whether the *values* in the files
are what the model says they are, and answers with the exact file, row, line and column.

DuckDB is the engine the services read these files with, so it is the engine used here. Each file
is loaded twice. A dry run loads it the way a service does, with type detection, and compares what
DuckDB made of it with the model: a header it failed to recognise, or a column it typed
differently, is a problem a service would meet. The checks proper load it again as text, so that
every value is seen exactly as written, and test each value against the format its declared type
requires. DuckDB's own casting is lenient (it accepts ``' 12 '``), so the formats are stated
here as patterns.

Rows are numbered from 1 after the header and also carry the line of the file they start on,
which differs when a quoted value contains a line break.
"""

# ruff: noqa: S608
# The queries interpolate only column names, quoted by _quote(), taken from the files'
# own headers. A value from a file is never put into SQL text.

from __future__ import annotations

import csv
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from agentcraft2_cli.data_model import MODEL_FILE, parse_foreign_key, tables_of
from agentcraft2_cli.data_model import Table as ModelTable

if TYPE_CHECKING:
    import duckdb

__all__ = ["Finding", "describe_load_mismatch", "validate_data"]

Level = Literal["error", "warning"]

VALUE_LIMIT = 40
"""A value is shown at most this many characters long."""

_INTEGER = r"[+-]?[0-9]+"
_DECIMAL = r"[+-]?([0-9]+(\.[0-9]*)?|\.[0-9]+)"
_DATE = r"[0-9]{4}-[0-9]{2}-[0-9]{2}"
_TIME = r"[0-9]{2}:[0-9]{2}(:[0-9]{2}(\.[0-9]+)?)?"
_ZONE = r"(Z|[+-][0-9]{2}:?[0-9]{2})"
_TIMESTAMP = rf"{_DATE}[T ]{_TIME}{_ZONE}?"
_TRUE = ("yes", "y", "true", "1")
_FALSE = ("no", "n", "false", "0")
_MISSING = frozenset({"n/a", "na", "null", "none", "nan", "-", "--", "?", "tbd", "unknown", "nil"})


@dataclass(frozen=True)
class Finding:
    """One thing wrong in the data, and exactly where.

    ``row`` is the data row (1 is the first after the header) and ``line`` the line of the file
    it starts on. Both are ``None`` for a finding about a whole column or file.
    """

    level: Level
    rule: str
    file: str
    row: int | None
    line: int | None
    column: str | None
    message: str
    value: str | None = None

    def where(self) -> str:
        """The location as a person reads it: file, row and line, column."""
        parts = [self.file]
        if self.row is not None:
            parts.append(f"row {self.row} (line {self.line})")
        if self.column:
            parts.append(f"column {self.column}")
        return ", ".join(parts)


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _short(value: str) -> str:
    return value if len(value) <= VALUE_LIMIT else value[: VALUE_LIMIT - 3] + "..."


@dataclass
class _Loaded:
    """One file loaded as text: its connection, header and the file position of each row."""

    name: str
    path: Path
    connection: duckdb.DuckDBPyConnection
    header: list[str]
    places: list[tuple[int, int]]
    """(row, line) of each loaded row, in the order DuckDB loaded them."""


def validate_data(
    rows: Sequence[Mapping[str, str]],
    data_dir: Path,
    *,
    tables: Sequence[str] | None = None,
    show_values: bool = True,
) -> list[Finding]:
    """Check every value in the files of ``data_dir`` against the model, with DuckDB.

    Args:
        rows: The rows of the data model.
        data_dir: The folder of CSV files.
        tables: Only these tables, if given.
        show_values: Whether findings carry the offending value. Leave it out when the output
            may be shared.
    """
    import duckdb  # noqa: F401  (a missing DuckDB should fail here, once, with its own message)

    described = {t.name: t for t in tables_of(rows)}
    paths = {p.stem: p for p in sorted(data_dir.glob("*.csv")) if p.stem != MODEL_FILE}
    chosen = set(tables) if tables else None
    findings: list[Finding] = []
    loaded: dict[str, _Loaded] = {}
    for name, path in paths.items():
        if chosen is not None and name not in chosen:
            continue
        if name not in described:
            findings.append(
                Finding(
                    "warning",
                    "columns",
                    path.name,
                    None,
                    None,
                    None,
                    f"{path.name} has no table in the model, so its values were not checked",
                )
            )
            continue
        table = _load(path, described[name], findings)
        if table is not None:
            loaded[name] = table
    checker = _Checker(show_values, findings)
    for name, table in loaded.items():
        checker.table(described[name], table)
    for name, table in loaded.items():
        checker.joins(described[name], table, loaded, described)
    for table in loaded.values():
        table.connection.close()
    return sorted(
        findings,
        key=lambda f: (f.file, f.row is None, f.row or 0, f.column or "", f.rule),
    )


def _records(path: Path) -> list[tuple[int, list[str]]]:
    """Every non-blank record of the file with the line it starts on."""
    records: list[tuple[int, list[str]]] = []
    with path.open(newline="", encoding="utf-8-sig", errors="replace") as handle:
        reader = csv.reader(handle)
        previous_end = 0
        for record in reader:
            start = previous_end + 1
            previous_end = reader.line_num
            if record:
                records.append((start, record))
    return records


def _load(path: Path, model: ModelTable, findings: list[Finding]) -> _Loaded | None:
    import duckdb

    records = _records(path)
    if not records:
        findings.append(
            Finding(
                "error",
                "load",
                path.name,
                None,
                None,
                None,
                "the file is empty: it has no header row",
            )
        )
        return None
    header = [name.strip() for name in records[0][1]]
    runtime = duckdb.connect(":memory:")
    try:
        runtime.read_csv(str(path)).create("runtime")
        runtime_types = {
            str(row[0]): str(row[1]).upper()
            for row in runtime.execute("DESCRIBE runtime").fetchall()
        }
    except duckdb.Error as error:
        findings.append(
            Finding(
                "error",
                "load",
                path.name,
                None,
                None,
                None,
                f"DuckDB cannot load the file: {_first_line(error)}",
            )
        )
        runtime.close()
        return None
    finally:
        runtime.close()
    if list(runtime_types) != header:
        findings.append(
            Finding(
                "error",
                "load",
                path.name,
                None,
                None,
                None,
                describe_load_mismatch(list(runtime_types), header),
            )
        )
    connection = duckdb.connect(":memory:")
    try:
        connection.execute(
            "CREATE TABLE raw AS SELECT * FROM read_csv(?, all_varchar = true, header = true, "
            "ignore_errors = true, store_rejects = true)",
            [str(path)],
        )
    except duckdb.Error as error:
        findings.append(
            Finding(
                "error",
                "load",
                path.name,
                None,
                None,
                None,
                f"DuckDB cannot read the file as text: {_first_line(error)}",
            )
        )
        connection.close()
        return None
    rejected = _structure(connection, path, records, len(header), findings)
    places = [
        (number, start)
        for number, (start, _) in enumerate(records[1:], start=1)
        if start not in rejected
    ]
    count = connection.execute("SELECT count(*) FROM raw").fetchone()
    if count is None or count[0] != len(places):
        # DuckDB and the reader disagree about what a row is, so a row number could be wrong.
        places = []
        findings.append(
            Finding(
                "warning",
                "structure",
                path.name,
                None,
                None,
                None,
                "row numbers are not available for this file",
            )
        )
    loaded = _Loaded(path.stem, path, connection, header, places)
    _columns(model, loaded, runtime_types, findings)
    return loaded


def describe_load_mismatch(loaded: list[str], header: list[str]) -> str:
    """Say what DuckDB made of a file whose columns it did not take from the header.

    Rows with a different number of fields are the usual cause: they make DuckDB's detection
    of the delimiter and of the header fail, and a service reads the file the same way.
    """
    cause = "Look for rows with more or fewer fields than the header."
    if len(loaded) == 1 and len(header) > 1:
        return (
            "DuckDB read the whole file as a single column, because it could not find the "
            f"delimiter, so a service would see none of its {len(header)} columns. {cause}"
        )
    if all(re.fullmatch(r"column\d+", name) for name in loaded):
        return (
            "DuckDB did not recognise the header row and named the columns "
            f"{', '.join(loaded[:3])}..., so a service would treat the header as data. {cause}"
        )
    return (
        f"DuckDB loaded {len(loaded)} columns where the header has {len(header)}, "
        f"so a service would not see the columns the model describes. {cause}"
    )


def _first_line(error: Exception) -> str:
    return str(error).strip().splitlines()[0][:300] if str(error).strip() else type(error).__name__


def _structure(
    connection: duckdb.DuckDBPyConnection,
    path: Path,
    records: list[tuple[int, list[str]]],
    width: int,
    findings: list[Finding],
) -> set[int]:
    """Report the rows DuckDB rejected, with the line of each; return those lines."""
    rows = connection.execute(
        "SELECT line, min(error_message) FROM reject_errors GROUP BY line ORDER BY line"
    ).fetchall()
    number = {start: n for n, (start, _) in enumerate(records[1:], start=1)}
    for line, message in rows:
        found = re.search(r"Found: (\d+)", str(message))
        detail = (
            f"the row has {found.group(1)} fields and the header has {width}"
            if found
            else _first_line(Exception(message))
        )
        findings.append(
            Finding("error", "structure", path.name, number.get(int(line)), int(line), None, detail)
        )
    return {int(line) for line, _ in rows}


_FAMILY = {
    "VARCHAR": "string",
    "BOOLEAN": "boolean",
    "DATE": "date",
    "BIGINT": "integer",
    "INTEGER": "integer",
    "SMALLINT": "integer",
    "TINYINT": "integer",
    "HUGEINT": "integer",
    "UBIGINT": "integer",
    "DOUBLE": "decimal",
    "FLOAT": "decimal",
}


def _family(duck_type: str) -> str:
    if duck_type.startswith("DECIMAL"):
        return "decimal"
    if duck_type.startswith("TIMESTAMP"):
        return "timestamp"
    return _FAMILY.get(duck_type, "string")


def _loads_as(declared: str, seen: str) -> bool:
    if declared == seen:
        return True
    return (declared, seen) in {
        ("decimal", "integer"),
        ("timestamp", "date"),
        ("boolean", "string"),
        ("boolean", "integer"),
    }


def _columns(
    model: ModelTable, loaded: _Loaded, runtime_types: Mapping[str, str], findings: list[Finding]
) -> None:
    file = loaded.path.name
    for column in model.columns:
        name = column["column"]
        if name not in loaded.header:
            findings.append(
                Finding(
                    "error",
                    "columns",
                    file,
                    None,
                    None,
                    name,
                    "the model has this column; the file does not",
                )
            )
            continue
        duck = runtime_types.get(name)
        if duck is None or list(runtime_types) != loaded.header:
            continue
        seen = _family(duck)
        q = _quote(name)
        filled = loaded.connection.execute(
            f"SELECT count(*) FROM raw WHERE {q} IS NOT NULL AND trim({q}) <> ''"
        ).fetchone()
        if not filled or not filled[0]:
            continue  # DuckDB types a column with no values as text: nothing to compare
        if not _loads_as(column["datatype"], seen):
            findings.append(
                Finding(
                    "warning",
                    "runtime_type",
                    file,
                    None,
                    None,
                    name,
                    f"the model says {column['datatype']}; DuckDB loads this column as {duck}, "
                    "so a query written for the model's type may fail or compare wrongly",
                )
            )
    for name in loaded.header:
        if name not in {c["column"] for c in model.columns}:
            findings.append(
                Finding(
                    "warning",
                    "columns",
                    file,
                    None,
                    None,
                    name,
                    "the file has this column; the model does not",
                )
            )


class _Checker:
    """Runs the rules over one loaded table at a time."""

    def __init__(self, show_values: bool, findings: list[Finding]) -> None:
        self._show = show_values
        self._findings = findings

    def _add(
        self,
        level: Level,
        rule: str,
        table: _Loaded,
        rowid: int | None,
        column: str | None,
        message: str,
        value: str | None = None,
    ) -> None:
        row = line = None
        if rowid is not None and rowid < len(table.places):
            row, line = table.places[rowid]
        shown = None if value is None or not self._show else _short(value)
        self._findings.append(
            Finding(level, rule, table.path.name, row, line, column, message, shown)
        )

    def _subject(self, value: str) -> str:
        return repr(_short(value)) if self._show else "the value"

    def table(self, model: ModelTable, table: _Loaded) -> None:
        for column in model.columns:
            name = column["column"]
            if name not in table.header:
                continue
            self._empty(column, table)
            self._whitespace(name, table)
            self._type(column, table)
        self._keys(model, table)

    # --- one column ---------------------------------------------------------------------

    def _empty(self, column: dict[str, str], table: _Loaded) -> None:
        if column["nullable"] != "N" and column["pk"] != "Y":
            return
        name = column["column"]
        for (rowid,) in table.connection.execute(
            f"SELECT rowid FROM raw WHERE {_quote(name)} IS NULL OR trim({_quote(name)}) = '' "
            "ORDER BY rowid"
        ).fetchall():
            self._add(
                "error",
                "empty",
                table,
                rowid,
                name,
                "the value is empty, and the model says this column is never empty",
            )

    def _whitespace(self, name: str, table: _Loaded) -> None:
        q = _quote(name)
        for rowid, value in table.connection.execute(
            f"SELECT rowid, {q} FROM raw WHERE {q} IS NOT NULL AND trim({q}) <> '' "
            f"AND regexp_matches({q}, '^\\s|\\s$') ORDER BY rowid"
        ).fetchall():
            self._add(
                "warning",
                "whitespace",
                table,
                rowid,
                name,
                "the value has spaces at its start or end, which a join will not ignore",
                value,
            )

    def _type(self, column: dict[str, str], table: _Loaded) -> None:
        kind, name = column["datatype"], column["column"]
        if kind == "string":
            return
        q = _quote(name)
        good = {
            "integer": f"regexp_full_match(trim({q}), '{_INTEGER}')",
            "decimal": f"regexp_full_match(trim({q}), '{_DECIMAL}')",
            "boolean": f"lower(trim({q})) IN ({', '.join(repr(v) for v in (*_TRUE, *_FALSE))})",
            "date": (
                f"regexp_full_match(trim({q}), '{_DATE}') "
                f"AND try_cast(trim({q}) AS DATE) IS NOT NULL"
            ),
            "timestamp": (
                f"(regexp_full_match(trim({q}), '{_DATE}') "
                f"AND try_cast(trim({q}) AS DATE) IS NOT NULL) "
                f"OR (regexp_full_match(trim({q}), '{_TIMESTAMP}') "
                f"AND try_cast(trim({q}) AS TIMESTAMPTZ) IS NOT NULL)"
            ),
        }[kind]
        for rowid, value in table.connection.execute(
            f"SELECT rowid, trim({q}) FROM raw WHERE {q} IS NOT NULL AND trim({q}) <> '' "
            f"AND NOT ({good}) ORDER BY rowid"
        ).fetchall():
            self._add(
                "error",
                "type",
                table,
                rowid,
                name,
                f"{self._subject(value)} is not {_ARTICLE[kind]}: {_why(kind, value)}",
                value,
            )
        if kind == "boolean":
            self._mixed_booleans(name, table)
        if kind == "timestamp":
            self._mixed_zones(name, table)

    def _mixed_booleans(self, name: str, table: _Loaded) -> None:
        q = _quote(name)
        counts = table.connection.execute(
            f"SELECT trim({q}), count(*) FROM raw WHERE lower(trim({q})) IN "
            f"({', '.join(repr(v) for v in (*_TRUE, *_FALSE))}) GROUP BY 1 ORDER BY 2 DESC, 1"
        ).fetchall()
        yes = [(v, n) for v, n in counts if v.lower() in _TRUE]
        no = [(v, n) for v, n in counts if v.lower() in _FALSE]
        if len(yes) > 1 or len(no) > 1:
            spellings = ", ".join(f"{v} ({n})" for v, n in [*yes, *no])
            self._add(
                "warning",
                "mixed",
                table,
                None,
                name,
                f"yes and no are spelled several ways: {spellings}; use one spelling for each",
            )

    def _mixed_zones(self, name: str, table: _Loaded) -> None:
        q = _quote(name)
        row = table.connection.execute(
            f"SELECT count(*) FILTER "
            f"(WHERE regexp_full_match(trim({q}), '{_DATE}[T ]{_TIME}{_ZONE}')), "
            f"count(*) FILTER (WHERE regexp_full_match(trim({q}), '{_DATE}[T ]{_TIME}')) FROM raw"
        ).fetchone()
        if row and row[0] and row[1]:
            self._add(
                "warning",
                "mixed",
                table,
                None,
                name,
                f"{row[0]} timestamps carry a time zone and {row[1]} do not; "
                "the zone of the others "
                "is a guess",
            )

    # --- keys and joins -----------------------------------------------------------------

    def _keys(self, model: ModelTable, table: _Loaded) -> None:
        key = [c for c in model.primary_key if c in table.header]
        if key and len(key) == len(model.primary_key):
            self._repeats(table, key, "the primary key")
        for column in model.columns:
            if column["uk"] == "Y" and column["column"] in table.header:
                self._repeats(table, [column["column"]], "this unique column")

    def _repeats(self, table: _Loaded, columns: list[str], label: str) -> None:
        parts = ", ".join(f"trim({_quote(c)})" for c in columns)
        present = " AND ".join(
            f"{_quote(c)} IS NOT NULL AND trim({_quote(c)}) <> ''" for c in columns
        )
        for rowid, first in table.connection.execute(
            f"SELECT rowid, first FROM "
            f"(SELECT rowid, min(rowid) OVER (PARTITION BY {parts}) AS first "
            f"FROM raw WHERE {present}) WHERE rowid <> first ORDER BY rowid"
        ).fetchall():
            earlier = table.places[first][0] if first < len(table.places) else first + 1
            self._add(
                "error",
                "duplicate",
                table,
                rowid,
                ", ".join(columns),
                f"{label} repeats row {earlier}",
            )

    def joins(
        self,
        model: ModelTable,
        table: _Loaded,
        loaded: Mapping[str, _Loaded],
        described: Mapping[str, ModelTable],
    ) -> None:
        by_domain = {(t.domain, t.name): t.name for t in described.values()}
        for column in model.columns:
            parsed = parse_foreign_key(column["fk"]) if column["fk"] else None
            if parsed is None or parsed[2] is None or column["column"] not in table.header:
                continue
            target = loaded.get(by_domain.get((parsed[0], parsed[1])) or "")
            if target is None or parsed[2] not in target.header or "(" in column["fk"]:
                continue  # a join qualified in brackets is not promised to match
            known = {
                str(v[0])
                for v in target.connection.execute(
                    f"SELECT DISTINCT trim({_quote(parsed[2])}) FROM raw "
                    f"WHERE {_quote(parsed[2])} IS NOT NULL"
                ).fetchall()
            }
            q = _quote(column["column"])
            for rowid, value in table.connection.execute(
                f"SELECT rowid, trim({q}) FROM raw "
                f"WHERE {q} IS NOT NULL AND trim({q}) <> '' ORDER BY rowid"
            ).fetchall():
                if value not in known:
                    self._add(
                        "error",
                        "orphan",
                        table,
                        rowid,
                        column["column"],
                        f"{self._subject(value)} has no match in {target.name}.{parsed[2]}",
                        value,
                    )


_ARTICLE = {
    "integer": "a whole number",
    "decimal": "a plain number",
    "boolean": "a yes or no value",
    "date": "a date",
    "timestamp": "a timestamp",
}


def _why(kind: str, value: str) -> str:
    """Say what is wrong with a value that is not the type its column declares."""
    if value.lower() in _MISSING:
        return "this looks like a missing-value marker; leave the cell empty"
    if kind in ("integer", "decimal"):
        return _why_number(kind, value)
    if kind == "boolean":
        return "use Yes/No, Y/N, true/false or 1/0"
    if kind == "date":
        return _why_date(value)
    return _why_timestamp(value)


def _why_number(kind: str, value: str) -> str:
    if re.search(r"[$€£¥]", value):
        return "it has a currency symbol; keep only the digits"
    if value.endswith("%"):
        return "it has a percent sign; write the number without it"
    if value.startswith("(") and value.endswith(")"):
        return "it has brackets for a negative number; write a minus sign"
    if re.fullmatch(r"[+-]?[0-9]{1,3}(,[0-9]{3})+(\.[0-9]+)?", value):
        return "it has a thousands separator; remove the commas"
    if value.count(".") > 1:
        return "it has more than one decimal point"
    if kind == "integer" and re.fullmatch(_DECIMAL, value):
        return "it has a decimal point, and this column holds whole numbers"
    if re.fullmatch(r"[+-]?[0-9.]+[eE][+-]?[0-9]+", value):
        return "it is in scientific notation; write the digits out"
    return "it is not a number"


def _why_date(value: str) -> str:
    if re.fullmatch(_DATE, value):
        return "it is not a real date"
    if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}[T ].*", value):
        return "it has a time of day, and a date column holds only the date"
    if re.fullmatch(r"[0-9]{1,2}[/.-][0-9]{1,2}[/.-][0-9]{2,4}", value):
        return "the day and month order is ambiguous; write it as YYYY-MM-DD"
    return "write it as YYYY-MM-DD"


def _why_timestamp(value: str) -> str:
    if re.fullmatch(_TIMESTAMP, value):
        return "the date or the time is not a real one"
    return "write it as YYYY-MM-DD hh:mm:ss (or with a T, and an optional zone)"
