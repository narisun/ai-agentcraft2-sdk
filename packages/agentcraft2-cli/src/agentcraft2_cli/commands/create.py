"""The commands that write: a workspace, its services, their links and pins, and updates."""

from __future__ import annotations

import difflib
from dataclasses import replace
from pathlib import Path
from typing import Any

import click

from agentcraft2_cli.commands.common import (
    library_source,
    pass_toolbox,
    port_of,
    print_report,
    workspace_option,
    workspace_root,
)
from agentcraft2_cli.commands.data_options import print_proposal, proposal_for, redshift_options
from agentcraft2_cli.create_mcp import create_mcp, default_description
from agentcraft2_cli.errors import CliError, invocation
from agentcraft2_cli.files import read_text
from agentcraft2_cli.names import check_name, server_id_for
from agentcraft2_cli.toolbox import Toolbox
from agentcraft2_cli.workspace import (
    AGENTS_FOLDER,
    MODEL_PROVIDERS,
    AgentAnswers,
    WorkspaceAnswers,
    load_answers,
)

__all__ = ["init", "link", "new", "registry", "update"]

_FIRST_AGENT_PORT = 8000
_FIRST_SERVER_PORT = 8100


@click.command()
@click.argument("name", required=False)
@click.option(
    "--dir",
    "parent",
    type=click.Path(file_okay=False, path_type=Path),
    default=Path(),
    help="Where to create the workspace folder. Default: the current folder.",
)
@click.option("--owner", default=None, help="The team that owns the workspace.")
@click.option(
    "--lib-path",
    type=click.Path(file_okay=False, exists=True, path_type=Path),
    default=None,
    help="A local checkout of agentcraft2-sdk to install the library from.",
)
@click.option(
    "--lib-version", default=None, help="Install the library from a package index, e.g. '>=0.1'."
)
@click.option(
    "--answers",
    "answers_file",
    type=click.Path(dir_okay=False, exists=True, path_type=Path),
    default=None,
    help="Generate the workspace an agentcraft2.toml describes, services included.",
)
@click.option("--force", is_flag=True, help="Overwrite files that exist and differ.")
@pass_toolbox
def init(
    tools: Toolbox,
    name: str | None,
    parent: Path,
    owner: str | None,
    lib_path: Path | None,
    lib_version: str | None,
    answers_file: Path | None,
    force: bool,
) -> None:
    """Create a workspace folder called NAME: the project that holds your agents.

    A workspace is one folder with a place for agents (agents/), for MCP servers that give
    them tools (mcp-servers/), for the registries and the policy rules every service shares,
    and for the checks CI runs. Nothing is installed and nothing leaves your machine.

    \b
    Examples:
      ac2 init my-workspace                 create ./my-workspace
      ac2 init my-workspace --owner payments-team
      ac2 init my-workspace --dir ~/work    create it under ~/work
      ac2 init my-workspace --lib-version ">=0.1"
                                            install the library from a package index
                                            instead of this checkout
      ac2 init --answers agentcraft2.toml   recreate a workspace from its answers file

    Next: cd into it, then 'ac2 new agent <name>' and 'ac2 new mcp <name>'.
    """
    if answers_file is not None:
        answers = WorkspaceAnswers.from_toml(
            read_text(answers_file, what="the answers file"), what=str(answers_file)
        )
        if lib_path is not None or lib_version is not None:
            answers = replace(answers, library=library_source(lib_path, lib_version))
    else:
        if name is None:
            raise click.UsageError("give the workspace a NAME, or --answers <agentcraft2.toml>")
        answers = WorkspaceAnswers(
            name=check_name("workspace", name),
            owner=tools.ask(owner, "Which team owns this workspace?", "platform-team"),
            library=library_source(lib_path, lib_version),
        )
    root = parent / (name or answers.name)
    report = tools.scaffolder().init(root, answers, force=force)
    click.echo(f"Workspace {answers.name} at {root}")
    print_report(report, root)
    run = invocation()
    click.echo(
        "\nNext:\n"
        f"  cd {root}\n"
        f'  {run} new mcp hello-mcp --description "What the server offers"\n'
        f'  {run} new agent hello-agent --mcp hello --description "What the agent does"'
        " --model fake\n"
        f"  {run} install\n"
        f"  {run} check\n"
        f"  {run} doctor\n"
        f"  {run} run hello-agent        # then: {run} logs hello-agent, {run} errors"
    )


