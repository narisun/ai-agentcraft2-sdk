"""What the commands share: the toolbox, the workspace option and how results are printed."""

from __future__ import annotations

import re
from pathlib import Path

import click

from agentcraft2_cli import __version__
from agentcraft2_cli.errors import CliError
from agentcraft2_cli.render import Report
from agentcraft2_cli.toolbox import Toolbox
from agentcraft2_cli.workspace import (
    AgentAnswers,
    LibrarySource,
    McpAnswers,
    WorkspaceAnswers,
    find_workspace,
)
from agentcraft2_core.config import project_home

__all__ = [
    "library_source",
    "pass_toolbox",
    "port_of",
    "print_report",
    "workspace_option",
    "workspace_root",
]

pass_toolbox = click.make_pass_decorator(Toolbox, ensure=True)
"""Gives a command the toolbox as its first argument: the one the command line
was started with, or the real one."""

workspace_option = click.option(
    "--workspace",
    "workspace",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="A folder inside the workspace. Default: the current folder.",
)


def _checkout() -> Path | None:
    """Return the library checkout this command runs from, if it runs from one."""
    here = Path(__file__).resolve()
    for folder in here.parents:
        if (folder / "packages" / "agentcraft2-core" / "pyproject.toml").is_file():
            return folder
    return None


def installed_specifier() -> str | None:
    """Return the versions of the installed SDK that a workspace may require, if it is installed.

    A workspace that requires them is satisfied by the SDK already installed, without a path
    to a checkout and without a package index. Before 1.0 a new minor version may break, so
    the range ends at the next one.
    """
    found = re.match(r"(\d+)\.(\d+)", __version__)
    if found is None or __version__ == "0.0.0":
        return None
    major, minor = int(found[1]), int(found[2])
    upper = f"{major}.{minor + 1}" if major == 0 else f"{major + 1}"
    return f">={major}.{minor},<{upper}"


def library_source(path: Path | None, version: str | None) -> LibrarySource:
    """Return where a new workspace installs the library from.

    An explicit ``--lib-path`` or ``--lib-version`` wins. Without one, a command that runs from
    a checkout uses that checkout; any other uses the versions of the SDK that is installed.
    """
    if path is not None and version is not None:
        raise click.UsageError("give --lib-path or --lib-version, not both")
    if version is not None:
        return LibrarySource(kind="index", version=version)
    checkout = path or _checkout()
    if checkout is None:
        installed = installed_specifier()
        if installed is None:
            raise CliError(
                "the SDK is not installed here, so there is no version to require. Install it "
                "(python tools/install_sdk.py, in your checkout of the SDK), or give "
                "--lib-path <checkout> or --lib-version '<specifier>'."
            )
        return LibrarySource(kind="index", version=installed)
    if not (checkout / "packages" / "agentcraft2-core" / "pyproject.toml").is_file():
        raise CliError(f"{checkout} is not a checkout of the library")
    return LibrarySource.from_path(checkout)


def workspace_root(workspace: Path | None) -> Path:
    """Return the root of the workspace a command works on.

    ``--workspace`` wins. Then the project home, when the environment names one, so a
    command works on the project from any folder. Otherwise the working directory.
    """
    return find_workspace(workspace or project_home() or Path.cwd())


def port_of(
    existing: AgentAnswers | McpAnswers | None, answers: WorkspaceAnswers, first: int
) -> int:
    """Return the port a service keeps, or the first one no service uses."""
    if existing is not None:
        return existing.port
    port = first
    while port in answers.used_ports():
        port += 1
    return port


def print_report(report: Report, root: Path) -> None:
    """Print which files a command created or updated."""
    for label, paths in (("created", report.created), ("updated", report.updated)):
        for path in paths:
            click.echo(f"  {label}  {path.relative_to(root).as_posix()}")
    if not report.created and not report.updated:
        click.echo("  nothing to do: every file is already as it would be written")
    for note in report.notes:
        click.echo(note, err=True)
