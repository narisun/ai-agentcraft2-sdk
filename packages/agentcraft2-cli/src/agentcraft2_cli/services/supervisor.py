"""The detached process that runs one service, reports on it, and stops it when asked.

``ac2 services start`` starts this with the run folder of the service as its one argument and
returns. The supervisor reads ``spec.json`` there, holds the lock of the folder, starts the
service, publishes its state in ``status.json`` and, when a ``stop.request`` file appears,
stops the service the way a person would with Ctrl+C: it asks, waits, and only then forces.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from agentcraft2_cli.services.control import (
    SPEC,
    hold_lock,
    stop_requested,
    write_status,
)
from agentcraft2_cli.services.probe import is_ready

__all__ = ["main", "supervise"]

_POLL_SECONDS = 0.2
_INTERRUPT_EXITS = {130, 3221225786, -1073741510} if sys.platform == "win32" else {130, -2}
"""The exits a service makes when it ends because it was asked to, and not because it failed."""


def _start(spec: dict[str, Any]) -> subprocess.Popen[bytes]:
    log = Path(spec["log"])
    log.parent.mkdir(parents=True, exist_ok=True)
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
    with log.open("ab") as output:
        return subprocess.Popen(  # noqa: S603 - the explicit command of the service
            spec["command"],
            cwd=spec["cwd"],
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.STDOUT,
            creationflags=flags,
            start_new_session=sys.platform != "win32",
        )


def _stop(child: subprocess.Popen[bytes], seconds: float) -> tuple[bool, int | None]:
    """Ask the service to stop and wait; force it after ``seconds``. Return (clean, exit code)."""
    if child.poll() is not None:
        return child.returncode in {0, *_INTERRUPT_EXITS}, child.returncode
    try:
        child.send_signal(signal.CTRL_BREAK_EVENT if sys.platform == "win32" else signal.SIGINT)
        code = child.wait(timeout=seconds)
    except (OSError, subprocess.TimeoutExpired):
        child.kill()
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            return False, None
        return False, child.returncode
    return code in {0, *_INTERRUPT_EXITS}, code


def supervise(run: Path, spec: dict[str, Any]) -> int:
    """Run the service of ``spec`` until it ends or is asked to stop. Return the exit code."""
    port = int(spec["port"])
    base = {"port": port, "supervisor": os.getpid()}
    write_status(run, "starting", **base)
    try:
        child = _start(spec)
    except OSError as problem:
        write_status(run, "failed", detail=f"the service could not be started: {problem}", **base)
        return 1
    base["child"] = child.pid
    write_status(run, "starting", **base)
    deadline = time.monotonic() + float(spec["startup_timeout"])
    ready = False
    while True:
        if stop_requested(run):
            break
        code = child.poll()
        if code is not None:
            detail = f"the service ended with exit code {code} before it was ready"
            write_status(run, "failed", detail=detail, exit_code=code, **base)
            return 1
        if not ready and is_ready(port, spec["health_path"]):
            ready = True
            write_status(run, "running", **base)
        if not ready and time.monotonic() >= deadline:
            _stop(child, 5)
            detail = f"the service did not answer on port {port} within {spec['startup_timeout']} s"
            write_status(run, "failed", detail=detail, **base)
            return 1
        time.sleep(_POLL_SECONDS)
        if ready and child.poll() is not None:
            code = child.returncode
            state = "stopped" if code in {0, *_INTERRUPT_EXITS} else "failed"
            detail = f"the service ended with exit code {code}"
            write_status(run, state, detail=detail, exit_code=code, **base)
            return 0 if state == "stopped" else 1
    write_status(run, "stopping", **base)
    clean, code = _stop(child, float(spec["stop_timeout"]))
    if clean:
        write_status(run, "stopped", exit_code=code, **base)
        return 0
    detail = f"the service did not stop cleanly (exit code {code}); it was forced to end"
    write_status(run, "failed", detail=detail, exit_code=code, **base)
    return 1


def main(argv: list[str] | None = None) -> int:
    """Read the spec of the run folder in ``argv[0]`` and supervise it."""
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) != 1:
        sys.stderr.write("usage: python -m agentcraft2_cli.services.supervisor RUN_FOLDER\n")
        return 2
    run = Path(arguments[0])
    spec = json.loads((run / SPEC).read_text("utf-8"))
    try:
        with hold_lock(run):
            return supervise(run, spec)
    except RuntimeError as problem:
        sys.stderr.write(f"{problem}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
