"""Starting, stopping and reporting on a service, in the state folder of a workspace.

Every step checks before it acts and stops at the first problem with a message that says what
was expected, what was found and what to do. Nothing here prints a traceback for a mistake of
the person at the keyboard; those are :class:`~agentcraft2_cli.errors.CliError`.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
import warnings
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import click

from agentcraft2_cli.errors import CliError
from agentcraft2_cli.logbook import tail_lines
from agentcraft2_cli.services import packages
from agentcraft2_cli.services.control import (
    SPEC,
    active,
    read_status,
    request_stop,
)
from agentcraft2_cli.services.probe import is_ready, port_free
from agentcraft2_cli.services.registry import Paths, ServiceSpec, paths_of
from agentcraft2_cli.workspace import LibrarySource

__all__ = ["ServiceStatus", "ServiceTools", "launch_supervisor", "start", "status", "stop"]

STARTUP_SECONDS = 90
"""How long a service has to answer its health path once it is started."""
STOP_SECONDS = 30
"""How long a service has to stop when asked, before the supervisor forces it."""


def launch_supervisor(run: Path) -> None:
    """Start the supervisor of a run folder, detached, so it outlives this command."""
    flags = 0
    if sys.platform == "win32":
        # A hidden console of its own: the supervisor outlives the terminal that started it,
        # and the service it starts can be sent Ctrl+Break through that console.
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    # The supervisor is meant to outlive this command, so Python's warning that the child is
    # still running when its handle goes out of scope does not apply.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", ResourceWarning)
        subprocess.Popen(  # noqa: S603 - this Python and the supervisor module
            [sys.executable, "-m", "agentcraft2_cli.services.supervisor", str(run)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
            start_new_session=sys.platform != "win32",
            close_fds=True,
        )


@dataclass(frozen=True, slots=True)
class ServiceTools:
    """What the manager uses to reach outside the process; a test passes its own."""

    launch: Callable[[Path], None] = launch_supervisor
    pip: packages.PipRunner = packages.run_pip
    port_free: Callable[[int], bool] = port_free
    is_ready: Callable[[int, str], bool] = is_ready
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic
    check_paths: Callable[[ServiceSpec, Paths], None] = lambda spec, paths: check_path_length(
        spec, paths
    )


@dataclass(frozen=True, slots=True)
class ServiceStatus:
    """What is known about a service right now.

    Attributes:
        name: The service's name.
        title: What it is.
        state: ``not installed``, ``stopped``, ``starting``, ``running``, ``stopping`` or
            ``failed``.
        address: Where it listens, when it is up.
        detail: What else matters: a problem, or how it ended.
    """

    name: str
    title: str
    state: str
    address: str = ""
    detail: str = ""


def status(spec: ServiceSpec, state: Path, tools: ServiceTools | None = None) -> ServiceStatus:
    """Report what is true of a service: installed, running, healthy, or how it ended."""
    tools = tools or ServiceTools()
    paths = paths_of(state, spec.name)
    published = read_status(paths.run)
    alive = active(paths.run)
    recorded = str(published.get("state", ""))
    if alive and recorded in {"starting", "running", "stopping"}:
        if recorded == "running" and not tools.is_ready(spec.port, spec.health_path):
            return ServiceStatus(
                spec.name, spec.title, "failed", detail=f"nothing answers on port {spec.port}"
            )
        address = spec.url if recorded != "stopping" else ""
        return ServiceStatus(spec.name, spec.title, recorded, address)
    if alive:
        return ServiceStatus(spec.name, spec.title, "stopping")
    if recorded in {"starting", "running", "stopping"}:
        return ServiceStatus(
            spec.name,
            spec.title,
            "failed",
            detail=f"it ended without saying how; read {paths.log}",
        )
    if recorded == "failed":
        return ServiceStatus(
            spec.name,
            spec.title,
            "failed",
            detail=f"{published.get('detail', 'it failed')}; read {paths.log}",
        )
    if spec.requirements is not None:
        why = packages.problem(paths.base)
        if why is not None:
            installed = packages.site_packages(paths.base).exists()
            return ServiceStatus(
                spec.name,
                spec.title,
                "not installed",
                detail=why if installed else "'ac2 services start' installs it",
            )
    return ServiceStatus(spec.name, spec.title, "stopped")


WINDOWS_PATH_LIMIT = 259
"""The longest path Windows opens, unless an administrator has enabled long paths."""


def check_path_length(
    spec: ServiceSpec, paths: Paths, *, windows: bool = sys.platform == "win32"
) -> None:
    """Refuse to install packages whose paths Windows would not open.

    Raises:
        CliError: If the folder is so deep that the longest file of the service would pass the
            limit, with the length found and what to do.
    """
    if not windows or spec.requirements is None:
        return
    needed = len(str(packages.site_packages(paths.base))) + 1 + spec.longest_path
    if needed > WINDOWS_PATH_LIMIT:
        raise CliError(
            f"the path to the packages of {spec.name} is too long for Windows",
            expected=f"paths of at most {WINDOWS_PATH_LIMIT} characters",
            actual=(
                f"{needed} characters for the longest file below "
                f"{packages.site_packages(paths.base)}"
            ),
            fix=(
                "use a workspace in a shorter folder, such as C:\\work\\my-workspace; or ask "
                "your administrators to turn on long paths (the LongPathsEnabled setting)"
            ),
        )


def _log_tail(log: Path, lines: int = 12) -> str:
    shown = tail_lines(log, lines)
    return "\n".join(f"    {line}" for line in shown) if shown else "    (the log is empty)"


def start(
    spec: ServiceSpec,
    state: Path,
    library: LibrarySource,
    tools: ServiceTools | None = None,
    say: Callable[[str], None] = click.echo,
) -> None:
    """Install the service if needed, start it detached, and wait until it answers.

    Raises:
        CliError: At the first problem, with what to do about it.
    """
    tools = tools or ServiceTools()
    paths = paths_of(state, spec.name)
    current = status(spec, state, tools)
    if current.state in {"starting", "running"}:
        say(f"{spec.name} is already {current.state}: {spec.url}")
        return
    if current.state == "stopping":
        raise CliError(
            f"{spec.name} is still stopping",
            fix=f"wait a moment, then run 'ac2 services start {spec.name}' again",
        )
    if not tools.port_free(spec.port):
        raise CliError(
            f"port {spec.port} is already in use, so {spec.name} cannot listen on it",
            expected=f"port {spec.port} free on 127.0.0.1",
            actual="another program is listening on it",
            fix=(
                f"stop that program (it may be a {spec.name} started another way), "
                f"then run 'ac2 services start {spec.name}' again"
            ),
        )
    if spec.requirements is not None and packages.problem(paths.base) is not None:
        tools.check_paths(spec, paths)
        say(f"Installing the packages of {spec.name} into {packages.site_packages(paths.base)}")
        packages.install(paths.base, spec.requirements(library), pip=tools.pip)
        say("  installed")
    paths.data.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(paths.run, ignore_errors=True)
    paths.run.mkdir(parents=True)
    (paths.run / SPEC).write_text(
        json.dumps(
            {
                "name": spec.name,
                "command": spec.command(paths),
                "cwd": str(paths.base),
                "port": spec.port,
                "health_path": spec.health_path,
                "log": str(paths.log),
                "startup_timeout": STARTUP_SECONDS,
                "stop_timeout": STOP_SECONDS,
            }
        ),
        encoding="utf-8",
    )
    say(f"Starting {spec.name}; log: {paths.log}")
    tools.launch(paths.run)
    _wait_until_up(spec, paths, tools)
    say(f"{spec.name} is ready: {spec.url}")


def _wait_until_up(spec: ServiceSpec, paths: Paths, tools: ServiceTools) -> None:
    """Wait for the supervisor to report that the service answers, or that it failed."""
    start_time = tools.clock()
    limit = STARTUP_SECONDS + 15
    while True:
        published = read_status(paths.run)
        state = str(published.get("state", ""))
        if state == "running":
            return
        if state in {"failed", "stopped"}:
            raise CliError(
                f"{spec.name} did not start: {published.get('detail', 'it ended')}",
                fix=f"read the log, fix what it names, then run 'ac2 services start {spec.name}'"
                f"\n  the end of {paths.log}:\n{_log_tail(paths.log)}",
            )
        waited = tools.clock() - start_time
        if not state and waited > 10 and not active(paths.run):
            raise CliError(
                f"the supervisor of {spec.name} did not start",
                fix=f"run 'python -m agentcraft2_cli.services.supervisor {paths.run}' to see why",
            )
        if waited > limit:
            raise CliError(
                f"{spec.name} was not ready after {limit} s",
                fix=f"read {paths.log}, then run 'ac2 services stop {spec.name}' and start again",
            )
        tools.sleep(0.2)


def stop(
    spec: ServiceSpec,
    state: Path,
    tools: ServiceTools | None = None,
    say: Callable[[str], None] = click.echo,
) -> None:
    """Ask a running service to stop, and wait until it has.

    Raises:
        CliError: If it did not stop, or ended badly.
    """
    tools = tools or ServiceTools()
    paths = paths_of(state, spec.name)
    if not active(paths.run):
        say(f"{spec.name} is not running")
        return
    request_stop(paths.run)
    say(f"Stopping {spec.name}...")
    start_time = tools.clock()
    limit = STOP_SECONDS + 15
    while active(paths.run):
        if tools.clock() - start_time > limit:
            raise CliError(
                f"{spec.name} is still running {limit} s after it was asked to stop",
                fix=f"read {paths.log}; close the program that holds port {spec.port}",
            )
        tools.sleep(0.2)
    published = read_status(paths.run)
    if published.get("state") != "stopped":
        raise CliError(
            f"{spec.name} stopped badly: {published.get('detail', 'no reason was recorded')}",
            fix=f"read {paths.log}",
        )
    say(f"{spec.name} stopped")
