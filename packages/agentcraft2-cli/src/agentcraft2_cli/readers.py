"""From the developer's data to a plan for an MCP server.

Each function here runs one reader, proposes queries from what it found,
tries them, and returns the plan together with everything the server needs
beside its generated code.
"""

from __future__ import annotations

import json
import tempfile
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePosixPath
from typing import Protocol

from agentcraft2_cli.dataplan import (
    API_RESPONSES_FILE,
    MAX_QUERY_NAME,
    DataPlan,
    PlannedParameter,
    PlannedQuery,
)
from agentcraft2_cli.errors import CliError
from agentcraft2_cli.proposals import is_sensitive, propose_queries
from agentcraft2_cli.read_csv import read_csv_folder, stage_csv_files
from agentcraft2_cli.read_model import (
    MIN_DESCRIPTION,
    Model,
    Use,
    analyse,
    example_value,
    has_classification,
    load_model,
    model_type,
    stand_in,
)
from agentcraft2_cli.read_openapi import read_openapi
from agentcraft2_cli.read_redshift import (
    CatalogTableLike,
    RedshiftRequest,
    stand_in_csv,
    tables_from_catalog,
)
from agentcraft2_core.adapters import (
    DuckDbCsvDataSource,
    DuckDbCsvOptions,
    RestDataSource,
    RestOptions,
    SystemClock,
)
from agentcraft2_core.adapters.sql import QueryCatalog
from agentcraft2_core.contracts import (
    AgentCraft2Error,
    DataSource,
    QueryParameter,
    SupportsAsyncClose,
)
from agentcraft2_core.kit import checked_base_url
from agentcraft2_core.testing import rest_stub_transport

__all__ = [
    "Proposal",
    "StartedSource",
    "check_queries",
    "csv_source",
    "from_csv",
    "from_model",
    "from_openapi",
    "from_redshift",
    "selected",
]

_MAX_OPERATIONS = 12


class StartedSource(DataSource, SupportsAsyncClose, Protocol):
    """A data source that has been started and has to be closed."""


Opener = Callable[[Path], Awaitable[StartedSource]]
"""Given a folder of query files, returns a started data source over them."""


@dataclass(frozen=True, slots=True)
class Proposal:
    """What a reader proposes for a new MCP server.

    Attributes:
        plan: The queries the server would offer.
        summary: One line on what was read.
        description: What the server offers, in one line.
        notes: What was left out or dropped, and why.
        data_files: Files to copy into the server's ``data`` folder, by the
            name each gets there.
        extra_files: More text files for the server's folder, by relative path.
    """

    plan: DataPlan
    summary: str
    description: str
    notes: tuple[str, ...] = ()
    data_files: Mapping[str, Path] = field(default_factory=dict)
    extra_files: Mapping[PurePosixPath, str] = field(default_factory=dict)


def selected(queries: Sequence[PlannedQuery], only: Sequence[str]) -> list[PlannedQuery]:
    """Return the queries named in ``only``, or all of them when it is empty.

    Raises:
        CliError: If a name is not among the proposed queries.
    """
    if not only:
        return list(queries)
    by_name = {query.name: query for query in queries}
    unknown = [name for name in only if name not in by_name]
    if unknown:
        raise CliError(
            f"not among the proposed queries: {', '.join(unknown)} "
            f"(proposed: {', '.join(by_name) or 'none'})"
        )
    return [by_name[name] for name in dict.fromkeys(only)]


async def _try(
    open_source: Opener, suffix: str, queries: Sequence[PlannedQuery]
) -> list[PlannedQuery]:
    """Run each query once with its example values; return them as they turned out."""
    with tempfile.TemporaryDirectory() as scratch:
        queries_dir = Path(scratch)
        for query in queries:
            (queries_dir / f"{query.name}{suffix}").write_text(query.definition, encoding="utf-8")
        source = await open_source(queries_dir)
        try:
            checked = []
            for query in queries:
                result = await source.query(
                    query.name, {p.name: p.example for p in query.parameters}
                )
                checked.append(
                    replace(query, columns=result.columns, returns_rows=bool(result.rows))
                )
            return checked
        finally:
            await source.aclose()


