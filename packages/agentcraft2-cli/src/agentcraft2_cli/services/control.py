"""The files by which ``ac2 services`` and a service's supervisor talk, with nothing else.

Each started service has a supervisor: a small detached process that runs the service and
holds a lock on a file for as long as it lives. An operating-system lock proves that the
supervisor is alive, and, unlike a stored process ID, cannot be mistaken for an unrelated
process that was given the ID later. A stop is a request file the supervisor sees; no command
signals a process it only knows by number.
"""

from __future__ import annotations

import errno
import json
import sys
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO

__all__ = [
    "active",
    "hold_lock",
    "read_status",
    "request_stop",
    "stop_requested",
    "write_status",
]

LOCK = "supervisor.lock"
STATUS = "status.json"
STOP = "stop.request"
SPEC = "spec.json"
_VERSION = 1


def _try_lock(handle: BinaryIO) -> bool:
    handle.seek(0)
    try:
        if sys.platform == "win32":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as error:
        if error.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
            return False
        raise
    return True


def active(run: Path) -> bool:
    """Whether a supervisor holds the lock of this run folder, which means it is alive."""
    try:
        with (run / LOCK).open("r+b") as handle:
            return not _try_lock(handle)
    except FileNotFoundError:
        return False


@contextmanager
def hold_lock(run: Path) -> Iterator[None]:
    """Hold the lock of a run folder until the block ends; the system frees it after a crash."""
    # Opened for appending, so that a second supervisor cannot cut the file the first holds.
    with (run / LOCK).open("a+b") as handle:
        if not _try_lock(handle):
            raise RuntimeError(f"another supervisor already runs in {run}")
        handle.seek(0)
        handle.write(b"0")
        handle.flush()
        yield


def write_status(run: Path, state: str, **fields: Any) -> None:
    """Publish the state of the run atomically, so a reader never sees half of it."""
    pending = run / "status.tmp"
    pending.write_text(
        json.dumps(
            {
                "version": _VERSION,
                "state": state,
                "time": datetime.now(UTC).isoformat(timespec="seconds"),
                **fields,
            }
        ),
        encoding="utf-8",
    )
    pending.replace(run / STATUS)


def read_status(run: Path) -> Mapping[str, Any]:
    """Read the published state, or an empty mapping when nothing was published yet."""
    try:
        value = json.loads((run / STATUS).read_text("utf-8"))
    except (FileNotFoundError, ValueError):
        return {}
    return value if isinstance(value, dict) and value.get("version") == _VERSION else {}


def request_stop(run: Path) -> None:
    """Ask the supervisor of the run to stop its service."""
    (run / STOP).touch()


def stop_requested(run: Path) -> bool:
    """Whether a stop was asked for in this run."""
    return (run / STOP).is_file()
