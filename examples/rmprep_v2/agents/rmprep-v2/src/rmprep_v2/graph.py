"""One agent service: route each question to the memo or Q&A specialist."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from typing import Any

from agentcraft2_core import RequestContext, ServiceContainer
from agentcraft2_core.adapters.checkpoint_options import SqliteCheckpointOptions
from agentcraft2_core.contracts import Section
from langchain_core.messages import HumanMessage
from langchain_core.tools import BaseTool
from langchain_core.utils.function_calling import convert_to_openai_tool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from rmprep_v2.artifacts import memo_artifacts
from rmprep_v2.context import ContextBuilder
from rmprep_v2.evidence_store import EvidenceStore, SqliteEvidenceStore
from rmprep_v2.prompts import MEMO_PROMPT, QNA_PROMPT
from rmprep_v2.qna_graph import build_qna_graph
from rmprep_v2.retrieval import retrieval_tools
from rmprep_v2.router import destination, router
from rmprep_v2.settings import QnaLimits
from rmprep_v2.specialist import build_specialist
from rmprep_v2.state import State
from rmprep_v2.tool_names import RETRIEVAL_TOOL_NAMES

__all__ = [
    "APPLICATION",
    "State",
    "answer",
    "ask",
    "build_graph",
    "registered_mcp_tools",
    "response",
    "steps",
]

APPLICATION = "rmprep-v2"
"""The agent's name in the registry, in policy and in the audit log."""


async def registered_mcp_tools(services: ServiceContainer) -> list[BaseTool]:
    """Return the tools of every MCP server the registry lists for this agent."""
    entry = services.registry.agents.get(APPLICATION)
    tools: list[BaseTool] = []
    for server in entry.mcp_servers if entry is not None else ():
        tools += await services.mcp_tools(server)
    return tools


def _evidence_store(services: ServiceContainer, given: EvidenceStore | None) -> EvidenceStore:
    """The store the caller gave, or a file beside the checkpoint database.

    Evidence belongs with the checkpoints that point at it, wherever the service or a test put
    them. Without a SQLite checkpoint database the store is in memory, so a restart cannot leave
    a checkpoint pointing at evidence that no longer exists unnoticed: the model is told the
    evidence is missing.
    """
    if given is not None:
        return given
    checkpoints = services.config.section(Section.CHECKPOINT)
    if checkpoints.provider == "sqlite":
        folder = checkpoints.parse_options(SqliteCheckpointOptions).path.parent
        return SqliteEvidenceStore(folder / "evidence.sqlite")
    return SqliteEvidenceStore(":memory:")


