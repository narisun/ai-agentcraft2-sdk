"""Tools that read the stored evidence of the memo the application selected.

The application decides which memo or memos a question is about; a model cannot widen that
set. Every call is confined to the request's thread scope and to the sources of the selected
memos, so another thread's evidence, and an unselected version's, are never returned. Each
answer is bounded in size and says when it left something out. Search hits are discovery
material: only a fetched card or a section read makes a reference citable.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Annotated, Any

from langchain_core.tools import BaseTool, tool
from langgraph.prebuilt import InjectedState

from rmprep_v2.cards import json_tokens
from rmprep_v2.evidence_store import EvidenceStore
from rmprep_v2.offload import evidence_scope
from rmprep_v2.tool_names import EVIDENCE_TOOL_NAMES, RETRIEVAL_TOOL_NAMES

__all__ = [
    "EVIDENCE_TOOL_NAMES",
    "RETRIEVAL_TOOL_NAMES",
    "get_analysis_note",
    "get_evidence",
    "get_memo_section",
    "retrieval_tools",
    "search_evidence",
    "selected_memos",
]

SEARCH_TOKEN_CAP = 2_000
EVIDENCE_TOKEN_CAP = 3_000
SECTION_TOKEN_CAP = 3_000
NOTE_TOKEN_CAP = 1_500
MAX_RESULTS = 10
MAX_IDS = 10
SECTION_PAGE = 50
SNIPPET_WORDS = 60

_DISCOVERY = (
    "Search results are discovery material. Call get_evidence to read a record before citing it."
)
_ABSENCE = " A search that finds nothing does not show the evidence is absent."

Memo = Mapping[str, Any]


def selected_memos(state: Mapping[str, Any]) -> list[Memo]:
    """The memos the application selected for this question, in the order it chose them."""
    selection = state.get("selection") or {}
    by_id = {memo["memo_id"]: memo for memo in state.get("memos", [])}
    return [by_id[identity] for identity in selection.get("memo_ids", []) if identity in by_id]


def _source_ids(memos: Sequence[Memo]) -> list[str]:
    return [source for memo in memos for source in memo.get("source_ids", [])]


def _memo_of(memos: Sequence[Memo]) -> dict[str, Memo]:
    return {source: memo for memo in memos for source in memo.get("source_ids", [])}


def _unusable(memos: Sequence[Memo]) -> dict[str, Any] | None:
    if not memos:
        return {
            "status": "no_memo",
            "message": "No saved memo is selected, so there is no stored evidence to read. "
            "Name a memo reference, or retrieve records with get_client_records.",
        }
    if not _source_ids(memos):
        return {
            "status": "no_stored_evidence",
            "message": "This memo has no stored evidence. Retrieve records with get_client_records.",
        }
    return None


def _snippet(card: Mapping[str, Any]) -> str:
    """The first fields of a card as ``name=value`` pairs, at most 60 words."""
    words: list[str] = []
    for key, value in card["fields"].items():
        pair = f"{key}={value};".split()
        room = SNIPPET_WORDS - len(words)
        words += pair[:room]
        if len(pair) > room:
            words.append("...")
            break
    return " ".join(words)


def _record(card: Mapping[str, Any], memo: Memo | None) -> dict[str, Any]:
    shown: dict[str, Any] = {
        "card_id": card["card_id"],
        "memo_id": memo["memo_id"] if memo else None,
        "section": card["section"],
        "source_ref": card["source_ref"],
        "fields": card["fields"],
        "limits": card["limits"],
        "as_of": card["as_of"],
    }
    if card.get("oversized"):
        shown["oversized"] = True
    return shown


async def search_evidence(
    store: EvidenceStore,
    scope: str,
    memos: Sequence[Memo],
    query: str,
    section: str = "",
    limit: int = 5,
    offset: int = 0,
) -> dict[str, Any]:
    """Keyword search over the selected memos' cards. Finds candidates; does not make them citable."""
    if (unusable := _unusable(memos)) is not None:
        return unusable
    sources = _source_ids(memos)
    owner = _memo_of(memos)
    limit = max(1, min(limit, MAX_RESULTS))
    offset = max(0, offset)
    page = await store.search_cards(
        scope, sources, query, section=section or None, limit=limit, offset=offset
    )
    results = [
        {
            "card_id": hit.card["card_id"],
            "memo_id": owner[hit.card["source_id"]]["memo_id"],
            "section": hit.card["section"],
            "source_ref": hit.card["source_ref"],
            "snippet": _snippet(hit.card),
            "as_of": hit.card["as_of"],
        }
        for hit in page.hits
    ]
    sections = await store.list_sections(scope, sources)
    result: dict[str, Any] = {
        "status": "ok",
        "query": query,
        "section": section or None,
        "results": results,
        "returned": len(results),
        "total_matches": page.total,
        "next_offset": None,
        "truncated": False,
        "note": _DISCOVERY + (_ABSENCE if page.total == 0 else ""),
        "coverage": {
            "sections_searched": [section] if section else list(sections),
            "cards_in_memo": sum(sections.values()),
        },
    }
    while json_tokens(result) > SEARCH_TOKEN_CAP and len(results) > 1:
        results.pop()
        result["truncated"] = True
    result["returned"] = len(results)
    if offset + len(results) < page.total:
        result["next_offset"] = offset + len(results)
    return result


