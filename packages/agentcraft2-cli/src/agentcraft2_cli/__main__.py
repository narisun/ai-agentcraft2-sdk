"""Entry point of the ``agentcraft2`` command."""

from __future__ import annotations

import sys
from collections.abc import Sequence

import click

from agentcraft2_cli.app import cli
from agentcraft2_cli.errors import (
    EXIT_FAILED,
    EXIT_INTERRUPTED,
    EXIT_OK,
    EXIT_USAGE,
    program_name,
    report_bug,
    report_error,
    set_trace,
)
from agentcraft2_cli.toolbox import Toolbox
from agentcraft2_core.contracts import AgentCraft2Error

__all__ = ["main"]


def main(argv: Sequence[str] | None = None, toolbox: Toolbox | None = None) -> int:
    """Run the command line and return its exit code.

    0 means done, 1 means the command was understood and could not be done,
    2 means the command line itself was wrong. A problem is reported on
    standard error in a few lines: what went wrong, what was expected and
    what was found, and what to do. A bug in the command is a traceback.

    Args:
        argv: The command line. By default the process's own.
        toolbox: What the commands use to reach outside the process. By
            default the real tools; a test passes its own.
    """
    set_trace(False)
    try:
        result = cli.main(
            args=list(argv) if argv is not None else None,
            prog_name=program_name(),
            standalone_mode=False,
            obj=toolbox if toolbox is not None else Toolbox(),
        )
    except click.UsageError as problem:
        problem.show()
        return EXIT_USAGE
    except click.ClickException as problem:
        problem.show()
        return EXIT_FAILED
    except click.Abort as stopped:
        click.echo(f"{program_name()}: stopped", err=True)
        # Click turns Ctrl+C into this, keeping the interrupt as its context.
        return (
            EXIT_INTERRUPTED if isinstance(stopped.__context__, KeyboardInterrupt) else EXIT_FAILED
        )
    except KeyboardInterrupt:
        click.echo(f"{program_name()}: stopped", err=True)
        return EXIT_INTERRUPTED
    except AgentCraft2Error as problem:
        # CliError is one of these: one place prints every error the CLI reports.
        return report_error(problem)
    except Exception as bug:  # noqa: BLE001 - whatever it is, the developer gets it whole
        return report_bug(bug)
    return result if isinstance(result, int) else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
