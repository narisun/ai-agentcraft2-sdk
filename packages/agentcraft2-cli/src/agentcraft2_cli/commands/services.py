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
from agentcraft2_core.config import sdk_path, state_dir
from agentcraft2_core.contracts import ConfigurationError

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


def _where(ctx: click.Context, workspace: Path | None) -> tuple[Path, Path, Path]:
    """Return the workspace root, its state folder, and the SDK's folder."""
    root = workspace_root(workspace or ctx.meta.get(_META))
    try:
        return root, state_dir(root), sdk_path()
    except ConfigurationError as problem:
        raise CliError.from_error(problem) from None


def _row(current: ServiceStatus) -> str:
    lines = [f"  {current.name:<9}{current.state:<18}{current.address}".rstrip()]
    lines.append(f"            {current.title}")
    if current.detail:
        lines.append(f"            {current.detail}")
    return "\n".join(lines)


def _list(ctx: click.Context, tools: Toolbox, workspace: Path | None) -> None:
    root, state, sdk = _where(ctx, workspace)
    click.echo(f"Services for {root}")
    click.echo(f"  installed with the SDK in: {sdk}")
    click.echo(f"  data, logs and run state in: {state}")
    click.echo("")
    shown = [status(spec, state, sdk, tools.services) for spec in SERVICES.values()]
    for current in shown:
        click.echo(_row(current))
    if any(current.state == "not installed" for current in shown):
        click.echo("")
        click.echo("Install the SDK's tools with: python tools/install_sdk.py (ac2 never does)")
    elif any(current.state == "stopped" for current in shown):
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
    """List, start and stop the services the SDK provides for developing agents.

    The SDK installs them once, with itself, in its own folder, apart from your agents and
    their packages. 'ac2' only starts and stops them, and shows what they need. A service
    runs on your machine only, in the background, and writes its data, logs and run state
    to this workspace's .agentcraft2 folder (under the project home, when you set one),
    which Git ignores. 'ac2 services' lists each one with its state and address.

    \b
    Services:
      phoenix   the viewer for the traces of your agents (http://127.0.0.1:6006)

    \b
    Examples:
      ac2 services                   what is installed and running
      ac2 services start             start every service and wait until it answers
      ac2 services start phoenix     start one
      ac2 services stop              stop every service
      ac2 logs phoenix               read the log of a service

    A service that is not installed is installed by the SDK's own installer, not by 'ac2'.
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


@services.command("start", short_help="Start services and wait until they answer.")
@click.argument("names", nargs=-1)
@workspace_option
@pass_toolbox
@click.pass_context
def start_services(
    ctx: click.Context, tools: Toolbox, names: tuple[str, ...], workspace: Path | None
) -> None:
    """Start the named services, or all of them, and wait until each answers.

    Each service is checked first: it must be installed with this version of the SDK, its
    folders must be on a local disk, and its port must be free. Starting a service that is
    already running does nothing.

    \b
    Examples:
      ac2 services start             every service
      ac2 services start phoenix     one
    """
    _, state, sdk = _where(ctx, workspace)
    for spec in _selected(names):
        start(spec, state, sdk, tools.services)


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
    has not stopped after 30 seconds it is ended. What it saved, such as traces, is kept. A
    service started from another workspace is stopped too: there is one of it.

    \b
    Examples:
      ac2 services stop              every service
      ac2 services stop phoenix      one
    """
    _, state, sdk = _where(ctx, workspace)
    for spec in reversed(_selected(names)):
        stop(spec, state, sdk, tools.services)
