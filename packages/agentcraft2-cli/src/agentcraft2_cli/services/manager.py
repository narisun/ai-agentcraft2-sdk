"""Starting, stopping and reporting on a service installed with the SDK.

A service is installed once, with the SDK, in the SDK's own folder; this module only runs it.
What it writes, and its run state, go to the workspace's state folder. Every step checks before
it acts and stops at the first problem with a message that says what was expected, what was
found and what to do. A mistake of the person at the keyboard is a
:class:`~agentcraft2_cli.errors.CliError`, never a traceback.
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
from datetime import UTC, datetime
from pathlib import Path

import click

from agentcraft2_cli import __version__
from agentcraft2_cli.errors import CliError
from agentcraft2_cli.logbook import tail_lines
from agentcraft2_cli.services.control import SPEC, active, read_status, request_stop
from agentcraft2_cli.services.installed import installed_problem
from agentcraft2_cli.services.probe import is_ready, port_free
from agentcraft2_cli.services.registry import Paths, ServiceSpec, paths_of
from agentcraft2_core.config import sdk_variable
from agentcraft2_core.config.local_disk import local_disk_problem

__all__ = ["ServiceStatus", "ServiceTools", "launch_supervisor", "start", "status", "stop"]

STARTUP_SECONDS = 90
"""How long a service has to answer its health path once it is started."""
STOP_SECONDS = 30
"""How long a service has to stop when asked, before the supervisor forces it."""
RUNNING_FOLDER = "running"
"""The folder in the SDK's folder that says which workspace a service was started from."""


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
    port_free: Callable[[int], bool] = port_free
    is_ready: Callable[[int, str], bool] = is_ready
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic
    disk_problem: Callable[[Path], str | None] = local_disk_problem
    version: str = __version__


@dataclass(frozen=True, slots=True)
class ServiceStatus:
    """What is known about a service right now.

    Attributes:
        name: The service's name.
        title: What it is.
        state: ``not installed``, ``stopped``, ``starting``, ``running``, ``running elsewhere``
            (started from another workspace), ``stopping`` or ``failed``.
        address: Where it listens, when it is up.
        detail: What else matters: a problem, or how it ended.
    """

    name: str
    title: str
    state: str
    address: str = ""
    detail: str = ""


def _pointer(sdk: Path, name: str) -> Path:
    return sdk / RUNNING_FOLDER / f"{name}.json"


def _started_from(sdk: Path, name: str) -> Path | None:
    """Return the state folder of the workspace that started the service, if it is running."""
    try:
        recorded = json.loads(_pointer(sdk, name).read_text("utf-8"))
        other = Path(str(recorded["state"]))
    except (OSError, ValueError, KeyError):
        return None
    return other if active(paths_of(other, sdk, name).run) else None


def status(
    spec: ServiceSpec, state: Path, sdk: Path, tools: ServiceTools | None = None
) -> ServiceStatus:
    """Report what is true of a service: installed, running, healthy, or how it ended."""
    tools = tools or ServiceTools()
    paths = paths_of(state, sdk, spec.name)
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
    why = installed_problem(spec, paths.installed, tools.version)
    if why is not None:
        return ServiceStatus(spec.name, spec.title, "not installed", detail=why)
    elsewhere = _started_from(sdk, spec.name)
    if elsewhere is not None and elsewhere != state:
        return ServiceStatus(
            spec.name,
            spec.title,
            "running elsewhere",
            spec.url,
            f"started from {elsewhere.parent}; 'ac2 services stop {spec.name}' ends it",
        )
    return ServiceStatus(spec.name, spec.title, "stopped")


def _log_tail(log: Path, lines: int = 12) -> str:
    shown = tail_lines(log, lines)
    return "\n".join(f"    {line}" for line in shown) if shown else "    (the log is empty)"


