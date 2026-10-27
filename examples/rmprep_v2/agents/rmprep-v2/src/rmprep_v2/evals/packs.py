"""Read memo evidence packs from the real data server, in process, for evaluation and tests."""

from __future__ import annotations

import json
import tempfile
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from agentcraft2_core import ServiceContainer
from agentcraft2_core.pipeline import bind_request_context
from agentcraft2_core.testing import load_test_config, scripted_providers
from agentcraft2_core.testing.mcp import InProcessMcpConnector

from rmprep_v2.graph import APPLICATION
from rmprep_v2.settings import ENV_FILE
from rmprep_v2_data import SERVER_ID, build_server

__all__ = ["WORKSPACE", "load_packs", "references", "unframe"]

WORKSPACE = ENV_FILE.parent.parents[1]


def unframe(text: str) -> dict[str, Any]:
    """Return the JSON a framed tool result carries, or an empty dict."""
    if text.startswith("<untrusted_tool_result"):
        text = text.split(">\n", 1)[1].rsplit("\n</untrusted_tool_result>", 1)[0]
    try:
        payload = json.loads(text)
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


def references(pack: dict[str, Any], section: str | None = None) -> list[str]:
    """The ``source_ref`` of every record of one section, or of the whole pack, in order."""
    pages = pack.get("sections", {})
    chosen = [pages.get(section, {})] if section else list(pages.values())
    return [
        str(record["source_ref"])
        for page in chosen
        for record in page.get("records", [])
        if "source_ref" in record
    ]


async def load_packs(requests: Iterable[tuple[str, str]]) -> dict[tuple[str, str], dict[str, Any]]:
    """Return ``get_memo_evidence`` for each ``(uen, meeting_date)``, as an analyst would see it."""
    wanted = list(dict.fromkeys(requests))
    packs: dict[tuple[str, str], dict[str, Any]] = {}
    with tempfile.TemporaryDirectory(prefix="rmprep-v2-packs-") as folder:
        state = Path(folder)
        server = load_test_config(
            WORKSPACE / "mcp-servers/rmprep-v2-data/.env.example", state_dir=state / "server"
        )
        agent = load_test_config(
            WORKSPACE / "agents/rmprep-v2/.env.example", state_dir=state / "agent"
        )
        async with ServiceContainer(server) as server_services:
            connector = InProcessMcpConnector({SERVER_ID: build_server(server_services)})
            async with ServiceContainer(
                agent, scripted_providers("unused"), mcp_connector=connector
            ) as services:
                tools = await services.mcp_tools(SERVER_ID)
                tool = next(t for t in tools if t.name.endswith("get_memo_evidence"))
                caller = await services.authenticate(None, application=APPLICATION, thread_id="p")
                for uen, meeting_date in wanted:
                    with bind_request_context(caller):
                        raw = await tool.ainvoke({"uen": uen, "meeting_date": meeting_date})
                    text = raw if isinstance(raw, str) else json.dumps(raw)
                    packs[(uen, meeting_date)] = unframe(text)
    return packs