@click.group()
def new() -> None:
    """Add a service to the workspace: an agent, or an MCP server that gives agents tools.

    Each is written with its tests, its settings (.env.example, and .env for you to edit),
    its registry entry and the policy rules it needs, and the tests pass with no account.

    \b
    Examples:
      ac2 new agent helper                  an agent that answers with a scripted model
      ac2 new agent helper --model bedrock  an agent on Amazon Bedrock (needs AWS sign-in)
      ac2 new mcp people                    an MCP server over sample CSV data
      ac2 new mcp people --from-csv ./data  an MCP server over your own CSV files
      ac2 new agent helper --mcp people     an agent that may call the server's tools
    """


@new.command("agent")
@click.argument("name")
@click.option("--description", default=None, help="One line on what the agent does.")
@click.option(
    "--model",
    "model_provider",
    type=click.Choice(MODEL_PROVIDERS),
    default=None,
    help="The model provider. 'fake' needs no account. Default: fake.",
)
@click.option(
    "--mcp", "mcp_servers", multiple=True, help="An MCP server the agent may call. Repeatable."
)
@click.option("--port", type=click.IntRange(1024, 65535), default=None, help="Local HTTP port.")
@click.option("--force", is_flag=True, help="Overwrite files that exist and differ.")
@workspace_option
@pass_toolbox
def new_agent(
    tools: Toolbox,
    name: str,
    description: str | None,
    model_provider: str | None,
    mcp_servers: tuple[str, ...],
    port: int | None,
    force: bool,
    workspace: Path | None,
) -> None:
    """Add a hello-world LangGraph agent called NAME, with its tests.

    The agent is a LangGraph graph you edit (src/<name>/graph.py), a tool or two
    (tools.py), and an HTTP entry point that serves it. With --model fake it needs no
    account; with --model anthropic it reads ANTHROPIC_API_KEY, and with --model bedrock
    it signs in with your AWS profile (AWS_PROFILE, AWS_REGION).

    \b
    Examples:
      ac2 new agent helper
      ac2 new agent helper --description "Greets people by name."
      ac2 new agent analyst --model bedrock --mcp people
      ac2 new agent helper --port 8010

    Then: ac2 install, ac2 doctor helper, ac2 run helper.
    """
    root = workspace_root(workspace)
    answers = load_answers(root)
    agent = AgentAnswers(
        name=check_name("agent", name),
        description=tools.ask(description, "What does the agent do?", "Greets people by name."),
        model_provider=tools.ask(
            model_provider, f"Model provider ({', '.join(MODEL_PROVIDERS)})?", "fake"
        ),
        port=port or port_of(answers.agent(name), answers, _FIRST_AGENT_PORT),
        mcp_servers=mcp_servers,
    )
    if agent.model_provider not in MODEL_PROVIDERS:
        raise CliError(f"the model provider must be one of: {', '.join(MODEL_PROVIDERS)}")
    report = tools.scaffolder().add_agent(root, agent, force=force, answers=answers)
    click.echo(f"Agent {agent.name} in {AGENTS_FOLDER}/{agent.name}")
    print_report(report, root)
    linked = [answers.mcp_server(name) for name in mcp_servers]
    start = "".join(
        f"  {invocation()} run {server.name}        # in another terminal: the agent calls it\n"
        for server in linked
        if server is not None
    )
    run = invocation()
    click.echo(
        "\nNext:\n"
        f"  {run} install\n"
        f"  {run} check\n"
        f"  {run} doctor {agent.name}\n"
        f"{start}  {run} run {agent.name}        # serves it; {run} logs shows the log\n"
        f'  {agent.name} "Say hello to Ada."'
    )


