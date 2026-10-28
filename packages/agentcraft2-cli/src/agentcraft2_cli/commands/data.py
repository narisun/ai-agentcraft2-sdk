"""The ``data`` commands: draft a data model from CSV files, compare it, check it, validate."""

from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path

import click

from agentcraft2_cli.data_model import (
    MODEL_FILE,
    PLACEHOLDER_TEXT,
    check_data_model,
    read_data_model,
)
from agentcraft2_cli.data_model_diff import compare_model
from agentcraft2_cli.data_model_draft import draft_model, write_model
from agentcraft2_cli.data_model_validate import Finding, validate_data
from agentcraft2_cli.errors import CliError

__all__ = ["data"]

_FOLDER = click.Path(file_okay=False, path_type=Path)
_MODEL = click.option(
    "--model",
    "model_file",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="The data_model.csv to use. By default it is data_model.csv in the data folder.",
)


def _model_path(data_dir: Path, given: Path | None) -> Path:
    path = given or data_dir / f"{MODEL_FILE}.csv"
    if not path.is_file():
        raise CliError(
            f"there is no data model at {path}",
            fix=f"draft one from the files with: ac2 data model draft {data_dir}",
        )
    return path


def _read(path: Path) -> list[dict[str, str]]:
    try:
        return read_data_model(path)
    except ValueError as error:
        raise CliError(
            str(error), fix="use the standard's columns; see ac2 data model draft"
        ) from error


@click.group()
def data() -> None:
    """Describe the data an agent reads: its data_model.csv."""


@data.group()
def model() -> None:
    """Draft, compare and check a data_model.csv.

    A data_model.csv says, for every table in a folder of CSV files, what the table
    holds and what each column means, which columns identify a row and how tables join.
    """


@model.command()
@click.argument("data_dir", type=_FOLDER)
@click.option(
    "-o",
    "--output",
    "output",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Where to write the draft. By default data_model.csv in the data folder.",
)
@click.option("--force", is_flag=True, help="Replace the file if it exists.")
def draft(data_dir: Path, output: Path | None, force: bool) -> None:
    """Draft a data_model.csv from the CSV files in DATA_DIR.

    Each file is a table. The draft states what the files show: the columns in order,
    a type, whether a column is ever empty, a primary key and the joins between tables.
    What only a person knows is marked TODO: the business domain of each table and every
    description. 'ac2 data model check' reports each TODO until it is replaced.

    \b
    Examples:
      ac2 data model draft data
      ac2 data model draft data --output data_model.draft.csv

    It never replaces an existing model unless --force is given. To compare an existing
    model with the files, use 'ac2 data model diff'. Inferences come from the rows present,
    so a key that is unique today may not be tomorrow: review every one.
    """
    target = output or data_dir / f"{MODEL_FILE}.csv"
    if target.exists() and not force:
        raise CliError(
            f"{target} already exists",
            fix=f"compare it with the files using: ac2 data model diff {data_dir}; "
            "or replace it with --force, or write elsewhere with --output",
        )
    rows = draft_model(data_dir)
    write_model(rows, target)
    tables = {r["table"] for r in rows}
    without_key = sorted(
        t for t in tables if not any(r["pk"] == "Y" for r in rows if r["table"] == t)
    )
    joins = sum(1 for r in rows if r["fk"])
    placeholders = sum(1 for r in rows if r["description"].startswith(PLACEHOLDER_TEXT)) + len(
        tables
    )
    click.echo(f"Wrote {target}: {len(tables)} tables, {len(rows)} columns.")
    click.echo(f"Inferred from the files: types, empty values, keys and {joins} joins.")
    if without_key:
        click.echo(f"No primary key could be found for: {', '.join(without_key)}.")
    click.echo(
        f"Still to write: {placeholders} marked {PLACEHOLDER_TEXT} "
        "(each table's domain and description, and every column description)."
    )
    click.echo(f"Then run: ac2 data model check {data_dir}")


@model.command()
@click.argument("data_dir", type=_FOLDER)
@_MODEL
def check(data_dir: Path, model_file: Path | None) -> None:
    """Check a data_model.csv against the standard and the files in DATA_DIR.

    Errors are missing or wrong facts: a table without a description, a key that is
    nullable, a column that differs from its file's header. Warnings are for a reviewer.
    The exit code is 1 if there is an error.

    \b
    Examples:
      ac2 data model check data
      ac2 data model check data --model reviewed/data_model.csv
    """
    issues = check_data_model(_read(_model_path(data_dir, model_file)), data_dir)
    errors = [i for i in issues if i.level == "error"]
    warnings = [i for i in issues if i.level == "warning"]
    for issue in [*errors, *warnings]:
        click.echo(str(issue))
    click.echo(f"{len(errors)} errors, {len(warnings)} warnings.")
    if errors:
        raise CliError(f"the model has {len(errors)} errors", fix="fix each error listed above")
    click.echo("The model follows the standard.")