async def check_queries(
    open_source: Opener, suffix: str, queries: Sequence[PlannedQuery]
) -> tuple[list[PlannedQuery], list[str]]:
    """Run the proposed queries through the real data source, and keep the ones that work.

    Args:
        open_source: Starts the data source over a folder of query files.
        suffix: The suffix of a query file of this kind of source.
        queries: The proposed queries.

    Returns:
        The queries that ran, with the columns they returned and whether the
        example values matched a row, and one note for each that was dropped.
    """
    try:
        return await _try(open_source, suffix, queries), []
    except AgentCraft2Error:
        pass  # at least one does not work: find out which
    kept: list[PlannedQuery] = []
    notes: list[str] = []
    for query in queries:
        try:
            kept += await _try(open_source, suffix, [query])
        except AgentCraft2Error as problem:
            # The summary keeps the facts and the fix; the class name alone says nothing.
            notes.append(f"dropped {query.name}: {problem.summary}")
    return kept, notes


def csv_source(data_dir: Path, source: str) -> Opener:
    """Return what starts the local CSV data source over ``data_dir``."""

    async def open_source(queries_dir: Path) -> StartedSource:
        adapter = DuckDbCsvDataSource(
            source, DuckDbCsvOptions(data_dir=data_dir, queries_dir=queries_dir), SystemClock()
        )
        await adapter.start()
        return adapter

    return open_source


async def from_csv(
    folder: Path, *, source: str, per_table: int = 3, only: Sequence[str] = ()
) -> Proposal:
    """Propose a server over the CSV files in ``folder``.

    Every proposed query is run once over the files, so what is proposed works.

    Raises:
        CliError: If the folder cannot be read or nothing can be proposed from it.
    """
    found = read_csv_folder(folder)
    proposed = selected(propose_queries(found.tables, per_table=per_table), only)
    if not proposed:
        raise CliError(f"nothing could be proposed from the files in {folder}")
    with tempfile.TemporaryDirectory() as staged:
        stage_csv_files(found.files, Path(staged))
        queries, dropped = await check_queries(csv_source(Path(staged), source), ".sql", proposed)
    if not queries:
        raise CliError(
            f"none of the queries proposed from {folder} ran over the files: " + "; ".join(dropped)
        )
    tables = ", ".join(f"{table.name} ({table.rows} rows)" for table in found.tables)
    count = len(found.tables)
    return Proposal(
        plan=DataPlan(origin="csv", source=source, kind="duckdb_csv", queries=tuple(queries)),
        summary=f"Read {count} table{'s' if count != 1 else ''} from {folder}: {tables}",
        description=f"Read-only tools over the {source.replace('_', ' ')} data.",
        notes=(*found.notes, *dropped),
        data_files={f"{name}.csv": path for name, path in found.files.items()},
    )


def _with_notes(problem: AgentCraft2Error) -> str:
    """A library error as one line, with the notes that name the file it is about."""
    notes = "; ".join(getattr(problem, "__notes__", ()))
    return f"{problem.summary} [{notes}]" if notes else problem.summary


def _sentence(text: str) -> str:
    text = text.strip()
    return text if text.endswith((".", "!", "?")) else f"{text}."


def _plan_query(
    name: str, text: str, catalog: QueryCatalog, model: Model, notes: list[str]
) -> PlannedQuery:
    """One query file, as a tool: its description and arguments, taken from the model as well."""
    wanted = catalog.describe()[name]
    if len(name) > MAX_QUERY_NAME:
        raise CliError(
            f"the query file {name}.sql has a name that is too long for a tool",
            expected=f"at most {MAX_QUERY_NAME} characters",
            fix="rename the file; its name is the tool's name",
        )
    if len(wanted.description.strip()) < MIN_DESCRIPTION:
        raise CliError(
            f"query {name} ({name}.sql) has a description that says too little",
            expected=f"at least {MIN_DESCRIPTION} characters: a model reads it to choose the tool",
            actual=repr(wanted.description),
            fix="say what the query returns, in its '-- description:' line",
        )
    use = analyse(catalog.get(name).expression, model)
    _refuse_what_the_model_lacks(name, use)
    parameters = tuple(_planned_parameter(name, p, use, model, notes) for p in wanted.parameters)
    description = _sentence(wanted.description)
    if len(use.tables) == 1 and model.tables[use.tables[0]].primary_key:
        key = ", ".join(model.tables[use.tables[0]].primary_key)
        description += f" One row per {key}."
    if has_classification(text):
        classification = wanted.classification.name.lower()
    else:
        classification = "confidential"
        notes.append(
            f"{name} has no classification, so it is confidential; "
            f"add '-- classification: ...' to {name}.sql to choose"
        )
    return PlannedQuery(
        name=name,
        description=description,
        parameters=parameters,
        columns=(),
        masked=(),
        classification=classification,
        max_rows=wanted.max_rows,
        definition=text,
    )


