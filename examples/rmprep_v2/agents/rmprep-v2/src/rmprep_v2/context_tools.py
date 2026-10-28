"""Keep tool arguments aligned with the client and evidence date selected for this turn."""

from collections.abc import Sequence

from langchain_core.messages import AIMessage

from rmprep_v2.memos import MemoVersion
from rmprep_v2.selection import ContextSelection
from rmprep_v2.tool_names import RETRIEVAL_TOOL_NAMES


def tool_context_issues(
    reply: AIMessage,
    selection: ContextSelection,
    memos: Sequence[MemoVersion],
    today: str,
) -> list[str]:
    """Reject a model's client/date drift before any tool executes."""
    selected = [memo for memo in memos if memo["memo_id"] in selection["memo_ids"]]
    clients = {memo["uen"] for memo in selected} | set(selection.get("client_uens", []))
    if selection["uen"]:
        clients.add(selection["uen"])
    issues: list[str] = []
    requested_clients = {
        str(call["args"]["uen"]).upper() for call in reply.tool_calls if "uen" in call["args"]
    }
    if len(requested_clients) > 1 and len(clients) < 2:
        issues.append(
            "Retrieve evidence only for the selected client or an explicitly selected group."
        )
    issues += _retrieval_issues(reply, selection)
    for call in reply.tool_calls:
        arguments = call["args"]
        if call["name"] in RETRIEVAL_TOOL_NAMES:
            # These tools take no client or date: they read the selected memo's stored evidence.
            continue
        uen = str(arguments.get("uen", "")).upper()
        if uen and clients and uen not in clients:
            issues.append("Use only the selected client UEN: " + ", ".join(sorted(clients)) + ".")
        if call["name"].endswith("find_clients"):
            continue
        dates = {memo["meeting_date"] for memo in selected if not uen or memo["uen"] == uen}
        if selection["meeting_date"]:
            dates.add(selection["meeting_date"])
        if selection["retrieval_date"]:
            dates = {selection["retrieval_date"]}
        if selection["mode"] == "refresh":
            dates = {selection["retrieval_date"] or today}
        key = "meeting_date" if call["name"].endswith("get_memo_evidence") else "end_date"
        if dates and str(arguments.get(key, "")) not in dates:
            issues.append(
                f"Set {key} explicitly to the selected evidence date: "
                + ", ".join(sorted(dates))
                + "."
            )
    return issues


def _retrieval_issues(reply: AIMessage, selection: ContextSelection) -> list[str]:
    """Reject a retrieval call the selected memo cannot answer, before any tool runs."""
    calls = [call for call in reply.tool_calls if call["name"] in RETRIEVAL_TOOL_NAMES]
    if not calls:
        return []
    chosen = list(selection["memo_ids"])
    if not chosen:
        return [
            "Retrieval tools read a saved memo's evidence and no saved memo is selected. "
            "Name a memo reference, or retrieve records with get_client_records."
        ]
    return [
        "Use only the selected memo(s): " + ", ".join(chosen) + "."
        for call in calls
        if call["name"] == "get_memo_section"
        and call["args"].get("memo_id")
        and call["args"]["memo_id"] not in chosen
    ]