@model.command()
@click.argument("data_dir", type=_FOLDER)
@_MODEL
def diff(data_dir: Path, model_file: Path | None) -> None:
    """List where the CSV files in DATA_DIR differ from a data_model.csv.

    The model is compared with what the files show, the way 'draft' would infer it.
    A difference is a contradiction: a table or column on one side only, a column the model
    says is never empty that is, a key with duplicates, a foreign key value with no match.
    A note is something to know that does not contradict the model: a key larger than it needs
    to be, text that holds numbers, a join the data shows that the model does not state.
    Values are counted, never printed. The exit code is 1 if there is a difference.

    \b
    Examples:
      ac2 data model diff data
      ac2 data model diff data --model reviewed/data_model.csv
    """
    found = compare_model(_read(_model_path(data_dir, model_file)), data_dir)
    differences = [d for d in found if d.level == "difference"]
    notes = [d for d in found if d.level == "note"]
    if differences:
        click.echo(f"Differences ({len(differences)}):")
        for item in differences:
            click.echo(f"  {item.kind:<22} {item.where}: {item.message}")
    if notes:
        click.echo(f"Notes ({len(notes)}):")
        for item in notes:
            click.echo(f"  {item.kind:<22} {item.where}: {item.message}")
    noun = "difference" if len(differences) == 1 else "differences"
    note_noun = "note" if len(notes) == 1 else "notes"
    if not differences:
        click.echo(f"The model and the files have no differences ({len(notes)} {note_noun}).")
        return
    click.echo(f"{len(differences)} {noun}, {len(notes)} {note_noun}.")
    raise click.exceptions.Exit(1)


def _write_report(findings: list[Finding], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\r\n")
        writer.writerow(["level", "rule", "file", "row", "line", "column", "value", "message"])
        for f in findings:
            writer.writerow(
                [
                    f.level,
                    f.rule,
                    f.file,
                    "" if f.row is None else f.row,
                    "" if f.line is None else f.line,
                    f.column or "",
                    f.value or "",
                    f.message,
                ]
            )


def _print_findings(findings: list[Finding], limit: int) -> None:
    """Print findings by file; show ``limit`` of each rule in each column, and count the rest."""
    for file in dict.fromkeys(f.file for f in findings):
        click.echo(file)
        shown: Counter[tuple[str, str, str]] = Counter()
        skipped: Counter[tuple[str, str, str]] = Counter()
        for f in (f for f in findings if f.file == file):
            group = (f.level, f.rule, f.column or "")
            if shown[group] >= limit:
                skipped[group] += 1
                continue
            shown[group] += 1
            place = ", ".join(
                part
                for part in (
                    f"row {f.row} (line {f.line})" if f.row is not None else "",
                    f"column {f.column}" if f.column else "",
                )
                if part
            )
            click.echo(f"  {f.level:<7} {f.rule:<12} {place + ': ' if place else ''}{f.message}")
        for (level, rule, column), count in skipped.items():
            where = f" in column {column}" if column else ""
            click.echo(f"  ... and {count} more {level} {rule} findings{where}")


@model.command()
@click.argument("data_dir", type=_FOLDER)
@_MODEL
@click.option(
    "--table",
    "tables",
    multiple=True,
    help="Validate only this table. Repeat it for more than one.",
)
@click.option(
    "--limit",
    type=click.IntRange(min=1),
    default=5,
    show_default=True,
    help="How many findings of one rule in one column to print. The rest are counted.",
)
@click.option(
    "--report",
    "report",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Write every finding, with its file, row, line and column, to this CSV file.",
)
@click.option(
    "--hide-values",
    is_flag=True,
    help="Leave the offending values out of the output and the report.",
)
def validate(
    data_dir: Path,
    model_file: Path | None,
    tables: tuple[str, ...],
    limit: int,
    report: Path | None,
    hide_values: bool,
) -> None:
    """Check every value in the CSV files in DATA_DIR against the data_model.csv.

    Each file is loaded with DuckDB, which is what the services read the files with. A dry
    run loads it as a service does and compares what DuckDB made of it with the model; the
    checks then read every value as written and test it against its column's type.

    \b
    Each problem is reported with its file, row, line and column:
      type        a value that is not what the type says: a date that is not YYYY-MM-DD, a
                  number with a thousands separator or a currency symbol, a yes/no that is not
      empty       an empty value in a column the model says is never empty
      duplicate   a repeat of a primary key or of a unique column
      orphan      a foreign key value that matches nothing in the table it points at
      structure   a row with more or fewer fields than the header, or a file DuckDB cannot read
      whitespace  spaces at the start or end of a value (a warning)
      mixed       several spellings for yes and no, or timestamps with and without a time zone
      runtime_type DuckDB would load the column as a different type from the model's (a warning)

    A row is numbered from 1 after the header and also shows its line in the file. An error
    makes the exit code 1; warnings do not. Values are printed, up to 40 characters, unless
    --hide-values is given.

    \b
    Examples:
      ac2 data model validate data
      ac2 data model validate data --table claims --report problems.csv
      ac2 data model validate data --hide-values
    """
    rows = _read(_model_path(data_dir, model_file))
    try:
        findings = validate_data(
            rows, data_dir, tables=list(tables) or None, show_values=not hide_values
        )
    except ModuleNotFoundError as error:
        if error.name != "duckdb":
            raise
        raise CliError(
            "validating needs DuckDB",
            fix='install it with: pip install "agentcraft2-core[duckdb]"',
        ) from error
    if report is not None:
        _write_report(findings, report)
    _print_findings(findings, limit)
    errors = sum(1 for f in findings if f.level == "error")
    warnings = sum(1 for f in findings if f.level == "warning")
    files = len({f.file for f in findings})
    if (
        any(
            count > limit
            for count in Counter((f.file, f.level, f.rule, f.column) for f in findings).values()
        )
        and report is None
    ):
        click.echo("Use --report FILE to write every finding to a file.")
    if not findings:
        click.echo("The values follow the model: no problems found.")
        return
    noun = "error" if errors == 1 else "errors"
    click.echo(
        f"{errors} {noun}, {warnings} {'warning' if warnings == 1 else 'warnings'} "
        f"in {files} {'file' if files == 1 else 'files'}."
    )
    if errors:
        raise click.exceptions.Exit(1)
