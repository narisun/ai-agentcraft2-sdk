"""Errors the command line reports, and the exit codes it uses."""

from __future__ import annotations

import sys
from pathlib import PureWindowsPath
from typing import Self

import click

from agentcraft2_core.contracts import AgentCraft2Error
from agentcraft2_core.observability import explain, stack_trace

__all__ = [
    "EXIT_FAILED",
    "EXIT_INTERRUPTED",
    "EXIT_OK",
    "EXIT_USAGE",
    "CliError",
    "invocation",
    "program_name",
    "report_bug",
    "report_error",
    "set_trace",
]

EXIT_OK = 0
EXIT_FAILED = 1
"""The command was understood and could not be done."""
EXIT_USAGE = 2
"""The command line itself was wrong: an unknown option, a missing argument."""
EXIT_INTERRUPTED = 130
"""The person stopped the command with Ctrl+C."""

_trace = False


def set_trace(on: bool) -> None:
    """Choose whether a mistake of the person at the keyboard also gets its stack trace."""
    global _trace
    _trace = on


def invocation() -> str:
    """Return how to type this command in a hint: as it was started, or ``ac2``.

    A hint a person is meant to copy uses the form they ran, so it works where they are:
    ``python -m agentcraft2_cli`` when that is how they started it, otherwise the short name.
    """
    script = _started_as()
    if script.name == "__main__.py" and script.parent.name == "agentcraft2_cli":
        return "python -m agentcraft2_cli"
    return "agentcraft2" if program_name() == "agentcraft2" else "ac2"


def _started_as() -> PureWindowsPath:
    """Return the path the command was started by, read with Windows rules.

    Windows rules take a slash and a backslash as separators, so the name of a Windows script is
    found on any system, and a path with slashes is read the same way.
    """
    return PureWindowsPath(sys.argv[0])


def program_name() -> str:
    """Return the name the command was started under: ``ac2`` or ``agentcraft2``."""
    started = _started_as().name.lower().removesuffix(".exe").removesuffix(".py")
    return "ac2" if started == "ac2" else "agentcraft2"


class CliError(AgentCraft2Error):
    """A problem the developer can fix, reported in a few lines without a traceback.

    It is one of the library's errors, so it carries the same facts
    (``expected``, ``actual``, ``fix``) and is printed the same way. What sets
    it apart is its audience: its message is written for the person at the
    terminal and names what they typed or which file to change.

    Args:
        message: What went wrong.
        expected: What the command needed.
        actual: What it found instead.
        fix: What to do about it.
        detail: Text that may hold file content, shown only with the facts.
    """

    @classmethod
    def from_error(  # narrower than the base: adds `where`
        cls, error: AgentCraft2Error, *, where: str | None = None
    ) -> Self:
        """Return a command-line error that says what ``error`` says, its facts kept apart.

        Args:
            error: The library's error.
            where: What the error is about, put before its message, such as a file.
        """
        message = f"{where}: {error.message}" if where else error.message
        notes = "; ".join(getattr(error, "__notes__", ()))
        return cls(
            f"{message} ({notes})" if notes else message,
            expected=error.expected,
            actual=error.actual,
            fix=error.fix,
            detail=error.detail,
        )


def report_error(problem: AgentCraft2Error) -> int:
    """Print ``problem`` for the terminal and return the exit code to use.

    A :class:`CliError` is a mistake of the person at the keyboard (a name that is not
    there, a file that is missing): it is shown as written, in a few lines, and its stack
    trace only with ``--trace``. Any other library error came from deeper down: the exact
    message, the type, the facts, the causes and the full stack trace are shown, as this
    runs on a developer's machine.
    """
    own = isinstance(problem, CliError)
    text = str(problem) if own else explain(problem)
    if _trace or not own:
        click.echo(stack_trace(problem).rstrip(), err=True)
    click.echo(f"{program_name()}: error: {text}", err=True)
    return EXIT_FAILED


def report_bug(bug: BaseException) -> int:
    """Print an error nobody expected, with its exact message and full stack trace."""
    click.echo(stack_trace(bug).rstrip(), err=True)
    click.echo(
        f"{program_name()}: unexpected error: {type(bug).__name__}: {bug}\n"
        "  This is a fault in the command, not in your workspace. The stack trace above says\n"
        "  where; it is what to send with a report of it.",
        err=True,
    )
    return EXIT_FAILED