@new.command("mcp")
@click.argument("name")
@click.option("--server-id", default=None, help="The server's ID in the tool registry.")
@click.option("--description", default=None, help="One line on what the server offers.")
@click.option("--port", type=click.IntRange(1024, 65535), default=None, help="Local HTTP port.")
@click.option("--no-pin", is_flag=True, help="Do not pin the tools' input schemas now.")
@click.option("--force", is_flag=True, help="Overwrite files that exist and differ.")
@click.option(
    "--from-csv",
    "csv_folder",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help="Propose the tools from the CSV files in this folder, and copy the files in.",
)
@click.option(
    "--from-model",
    "model_folder",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help="Make the tools from the query files given with --queries, using the data_model.csv "
    "in this folder, and copy the files in.",
)
@click.option(
    "--queries",
    "queries",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help="With --from-model: the folder of query files. Each file is one tool.",
)
@click.option(
    "--mask",
    "mask",
    multiple=True,
    help="With --from-model: mask this column for analysts, besides those whose names look "
    "personal. May be repeated.",
)
@click.option(
    "--from-openapi",
    "openapi",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Propose the tools from the GET operations of this OpenAPI 3 document.",
)
@click.option("--base-url", default=None, help="Where the API is. Default: the document's.")
@click.option(
    "--from-redshift",
    "redshift_schema",
    metavar="SCHEMA",
    default=None,
    help="Propose the tools from the tables of this Redshift schema. Needs --database.",
)
@redshift_options
@click.option("--source", default=None, help="The name of the data source. Default: the ID.")
@click.option(
    "--per-table",
    type=click.IntRange(1, 10),
    default=3,
    show_default=True,
    help="The most tools to propose for one table.",
)
@click.option(
    "--query", "only", multiple=True, help="Keep only this proposed query. May be repeated."
)
@click.option("--propose", is_flag=True, help="Show what would be proposed and write nothing.")
@workspace_option
@pass_toolbox
def new_mcp(
    tools: Toolbox,
    /,
    name: str,
    server_id: str | None,
    description: str | None,
    port: int | None,
    no_pin: bool,
    force: bool,
    propose: bool,
    workspace: Path | None,
    **data: Any,
) -> None:
    """Add an MCP server called NAME, with its tests.

    Without a data option the server is a hello-world over sample data.

    With --from-csv its tools are proposed from your own files: a lookup by
    the column that identifies a row, and a filter for each column that sorts
    rows into a few groups.

    With --from-model there is one tool for each query file in the folder given with
    --queries, over the CSV files and the data_model.csv of the folder given. A tool's
    description is its query's, and says what one row is; each argument is described by
    the column it is compared with. A query that uses what the model does not describe
    is refused.

    With --from-openapi there is one tool for each GET operation that answers
    with JSON records. The API is not called; the tests answer for it.

    With --from-redshift the tools are proposed from the tables of a schema.
    Only the catalogue is read. The server runs locally over stand-in CSV
    files with the tables' columns, and on Redshift when deployed.

    In each case, columns that look personal are masked for analysts.
    """
    root = workspace_root(workspace)
    answers = load_answers(root)
    check_name("MCP server", name)
    sid = server_id or server_id_for(name)
    proposal = proposal_for(tools, sid.replace("-", "_"), data)
    if proposal is not None:
        print_proposal(sid, proposal)
    elif propose:
        raise CliError("--propose needs a data option such as --from-csv")
    if propose:
        return
    created = create_mcp(
        tools.scaffolder(),
        root,
        answers,
        name=name,
        server_id=sid,
        description=tools.ask(
            description, "What does the server offer?", default_description(proposal)
        ),
        port=port or port_of(answers.mcp_server(name), answers, _FIRST_SERVER_PORT),
        proposal=proposal,
        pin=not no_pin,
        force=force,
    )
    click.echo(created.headline)
    print_report(created.report, root)
    if created.pin_note is not None:
        click.echo(created.pin_note, err=created.not_pinned is not None)
    click.echo("\nNext:\n" + "\n".join(f"  {step}" for step in created.next_steps))


