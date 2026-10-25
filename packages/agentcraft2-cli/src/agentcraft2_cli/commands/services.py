"""The ``services`` command: list, start and stop the services the SDK provides."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import click

from agentcraft2_cli.commands.common import pass_toolbox, workspace_option, workspace_root
from agentcraft2_cli.errors import CliError
from agentcraft2_cli.services.manager import ServiceStatus, start, status, stop
from agentcraft2_cli.services.registry import SERVICES, ServiceSpec
from agentcraft2_cli.toolbox import Toolbox
from agentcraft2_cli.workspace import LibrarySource, load_answers
from agentcraft2_core.config import state_dir

__all__ = ["services"]

_META = "agentcraft2.services.workspace"


def _selected(names: Sequence[str]) -> list[ServiceSpec]:
    """Return the services asked for, in the order they start; all of them if none was named."""
    unknown = [name for name in names if name not in SERVICES]
    if unknown:
        raise CliError(
            f"there is no service called {', '.join(map(repr, unknown))}",
            expected=f"one of: {', '.join(SERVICES)}",
            fix="run 'ac2 services' to see the services",
        )
    return [spec for name, spec in SERVICES.items() if not names or name in names]


def _where(ctx: click.Context, workspace: Path | None) -> tuple[Path, Path, LibrarySource]:
    """Return the workspace root, its state folder and where it takes the SDK from."""
    root = workspace_root(workspace or ctx.meta.get(_META))
    return root, state_dir(root), load_answers(root).library


def _row(current: ServiceStatus) -> str:
    lines = [f"  {current.name:<9}{current.state:<14}{current.address}".rstrip()]
    lines.append(f"            {current.title}")
    if current.detail:
        lines.append(f"            {current.detail}")
    return "\n".join(lines)


def _list(ctx: click.Context, tools: Toolbox, workspace: Path | None) -> None:
    root, state, library = _where(ctx, workspace)
    source = f"the checkout at {library.path}" if library.kind == "path" else "the package index"
    click.echo(f"Services for {root}")
    click.echo(f"  state: {state}")
    click.echo(f"  installed from: {source}")
    click.echo("")
    shown = [status(spec, state, tools.services) for spec in SERVICES.values()]
    for current in shown:
        click.echo(_row(current))
    if any(current.state in {"stopped", "not installed", "failed"} for current in shown):
        click.echo("")
        click.echo("Start them with: ac2 services start")


@click.group(
    short_help="List, start and stop Phoenix, the viewer for traces.",
    invoke_without_command=True,
)
@workspace_option
@pass_toolbox
@click.pass_context
def services(ctx: click.Context, tools: Toolbox, workspace: Path | None) -> None:
    """Check, start and stop the services the SDK provides for developing agents.

    A service runs on your machine only, in the background, and keeps its packages, data and
    log in this workspace's .agentcraft2 folder (under the project home, when you set one),
    which Git ignores. 'ac2 services' lists each one with its state and address.

    \b
    Services:
      phoenix   the viewer for the traces of your agents (http://127.0.0.1:6006)

    \b
    Examples:
      ac2 services                   what is installed and running
      ac2 services start             install what is missing, then start every service
      ac2 services start phoenix     start one
      ac2 services stop              stop every service
      ac2 logs phoenix               read the log of a service

    The first start installs the packages of a service with this Python and its pip, so it
    needs your package index. Nothing is installed anywhere else.
    """
    ctx.meta[_META] = workspace
    if ctx.invoked_subcommand is None:
        _list(ctx, tools, workspace)


@services.command("list", short_help="Show each service and its state.")
@workspace_option
@pass_toolbox
@click.pass_context
def list_services(ctx: click.Context, tools: Toolbox, workspace: Path | None) -> None:
    """Show each service with its state (not installed, stopped, starting, running, failed)."""
    _list(ctx, tools, workspace)


@services.command("start", short_help="Install if needed, start, and wait until ready.")
@click.argument("names", nargs=-1)
@workspace_option
@pass_toolbox
@click.pass_context
def start_services(
    ctx: click.Context, tools: Toolbox, names: tuple[str, ...], workspace: Path | None
) -> None:
    """Start the named services, or all of them, and wait until each answers.

    Each service is checked first: its port must be free, and, if its packages are missing,
    they are installed into the workspace's .agentcraft2 folder. Starting a service that is
    already running does nothing.

    \b
    Examples:
      ac2 services start             every service
      ac2 services start phoenix     one
    """
    _, state, library = _where(ctx, workspace)
    for spec in _selected(names):
        start(spec, state, library, tools.services)


@services.command("stop", short_help="Stop services and wait until they have stopped.")
@click.argument("names", nargs=-1)
@workspace_option
@pass_toolbox
@click.pass_context
def stop_services(
    ctx: click.Context, tools: Toolbox, names: tuple[str, ...], workspace: Path | None
) -> None:
    """Stop the named services, or all of them, and wait until each has stopped.

    A service is asked to stop the way Ctrl+C asks, so it can finish what it is doing. If it
    has not stopped after 30 seconds it is ended. What it saved, such as traces, is kept.

    \b
    Examples:
      ac2 services stop              every service
      ac2 services stop phoenix      one
    """
    _, state, _ = _where(ctx, workspace)
    for spec in reversed(_selected(names)):
        stop(spec, state, tools.services)
