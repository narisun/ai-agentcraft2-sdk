"""Install the tools the SDK provides for developers, into the SDK's own folder.

``tools/install_sdk.py`` runs this after it has installed the SDK, and a developer who got the
SDK from a package index runs it by hand::

    python -m agentcraft2_cli.sdk install --version ">=0.1,<0.2"
    python -m agentcraft2_cli.sdk check

It is not an ``ac2`` command: ``ac2`` starts and stops the services, and never installs them.
Phoenix, and Agent Lab after it, go in a folder of their own each, below the SDK's own folder,
because their packages conflict with each other and with the SDK's. Everything they write,
such as traces and conversations, goes to the workspace that started them.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path

import click

from agentcraft2_cli import __version__
from agentcraft2_cli.commands.common import library_source
from agentcraft2_cli.errors import CliError, report_error
from agentcraft2_cli.services import packages
from agentcraft2_cli.services.installed import (
    check_path_length,
    fingerprint,
    installed_problem,
    outdated,
)
from agentcraft2_cli.services.registry import INSTALLED, SERVICES, ServiceSpec, installed_dir
from agentcraft2_cli.workspace import LibrarySource
from agentcraft2_core.config import sdk_path, sdk_variable
from agentcraft2_core.config.local_disk import free_bytes, local_disk_problem

__all__ = ["check_services", "install_services", "main"]

_MARGIN = 1.5
"""How much more room than the packages take is wanted: the old copy stays until the new works."""


def install_services(
    sdk: Path,
    library: LibrarySource,
    specs: Sequence[ServiceSpec] | None = None,
    *,
    pip: packages.PipRunner = packages.run_pip,
    say: Callable[[str], None] = click.echo,
    disk_problem: Callable[[Path], str | None] = local_disk_problem,
    free_space: Callable[[Path], int] = free_bytes,
    check_paths: Callable[[ServiceSpec, Path], None] = check_path_length,
    version: str = __version__,
    quiet: bool = False,
    force: bool = False,
) -> None:
    """Install the services into the SDK's folder, checking everything before the first change.

    A service that is already installed, for this SDK and this Python, from the same requirements,
    is left alone: its packages are large and rarely change, and the code of a checkout is
    installed editable. A service that is missing, damaged or out of date is installed.

    Args:
        sdk: The SDK's own folder.
        library: Where the SDK is taken from: a checkout, or a package index.
        specs: The services to install; by default every one that has packages.
        pip: Runs pip; a test passes its own.
        say: Where progress goes.
        disk_problem: Says why a folder is not on a local disk; a test passes its own.
        free_space: Says how much room a folder's disk has.
        check_paths: Refuses a folder whose files Windows would not open; a test passes its own.
        version: The SDK's version, recorded with each installed service.
        quiet: Whether pip prints only problems, not every package it collects.
        force: Whether to install every service even if it is current.

    Raises:
        CliError: At the first problem, with what to do about it. Nothing is changed by a
            problem found before the first install, and a service whose install fails keeps
            its earlier packages.
    """
    wanted = [spec for spec in (specs or list(SERVICES.values())) if spec.requirements]
    chosen = []
    for spec in wanted:
        target = installed_dir(sdk, spec.name)
        why = None if force else outdated(spec, target, library, version)
        if not force and why is None:
            say(
                f"{spec.name} is installed and current in {target}; skipped. "
                "To install it again, use --force-services of tools/install_sdk.py"
            )
            continue
        chosen.append(spec)
        if not force and target.exists():
            say(f"{spec.name} must be installed again: {why}")
    if not chosen:
        return
    reason = disk_problem(sdk)
    if reason is not None:
        raise CliError(
            "the folder for the SDK's tools is not on a local disk",
            expected="a folder on a disk of this machine: not a network drive, not synced, "
            "not roamed with the profile",
            actual=reason,
            fix=f"set {sdk_variable()} to a folder on a local disk, such as C:\\ac2-sdk",
        )
    for spec in chosen:
        check_paths(spec, installed_dir(sdk, spec.name))
    needed = int(sum(spec.megabytes for spec in chosen) * _MARGIN) * 1024 * 1024
    if free_space(sdk) < needed:
        raise CliError(
            f"there is not enough room for the SDK's tools in {sdk}",
            expected=f"about {needed // 1024 // 1024} MB free",
            actual=f"{free_space(sdk) // 1024 // 1024} MB free",
            fix=f"free some space, or set {sdk_variable()} to a folder on a larger local disk",
        )
    for spec in chosen:
        target = installed_dir(sdk, spec.name)
        say(f"Installing {spec.name} into {target}")
        try:
            packages.install(target, _requirements(spec, library), pip=pip, quiet=quiet)
        except OSError as problem:
            raise CliError(
                f"the packages of {spec.name} could not be replaced ({problem})",
                expected=f"nothing to use the files below {target}",
                actual="a file or folder there is in use, usually by the running service",
                fix="stop it with 'ac2 services stop', then run the installer again. "
                "The earlier packages were kept",
            ) from problem
        (target / INSTALLED).write_text(
            json.dumps(
                {
                    "service": spec.name,
                    "sdk_version": version,
                    "fingerprint": fingerprint(spec, library),
                    "installed": datetime.now(UTC).isoformat(timespec="seconds"),
                }
            ),
            encoding="utf-8",
        )
        say(f"  {spec.name} installed")


def _requirements(spec: ServiceSpec, library: LibrarySource) -> list[str]:
    assert spec.requirements is not None  # noqa: S101 - narrowed by the caller
    return spec.requirements(library)


def check_services(sdk: Path, say: Callable[[str], None] = click.echo) -> None:
    """Say whether each service is installed and usable.

    Raises:
        CliError: If one is not, naming it and what is wrong.
    """
    problems = []
    for spec in SERVICES.values():
        installed = installed_dir(sdk, spec.name)
        why = installed_problem(spec, installed)
        if why is None:
            say(f"{spec.name}: installed in {installed}")
        else:
            problems.append(f"{spec.name}: {why}")
    if problems:
        raise CliError(
            "the SDK's tools are not all installed",
            expected=f"every service installed in {sdk}",
            actual="; ".join(problems),
            fix="run the SDK installer again: python tools/install_sdk.py",
        )


def _library(checkout: Path | None, version: str | None) -> LibrarySource:
    try:
        return library_source(checkout, version)
    except CliError:
        raise CliError(
            "this command cannot tell where to install the SDK's tools from",
            expected="a run from a checkout of the SDK, or one of the options below",
            fix="give --checkout DIR (a checkout of the SDK) or --version SPECIFIER "
            "(a version from your package index, such as '>=0.1,<0.2')",
        ) from None


def main(argv: Sequence[str] | None = None) -> int:
    """Run ``install`` or ``check``. Return the exit code."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else None)
    commands = parser.add_subparsers(dest="command", required=True)
    install = commands.add_parser("install", help="install Phoenix, and Agent Lab, for the SDK")
    where = install.add_mutually_exclusive_group()
    where.add_argument("--checkout", type=Path, help="install from this checkout of the SDK")
    where.add_argument("--version", help="install this version from the package index")
    install.add_argument(
        "--verbose", action="store_true", help="show everything pip prints, not only problems"
    )
    install.add_argument(
        "--force",
        action="store_true",
        help="install every service again, even one that is installed and current",
    )
    commands.add_parser("check", help="say whether the services are installed and usable")
    arguments = parser.parse_args(argv)
    try:
        sdk = sdk_path()
        if arguments.command == "install":
            install_services(
                sdk,
                _library(arguments.checkout, arguments.version),
                quiet=not arguments.verbose,
                force=arguments.force,
            )
        else:
            check_services(sdk)
    except CliError as problem:
        return report_error(problem)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
