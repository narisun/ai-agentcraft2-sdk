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
import re
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
    "SearchHit",
    "SearchPage",
    "SqliteEvidenceStore",
    "StoredSource",
    "canonical_json",
    "fts_query",
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
CREATE TABLE IF NOT EXISTS cards (
    scope TEXT NOT NULL,
    card_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    section TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    body TEXT NOT NULL,
    seq INTEGER NOT NULL,
    PRIMARY KEY (scope, card_id)
);
CREATE INDEX IF NOT EXISTS cards_by_source ON cards (scope, source_id, section, seq);
CREATE TABLE IF NOT EXISTS notes (
    scope TEXT NOT NULL,
    note_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    body TEXT NOT NULL,
    seq INTEGER NOT NULL,
    PRIMARY KEY (scope, note_id)
);
CREATE TABLE IF NOT EXISTS visibility (
    scope TEXT NOT NULL,
    key TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    body TEXT NOT NULL,
    PRIMARY KEY (scope, key)
);
CREATE VIRTUAL TABLE IF NOT EXISTS cards_fts USING fts5(
    scope UNINDEXED, card_id UNINDEXED, source_id UNINDEXED, section UNINDEXED, head, body,
    tokenize = 'porter unicode61'
);
"""

# Words that carry no meaning in a keyword search.
_STOP_WORDS = frozenset(
    [
        "the",
        "of",
        "and",
        "or",
        "to",
        "in",
        "is",
        "was",
        "what",
        "how",
        "did",
        "does",
        "do",
        "for",
        "on",
        "by",
        "with",
        "that",
        "this",
        "it",
        "its",
        "are",
        "be",
        "a",
        "an",
        "as",
        "at",
        "from",
        "has",
        "have",
        "had",
        "not",
        "but",
        "if",
        "so",
        "than",
        "then",
        "there",
        "these",
        "those",
        "which",
        "who",
        "whom",
        "when",
        "where",
        "why",
        "will",
        "would",
        "can",
        "could",
        "should",
        "may",
        "might",
        "any",
        "all",
        "some",
        "more",
        "most",
        "about",
        "into",
        "over",
        "under",
        "between",
        "please",
        "tell",
        "show",
        "give",
        "explain",
        "describe",
        "need",
        "needs",
        "needed",
        "discuss",
        "discussed",
        "discussion",
        "client",
        "clients",
        "customer",
        "memo",
        "say",
        "says",
        "said",
    ]
)
# Controlled banking synonyms: a phrase in the question also looks for the term the data uses.
_SYNONYMS = {
    "debt service coverage": ("dscr",),
    "cash conversion cycle": ("ccc",),
    "days sales outstanding": ("dso",),
    "days inventory outstanding": ("dio",),
    "days payable outstanding": ("dpo",),
    "fixed charge coverage": ("fccr",),
    "net working capital": ("nwc",),
    "return on equity": ("roe",),
    # Everyday words an RM uses for what the data calls something else.
    "refinanc": ("facilities", "maturity", "renewal"),
    "service issue": ("operational", "service", "cases", "requests"),
    "collections": ("dso", "receivables"),
    "receivables": ("dso", "ar"),
}
_WORD = re.compile(r"[^\W_]+")


def fts_query(text: str) -> str:
    """Turn free text into a safe FTS5 expression: quoted words joined by OR, never syntax."""
    lowered = text.casefold()
    found = _WORD.findall(lowered)
    words = [word for word in found if word not in _STOP_WORDS]
    spaced = " ".join(found)
    for phrase, extra in _SYNONYMS.items():
        if phrase in spaced:
            words += extra
    return " OR ".join(f'"{word}"' for word in dict.fromkeys(words))


def _card_text(card: Mapping[str, Any]) -> tuple[str, str]:
    """What the keyword index holds for one card: its labels, and separately its values.

    A question names a topic far more often than a value, so a match on the section, the
    reference or a field name outranks the same word appearing in free text.
    """
    head = [str(card["section"]), str(card["source_ref"])]
    head += [key.replace("_", " ") for key in card["fields"]]
    body = ["" if value is None else str(value) for value in card["fields"].values()]
    return " ".join(head), " ".join(body)


@dataclass(frozen=True, slots=True)
class SearchHit:
    """One card found by a keyword search, best match first."""

    card: dict[str, Any]
    score: float


@dataclass(frozen=True, slots=True)
class SearchPage:
    """A page of hits and how many cards matched in all."""

    hits: list[SearchHit]
    total: int


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

    async def put_cards(
        self,
        scope: str,
        source_id: str,
        cards: Sequence[Mapping[str, Any]],
        notes: Sequence[Mapping[str, Any]],
    ) -> None:
        """Store the cards and notes built from one source; the same ones again are a no-op."""
        ...

    async def search_cards(
        self,
        scope: str,
        source_ids: Sequence[str],
        query: str,
        *,
        section: str | None = None,
        limit: int = 5,
        offset: int = 0,
    ) -> SearchPage:
        """Keyword search over the cards of these sources only."""
        ...

    async def get_cards(
        self, scope: str, source_ids: Sequence[str], card_ids: Sequence[str]
    ) -> list[dict[str, Any]]:
        """The named cards of these sources, in the order asked; unknown IDs are skipped."""
        ...

    async def section_cards(
        self,
        scope: str,
        source_ids: Sequence[str],
        section: str,
        *,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[dict[str, Any]], int]:
        """One page of a section's cards and the section's card count."""
        ...

    async def list_sections(self, scope: str, source_ids: Sequence[str]) -> dict[str, int]:
        """The sections these sources have cards for, with their card counts."""
        ...

    async def list_notes(self, scope: str, source_ids: Sequence[str]) -> list[dict[str, Any]]:
        """The analysis notes of these sources, in the order they were built."""
        ...

    async def get_note(
        self, scope: str, source_ids: Sequence[str], note_id: str
    ) -> dict[str, Any] | None:
        """One note of these sources, or ``None``."""
        ...

    async def put_visibility(self, scope: str, key: str, record: Mapping[str, Any]) -> None:
        """Keep what one model call could see; the same record again is a no-op."""
        ...

    async def get_visibility(self, scope: str, key: str) -> dict[str, Any] | None:
        """The record of one model call, or ``None``."""
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
            for table in ("manifests", "sources", "cards", "notes", "visibility", "cards_fts"):
                cursor = await connection.execute(f"DELETE FROM {table} WHERE scope = ?", (scope,))
                removed += cursor.rowcount if table != "cards_fts" else 0
            await connection.commit()
        return removed

    async def put_cards(
        self,
        scope: str,
        source_id: str,
        cards: Sequence[Mapping[str, Any]],
        notes: Sequence[Mapping[str, Any]],
    ) -> None:
        async with self._session() as connection:
            fresh_cards = await self._fresh(connection, "cards", "card_id", scope, cards)
            fresh_notes = await self._fresh(connection, "notes", "note_id", scope, notes)
            for seq, card, text, digest in fresh_cards:
                await connection.execute(
                    "INSERT INTO cards VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (scope, card["card_id"], source_id, card["section"], digest, text, seq),
                )
                head, body = _card_text(card)
                await connection.execute(
                    "INSERT INTO cards_fts (scope, card_id, source_id, section, head, body) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (scope, card["card_id"], source_id, card["section"], head, body),
                )
            for seq, note, text, digest in fresh_notes:
                await connection.execute(
                    "INSERT INTO notes VALUES (?, ?, ?, ?, ?, ?)",
                    (scope, note["note_id"], source_id, digest, text, seq),
                )
            await connection.commit()

    @staticmethod
    async def _fresh(
        connection: aiosqlite.Connection,
        table: str,
        key: str,
        scope: str,
        items: Sequence[Mapping[str, Any]],
    ) -> list[tuple[int, Mapping[str, Any], str, str]]:
        """The items not stored yet; an item stored with other content is a conflict."""
        fresh: list[tuple[int, Mapping[str, Any], str, str]] = []
        for seq, item in enumerate(items):
            text = canonical_json(item)
            digest = _digest(text)
            row = await (
                await connection.execute(
                    f"SELECT sha256 FROM {table} WHERE scope = ? AND {key} = ?",
                    (scope, item[key]),
                )
            ).fetchone()
            if row is None:
                fresh.append((seq, item, text, digest))
            elif row[0] != digest:
                raise EvidenceConflict(f"{table[:-1]} {item[key]} is stored with other content")
        return fresh

    async def search_cards(
        self,
        scope: str,
        source_ids: Sequence[str],
        query: str,
        *,
        section: str | None = None,
        limit: int = 5,
        offset: int = 0,
    ) -> SearchPage:
        match = fts_query(query)
        if not match or not source_ids:
            return SearchPage([], 0)
        marks = ",".join("?" * len(source_ids))
        where = f"cards_fts MATCH ? AND cards_fts.scope = ? AND cards_fts.source_id IN ({marks})"
        arguments: list[Any] = [match, scope, *source_ids]
        if section:
            where += " AND cards_fts.section = ?"
            arguments.append(section)
        async with self._session() as connection:
            total_row = await (
                await connection.execute(f"SELECT COUNT(*) FROM cards_fts WHERE {where}", arguments)
            ).fetchone()
            rows = await (
                await connection.execute(
                    "SELECT c.body, bm25(cards_fts, 0.0, 0.0, 0.0, 0.0, 6.0, 1.0) AS score FROM cards_fts "
                    "JOIN cards c ON c.scope = cards_fts.scope AND c.card_id = cards_fts.card_id "
                    f"WHERE {where} ORDER BY score, c.seq LIMIT ? OFFSET ?",
                    [*arguments, max(0, limit), max(0, offset)],
                )
            ).fetchall()
        hits = [SearchHit(json.loads(body), float(score)) for body, score in rows]
        return SearchPage(hits, int(total_row[0]) if total_row else 0)

    async def get_cards(
        self, scope: str, source_ids: Sequence[str], card_ids: Sequence[str]
    ) -> list[dict[str, Any]]:
        if not source_ids or not card_ids:
            return []
        sources = ",".join("?" * len(source_ids))
        cards = ",".join("?" * len(card_ids))
        async with self._session() as connection:
            rows = await (
                await connection.execute(
                    "SELECT card_id, body FROM cards WHERE scope = ? "
                    f"AND source_id IN ({sources}) AND card_id IN ({cards})",
                    [scope, *source_ids, *card_ids],
                )
            ).fetchall()
        found = {card_id: json.loads(body) for card_id, body in rows}
        return [found[card_id] for card_id in dict.fromkeys(card_ids) if card_id in found]

    async def section_cards(
        self,
        scope: str,
        source_ids: Sequence[str],
        section: str,
        *,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[dict[str, Any]], int]:
        if not source_ids:
            return [], 0
        sources = ",".join("?" * len(source_ids))
        where = f"scope = ? AND section = ? AND source_id IN ({sources})"
        arguments: list[Any] = [scope, section, *source_ids]
        async with self._session() as connection:
            total_row = await (
                await connection.execute(f"SELECT COUNT(*) FROM cards WHERE {where}", arguments)
            ).fetchone()
            rows = await (
                await connection.execute(
                    f"SELECT body FROM cards WHERE {where} ORDER BY source_id, seq "
                    "LIMIT ? OFFSET ?",
                    [*arguments, max(0, limit), max(0, offset)],
                )
            ).fetchall()
        return [json.loads(body) for (body,) in rows], int(total_row[0]) if total_row else 0

    async def list_sections(self, scope: str, source_ids: Sequence[str]) -> dict[str, int]:
        if not source_ids:
            return {}
        sources = ",".join("?" * len(source_ids))
        async with self._session() as connection:
            rows = await (
                await connection.execute(
                    "SELECT section, COUNT(*) FROM cards WHERE scope = ? "
                    f"AND source_id IN ({sources}) GROUP BY section ORDER BY MIN(rowid)",
                    [scope, *source_ids],
                )
            ).fetchall()
        return {section: int(count) for section, count in rows}

    async def list_notes(self, scope: str, source_ids: Sequence[str]) -> list[dict[str, Any]]:
        if not source_ids:
            return []
        sources = ",".join("?" * len(source_ids))
        async with self._session() as connection:
            rows = await (
                await connection.execute(
                    "SELECT body FROM notes WHERE scope = ? "
                    f"AND source_id IN ({sources}) ORDER BY source_id, seq",
                    [scope, *source_ids],
                )
            ).fetchall()
        return [json.loads(body) for (body,) in rows]

    async def get_note(
        self, scope: str, source_ids: Sequence[str], note_id: str
    ) -> dict[str, Any] | None:
        if not source_ids:
            return None
        sources = ",".join("?" * len(source_ids))
        async with self._session() as connection:
            row = await (
                await connection.execute(
                    "SELECT body FROM notes WHERE scope = ? AND note_id = ? "
                    f"AND source_id IN ({sources})",
                    [scope, note_id, *source_ids],
                )
            ).fetchone()
        return json.loads(row[0]) if row else None

    async def put_visibility(self, scope: str, key: str, record: Mapping[str, Any]) -> None:
        text = canonical_json(record)
        digest = _digest(text)
        async with self._session() as connection:
            row = await (
                await connection.execute(
                    "SELECT sha256 FROM visibility WHERE scope = ? AND key = ?", (scope, key)
                )
            ).fetchone()
            if row is None:
                await connection.execute(
                    "INSERT INTO visibility VALUES (?, ?, ?, ?)", (scope, key, digest, text)
                )
                await connection.commit()
            elif row[0] != digest:
                raise EvidenceConflict(f"visibility record {key} is stored with other content")

    async def get_visibility(self, scope: str, key: str) -> dict[str, Any] | None:
        async with self._session() as connection:
            row = await (
                await connection.execute(
                    "SELECT body FROM visibility WHERE scope = ? AND key = ?", (scope, key)
                )
            ).fetchone()
        return json.loads(row[0]) if row else None
