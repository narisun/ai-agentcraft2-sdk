"""The commands that read what a running service wrote: ``logs`` and ``errors``."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path

import click

from agentcraft2_cli.commands.common import workspace_option, workspace_root
from agentcraft2_cli.diagnostics import service_folder
from agentcraft2_cli.errors import CliError
from agentcraft2_cli.logbook import (
    Entry,
    errors_in,
    format_entry,
    format_error,
    log_file,
    parse_line,
    tail_lines,
)
from agentcraft2_cli.workspace import load_answers
from agentcraft2_core.config import state_dir

__all__ = ["errors", "follow", "logs"]

_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
_RANK = {name: index for index, name in enumerate(_LEVELS)}
_SCANNED = 5_000
"""How many of the last lines of each log ``errors`` looks through."""
_POLL_SECONDS = 0.5


def _logs_of(workspace: Path | None, service: str | None) -> list[tuple[str, Path]]:
    """Return the (service, log file) pairs asked for: one service's, or every service's."""
    root = workspace_root(workspace)
    answers = load_answers(root)
    names = [service] if service is not None else sorted(answers.service_names())
    return [
        (name, log_file(state_dir(folder), name))
        for name in names
        for folder in [service_folder(root, answers, name)]
    ]


def _entries(files: list[tuple[str, Path]], count: int) -> list[Entry]:
    """Read the last ``count`` lines of each log and return their entries, oldest first."""
    found = [
        parse_line(line, name) for name, path in files for line in tail_lines(path, count) if line
    ]
    return sorted(found, key=lambda entry: entry.time)


def _nothing_yet(files: list[tuple[str, Path]]) -> CliError:
    where = files[0][1] if len(files) == 1 else files[0][1].parent
    return CliError(
        "there are no logs yet",
        expected="a service that ran under 'ac2 run', which keeps its log",
        actual=f"nothing in {where}",
        fix=(
            "start a service with 'ac2 run <service>' and use it; a service started another "
            "way, such as 'python -m', writes only to its own terminal"
        ),
    )


def follow(
    files: list[tuple[str, Path]],
    show: Callable[[Entry], None],
    *,
    sleep: Callable[[float], None] = time.sleep,
    cycles: int | None = None,
) -> None:
    """Show each line added to the logs, as it is added, until interrupted.

    Args:
        files: The (service, log file) pairs.
        show: Called with each new entry.
        sleep: Waits between looks.
        cycles: Look this many times and return; by default, until interrupted.
    """
    offsets = {path: path.stat().st_size if path.is_file() else 0 for _, path in files}
    looked = 0
    while cycles is None or looked < cycles:
        sleep(_POLL_SECONDS)
        looked += 1
        for name, path in files:
            if not path.is_file():
                continue
            size = path.stat().st_size
            if size < offsets[path]:  # set aside and started again
                offsets[path] = 0
            if size == offsets[path]:
                continue
            with path.open("rb") as handle:
                handle.seek(offsets[path])
                chunk = handle.read(size - offsets[path])
            offsets[path] = offsets[path] + len(chunk)
            for line in chunk.decode("utf-8", errors="replace").splitlines():
                if line:
                    show(parse_line(line, name))


@click.command(short_help="Show the log of a service.")
@click.argument("service", required=False)
@click.option(
    "-n", "--lines", default=40, show_default=True, help="How many of the latest lines to show."
)
@click.option(
    "--level",
    type=click.Choice(_LEVELS, case_sensitive=False),
    help="Only this level and above.",
)
@click.option("--request", "request_id", help="Only the lines of this request ID.")
@click.option("-f", "--follow", "following", is_flag=True, help="Keep going; show new lines.")
@click.option("--raw", is_flag=True, help="Print the lines as the service wrote them (JSON).")
@workspace_option
def logs(
    service: str | None,
    lines: int,
    level: str | None,
    request_id: str | None,
    following: bool,
    raw: bool,
    workspace: Path | None,
) -> None:
    """Show the log of SERVICE, or of every service, from the latest runs.

    A service keeps a log when it runs under 'ac2 run'. Each line shows the time,
    the level, the service and what happened, then the facts that matter: the
    request ID, the tool, the outcome and how long it took.

    \b
    Examples:
      ac2 logs                       the latest lines of every service
      ac2 logs helper -n 100         the last 100 lines of one
      ac2 logs helper --level ERROR  only what failed
      ac2 logs --request r-42        everything one request did, across services
      ac2 logs helper -f             keep showing new lines (Ctrl+C stops)
      ac2 logs helper --raw          the JSON lines, for jq and scripts

    A line holds the exact message and the stack trace of a failure; 'ac2 errors'
    lists only those.
    """
    files = _logs_of(workspace, service)
    if not any(path.is_file() for _, path in files):
        raise _nothing_yet(files)
    floor = _RANK[level.upper()] if level else 0

    def wanted(entry: Entry) -> bool:
        if _RANK.get(entry.level, _RANK["INFO"]) < floor:
            return False
        return request_id is None or entry.fields.get("request_id") == request_id

    def show(entry: Entry) -> None:
        if wanted(entry):
            click.echo(_raw(entry) if raw else format_entry(entry))

    for entry in _entries(files, lines * 4 if (level or request_id) else lines)[-lines:]:
        show(entry)
    if following:
        follow(files, show)


def _raw(entry: Entry) -> str:
    return json.dumps(
        {
            "time": entry.time,
            "level": entry.level,
            "service": entry.service,
            "message": entry.message,
            **entry.fields,
        },
        ensure_ascii=False,
        default=str,
    )


@click.command(short_help="List what failed, with the stack traces.")
@click.argument("service", required=False)
@click.option("-n", "--count", default=10, show_default=True, help="How many failures to show.")
@click.option("--no-trace", is_flag=True, help="Leave out the stack traces.")
@workspace_option
def errors(service: str | None, count: int, no_trace: bool, workspace: Path | None) -> None:
    """List what went wrong in SERVICE, or in any service, oldest of the latest first.

    Each failure shows the exact message, what was expected and found, how to fix
    it, where it was raised, and the full stack trace, as the service wrote them
    on this machine. A service that crashed on start is listed too, with what it
    printed.

    \b
    Examples:
      ac2 errors                     the latest failures of every service
      ac2 errors helper -n 3         the last three of one
      ac2 errors --no-trace          the messages without the stacks
      ac2 logs --request r-42        then read everything around one failure

    There is nothing to list until a service has run under 'ac2 run'.
    """
    files = _logs_of(workspace, service)
    if not any(path.is_file() for _, path in files):
        raise _nothing_yet(files)
    found = errors_in(_entries(files, _SCANNED))[-count:]
    if not found:
        click.echo("No errors in the logs.")
        return
    for position, entry in enumerate(found):
        if position:
            click.echo()
        click.echo(format_error(entry, trace=not no_trace))
    click.echo(f"\n{len(found)} failure{'s' if len(found) != 1 else ''} shown.")