@click.command()
@click.argument("agent")
@click.argument("server")
@click.option("--force", is_flag=True, help="Overwrite files that exist and differ.")
@workspace_option
@pass_toolbox
def link(tools: Toolbox, agent: str, server: str, force: bool, workspace: Path | None) -> None:
    """Let AGENT call the tools of the MCP server SERVER.

    Adds the server to the agent's entry in the registry, the policy rules that let the
    agent's callers use its tools, and the agent's test that calls it.

    \b
    Example:
      ac2 link helper people
    """
    root = workspace_root(workspace)
    answers = load_answers(root)
    report = tools.scaffolder().link(root, agent, server, answers=answers, force=force)
    click.echo(f"{agent} may now call the tools of {server}")
    print_report(report, root)
    linked = answers.mcp_server(server)
    if linked is not None:
        click.echo(
            f"\nFrom now on {agent} needs {linked.name} running: "
            f"{invocation()} run {linked.name}\n"
            f"The tests need nothing running: {invocation()} check"
        )


@click.group()
def registry() -> None:
    """Maintain the registries of the workspace: which agents and tool servers exist."""


@registry.command("pin")
@click.argument("server")
@workspace_option
@pass_toolbox
def registry_pin(tools: Toolbox, server: str, workspace: Path | None) -> None:
    """Register the tools of the MCP server SERVER as the server offers them, and pin them.

    A tool the server offers that the registry lacks is added (confidential and not
    read-only: set its classification, read-only flag and description in
    registry/mcp-tools.yaml). A registered tool the server no longer offers is dropped.
    Every tool's input schema is pinned.
    """
    root = workspace_root(workspace)
    report = tools.scaffolder().pin(root, server)
    click.echo(f"Pins of {server}")
    print_report(report, root)


@click.command()
@click.option(
    "--diff", "show_diff", is_flag=True, help="Show what would change in files you changed."
)
@click.option("--plan", "plan_only", is_flag=True, help="Preview every change; write nothing.")
@workspace_option
@pass_toolbox
def update(tools: Toolbox, show_diff: bool, plan_only: bool, workspace: Path | None) -> None:
    """Bring generated files up to what this version of agentcraft2 writes.

    A file you have not changed since it was generated is replaced. A file you
    changed is left alone and listed. Registries, rules, samples and .env
    files are never touched. --plan previews every change and writes nothing.
    """
    root = workspace_root(workspace)
    report = tools.scaffolder().plan(root) if plan_only else tools.scaffolder().update(root)
    verb = {"updated": "would update", "created": "would create"} if plan_only else {}
    for label, paths in (("updated", report.updated), ("created", report.created)):
        for path in paths:
            click.echo(f"  {verb.get(label, label)}  {path.as_posix()}")
    for path in report.kept:
        click.echo(f"  kept     {path.as_posix()}  (you changed it)")
        if show_diff:
            current = read_text(root.joinpath(*path.parts), what="a kept file")
            lines = difflib.unified_diff(
                current.splitlines(),
                report.new_text[path].splitlines(),
                fromfile=f"{path.as_posix()} (yours)",
                tofile=f"{path.as_posix()} (generated now)",
                lineterm="",
            )
            click.echo("\n".join(f"    {line}" for line in lines))
    if not (report.updated or report.created or report.kept):
        click.echo("  nothing to do: every generated file is up to date")
    elif report.kept and not show_diff:
        command = "agentcraft2 update --plan --diff" if plan_only else "agentcraft2 update --diff"
        click.echo(f"\nSee what the templates would change in the files you changed: {command}")
