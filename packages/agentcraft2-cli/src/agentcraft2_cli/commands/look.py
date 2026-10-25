"""The commands that look and run without writing: doctor, config, run and graph."""

from __future__ import annotations

import asyncio
from pathlib import Path

import click

from agentcraft2_cli.commands.common import pass_toolbox, workspace_option, workspace_root
from agentcraft2_cli.diagnostics import (
    check_service,
    check_workspace,
    explain_service,
    service_folder,
)
from agentcraft2_cli.errors import CliError
from agentcraft2_cli.names import package_name
from agentcraft2_cli.options_listing import (
    LIMITS,
    adapter_lines,
    limit_lines,
    option_lines,
    options_schema,
)
from agentcraft2_cli.toolbox import Toolbox
from agentcraft2_cli.workspace import AGENTS_FOLDER, load_answers
from agentcraft2_core.config import settings_schema
from agentcraft2_core.contracts import CheckResult
from agentcraft2_core.di import ServiceProviders

__all__ = ["config", "doctor", "graph", "run"]


def _print_checks(title: str, results: tuple[CheckResult, ...]) -> int:
    """Print one group of results and return how many failed."""
    click.echo(title)
    for result in results:
        mark = "ok  " if result.ok else "FAIL"
        click.echo(f"  {mark}  {result.name}" + (f": {result.detail}" if result.detail else ""))
        if not result.ok and result.fix:
            click.echo(f"        fix: {result.fix}")
    return sum(1 for result in results if not result.ok)


@click.command()
@click.argument("service", required=False)
@click.option("--no-pins", is_flag=True, help="Do not start each MCP server to compare its pins.")
@workspace_option
@pass_toolbox
def doctor(tools: Toolbox, service: str | None, no_pins: bool, workspace: Path | None) -> None:
    """Verify the configuration: check what the workspace, or one SERVICE, needs to run.

    Each service is built from its .env file and everything it is configured to use is
    checked: the settings resolve, the variables are known, the adapters build, the
    registries and the policy rules are valid, and the pins of each MCP server's tools
    match the server. Nothing is changed. The exit code is 1 if a check fails, and each
    failure says what to fix.

    \b
    Examples:
      ac2 doctor                everything in the workspace
      ac2 doctor helper         one service
      ac2 doctor --no-pins      do not start the MCP servers to compare their pins

    With cloud adapters selected, building them can contact AWS.
    """
    root = workspace_root(workspace)
    answers = load_answers(root)
    names = [service] if service is not None else sorted(answers.service_names())
    failed = 0
    if service is None:
        shared = check_workspace(root, answers, pin_reader=None if no_pins else tools.read_pins)
        failed += _print_checks("workspace", shared)
    for name in names:
        folder = service_folder(root, answers, name)
        failed += _print_checks(folder.name, asyncio.run(check_service(folder)))
    if failed:
        raise CliError(f"{failed} check{'s' if failed != 1 else ''} failed")
    click.echo("Everything checked is usable.")


@click.group()
def config() -> None:
    """Look at how a service is configured: its settings, where each came from, what is valid.

    Settings come, strongest first, from the process environment, the service's .env, the
    project home's .env, agentcraft2.settings.toml, and the profile's defaults.

    \b
    Examples:
      ac2 config check helper             verify it (the same as ac2 doctor helper)
      ac2 config explain helper           every setting and where it came from
      ac2 config options model bedrock    what the Bedrock model accepts, with defaults
      ac2 config options                  the adapters of every section
      ac2 config schema                   JSON Schema of agentcraft2.settings.toml
    """


@config.command("options")
@click.argument("port", required=False)
@click.argument("name", required=False)
@click.option(
    "--schema", is_flag=True, help="Print the adapter's options as JSON Schema, for an editor."
)
def config_options(port: str | None, name: str | None, schema: bool) -> None:
    """List the adapters of each PORT, or the options of the adapter NAME with their defaults."""
    providers = ServiceProviders.default()
    if schema:
        if port is None or name is None:
            raise CliError(
                "--schema needs a PORT and a NAME",
                fix="e.g. agentcraft2 config options audit jsonl --schema",
            )
        click.echo(options_schema(providers, port, name))
        return
    if port == LIMITS:
        lines = limit_lines()
    elif port is None:
        lines = adapter_lines(providers)
    elif name is None:
        lines = adapter_lines(providers, [port])
    else:
        lines = option_lines(providers, port, name)
    for line in lines:
        click.echo(line)