def _definition_tokens(tools: Sequence[BaseTool]) -> int:
    """Estimated tokens of the tool definitions a model request carries, by the agent's estimate."""
    text = json.dumps([convert_to_openai_tool(tool) for tool in tools])
    return -(-len(text.encode("utf-8")) // 3)


def build_graph(
    services: ServiceContainer,
    mcp_tools: Sequence[BaseTool] = (),
    *,
    context_builder: ContextBuilder | None = None,
    evidence_store: EvidenceStore | None = None,
    offload_evidence: bool = True,
    qna_limits: QnaLimits | None = None,
) -> CompiledStateGraph[Any, Any, Any, Any]:
    """Build the agent graph from governed parts.

    Args:
        services: A started service container.
        mcp_tools: Governed tools of registered MCP servers, already loaded.
        context_builder: Optional policy for assembling bounded model input.
        evidence_store: Where evidence results are kept while the checkpoint holds
            references. Defaults to ``evidence.sqlite`` in the service's state folder, or
            memory when the service has none.
        offload_evidence: ``False`` keeps results in the conversation, as rmprep does.
            Only a comparison with the previous behaviour needs it. It also keeps the Q&A
            agent as one node, because the Q&A graph reads its evidence from the store.
        qna_limits: What one Q&A question may use; the defaults are the reviewed proposal.
    """
    # Both specialists use the same provider and governed tools, but different prompts.
    evidence = _evidence_store(services, evidence_store) if offload_evidence else None
    # The memo agent writes from the data tools. The Q&A agent can also read the stored
    # evidence of the memo the application selected.
    retrieval = (
        services.tools(retrieval_tools(evidence), read_only=RETRIEVAL_TOOL_NAMES)
        if evidence is not None
        else []
    )
    qna_tools = [*mcp_tools, *retrieval]
    model = services.model("default")
    memo_model = model.bind_tools(mcp_tools)
    qna_model = model.bind_tools(qna_tools)
    if evidence is not None:
        # Small steps with their own limits. Their private state never reaches this graph's.
        qna_node: Any = build_qna_graph(
            qna_model,
            qna_tools,
            QNA_PROMPT,
            evidence=evidence,
            limits=qna_limits or QnaLimits(),
            tool_tokens=_definition_tokens(qna_tools),
            now=services.clock.now,
        )
    else:
        qna_node = build_specialist(
            qna_model,
            qna_tools,
            QNA_PROMPT,
            now=services.clock.now,
            context_builder=context_builder,
            evidence=evidence,
        )
    builder = StateGraph(State, context_schema=RequestContext)
    builder.add_node("router", router)
    builder.add_node(
        "memoagent",
        build_specialist(
            memo_model,
            mcp_tools,
            MEMO_PROMPT,
            default_word_limit=1000,
            now=services.clock.now,
            context_builder=context_builder,
            evidence=evidence,
        ),
    )
    builder.add_node("qnaagent", qna_node)
    builder.add_edge(START, "router")
    builder.add_conditional_edges("router", destination)
    builder.add_edge("memoagent", END)
    builder.add_edge("qnaagent", END)
    # The parent owns the conversation. Both subgraphs receive its saved messages.
    return builder.compile(**services.compile_kwargs())


async def answer(
    graph: CompiledStateGraph[Any, Any, Any, Any],
    services: ServiceContainer,
    context: RequestContext,
    question: str,
) -> str:
    """Run a built graph for one question on behalf of ``context``."""
    return str((await response(graph, services, context, question))["answer"])


async def response(
    graph: CompiledStateGraph[Any, Any, Any, Any],
    services: ServiceContainer,
    context: RequestContext,
    question: str,
) -> dict[str, Any]:
    """Return the accepted answer and any memo produced by this invocation."""
    result = await graph.ainvoke(
        {"messages": [HumanMessage(question)]},
        **services.invocation(context),
    )
    output: dict[str, Any] = {"answer": str(result["messages"][-1].text)}
    if artifacts := memo_artifacts(result, context):
        output["artifacts"] = artifacts
    return output


def _shown(message: Any) -> dict[str, Any]:
    """What a step streams of a message: no prose beside a tool call (the model thinking aloud)."""
    calls = [call["name"] for call in getattr(message, "tool_calls", [])]
    return {
        "type": message.type,
        "content": "" if calls else str(message.content),
        "tool_calls": calls,
    }


async def steps(
    graph: CompiledStateGraph[Any, Any, Any, Any],
    services: ServiceContainer,
    context: RequestContext,
    question: str,
) -> AsyncIterator[dict[str, Any]]:
    """Run a built graph for one question and yield each step as it ends.

    Each step names the graph node that ran and the messages it added, so a
    caller sees tool calls and the answer as they happen.
    """
    async for namespace, update in graph.astream(
        {"messages": [HumanMessage(question)]},
        stream_mode="updates",
        subgraphs=True,
        **services.invocation(context),
    ):
        for node, change in update.items():
            # Subgraphs stream individual changes. Their final parent update also
            # contains the old conversation, so do not emit that a second time.
            if not namespace and node in {"memoagent", "qnaagent"}:
                continue
            specialist = namespace[0].split(":", 1)[0] if namespace else None
            added = change.get("messages", []) if isinstance(change, dict) else []
            yield {
                "node": f"{specialist}/{node}" if specialist else node,
                "route": change.get("route") if isinstance(change, dict) else None,
                "messages": [_shown(message) for message in added],
            }


async def ask(
    services: ServiceContainer,
    context: RequestContext,
    question: str,
    *,
    context_builder: ContextBuilder | None = None,
) -> str:
    """Build the graph, with the tools of its MCP servers, and ask one question.

    The command line uses this. A service builds the graph once and calls
    :func:`answer` for every request.
    """
    graph = build_graph(
        services, await registered_mcp_tools(services), context_builder=context_builder
    )
    return await answer(graph, services, context, question)
