"""The evidence store keeps original tool results exactly, per scope, and never rewrites them."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from rmprep_v2.evidence_store import (
    EvidenceConflict,
    EvidenceMissing,
    SqliteEvidenceStore,
    canonical_json,
    source_id,
)

SCOPE = "tenant/alice/rmprep-v2/thread-1"
OTHER = "tenant/alice/rmprep-v2/thread-2"
PACK: dict[str, Any] = {
    "status": "found",
    "uen": "JONE-0022",
    "sections": {"covenants": {"records": [{"source_ref": "covenants[name=DSCR]", "value": 1.08}]}},
    "empty": None,
    "text": "Café — “quoted”",
    "limit": 0.1,
}


@pytest.fixture
async def store(tmp_path: Path) -> Any:
    async with SqliteEvidenceStore(tmp_path / "evidence.sqlite") as opened:
        yield opened


async def put(
    store: SqliteEvidenceStore, scope: str = SCOPE, payload: dict[str, Any] = PACK
) -> str:
    identity = source_id("rmprep-v2-data.get_memo_evidence", {"uen": "JONE-0022"}, payload)
    await store.put_source(
        scope,
        identity,
        payload,
        tool="rmprep-v2-data.get_memo_evidence",
        arguments={"uen": "JONE-0022"},
    )
    return identity


async def test_a_stored_source_comes_back_exactly(store: SqliteEvidenceStore) -> None:
    identity = await put(store)
    stored = await store.get_source(SCOPE, identity)
    assert stored is not None
    assert stored.payload == PACK
    assert canonical_json(stored.payload) == canonical_json(PACK)
    assert stored.tool == "rmprep-v2-data.get_memo_evidence"
    assert stored.arguments == {"uen": "JONE-0022"}


async def test_the_same_source_stored_twice_is_one_source(store: SqliteEvidenceStore) -> None:
    first = await put(store)
    second = await put(store)
    assert first == second
    assert await store.count_sources(SCOPE) == 1


async def test_a_different_payload_under_the_same_id_is_refused(store: SqliteEvidenceStore) -> None:
    identity = await put(store)
    with pytest.raises(EvidenceConflict):
        await store.put_source(
            SCOPE, identity, {**PACK, "uen": "OTHER-0001"}, tool="t", arguments={}
        )
    stored = await store.get_source(SCOPE, identity)
    assert stored is not None
    assert stored.payload == PACK


async def test_the_id_depends_on_what_was_asked_and_what_came_back() -> None:
    base = source_id("tool", {"uen": "A"}, PACK)
    assert source_id("tool", {"uen": "A"}, dict(PACK)) == base
    assert source_id("tool", {"uen": "B"}, PACK) != base
    assert source_id("other", {"uen": "A"}, PACK) != base
    assert source_id("tool", {"uen": "A"}, {**PACK, "status": "changed"}) != base
    # Key order is not content.
    assert source_id("tool", {"b": 1, "a": 2}, PACK) == source_id("tool", {"a": 2, "b": 1}, PACK)


async def test_a_scope_cannot_read_another_scopes_sources(store: SqliteEvidenceStore) -> None:
    identity = await put(store)
    assert await store.get_source(OTHER, identity) is None
    await put(store, OTHER)
    assert await store.count_sources(SCOPE) == 1
    assert await store.count_sources(OTHER) == 1


async def test_sources_survive_closing_and_reopening(tmp_path: Path) -> None:
    path = tmp_path / "evidence.sqlite"
    async with SqliteEvidenceStore(path) as first:
        identity = await put(first)
    async with SqliteEvidenceStore(path) as second:
        stored = await second.get_source(SCOPE, identity)
    assert stored is not None
    assert stored.payload == PACK


async def test_simultaneous_identical_writes_store_one_source(store: SqliteEvidenceStore) -> None:
    await asyncio.gather(*(put(store) for _ in range(8)))
    assert await store.count_sources(SCOPE) == 1


async def test_a_manifest_needs_its_sources(store: SqliteEvidenceStore) -> None:
    manifest = {"memo_id": "memo-aaaaaaaaaaaa", "version": 1, "source_ids": ["src-missing"]}
    with pytest.raises(EvidenceMissing):
        await store.publish_manifest(SCOPE, manifest)
    assert await store.get_manifest(SCOPE, "man-anything") is None


async def test_a_published_manifest_is_immutable_and_idempotent(
    store: SqliteEvidenceStore,
) -> None:
    identity = await put(store)
    manifest = {"memo_id": "memo-aaaaaaaaaaaa", "version": 1, "source_ids": [identity]}
    first = await store.publish_manifest(SCOPE, manifest)
    assert await store.publish_manifest(SCOPE, dict(manifest)) == first
    assert await store.get_manifest(SCOPE, first) == manifest
    with pytest.raises(EvidenceConflict):
        await store.publish_manifest(SCOPE, {**manifest, "version": 2})


async def test_a_manifest_only_references_sources_of_its_own_scope(
    store: SqliteEvidenceStore,
) -> None:
    identity = await put(store, OTHER)
    with pytest.raises(EvidenceMissing):
        await store.publish_manifest(
            SCOPE, {"memo_id": "memo-bbbbbbbbbbbb", "version": 1, "source_ids": [identity]}
        )


async def test_retiring_a_scope_removes_only_that_scope(store: SqliteEvidenceStore) -> None:
    identity = await put(store)
    await store.publish_manifest(
        SCOPE, {"memo_id": "memo-aaaaaaaaaaaa", "version": 1, "source_ids": [identity]}
    )
    await put(store, OTHER)
    assert await store.retire_scope(SCOPE) == 2
    assert await store.count_sources(SCOPE) == 0
    assert await store.count_sources(OTHER) == 1