@config.command("schema")
def config_schema() -> None:
    """Print a JSON Schema of agentcraft2.settings.toml, for an editor or a coding assistant."""
    click.echo(settings_schema())


@config.command("check")
@click.argument("service", required=False)
@click.option("--no-pins", is_flag=True, help="Do not start each MCP server to compare its pins.")
@workspace_option
@click.pass_context
def config_check(
    ctx: click.Context, service: str | None, no_pins: bool, workspace: Path | None
) -> None:
    """Verify the configuration of the workspace, or of one SERVICE: the same as 'ac2 doctor'.

    \b
    Examples:
      ac2 config check             everything
      ac2 config check helper      one service
    """
    ctx.invoke(doctor, service=service, no_pins=no_pins, workspace=workspace)


@config.command("explain")
@click.argument("service")
@workspace_option
def config_explain(service: str, workspace: Path | None) -> None:
    """Show every setting of SERVICE and where it came from. Secrets are masked."""
    root = workspace_root(workspace)
    folder = service_folder(root, load_answers(root), service)
    settings, warnings = explain_service(folder)
    width = max(len(setting.variable) for setting in settings)
    for setting in settings:
        click.echo(f"{setting.variable.ljust(width)}  {setting.value}")
        click.echo(f"{' ' * width}    from {setting.origin}")
    for warning in warnings:
        click.echo(f"warning: {warning}", err=True)


@click.command(
    context_settings={"ignore_unknown_options": True},
    short_help="Start a service and keep it running, logging as it goes.",
)
@click.argument("service")
@click.argument("arguments", nargs=-1, type=click.UNPROCESSED)
@workspace_option
@click.pass_context
@pass_toolbox
def run(
    tools: Toolbox,
    ctx: click.Context,
    service: str,
    arguments: tuple[str, ...],
    workspace: Path | None,
) -> None:
    """Start SERVICE and keep it running: an agent's HTTP entry point, or an MCP server.

    Everything the service writes appears here as it is written and is kept in its log,
    so 'ac2 logs' and 'ac2 errors' can read it afterwards. A service that cannot start
    stops at once with the exact message and the full stack trace, and the failure is
    kept for 'ac2 errors'. Anything after the name is passed to the service, for example
    --port 9000. Stop it with Ctrl-C; requests in flight finish first.

    \b
    Examples:
      ac2 run people            start an MCP server (in one terminal)
      ac2 run helper            start an agent that calls it (in another)
      ac2 run helper --port 9000
      ac2 logs helper -f        watch its log from a third terminal

    \b
    Call a running agent:
      curl -X POST http://127.0.0.1:8000/invoke -H "content-type: application/json" \\
           -d '{"input": {"question": "Say hello to Ada."}, "thread_id": "t1"}'
    """
    root = workspace_root(workspace)
    answers = load_answers(root)
    folder = service_folder(root, answers, service)
    package = package_name(folder.name)
    module = f"{package}.service" if answers.agent(service) is not None else f"{package}.__main__"
    ctx.exit(tools.run_service(folder, module, arguments))


@click.command()
@click.argument("agent")
@workspace_option
@pass_toolbox
def graph(tools: Toolbox, agent: str, workspace: Path | None) -> None:
    """Print the graph of AGENT as a Mermaid diagram.

    The graph is built with the agent's own tools. Tools of MCP servers are
    not loaded, so no server has to be running.
    """
    root = workspace_root(workspace)
    answers = load_answers(root)
    if answers.agent(agent) is None:
        raise CliError(f"there is no agent called {agent!r} in this workspace")
    click.echo(tools.read_graph(root / AGENTS_FOLDER / agent, package_name(agent)).rstrip())
