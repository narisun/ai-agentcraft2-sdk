"""Local JSON-lines audit storage with per-record fsync enabled by default."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from agentcraft2_core.contracts import (
    AuditOptions,
    AuditRecord,
    ConfigurationError,
    IntegrityError,
    describe,
)

__all__ = ["JsonlAuditOptions", "JsonlAuditSink"]


class JsonlAuditOptions(AuditOptions):
    """Options for the JSONL audit sink.

    Attributes:
        path: The file to append to. Parent directories are created.
        fsync: Force each record to disk before ``write`` returns.
    """

    path: Path = Path(".agentcraft2/audit.jsonl")
    fsync: bool = True


class JsonlAuditSink:
    """Appends audit records to a file, one JSON object per line.

    Each record uses one ``os.write`` on an append-mode descriptor; a short
    write is treated as a failure. With ``fsync`` on, ``write`` also waits for
    the operating system to flush the record before returning. Disabling it
    trades crash durability for speed.

    This is the reference adapter for local development. It has no delivery
    guarantee beyond the local disk.
    """

    def __init__(self, options: JsonlAuditOptions) -> None:
        self._path = options.path
        self._fsync = options.fsync
        self._fd: int | None = None
        self._lock = asyncio.Lock()
        # Writes running in a worker thread. A cancelled caller stops waiting for its write,
        # but the thread goes on; closing the file under it would lose the record.
        self._writing: set[asyncio.Future[None]] = set()

    @property
    def path(self) -> Path:
        """The file records are appended to."""
        return self._path

    async def validate(self) -> None:
        """Check that the file can be opened for appending."""
        try:
            await asyncio.to_thread(self._open)
        except OSError as exc:
            raise ConfigurationError(
                f"the audit file cannot be opened: {self._path} ({describe(exc)})"
            ) from exc

    async def write(self, record: AuditRecord) -> None:
        """Append ``record``, fsync if configured, or raise ``IntegrityError``."""
        line = json.dumps(record.to_dict(), separators=(",", ":"), ensure_ascii=False) + "\n"
        async with self._lock:
            writing = asyncio.get_running_loop().run_in_executor(
                None, self._append, line.encode("utf-8")
            )
            self._writing.add(writing)
            writing.add_done_callback(self._writing.discard)
            # A failure of an abandoned write is not left unretrieved; its caller, if still
            # waiting, gets it from the await below.
            writing.add_done_callback(lambda done: done.cancelled() or done.exception())
            try:
                # Shielded: if this caller is cancelled the write still finishes, and
                # ``aclose`` waits for it.
                await asyncio.shield(writing)
            except OSError as exc:
                raise IntegrityError(
                    f"the audit record could not be written to {self._path}",
                    actual=describe(exc),
                ) from exc

    async def aclose(self) -> None:
        """Close the file. Safe to call more than once."""
        async with self._lock:
            if self._writing:
                # Each is already reported to its own caller; here only the wait matters.
                await asyncio.gather(*self._writing, return_exceptions=True)
            fd, self._fd = self._fd, None
            if fd is not None:
                await asyncio.to_thread(os.close, fd)

    def _open(self) -> int:
        if self._fd is None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            # O_BINARY: Windows would otherwise write the record's newline as CR LF, and
            # the bytes on disk would differ from the bytes the record is hashed from.
            flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_BINARY", 0)
            self._fd = os.open(self._path, flags, 0o600)
        return self._fd

    def _append(self, data: bytes) -> None:
        fd = self._open()
        written = os.write(fd, data)
        if written != len(data):
            raise OSError(f"short write: {written} of {len(data)} bytes")
        if self._fsync:
            os.fsync(fd)
