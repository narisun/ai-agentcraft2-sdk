"""The log a service keeps while it runs under ``ac2 run``, and how it is read back.

``ac2 run`` copies everything a service writes, its JSON log lines and whatever else reaches
the terminal, into one file per service under the state folder. ``ac2 logs`` and ``ac2 errors``
read it. Nothing here runs in a deployed service: it is a developer's tool, so the lines hold
the exact messages and stack traces the service wrote on a developer's machine.
"""

from __future__ import annotations

import json
import re
import threading
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any

__all__ = [
    "LOG_FOLDER",
    "Entry",
    "LogWriter",
    "errors_in",
    "format_entry",
    "format_error",
    "log_file",
    "parse_line",
    "tail_lines",
]

LOG_FOLDER = "logs"
"""The folder of the state folder that holds one log file per service."""

_MAX_BYTES = 5_000_000
_TAIL_BLOCK = 65_536
_ERROR_LEVELS = frozenset({"ERROR", "CRITICAL"})
_TRACE_START = "Traceback (most recent call last):"
_IDENTIFIER = re.compile(r"[A-Z][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*")
_SHOWN_FIELDS = ("request_id", "tool", "outcome", "duration_ms", "reason_code")
_FACT_LABELS = {
    "expected": "error_expected",
    "got": "error_actual",
    "fix": "error_fix",
    "detail": "error_detail",
    "note": "error_notes",
    "caused by": "error_causes",
    "your code": "error_in_your_code",
    "raised at": "error_at",
}
_FACT = re.compile(r"^ {2}(expected|got|fix|detail|note|caused by|your code|raised at): (.*)$")


def log_file(state: Path, service: str) -> Path:
    """Return the file the log of ``service`` is kept in, under the state folder ``state``."""
    return state / LOG_FOLDER / f"{service}.log"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


@dataclass(frozen=True, slots=True)
class Entry:
    """One line of a service's log, read back.

    Attributes:
        time: When it was written, ISO 8601.
        level: ``INFO``, ``ERROR`` and so on. ``OUTPUT`` for text that was not a log line.
        service: The service it came from.
        message: What it says.
        fields: Everything else on the line: the request ID, the error's facts and its stack.
    """

    time: str
    level: str
    service: str
    message: str
    fields: dict[str, Any] = field(default_factory=dict)

    @property
    def is_error(self) -> bool:
        """Whether this line reports a failure."""
        return self.level in _ERROR_LEVELS or "error_type" in self.fields


def parse_line(line: str, service: str) -> Entry:
    """Read one line of a log file; a line that is not JSON is output, shown as it is."""
    text = line.rstrip("\r\n")
    try:
        found = json.loads(text)
    except ValueError:
        found = None
    if not isinstance(found, dict):
        return Entry(_now(), "OUTPUT", service, text)
    fields = {
        key: value
        for key, value in found.items()
        if key not in {"time", "level", "message", "service"}
    }
    return Entry(
        str(found.get("time", "")),
        str(found.get("level", "INFO")).upper(),
        str(found.get("service") or service),
        str(found.get("message", "")),
        fields,
    )


def tail_lines(path: Path, count: int) -> list[str]:
    """Return the last ``count`` lines of a file, reading from its end."""
    if count <= 0 or not path.is_file():
        return []
    with path.open("rb") as handle:
        handle.seek(0, 2)
        position = handle.tell()
        data = b""
        while position > 0 and data.count(b"\n") <= count:
            step = min(_TAIL_BLOCK, position)
            position -= step
            handle.seek(position)
            data = handle.read(step) + data
    lines = data.decode("utf-8", errors="replace").splitlines()
    return lines[-count:]


def _clock(time: str) -> str:
    """Show the time of day of an ISO timestamp, in the developer's own time zone."""
    try:
        return datetime.fromisoformat(time).astimezone().strftime("%H:%M:%S")
    except ValueError:
        return time[-12:] if time else "--:--:--"


def format_entry(entry: Entry) -> str:
    """Return a log line for a person: the time, level, service and message, then key facts."""
    facts = " ".join(
        f"{key}={entry.fields[key]}" for key in _SHOWN_FIELDS if entry.fields.get(key) is not None
    )
    head = f"{_clock(entry.time)} {entry.level:<6} {entry.service}  {entry.message}"
    if entry.level == "OUTPUT":
        return head
    return f"{head}  {facts}" if facts else head


def errors_in(entries: Iterable[Entry]) -> list[Entry]:
    """Return the entries that report a failure, in the order they were written."""
    return [entry for entry in entries if entry.is_error]


def format_error(entry: Entry, *, trace: bool = True) -> str:
    """Return a failure for a person: what failed, what was expected, the fix, and the stack.

    Args:
        entry: The failure.
        trace: Whether to give the full stack trace, as the service wrote it.
    """
    fields = entry.fields
    kind = fields.get("error_type", "error")
    lines = [
        f"{_clock(entry.time)}  {entry.service}  {kind}",
        f"  {fields.get('error', entry.message)}",
    ]
    shown = str(fields.get("error", ""))
    if entry.message and shown not in entry.message:
        lines.append(f"  while: {entry.message}")
    for label, key in (
        ("expected", "error_expected"),
        ("found", "error_actual"),
        ("fix", "error_fix"),
        ("detail", "error_detail"),
    ):
        if fields.get(key):
            lines.append(f"  {label}: {fields[key]}")
    for note in fields.get("error_notes") or ():
        lines.append(f"  note: {note}")
    for cause in fields.get("error_causes") or ():
        lines.append(f"  caused by: {cause}")
    if fields.get("request_id"):
        lines.append(f"  request: {fields['request_id']}")
    where = fields.get("error_at")
    if where:
        lines.append(f"  raised at: {where[-1] if isinstance(where, list) else where}")
    if fields.get("error_in_your_code"):
        lines.append(f"  your code: {fields['error_in_your_code']}")
    if trace:
        stack = fields.get("error_traceback")
        if stack:
            lines.append("  stack trace:")
            lines += [f"    {row}" for row in str(stack).rstrip().splitlines()]
        elif isinstance(where, list) and where:
            lines.append("  stack (innermost last):")
            lines += [f"    {frame}" for frame in where]
    return "\n".join(lines)


