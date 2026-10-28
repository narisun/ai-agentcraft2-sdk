"""A reusable model/tool loop for specialists with different instructions."""

import hashlib
import json
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

from agentcraft2_core import RequestContext, ValidationFailed
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, ToolMessage
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool
from langgraph.graph import START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from rmprep_v2.clients import client_names, lookup_in_turn, recent_client_lookup, resolved_clients
from rmprep_v2.context import ContextBuilder
from rmprep_v2.context_tools import tool_context_issues
from rmprep_v2.evidence_store import EvidenceStore
from rmprep_v2.focus import ConversationFocus, conversation_focus, focus_update
from rmprep_v2.memos import MemoVersion, memo_draft, memo_from_turn, turns
from rmprep_v2.offload import (
    SOURCE_KEY,
    evidence_scope,
    hydrate_messages,
    offloading,
    stable_id,
)
from rmprep_v2.response_review import (
    response_advisories,
    response_issues,
    text_content,
    tool_payload,
    word_limit,
)
from rmprep_v2.selection import ContextSelection, select_context
from rmprep_v2.state import State


def _accepted_update(
    state: State,
    reply: AIMessage,
    memo: MemoVersion | None,
    selection: ContextSelection,
    diagnostics: dict[str, object],
) -> dict[str, Any]:
    """Commit a checked answer, its context decision and any new immutable memo together."""
    reply.additional_kwargs["rmprep_context"] = dict(selection)
    update: dict[str, Any] = {
        "messages": [reply],
        "context_diagnostics": diagnostics,
        "selection": selection,
    }
    if not reply.tool_calls:
        clients = selection.get("client_uens", []) or (
            [selection["uen"]] if selection["uen"] else []
        )
        current = turns(state["messages"])[-1]
        lookup = lookup_in_turn(current)
        if lookup is not None and not resolved_clients(current):
            # A discovery answer establishes a group for the next question, not a
            # single-client alias for the broad search term.
            clients = list(lookup.clients) if lookup.complete else []
        if not clients:
            # An unresolved lookup or clarification must not destroy prior focus.
            return update
        previous = conversation_focus(state)
        evidence_date = selection["retrieval_date"] or selection["meeting_date"]
        memo_ids = selection["memo_ids"] if selection["mode"] == "snapshot" else []
        evidence_ids = [str(message.id) for message in [*current, reply] if message.id]
        if not any(tool_payload(message) for message in current):
            evidence_ids = (
                previous["message_ids"]
                if set(previous["client_uens"]) == set(clients)
                and previous["evidence_date"] == evidence_date
                and previous["memo_ids"] == memo_ids
                else []
            )
        focus = ConversationFocus(
            client_uens=list(clients),
            memo_ids=list(memo_ids),
            evidence_date=evidence_date,
            message_ids=evidence_ids,
        )
        update.update(focus_update(focus))
    if memo is not None:
        update["memos"] = [memo]
        focus = ConversationFocus(
            client_uens=[memo["uen"]],
            memo_ids=[memo["memo_id"]],
            evidence_date=memo["meeting_date"],
            message_ids=list(memo["message_ids"]),
        )
        update.update(focus_update(focus))
    return update


def _reply_id(reply: AIMessage, current: Sequence[AnyMessage]) -> str:
    """An ID that a replay of this turn gives again; the provider's own ID is random."""
    last = current[-1].id
    if last is None:
        return reply.id or str(uuid4())
    return stable_id(str(last), len(current), text_content(reply), reply.tool_calls)


async def _publish(
    memo: MemoVersion, current: Sequence[AnyMessage], evidence: EvidenceStore, scope: str
) -> MemoVersion:
    """Publish the memo's manifest: the sources behind it and the hash of its text."""
    source_ids = sorted(
        {
            str(message.additional_kwargs[SOURCE_KEY])
            for message in current
            if isinstance(message, ToolMessage) and SOURCE_KEY in message.additional_kwargs
        }
    )
    manifest = {
        "memo_id": memo["memo_id"],
        "version": memo["version"],
        "uen": memo["uen"],
        "meeting_date": memo["meeting_date"],
        "source_ids": source_ids,
        "source_refs": memo["source_refs"],
        "body_sha256": hashlib.sha256(memo["content"].encode("utf-8")).hexdigest(),
    }
    manifest_id = await evidence.publish_manifest(scope, manifest)
    return MemoVersion(**{**memo, "manifest_id": manifest_id, "source_ids": source_ids})