def _not_installed(spec: ServiceSpec, sdk: Path, why: str) -> CliError:
    return CliError(
        f"{spec.name} is not installed",
        expected=f"{spec.name} installed with the SDK, in {sdk}",
        actual=why,
        fix=(
            "install the SDK with its tools: python tools/install_sdk.py, in your checkout of "
            "the SDK; or, from a package index: python -m agentcraft2_cli.sdk install "
            "--version SPECIFIER. 'ac2' starts and stops the tools and never installs them"
        ),
    )


def start(
    spec: ServiceSpec,
    state: Path,
    sdk: Path,
    tools: ServiceTools | None = None,
    say: Callable[[str], None] = click.echo,
    workspace: Path | None = None,
) -> None:
    """Start a service installed with the SDK, in the background, and wait until it answers.

    Args:
        spec: The service.
        state: The workspace's state folder.
        sdk: The SDK's own folder.
        tools: What reaches outside the process; a test passes its own.
        say: Where progress goes.
        workspace: The workspace root, for a service that is configured from the workspace.

    Raises:
        CliError: At the first problem, with what to do about it.
    """
    tools = tools or ServiceTools()
    paths = paths_of(state, sdk, spec.name)
    current = status(spec, state, sdk, tools)
    if current.state in {"starting", "running"}:
        say(f"{spec.name} is already {current.state}: {spec.url}")
        return
    if current.state == "stopping":
        raise CliError(
            f"{spec.name} is still stopping",
            fix=f"wait a moment, then run 'ac2 services start {spec.name}' again",
        )
    if current.state == "running elsewhere":
        raise CliError(
            f"{spec.name} is already running, for another workspace",
            expected=f"{spec.name} free to start here: it is one program on port {spec.port}",
            actual=current.detail,
            fix=f"run 'ac2 services stop {spec.name}' to end it, then start it here",
        )
    why = installed_problem(spec, paths.installed, tools.version)
    if why is not None:
        raise _not_installed(spec, sdk, why)
    for folder in (paths.installed, paths.data):
        reason = tools.disk_problem(folder)
        if reason is not None:
            raise CliError(
                f"{spec.name} must run from and write to a local disk",
                expected="a folder on a disk of this machine: not a network drive, not synced, "
                "not roamed with the profile",
                actual=reason,
                fix=(
                    "move the workspace to a local folder"
                    if folder == paths.data
                    else f"set {sdk_variable()} to a local folder and install the SDK again"
                ),
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
    paths.data.mkdir(parents=True, exist_ok=True)
    if spec.prepare is not None and workspace is not None:
        for note in spec.prepare(workspace, paths):
            say(note)
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
    pointer = _pointer(sdk, spec.name)
    pointer.parent.mkdir(parents=True, exist_ok=True)
    pointer.write_text(
        json.dumps({"state": str(state), "time": datetime.now(UTC).isoformat(timespec="seconds")}),
        encoding="utf-8",
    )
    say(f"{spec.name} is ready: {spec.url}")
    if spec.after_start is not None and workspace is not None:
        for line in spec.after_start(workspace, paths):
            say(line)


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
    sdk: Path,
    tools: ServiceTools | None = None,
    say: Callable[[str], None] = click.echo,
) -> None:
    """Ask a running service to stop, and wait until it has.

    A service started from another workspace is stopped from here too: there is one of it,
    and a person who asks for it to stop wants it stopped.

    Raises:
        CliError: If it did not stop, or ended badly.
    """
    tools = tools or ServiceTools()
    owner = state
    if not active(paths_of(state, sdk, spec.name).run):
        elsewhere = _started_from(sdk, spec.name)
        if elsewhere is None:
            say(f"{spec.name} is not running")
            return
        owner = elsewhere
        say(f"{spec.name} was started from {owner.parent}")
    paths = paths_of(owner, sdk, spec.name)
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
    _pointer(sdk, spec.name).unlink(missing_ok=True)
    published = read_status(paths.run)
    if published.get("state") != "stopped":
        raise CliError(
            f"{spec.name} stopped badly: {published.get('detail', 'no reason was recorded')}",
            fix=f"read {paths.log}",
        )
    say(f"{spec.name} stopped")