def _explanation(group: list[str]) -> tuple[str, str]:
    """Return the error type and message of what a failed program wrote, as best they read."""
    unindented = [row for row in group if row.strip() and not row[:1].isspace()]
    last = unindented[-1] if unindented else group[-1]
    for part in last.split(": "):
        if _IDENTIFIER.fullmatch(part.strip()):
            kind = part.strip()
            message = last.split(f"{kind}: ", 1)[-1]
            return kind, message
    return "", last


def _stack_part(group: list[str]) -> str:
    """Return the traceback of what a failed program wrote, without the explanation after it.

    The explanation (the message again, then what was expected and the fix) is kept as fields
    of the entry, so a person reading the failure sees it once.
    """
    unindented = [index for index, row in enumerate(group) if row.strip() and not row[:1].isspace()]
    if len(unindented) > 1 and any(row.startswith(_TRACE_START) for row in group[: unindented[-1]]):
        return "\n".join(group[: unindented[-1]])
    return "\n".join(group)


def _facts(group: list[str]) -> dict[str, object]:
    """Read the indented lines under an explanation back into the fields of a failure."""
    found: dict[str, object] = {}
    listed: dict[str, list[str]] = {}
    for row in group:
        match = _FACT.match(row)
        if match is None:
            continue
        key, value = _FACT_LABELS[match.group(1)], match.group(2)
        if key in {"error_notes", "error_causes", "error_at"}:
            listed.setdefault(key, []).append(value)
        else:
            found[key] = value
    return {**found, **listed}


class LogWriter:
    """Copies what a service writes into its log file, one JSON object per line.

    The service's own log lines are kept as they are. Anything else it prints, a traceback
    above all, is gathered into one entry, so a crash is one entry and not forty.

    Args:
        path: The log file. Its folder is created, and a file over 5 MB is set aside as
            ``<name>.1`` first.
        service: The service's name, for entries that do not name it.
    """

    def __init__(self, path: Path, service: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_file() and path.stat().st_size > _MAX_BYTES:
            path.replace(path.with_name(path.name + ".1"))
        self._service = service
        self._file: IO[str] = path.open("a", encoding="utf-8", newline="\n")
        self._pending: list[str] = []
        self._failed = False  # an entry for a failure has been written during this run
        self._lock = threading.Lock()

    def note(self, message: str, *, level: str = "INFO", **fields: object) -> None:
        """Record something ``ac2`` itself did, such as starting the service."""
        with self._lock:
            self._write(
                {"time": _now(), "level": level, "service": "ac2", "message": message, **fields}
            )

    def line(self, text: str) -> None:
        """Record one line the service wrote."""
        stripped = text.rstrip("\r\n")
        with self._lock:
            if stripped.startswith("{"):
                try:
                    found = json.loads(stripped)
                except ValueError:
                    found = None
                if isinstance(found, dict):
                    self._flush("OUTPUT")
                    self._file.write(stripped + "\n")
                    self._file.flush()
                    return
            if stripped.startswith(_TRACE_START) and not self._in_a_trace():
                self._flush("OUTPUT")  # what came before a crash is not part of it
            self._pending.append(stripped)

    def finish(self, exit_code: int) -> None:
        """Record how the service ended, and close the file."""
        with self._lock:
            self._flush("ERROR" if exit_code else "OUTPUT")
            # A failure already written says why it stopped; without one, the exit code is all
            # there is to say, and it is a failure.
            level = "ERROR" if exit_code and not self._failed else "INFO"
            self._write(
                {
                    "time": _now(),
                    "level": level,
                    "service": "ac2",
                    "message": f"{self._service} stopped with exit code {exit_code}",
                    "exit_code": exit_code,
                    **(
                        {"error_type": "ExitCode", "error": f"exit code {exit_code}"}
                        if level == "ERROR"
                        else {}
                    ),
                }
            )
            self._file.close()

    def _in_a_trace(self) -> bool:
        return any(row.startswith(_TRACE_START) for row in self._pending)

    def _flush(self, ended: str) -> None:
        if not self._pending:
            return
        group, self._pending = self._pending, []
        text = "\n".join(group)
        failed = any(row.startswith(_TRACE_START) for row in group) or ended == "ERROR"
        record: dict[str, object] = {
            "time": _now(),
            "level": "ERROR" if failed else "OUTPUT",
            "service": self._service,
            "message": group[-1] if len(group) == 1 else text.splitlines()[-1],
        }
        if failed:
            kind, message = _explanation(group)
            record["error_type"] = kind or "Error"
            record["error"] = message
            record["message"] = f"{kind}: {message}" if kind else message
            record["error_traceback"] = _stack_part(group)
            record.update(_facts(group))
            self._failed = True
        else:
            record["message"] = text
        self._write(record)

    def _write(self, record: dict[str, object]) -> None:
        self._file.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        self._file.flush()
