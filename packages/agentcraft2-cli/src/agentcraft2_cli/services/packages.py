"""The package folder of a service: installing it with pip, and the command that runs from it.

A service's packages go in a folder of their own, under the service's folder in the state
folder, because Agent Lab and Phoenix need packages that conflict with each other and with the
SDK's. They are installed with this Python and its pip, by ``pip --target``, and run with this
Python, started through :mod:`agentcraft2_cli.services.bootstrap`. No environment is created
and no other Python or pip is used.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from uuid import uuid4

from agentcraft2_cli.errors import CliError
from agentcraft2_cli.services import bootstrap

__all__ = ["PipRunner", "command", "install", "problem", "site_packages"]

PipRunner = Callable[[Sequence[str], Path], int]
"""Runs pip with these arguments in a folder, and returns its exit code."""

BOOTSTRAP = Path(bootstrap.__file__).resolve()


def run_pip(arguments: Sequence[str], folder: Path) -> int:
    """Run pip with the Python that runs this command, showing its output."""
    return subprocess.run(  # noqa: S603 - the current interpreter and explicit arguments
        [sys.executable, "-m", "pip", "--disable-pip-version-check", *arguments],
        cwd=folder,
        check=False,
    ).returncode


def site_packages(base: Path) -> Path:
    """Return the folder a service's packages are installed in, below the service's ``base``.

    The name is short on purpose: Windows refuses a path of 260 characters or more unless an
    administrator has enabled long paths, and the packages of a service go deep.
    """
    return base / "lib"


def problem(base: Path) -> str | None:
    """Say what is wrong with a service's packages for this Python, or ``None`` if usable."""
    return bootstrap.problem_with(site_packages(base))


def command(base: Path, module: str, *arguments: str) -> list[str]:
    """Return the command that runs ``module`` from a service's packages, with this Python."""
    return [
        sys.executable,
        "-I",
        "-S",
        "-u",
        # Isolated mode ignores PYTHONUTF8, and the logs of a service are read as UTF-8.
        "-X",
        "utf8",
        str(BOOTSTRAP),
        "--packages",
        str(site_packages(base)),
        "-m",
        module,
        *arguments,
    ]


def install(
    base: Path,
    requirements: Sequence[str],
    *,
    pip: PipRunner = run_pip,
    quiet: bool = False,
) -> None:
    """Build and check a new package set, and replace the old one only when it is good.

    Args:
        base: The service's folder; the packages go in its ``packages`` folder.
        requirements: The pip arguments that name what to install, such as ``-e path[extra]``.
        pip: Runs pip; a test passes its own.
        quiet: Whether pip prints only problems, not every package it collects.

    Raises:
        CliError: If pip fails or the installed set has broken requirements. The earlier
            packages, if any, are kept.
    """
    destination = site_packages(base)
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = destination.with_name(f".{destination.name}-{uuid4().hex}")
    previous = destination.with_name(f".{destination.name}-previous-{uuid4().hex}")
    try:
        flags = ["-q"] if quiet else []
        code = pip(["install", *flags, "--no-user", "--target", str(staging), *requirements], base)
        if code != 0:
            raise CliError(
                f"pip could not install the packages (exit code {code}); its output is above",
                fix=(
                    "fix the problem pip names, such as a package missing from your index, "
                    "then run the command again"
                ),
            )
        (staging / bootstrap.MANIFEST).write_text(
            json.dumps(bootstrap.runtime_identity()), encoding="utf-8"
        )
        _check(staging)
        if destination.exists():
            destination.rename(previous)
        try:
            staging.rename(destination)
        except OSError:
            if previous.exists():
                previous.rename(destination)
            raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    shutil.rmtree(previous, ignore_errors=True)


def _check(staging: Path) -> None:
    """Check that what was installed has no broken requirements, with the Python's own pip."""
    spec = importlib.util.find_spec("pip")
    if spec is None or spec.origin is None:
        raise CliError(
            "this Python has no pip module to check the installed packages with",
            fix="ask your package administrators for a Python that includes pip",
        )
    checked = subprocess.run(  # noqa: S603 - the current interpreter and explicit arguments
        [
            sys.executable,
            "-I",
            "-S",
            str(BOOTSTRAP),
            "--packages",
            str(staging),
            "--check",
            spec.origin,
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if checked.returncode != 0:
        raise CliError(
            "the installed packages have broken requirements: "
            + (checked.stdout + checked.stderr).strip(),
            fix="install what pip names into the same index, then run the command again",
        )
