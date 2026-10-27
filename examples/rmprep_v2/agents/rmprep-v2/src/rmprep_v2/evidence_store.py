"""Original tool results kept outside the graph, exact and immutable, addressed per scope.

A checkpoint holds references, not data. The store keeps what a governed tool returned,
byte for byte, so an answer can later cite the exact record it was shown. A source never
changes: writing the same content again is a no-op, and writing different content under
the same ID is an error. A scope is the opaque key of one tenant, subject, application and
thread (``RequestContext.scope.key(thread_id)``); no call can read across scopes.

SQLite is the development implementation. The protocol is small so a deployed relational
database can replace it behind the same contract tests.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Any, Protocol, Self

import aiosqlite

__all__ = [
    "EvidenceConflict",
    "EvidenceMissing",
    "EvidenceStore",
    "SqliteEvidenceStore",
    "StoredSource",
    "canonical_json",
    "source_id",
]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    scope TEXT NOT NULL,
    artifact_id TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    tool TEXT NOT NULL,
    arguments TEXT NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY (scope, artifact_id)
);
CREATE TABLE IF NOT EXISTS manifests (
    scope TEXT NOT NULL,
    manifest_id TEXT NOT NULL,
    memo_id TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    body TEXT NOT NULL,
    PRIMARY KEY (scope, manifest_id),
    UNIQUE (scope, memo_id)
);
"""


class EvidenceConflict(Exception):
    """A write would change something that is already stored."""


class EvidenceMissing(Exception):
    """A manifest names a source that is not stored in its scope."""


def canonical_json(value: object) -> str:
    """The one text form of a JSON value: sorted keys, no spaces, no escaping of text."""
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    )


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def source_id(tool: str, arguments: Mapping[str, Any], payload: Mapping[str, Any]) -> str:
    """A stable ID for one tool result: the same request and result always give the same ID.

    Replaying a turn therefore finds its source again instead of storing a second copy,
    and a result that differs gets a different ID rather than overwriting the first.
    """
    body = canonical_json({"tool": tool, "arguments": arguments, "payload": payload})
    return "src-" + _digest(body)[:24]


@dataclass(frozen=True, slots=True)
class StoredSource:
    """One stored tool result and what produced it."""

    artifact_id: str
    sha256: str
    tool: str
    arguments: dict[str, Any]
    payload: dict[str, Any]


class EvidenceStore(Protocol):
    """What the graph needs from evidence storage."""

    async def put_source(
        self,
        scope: str,
        artifact_id: str,
        payload: Mapping[str, Any],
        *,
        tool: str,
        arguments: Mapping[str, Any],
    ) -> StoredSource:
        """Store a result; the same ID and content again returns it, other content raises."""
        ...

    async def get_source(self, scope: str, artifact_id: str) -> StoredSource | None:
        """Return a stored result of this scope, or ``None``."""
        ...

    async def count_sources(self, scope: str) -> int:
        """How many sources this scope holds."""
        ...

    async def publish_manifest(self, scope: str, manifest: Mapping[str, Any]) -> str:
        """Publish an immutable memo manifest once every source it names is stored."""
        ...

    async def get_manifest(self, scope: str, manifest_id: str) -> dict[str, Any] | None:
        """Return a published manifest of this scope, or ``None``."""
        ...

    async def retire_scope(self, scope: str) -> int:
        """Delete everything of one scope and return how many rows went."""
        ...