def build_specialist(
    model: Runnable[Any, AIMessage],
    tools: Sequence[BaseTool],
    prompt: str,
    *,
    default_word_limit: int = 350,
    context_builder: ContextBuilder | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    evidence: EvidenceStore | None = None,
) -> CompiledStateGraph[Any, Any, Any, Any]:
    """Use injected, governed model and tools; inherit the parent checkpoint.

    With an ``evidence`` store, evidence results are kept there and the checkpoint holds
    references; each call reads the conversation with its references restored.
    """
    contexts = context_builder or ContextBuilder()

    async def call_model(state: State) -> dict[str, Any]:
        scope = evidence_scope() if evidence is not None else ""
        if evidence is not None:
            # A local copy for this call: the update returned below never carries it back.
            restored = await hydrate_messages(state["messages"], evidence, scope)
            state = cast(State, {**state, "messages": restored})
        current = turns(state["messages"])[-1]
        creating = state.get("route") == "memoagent"
        selection = state.get("selection") or select_context(
            str(current[0].content),
            state.get("memos", []),
            state.get("active_memo_id", ""),
            state.get("active_client", ""),
            creating_memo=creating,
            known_names=client_names(state["messages"], state.get("memos", [])),
            recent_lookup=recent_client_lookup(state["messages"][: -len(current)]),
            active_clients=state.get("active_client_uens", []),
            focus=conversation_focus(state),
        )
        if selection["clarification"]:
            return {
                "messages": [AIMessage(selection["clarification"])],
                "context_diagnostics": dict(selection),
            }
        # A name lookup can resolve a new client during the tool loop. Only
        # successful MCP payloads establish that identity, never assistant prose.
        clients = resolved_clients(current)
        if (
            not selection["uen"]
            and len(clients) == 1
            and not selection["memo_ids"]
            and len(selection.get("client_uens", [])) < 2
        ):
            selection = {**selection, "uen": next(iter(clients)), "client_uens": sorted(clients)}
        selection = {
            **selection,
            "retrieval_date": (selection["retrieval_date"] or now().date().isoformat())
            if selection["mode"] == "refresh"
            else selection["meeting_date"],
        }
        limit = word_limit(state["messages"], default_word_limit)
        # The application adds a stable reference only to evidence-backed memos.
        footer_reserve = (
            12 if creating and any(tool.name.endswith("get_memo_evidence") for tool in tools) else 0
        )
        body_limit = max(1, limit - footer_reserve)
        output_budget = json.dumps(
            {"output_budget_words": body_limit, "target_words": int(body_limit * 0.6)}
        )
        assembled = contexts.build(state, prompt, selection, output_budget=output_budget)
        conversation = list(assembled.messages)
        for attempt in range(3):
            contexts.check_size(conversation)
            reply = await model.ainvoke(conversation)
            reply = reply.model_copy(update={"id": _reply_id(reply, current)})
            if creating:
                reply = memo_draft(reply)
            draft = reply
            memo = (
                memo_from_turn([*current, reply], state.get("memos", []), now().isoformat())
                if creating
                else None
            )
            if memo is not None:
                reply = reply.model_copy(
                    update={
                        "content": memo["content"]
                        + (
                            f"\n\nMemo reference: {memo['memo_id']} | version {memo['version']} | "
                            f"{memo['uen']} | {memo['meeting_date']}"
                        )
                    }
                )
                memo["content"] = str(reply.content)
            issues = response_issues(reply, assembled.messages[1:], limit)
            issues += tool_context_issues(
                reply, selection, state.get("memos", []), now().date().isoformat()
            )
            if (
                creating
                and footer_reserve
                and not reply.tool_calls
                and memo is None
                and selection["uen"]
                and not any(
                    isinstance(message, ToolMessage)
                    and (message.name or "").endswith("get_memo_evidence")
                    for message in current
                )
            ):
                issues.append(
                    "Retrieve a successful get_memo_evidence pack before writing a memo. If unavailable, explain the missing client/evidence instead."
                )
            if (
                selection["mode"] == "refresh"
                and not reply.tool_calls
                and not any(tool_payload(message) for message in current)
            ):
                issues.append(
                    "Retrieve current evidence before describing what has changed; otherwise state that current evidence is unavailable."
                )
            if not issues:
                assembled.diagnostics["review_advisories"] = response_advisories(
                    reply, assembled.messages[1:]
                )
                if memo is not None and evidence is not None:
                    memo = await _publish(memo, current, evidence, scope)
                return _accepted_update(state, reply, memo, selection, assembled.diagnostics)
            if attempt < 2:
                # Drafts remain visible in model tracing but are never saved as
                # accepted answers in the shared conversation checkpoint.
                conversation.extend(
                    [
                        # Invalid tool calls are never sent as an unmatched call/result
                        # pair to a provider, and never reach the tool node.
                        AIMessage(
                            "Proposed tool calls (not executed): " + json.dumps(reply.tool_calls)
                        )
                        if reply.tool_calls
                        else draft,
                        HumanMessage(
                            "Revise the draft to pass these output checks: "
                            + " ".join(issues)
                            + " Return a corrected tool call or answer. Preserve evidence limits; "
                            "do not add unsupported claims while shortening. "
                            f"Aim for at most {max(1, int(body_limit * 0.6))} words. "
                            "Remove secondary figures and repeated explanations before shortening "
                            "source references or omitting evidence limits."
                        ),
                    ]
                )
        raise ValidationFailed(
            "The answer could not meet its length, citation or evidence-limit checks after two revisions."
        )

    builder = StateGraph(State, context_schema=RequestContext)
    builder.add_node("model", call_model)
    tool_node = ToolNode(tools)
    builder.add_node(
        "tools", offloading(tool_node, evidence) if evidence is not None else tool_node
    )
    builder.add_edge(START, "model")
    builder.add_conditional_edges("model", tools_condition)
    builder.add_edge("tools", "model")
    return builder.compile()