async def get_evidence(
    store: EvidenceStore, scope: str, memos: Sequence[Memo], card_ids: Sequence[str]
) -> dict[str, Any]:
    """Whole cards by ID, at most ten. The only way, with a section read, to make a record citable."""
    if (unusable := _unusable(memos)) is not None:
        return unusable
    wanted = list(dict.fromkeys(card_ids))
    taken, skipped = wanted[:MAX_IDS], wanted[MAX_IDS:]
    owner = _memo_of(memos)
    found = await store.get_cards(scope, _source_ids(memos), taken)
    present = {card["card_id"] for card in found}
    result: dict[str, Any] = {
        "status": "ok",
        "cards": [],
        "not_found": [identity for identity in taken if identity not in present],
        "omitted_ids": [],
        "skipped_ids": skipped,
        "truncated": False,
    }
    for card in found:
        result["cards"].append(_record(card, owner.get(card["source_id"])))
        if json_tokens(result) > EVIDENCE_TOKEN_CAP and len(result["cards"]) > 1:
            result["cards"].pop()
            break
    shown = {card["card_id"] for card in result["cards"]}
    result["omitted_ids"] = [card["card_id"] for card in found if card["card_id"] not in shown]
    result["truncated"] = bool(result["omitted_ids"])
    return result


async def get_memo_section(
    store: EvidenceStore,
    scope: str,
    memos: Sequence[Memo],
    section: str,
    memo_id: str = "",
    offset: int = 0,
) -> dict[str, Any]:
    """One page of a section of the memo's evidence, for a question about the whole section."""
    if (unusable := _unusable(memos)) is not None:
        return unusable
    chosen: Memo | None
    if memo_id:
        chosen = next((memo for memo in memos if memo["memo_id"] == memo_id), None)
        if chosen is None:
            return {
                "status": "memo_not_selected",
                "selected": [memo["memo_id"] for memo in memos],
                "message": "Only the selected memo or memos can be read.",
            }
    elif len(memos) > 1:
        return {
            "status": "memo_required",
            "memos": [memo["memo_id"] for memo in memos],
            "message": "Several memos are selected. Name the memo to read.",
        }
    else:
        chosen = memos[0]
    sources = list(chosen.get("source_ids", []))
    offset = max(0, offset)
    cards, total = await store.section_cards(
        scope, sources, section, offset=offset, limit=SECTION_PAGE
    )
    if total == 0:
        return {
            "status": "unknown_section",
            "section": section,
            "available": await store.list_sections(scope, sources),
        }
    result: dict[str, Any] = {
        "status": "ok",
        "memo_id": chosen["memo_id"],
        "version": chosen.get("version"),
        "meeting_date": chosen.get("meeting_date"),
        "section": section,
        "cards": [_record(card, chosen) for card in cards],
        "total_cards": total,
        "next_offset": None,
        "truncated": False,
    }
    while json_tokens(result) > SECTION_TOKEN_CAP and len(result["cards"]) > 1:
        result["cards"].pop()
        result["truncated"] = True
    if offset + len(result["cards"]) < total:
        result["next_offset"] = offset + len(result["cards"])
    return result