class SqliteEvidenceStore:
    """An :class:`EvidenceStore` on one SQLite file, or ``":memory:"`` for a test."""

    def __init__(self, path: Path | str) -> None:
        self._path = str(path)
        self._memory: aiosqlite.Connection | None = None
        self._ready = False
        self._lock = asyncio.Lock()

    async def __aenter__(self) -> Self:
        async with self._session():
            pass
        return self

    async def __aexit__(
        self,
        error_type: type[BaseException] | None,
        error: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.close()

    @asynccontextmanager
    async def _session(self) -> AsyncIterator[aiosqlite.Connection]:
        """One serialized use of the database.

        A file is opened and closed for each call, so no thread outlives the store;
        an in-memory database is one connection kept until :meth:`close`.
        """
        async with self._lock:
            if self._path == ":memory:":
                if self._memory is None:
                    self._memory = await aiosqlite.connect(":memory:")
                    await self._memory.executescript(_SCHEMA)
                yield self._memory
                return
            if not self._ready:
                Path(self._path).parent.mkdir(parents=True, exist_ok=True)
            connection = await aiosqlite.connect(self._path)
            try:
                if not self._ready:
                    await connection.executescript(_SCHEMA)
                    await connection.commit()
                    self._ready = True
                yield connection
            finally:
                await connection.close()

    async def close(self) -> None:
        """Release an in-memory database; a file store holds nothing open."""
        async with self._lock:
            if self._memory is not None:
                await self._memory.close()
                self._memory = None

    async def put_source(
        self,
        scope: str,
        artifact_id: str,
        payload: Mapping[str, Any],
        *,
        tool: str,
        arguments: Mapping[str, Any],
    ) -> StoredSource:
        text = canonical_json(payload)
        digest = _digest(text)
        async with self._session() as connection:
            row = await (
                await connection.execute(
                    "SELECT sha256 FROM sources WHERE scope = ? AND artifact_id = ?",
                    (scope, artifact_id),
                )
            ).fetchone()
            if row is not None:
                if row[0] != digest:
                    raise EvidenceConflict(
                        f"source {artifact_id} is already stored with different content"
                    )
            else:
                await connection.execute(
                    "INSERT INTO sources VALUES (?, ?, ?, ?, ?, ?)",
                    (scope, artifact_id, digest, tool, canonical_json(arguments), text),
                )
                await connection.commit()
        return StoredSource(artifact_id, digest, tool, dict(arguments), json.loads(text))

    async def get_source(self, scope: str, artifact_id: str) -> StoredSource | None:
        async with self._session() as connection:
            row = await (
                await connection.execute(
                    "SELECT sha256, tool, arguments, payload FROM sources "
                    "WHERE scope = ? AND artifact_id = ?",
                    (scope, artifact_id),
                )
            ).fetchone()
        if row is None:
            return None
        return StoredSource(artifact_id, row[0], row[1], json.loads(row[2]), json.loads(row[3]))

    async def count_sources(self, scope: str) -> int:
        async with self._session() as connection:
            row = await (
                await connection.execute("SELECT COUNT(*) FROM sources WHERE scope = ?", (scope,))
            ).fetchone()
        return int(row[0]) if row else 0

    async def publish_manifest(self, scope: str, manifest: Mapping[str, Any]) -> str:
        memo_id = str(manifest["memo_id"])
        source_ids: Sequence[str] = manifest["source_ids"]
        text = canonical_json(manifest)
        digest = _digest(text)
        manifest_id = "man-" + digest[:24]
        async with self._session() as connection:
            for identity in source_ids:
                found = await (
                    await connection.execute(
                        "SELECT 1 FROM sources WHERE scope = ? AND artifact_id = ?",
                        (scope, identity),
                    )
                ).fetchone()
                if found is None:
                    raise EvidenceMissing(f"source {identity} is not stored in this scope")
            existing = await (
                await connection.execute(
                    "SELECT manifest_id FROM manifests WHERE scope = ? AND memo_id = ?",
                    (scope, memo_id),
                )
            ).fetchone()
            if existing is not None:
                if existing[0] != manifest_id:
                    raise EvidenceConflict(f"memo {memo_id} already has a different manifest")
                return manifest_id
            await connection.execute(
                "INSERT INTO manifests VALUES (?, ?, ?, ?, ?)",
                (scope, manifest_id, memo_id, digest, text),
            )
            await connection.commit()
        return manifest_id

    async def get_manifest(self, scope: str, manifest_id: str) -> dict[str, Any] | None:
        async with self._session() as connection:
            row = await (
                await connection.execute(
                    "SELECT body FROM manifests WHERE scope = ? AND manifest_id = ?",
                    (scope, manifest_id),
                )
            ).fetchone()
        return json.loads(row[0]) if row else None

    async def retire_scope(self, scope: str) -> int:
        async with self._session() as connection:
            removed = 0
            for table in ("manifests", "sources"):
                cursor = await connection.execute(f"DELETE FROM {table} WHERE scope = ?", (scope,))
                removed += cursor.rowcount
            await connection.commit()
        return removed