def _refuse_what_the_model_lacks(name: str, use: Use) -> None:
    fix = (
        "describe it in data_model.csv (ac2 data model draft shows how), "
        "or take it out of the query"
    )
    if use.unknown_tables:
        raise CliError(
            f"{name}.sql reads the table {', '.join(use.unknown_tables)}, "
            "which the data model does not describe",
            fix=fix,
        )
    if use.unknown_columns:
        many = len(use.unknown_columns) > 1
        raise CliError(
            f"{name}.sql uses the column{'s' if many else ''} {', '.join(use.unknown_columns)}, "
            f"which the data model does not describe",
            fix=fix,
        )


def _planned_parameter(
    query: str, declared: QueryParameter, use: Use, model: Model, notes: list[str]
) -> PlannedParameter:
    name, kind = declared.name, declared.type
    found = use.arguments.get(name)
    if found is None:
        return PlannedParameter(name, kind.value, stand_in(kind))
    table, column = found
    row = model.column(table, column)
    assert row is not None  # noqa: S101 - analyse only returns columns the model has
    if model_type(row["datatype"]) is not kind:
        notes.append(
            f"{query}: argument {name} is declared {kind.value}, but {table}.{column} is "
            f"{row['datatype']} in the data model"
        )
    return PlannedParameter(
        name, kind.value, example_value(model, table, column, kind), row["description"].strip()
    )


async def from_model(
    data_dir: Path,
    queries_dir: Path,
    *,
    source: str,
    only: Sequence[str] = (),
    mask: Sequence[str] = (),
) -> Proposal:
    """Make tools from a folder of query files and the data model that describes the data.

    Each ``.sql`` file in ``queries_dir`` becomes a tool. The data model gives a tool's
    description what one row is, and each argument the description of the column it is compared
    with. Every query is run once over the files, so what is proposed works.

    Args:
        data_dir: The folder of CSV files and their ``data_model.csv``.
        queries_dir: The folder of query files.
        source: The name of the data source.
        only: Keep only these queries.
        mask: Columns to mask for analysts besides those whose names look personal.

    Raises:
        CliError: If the model or a query is not as it must be, with what to do about it. A
            query that does not run is an error, not left out: someone wrote it.
    """
    model = load_model(data_dir)
    if not queries_dir.is_dir():
        raise CliError(
            f"{queries_dir} is not a folder",
            expected="a folder of .sql files, one named query each",
            fix="give the path of the folder that holds the query files",
        )
    if not any(queries_dir.glob("*.sql")):
        raise CliError(
            f"there are no query files in {queries_dir}",
            expected="one .sql file for each tool, with a header of '-- description:' lines",
            fix="write the queries, or leave out --from-model to have some proposed",
        )
    try:
        catalog = QueryCatalog.load(queries_dir)
    except AgentCraft2Error as problem:
        raise CliError(_with_notes(problem)) from problem
    notes: list[str] = []
    planned = [
        _plan_query(
            name, (queries_dir / f"{name}.sql").read_text(encoding="utf-8"), catalog, model, notes
        )
        for name in sorted(catalog.describe())
    ]
    chosen = selected(planned, only)
    queries, dropped = await check_queries(csv_source(data_dir, source), ".sql", chosen)
    if dropped:
        raise CliError("these queries did not run over the files: " + "; ".join(dropped))
    unknown = [column for column in mask if not any(column in q.columns for q in queries)]
    if unknown:
        raise CliError(
            f"no query returns a column called {', '.join(unknown)}",
            fix="name a column that a query returns; ac2 new mcp --from-model --propose lists them",
        )
    queries = [
        replace(q, masked=tuple(c for c in q.columns if is_sensitive(c) or c in mask))
        for q in queries
    ]
    count = len(model.tables)
    return Proposal(
        plan=DataPlan(origin="model", source=source, kind="duckdb_csv", queries=tuple(queries)),
        summary=(
            f"Read {count} table{'s' if count != 1 else ''} from {model.path} and "
            f"{len(queries)} quer{'ies' if len(queries) != 1 else 'y'} from {queries_dir}"
        ),
        description=f"Read-only tools over the {source.replace('_', ' ')} data.",
        notes=tuple(notes),
        data_files={path.name: path for path in sorted(data_dir.glob("*.csv"))},
    )