async def get_analysis_note(
    store: EvidenceStore, scope: str, memos: Sequence[Memo], note_id: str
) -> dict[str, Any]:
    """An interpretation of the review rules and the cards behind it. Not a fact to cite."""
    if (unusable := _unusable(memos)) is not None:
        return unusable
    note = await store.get_note(scope, _source_ids(memos), note_id)
    if note is None:
        return {"status": "not_found", "note_id": note_id}
    owner = _memo_of(memos).get(note["source_id"])
    shown = {
        "note_id": note["note_id"],
        "memo_id": owner["memo_id"] if owner else None,
        "code": note["code"],
        "priority": note["priority"],
        "conclusion": note["conclusion"],
        "source_refs": note["source_refs"],
        "supporting_card_ids": note["supporting_card_ids"],
        "limitations": note["limitations"],
    }
    return {"status": "ok", "note": shown}


def retrieval_tools(store: EvidenceStore) -> list[BaseTool]:
    """The four tools, bound to a store. The scope and the memo selection are not arguments."""

    @tool("search_evidence")
    async def search_tool(
        query: str,
        state: Annotated[dict[str, Any], InjectedState],
        section: str = "",
        limit: int = 5,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Search the selected memo's stored evidence by keyword.

        Use it to find records the memo does not show. Results are candidates with short
        snippets, not evidence: call get_evidence on the card_id to read a record before
        you cite it. Optionally narrow to one section. Returns at most 10 results; use
        next_offset for more. A search that finds nothing does not show the evidence is absent.
        """
        return await search_evidence(
            store, evidence_scope(), selected_memos(state), query, section, limit, offset
        )

    @tool("get_evidence")
    async def evidence_tool(
        card_ids: list[str], state: Annotated[dict[str, Any], InjectedState]
    ) -> dict[str, Any]:
        """Read up to 10 stored evidence cards by card_id, exactly as recorded.

        Cite a card by its source_ref. A card lists its values, its period and the limits of
        its section. Use omitted_ids and not_found to see what was not returned.
        """
        return await get_evidence(store, evidence_scope(), selected_memos(state), card_ids)

    @tool("get_memo_section")
    async def section_tool(
        section: str,
        state: Annotated[dict[str, Any], InjectedState],
        memo_id: str = "",
        offset: int = 0,
    ) -> dict[str, Any]:
        """Read one section of the selected memo's evidence, for example covenants.

        Use it when the question is about a whole section. When two memos are selected, name
        the memo_id. Returns a page; use next_offset to continue. An unknown section lists the
        sections that exist.
        """
        return await get_memo_section(
            store, evidence_scope(), selected_memos(state), section, memo_id, offset
        )

    @tool("get_analysis_note")
    async def note_tool(
        note_id: str, state: Annotated[dict[str, Any], InjectedState]
    ) -> dict[str, Any]:
        """Read an analysis note: what the review rules concluded and the cards behind it.

        A note is an interpretation, not evidence. Read its supporting cards with
        get_evidence before stating any fact from it.
        """
        return await get_analysis_note(store, evidence_scope(), selected_memos(state), note_id)

    return [search_tool, evidence_tool, section_tool, note_tool]
