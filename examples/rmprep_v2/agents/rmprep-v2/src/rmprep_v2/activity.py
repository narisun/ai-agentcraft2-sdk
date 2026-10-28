"""What a reader is told while a question is answered.

A line of activity is a fixed label the application chose, at most a count or a reason code: never
the question, a draft, a tool argument or result, or a record's text. It is advisory. The SDK sends
it when a live response asked for it, and nothing here can change an answer or fail a request.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from rmprep_v2.tool_names import RETRIEVAL_TOOL_NAMES

__all__ = ["note", "report", "tool_activity"]

_LOG = logging.getLogger(__name__)

_SEARCH = "Searching the memo"
_READ = "Reading memo records"
_READING = ("get_evidence", "get_memo_section", "get_analysis_note", "open_source")


async def report(kind: str, label: str, detail: str | None = None) -> None:
    """Hand one line to the SDK, if this SDK can show activity (older ones cannot)."""
    from agentcraft2_core.integrations import langgraph as sdk

    sender = getattr(sdk, "report_activity", None)
    if sender is not None:
        await sender(kind, label, detail)


async def note(kind: str, label: str, detail: str | None = None) -> None:
    """Report a line of activity; a failure to show it is logged and otherwise ignored."""
    try:
        await report(kind, label, detail)
    except Exception:
        _LOG.warning("activity could not be reported", exc_info=True)


def tool_activity(names: Sequence[str]) -> tuple[str, str, str | None]:
    """Describe a round of tool calls by what they do, counting them rather than listing them."""
    first = names[0] if names else ""
    if first == "search_evidence":
        kind, label = "searching", _SEARCH
    elif first == "calculate":
        kind, label = "calculating", "Calculating a figure"
    elif first in _READING or first in RETRIEVAL_TOOL_NAMES:
        kind, label = "reading", _READ
    else:
        # Anything else is a data tool of the platform: a fresh read of the client's records.
        kind, label = "fetching", "Fetching current client data"
    return kind, label, f"{len(names)} lookups" if len(names) > 1 else None