async def from_openapi(
    document: Path, *, source: str, base_url: str | None = None, only: Sequence[str] = ()
) -> Proposal:
    """Propose a server over the API an OpenAPI document describes.

    The API is not called. Every proposed endpoint definition is run once
    through the real ``rest`` data source against an example response built
    from the document, so what is proposed loads and returns its columns.

    Raises:
        CliError: If the document cannot be read, names no usable address, or
            nothing in it can be offered.
    """
    reading = read_openapi(document, base_url=base_url)
    if reading.base_url is None:
        raise CliError(f"{document} names no server address this reader can use; give --base-url")
    try:
        address = checked_base_url("the API", reading.base_url, allow_http=False)
    except AgentCraft2Error as problem:
        raise CliError(str(problem)) from None
    notes = list(reading.notes)
    proposed = selected(reading.queries, only)
    if not proposed:
        raise CliError(f"no operation of {document} can be offered: " + "; ".join(notes))
    if not only and len(proposed) > _MAX_OPERATIONS:
        later = ", ".join(query.name for query in proposed[_MAX_OPERATIONS:])
        notes.append(
            f"kept the first {_MAX_OPERATIONS} of {len(proposed)} operations; "
            f"name the ones you want with --query. Not kept: {later}"
        )
        proposed = proposed[:_MAX_OPERATIONS]
    responses = {reading.calls[query.name][0]: reading.calls[query.name][1] for query in proposed}

    async def open_source(queries_dir: Path) -> StartedSource:
        adapter = RestDataSource(
            source,
            RestOptions(base_url=address, queries_dir=queries_dir),
            SystemClock(),
            transport=rest_stub_transport(responses),
        )
        await adapter.start()
        return adapter

    queries, dropped = await check_queries(open_source, ".yaml", proposed)
    if not queries:
        raise CliError(
            f"none of the operations of {document} could be offered: " + "; ".join(dropped)
        )
    if reading.wants_credentials:
        notes.append(
            "the document says callers must authenticate: the server's README says how to "
            "give it the API's token"
        )
    kept = {query.name for query in queries}
    stub = {key: body for name, (key, body) in reading.calls.items() if name in kept}
    count = len(reading.queries)
    return Proposal(
        plan=DataPlan(
            origin="openapi",
            source=source,
            kind="rest",
            queries=tuple(queries),
            options={"base_url": address},
        ),
        summary=(
            f"Read {document.name} ({reading.title}): {count} operation"
            f"{'s' if count != 1 else ''} can be offered, at {address}"
        ),
        description=f"Read-only tools over the {reading.title} API.",
        notes=(*notes, *dropped),
        extra_files={API_RESPONSES_FILE: json.dumps(stub, indent=2, sort_keys=True) + "\n"},
    )


async def from_redshift(
    found: Sequence[CatalogTableLike],
    request: RedshiftRequest,
    *,
    source: str,
    per_table: int = 3,
    only: Sequence[str] = (),
) -> Proposal:
    """Propose a server over the tables that were read from the Redshift catalogue.

    The server runs locally over stand-in CSV files with the tables' columns
    and made-up rows. Every proposed query is run once over them, so what is
    proposed works locally; the same files are what a deployed server runs on
    Redshift.

    Args:
        found: The tables, as the catalogue describes them.
        request: What was asked of the catalogue.
        source: The name of the data source.
        per_table: The most queries to propose for one table.
        only: Keep only the queries with these names.

    Raises:
        CliError: If nothing can be proposed from the tables.
    """
    where = f"{request.database}.{request.schema}"
    tables, left_out = tables_from_catalog(found)
    proposed = selected(propose_queries(tables, per_table=per_table), only)
    if not proposed:
        raise CliError(f"nothing could be proposed from the tables of {where}")
    stand_ins = {f"{table.name}.csv": stand_in_csv(table) for table in tables}
    with tempfile.TemporaryDirectory() as staged:
        for name, text in stand_ins.items():
            (Path(staged) / name).write_text(text, encoding="utf-8")
        queries, dropped = await check_queries(csv_source(Path(staged), source), ".sql", proposed)
    if not queries:
        raise CliError(f"none of the queries proposed from {where} ran: " + "; ".join(dropped))
    listed = ", ".join(f"{table.name} ({len(table.columns)} columns)" for table in tables)
    count = len(tables)
    return Proposal(
        plan=DataPlan(
            origin="redshift",
            source=source,
            kind="duckdb_csv",
            queries=tuple(queries),
            deployed=request.deployed(),
            notes=(
                "The queries name tables without a schema, because the local engine has none. "
                f"Set the search path of the server's database user to `{request.schema}`.",
                "The CSV files in `data/` are stand-ins: the columns of the real tables and two "
                "made-up rows each. No row was read from the database. Put in rows that look "
                "like the real ones, and keep anything personal out of them.",
            ),
        ),
        summary=f"Read {count} table{'s' if count != 1 else ''} of {where}: {listed}",
        description=f"Read-only tools over the {request.schema.replace('_', ' ')} tables.",
        notes=(*left_out, *dropped),
        extra_files={PurePosixPath("data", name): text for name, text in stand_ins.items()},
    )
