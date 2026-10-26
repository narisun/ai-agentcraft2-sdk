"""Install a workspace with the current interpreter's pip."""

from __future__ import annotations

import re
import sys
import tomllib
from collections.abc import Callable
from pathlib import Path

import click

from agentcraft2_cli.commands.common import (
    pass_toolbox,
    workspace_option,
    workspace_root,
)
from agentcraft2_cli.errors import CliError, invocation
from agentcraft2_cli.files import read_text
from agentcraft2_cli.toolbox import Toolbox
from agentcraft2_cli.workspace import AGENTS_FOLDER, SERVERS_FOLDER, load_answers

__all__ = ["install", "installation_arguments", "missing_sdk_packages"]


def installation_arguments(root: Path, *, dev: bool = True) -> tuple[str, ...]:
    """Return pip arguments for the workspace's own services and its development tools.

    All of them are resolved together, so pip checks their dependency constraints without
    needing workspace-aware tooling. The SDK is not among them: it is installed by the SDK's
    own installer, and only checked here (see :func:`missing_sdk_packages`).
    """
    answers = load_answers(root)
    arguments = ["install"]
    if dev:
        pyproject = root / "pyproject.toml"
        try:
            project = tomllib.loads(read_text(pyproject, what="the workspace pyproject.toml"))
        except tomllib.TOMLDecodeError as problem:
            raise CliError(
                f"the workspace pyproject.toml ({pyproject}) is not valid TOML ({problem})",
                fix="repair the file, then run 'agentcraft2 install' again",
            ) from problem
        requirements = project.get("dependency-groups", {}).get("dev", [])
        if not all(isinstance(item, str) for item in requirements):
            raise CliError("the dev dependency group must contain requirement strings")
        arguments.extend(requirements)
    for folder, services in (
        (AGENTS_FOLDER, answers.agents),
        (SERVERS_FOLDER, answers.mcp_servers),
    ):
        for service in services:
            arguments.extend(("--editable", str(root / folder / service.name)))
    if len(arguments) == 1:
        raise CliError("there are no projects or dependencies to install")
    return tuple(arguments)


_SDK_PACKAGES = {"agentcraft2-core": "agentcraft2_core", "agentcraft2-aws": "agentcraft2_aws"}
"""The SDK's packages a service can require, with the name each is imported by."""


def missing_sdk_packages(root: Path, is_installed: Callable[[str], bool]) -> list[str]:
    """Return the SDK packages the workspace's services require that this Python lacks.

    Only a workspace that takes the SDK from a checkout is asked: a checkout is not on a
    package index, so pip could not find what is missing, and the person must install the SDK.
    A workspace that takes it from the index lets pip resolve it as it does any dependency.
    """
    answers = load_answers(root)
    if answers.library.kind != "path":
        return []
    wanted: set[str] = set()
    for folder, services in (
        (AGENTS_FOLDER, answers.agents),
        (SERVERS_FOLDER, answers.mcp_servers),
    ):
        for service in services:
            pyproject = root / folder / service.name / "pyproject.toml"
            try:
                table = tomllib.loads(
                    read_text(pyproject, what=f"the pyproject.toml of {service.name}")
                )
            except tomllib.TOMLDecodeError as problem:
                raise CliError(
                    f"the pyproject.toml of {service.name} ({pyproject}) is not valid TOML "
                    f"({problem})",
                    fix="repair the file, then run 'agentcraft2 install' again",
                ) from problem
            for requirement in table.get("project", {}).get("dependencies", []):
                found = re.match(r"[A-Za-z0-9_.-]+", str(requirement))
                name = found.group(0).lower().replace("_", "-") if found else ""
                if name in _SDK_PACKAGES:
                    wanted.add(name)
    return sorted(name for name in wanted if not is_installed(_SDK_PACKAGES[name]))


def quiet(arguments: tuple[str, ...]) -> tuple[str, ...]:
    """Return pip's arguments so that it prints only warnings and errors.

    pip lists every requirement that is already satisfied, which buries what was installed
    and what failed. It also nags about a newer pip, which this command cannot act on.
    """
    return (arguments[0], "-q", "--disable-pip-version-check", *arguments[1:])


@click.command()
@click.option("--no-dev", is_flag=True, help="Omit the workspace's development tools.")
@click.option("--verbose", is_flag=True, help="Show everything pip prints, not only problems.")
@workspace_option
@pass_toolbox
def install(tools: Toolbox, no_dev: bool, verbose: bool, workspace: Path | None) -> None:
    """Install the workspace's own agents and MCP servers, with Python's pip.

    This installs what is yours: the agents and MCP servers of this workspace, with their
    dependencies and the workspace's development tools (pytest, ruff, mypy). It never installs
    the SDK or its tools (Phoenix, Agent Lab); the SDK's installer does that, once, and this
    command stops with the fix if the SDK is missing.

    Use the Python selected for this workspace. Run again after adding a service
    or changing its pyproject.toml. Pip uses that Python's configured package location,
    so a corporate index or proxy set in pip's own configuration applies. It prints only
    problems; --verbose shows all of pip's output.

    
    Examples:
      ac2 install               every service, with the development tools (pytest, ruff)
      ac2 install --no-dev      only what runs the services
      ac2 install --verbose     show everything pip prints
    """
    root = workspace_root(workspace)
    missing = missing_sdk_packages(root, tools.is_installed)
    if missing:
        raise CliError(
            "the SDK is not installed in this Python",
            expected=f"{', '.join(missing)} installed with the SDK, before the workspace",
            actual="not installed in " + sys.executable,
            fix=(
                "install the SDK first: python tools/install_sdk.py, in your checkout of the SDK. "
                "'ac2 install' installs only this workspace's own services"
            ),
        )
    arguments = installation_arguments(root, dev=not no_dev)
    answers = load_answers(root)
    installing = [service.name for service in answers.agents]
    installing += [service.name for service in answers.mcp_servers]
    if not no_dev:
        installing.append("the development tools")
    click.echo(f"Installing {', '.join(installing)}" + ("" if verbose else " with pip, quietly"))
    if tools.run_tool("pip", arguments if verbose else quiet(arguments), root) != 0:
        raise CliError(
            "pip could not install the workspace",
            fix="read pip's output above; 'install --verbose' shows all of it",
        )
    click.echo(f"Installed. Next: {invocation()} check")
