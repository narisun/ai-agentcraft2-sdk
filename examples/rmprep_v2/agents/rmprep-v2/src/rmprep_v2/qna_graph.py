"""The Q&A agent as small graph steps, built as one node of the agent graph.

    select -> prefetch -> tools -> generate -> validate -> commit
                 ^           |
                 |           +-> tools -> generate     (a tool request that passed its checks)
                 |           +-> generate              (a revision: the checks found issues)
                 |           +-> limitation            (a limit, or no revision left)
                 +-> limitation                         (a limit reached before a model call)

A model attempt, a revision and a tool step are each a graph step, so each is visible in a
stream and survives a resume. The draft being checked lives in an untracked channel: it is
never written to a checkpoint, so a rejected draft is saved nowhere. The ledger of the question
is private to this graph and starts empty for every question. A limit, a failure the graph can
explain, or an exhausted step budget ends in a plain answer from the ``limitation`` node
instead of an exception. A denial or a budget error from the platform is never turned into an
answer: it keeps its meaning to the caller.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Annotated, Any, NotRequired, cast

from agentcraft2_core import RequestContext
from agentcraft2_core.contracts import (
    BudgetExceeded,
    ConfigurationError,
    CredentialsExpiredError,
    IntegrityError,
    PolicyDenied,
)
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool
from langgraph.channels import UntrackedValue
from langgraph.errors import NodeError, NodeTimeoutError
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.managed import RemainingSteps
from langgraph.prebuilt import ToolNode
from langgraph.types import Command, RetryPolicy

from rmprep_v2.cards import card_id
from rmprep_v2.citations import CitationBasis, citation_issues, other_version_refs, resolve
from rmprep_v2.clients import client_names, recent_client_lookup, resolved_clients
from rmprep_v2.context import estimate_tokens
from rmprep_v2.context_tools import tool_context_issues
from rmprep_v2.evidence_store import EvidenceStore
from rmprep_v2.focus import conversation_focus
from rmprep_v2.memos import turns
from rmprep_v2.numeric_checks import numeric_warnings, warning_footer
from rmprep_v2.offload import evidence_scope, hydrate_messages, offloading, stable_id
from rmprep_v2.qna_context import assemble
from rmprep_v2.qna_ledger import (
    Ledger,
    charge_retries,
    model_gate,
    new_ledger,
    note_tool_calls,
    reconcile,
    reserve,
    tool_gate,
)
from rmprep_v2.response_review import (
    _REFERENCE,
    response_advisories,
    response_issues,
    text_content,
    tool_payload,
    word_limit,
)
from rmprep_v2.retrieval import MAX_IDS, memo_directory, selected_memos
from rmprep_v2.selection import select_context
from rmprep_v2.settings import QnaLimits
from rmprep_v2.specialist import _accepted_update
from rmprep_v2.state import State
from rmprep_v2.tool_names import EVIDENCE_TOOL_NAMES

__all__ = ["QnAState", "build_qna_graph", "limitation_text", "recover"]

# Steps held back so the limitation node can always run before the step limit is reached.
_STEP_RESERVE = 5

_LIMITATIONS = {
    "validation_failed": (
        "I could not produce an answer that passed the citation and length checks, so I am "
        "not giving one. Ask about one section or record, or ask me to read a section of the "
        "stored evidence directly."
    ),
    "model_calls": (
        "This question needed more model steps than are allowed for one question. Ask about "
        "one section or record to narrow it."
    ),
    "tool_calls": (
        "This question needed more evidence lookups than are allowed for one question. Ask "
        "about one section or record to narrow it."
    ),
    "tokens": (
        "This question would use more of the model budget than one question may. Ask about "
        "one section or record to narrow it."
    ),
    "deadline": (
        "This question ran past its time limit. Try again, or ask about one section or record."
    ),
    "context_too_large": (
        "The memo and conversation are too large to send in one request. Ask about one "
        "section, such as covenants or working capital, or start a new conversation."
    ),
    "question_too_large": "The question is too long. Split it into smaller questions.",
    "no_progress": (
        "I kept repeating the same lookup without finding anything new, so I stopped. Say what "
        "you want to check, or name a section to read."
    ),
    "steps": (
        "This question needed more steps than are allowed. Ask about one section or record to "
        "narrow it."
    ),
    "model_failed": (
        "The model service did not answer this question. Nothing was saved from the attempt; "
        "please try again."
    ),
    "unverifiable": (
        "I could not check this answer against the evidence it was given, so I am not giving "
        "one. Please try again."
    ),
    "tool_failed": (
        "An evidence lookup failed while I was answering. Nothing was saved from the attempt; "
        "please try again."
    ),
}


def limitation_text(reason: str) -> str:
    """The deterministic answer for a reason the turn could not finish. It repeats no draft."""
    return _LIMITATIONS.get(
        reason, "I could not finish this question. Please try again or narrow it."
    )


class QnAState(State):
    """The conversation, as the agent graph shares it, plus what only this graph keeps."""

    ledger: NotRequired[Ledger]
    issues: NotRequired[list[str]]
    outcome: NotRequired[str]
    attempt: NotRequired[Annotated[Any, UntrackedValue(object, guard=False)]]
    """The draft being checked and the request that produced it. Never checkpointed."""
    remaining_steps: RemainingSteps


def recover(state: Any, error: NodeError) -> Command[Any]:
    """Turn a failure the graph can explain into a limitation; let a platform error through."""
    failure = error.error
    if isinstance(
        failure,
        PolicyDenied
        | BudgetExceeded
        | CredentialsExpiredError
        | ConfigurationError
        | IntegrityError,
    ):
        raise failure
    if isinstance(failure, NodeTimeoutError):
        reason = "deadline"
    else:
        reason = "model_failed" if error.node == "generate" else "tool_failed"
    # Only the class of the error is kept; its message may hold detail a user must not see.
    return Command(
        update={"outcome": reason, "context_diagnostics": {"error": type(failure).__name__}},
        goto="limitation",
    )


def build_qna_graph(
    model: Runnable[Any, AIMessage],
    tools: Sequence[BaseTool],
    prompt: str,
    *,
    evidence: EvidenceStore,
    limits: QnaLimits,
    tool_tokens: int,
    now: Callable[[], datetime],
    default_word_limit: int = 350,
) -> CompiledStateGraph[Any, Any, Any, Any]:
    """The Q&A graph, with injected governed model and tools, ready to be an agent-graph node."""

    def clock() -> float:
        return now().timestamp()

    async def select(state: QnAState) -> Command[Any]:
        current = turns(state["messages"])[-1]
        selection = state.get("selection") or select_context(
            str(current[0].content),
            state.get("memos", []),
            state.get("active_memo_id", ""),
            state.get("active_client", ""),
            creating_memo=False,
            known_names=client_names(state["messages"], state.get("memos", [])),
            recent_lookup=recent_client_lookup(state["messages"][: -len(current)]),
            active_clients=state.get("active_client_uens", []),
            focus=conversation_focus(state),
        )
        if selection["clarification"]:
            text = selection["clarification"]
            reply = AIMessage(text, id=stable_id(str(current[-1].id), len(current), text, []))
            return Command(
                update={"messages": [reply], "context_diagnostics": dict(selection)}, goto=END
            )
        return Command(
            update={"ledger": new_ledger(clock()), "issues": [], "outcome": ""}, goto="prefetch"
        )

    async def prefetch(state: QnAState) -> Command[Any]:
        """Read the cards a question is most likely to need, through the governed tool.

        Candidates are the search hits for the question and the records the memo itself cites,
        so the model can answer and cite without a discovery round trip. The read is an ordinary
        ``get_evidence`` call: it is authorized, audited and counted against the tool limit.
        """
        current = turns(state["messages"])[-1]
        memos = selected_memos(state)
        sources = [source for memo in memos for source in memo.get("source_ids", [])]
        selection: dict[str, Any] = dict(state.get("selection") or {})
        if (
            not limits.prefetch_cards
            or not sources
            or len(current) > 1
            or selection.get("mode") == "refresh"
            or tool_gate(state["ledger"], limits, requested=1)
        ):
            return Command(goto="generate")
        scope = evidence_scope()
        page = await evidence.search_cards(
            scope, sources, str(current[0].content), limit=limits.prefetch_cards
        )
        wanted = [hit.card["card_id"] for hit in page.hits]
        cited = {ref for memo in memos for ref in _REFERENCE.findall(memo["content"])}
        wanted += [card_id(source, ref) for source in sources for ref in sorted(cited)]
        known = {c["card_id"] for c in await evidence.get_cards(scope, sources, wanted)}
        chosen = [identity for identity in dict.fromkeys(wanted) if identity in known][:MAX_IDS]
        if not chosen:
            return Command(goto="generate")
        arguments = {"card_ids": chosen}
        identity = "prefetch-" + hashlib.sha256(",".join(chosen).encode()).hexdigest()[:16]
        request = AIMessage(
            "",
            id=stable_id(str(current[-1].id), len(current), identity, []),
            tool_calls=[{"name": "get_evidence", "args": arguments, "id": identity}],
            additional_kwargs={"rmprep_prefetch": True},
        )
        ledger = note_tool_calls(state["ledger"], [("get_evidence", arguments)])
        return Command(update={"messages": [request], "ledger": ledger}, goto="tools")

    def stop(reason: str, ledger: Ledger, **extra: Any) -> Command[Any]:
        return Command(update={"outcome": reason, "ledger": ledger, **extra}, goto="limitation")

    async def generate(state: QnAState) -> Command[Any]:
        scope = evidence_scope()
        messages = await hydrate_messages(state["messages"], evidence, scope)
        seen = cast(State, {**state, "messages": messages})
        current = turns(messages)[-1]
        ledger = state["ledger"]
        if state["remaining_steps"] < _STEP_RESERVE:
            return stop("steps", ledger)

        selection = state.get("selection") or select_context(
            str(current[0].content),
            state.get("memos", []),
            state.get("active_memo_id", ""),
            state.get("active_client", ""),
            creating_memo=False,
            known_names=client_names(messages, state.get("memos", [])),
            recent_lookup=recent_client_lookup(messages[: -len(current)]),
            active_clients=state.get("active_client_uens", []),
            focus=conversation_focus(seen),
        )
        # A name lookup can resolve a new client during the tool loop. Only successful tool
        # payloads establish that identity, never assistant prose.
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

        words = word_limit(messages, default_word_limit)
        output_budget = json.dumps({"output_budget_words": words, "target_words": int(words * 0.6)})
        memos = selected_memos(seen)
        directory = await memo_directory(evidence, scope, memos, cap=limits.directory_tokens)
        context = assemble(
            seen,
            prompt,
            selection,
            limits=limits,
            tool_tokens=tool_tokens,
            directory=directory,
            output_budget=output_budget,
        )
        if context.problem:
            return stop(context.problem, ledger)

        request = list(context.messages)
        issues = state.get("issues") or []
        previous = state.get("attempt")
        if issues:
            if previous is not None and previous["reply"].tool_calls:
                # A rejected tool request must not reach the provider as an unmatched call.
                request.append(
                    AIMessage(
                        "Proposed tool calls (not executed): "
                        + json.dumps(previous["reply"].tool_calls)
                    )
                )
            elif previous is not None:
                request.append(previous["reply"])
            request.append(
                HumanMessage(
                    "Revise the draft to pass these output checks: "
                    + " ".join(issues)
                    + " Return a corrected tool call or answer. Preserve evidence limits; "
                    "do not add unsupported claims while shortening. "
                    f"Aim for at most {max(1, int(words * 0.6))} words. "
                    "Remove secondary figures and repeated explanations before shortening "
                    "source references or omitting evidence limits."
                )
            )
        planned = estimate_tokens(request) + tool_tokens
        if reason := model_gate(ledger, limits, clock(), planned_input=planned):
            return stop(reason, ledger, context_diagnostics=context.diagnostics)

        ledger = reserve(ledger, limits, planned)
        reply = await model.ainvoke(request)
        reply = reply.model_copy(
            update={
                "id": stable_id(
                    str(current[-1].id), len(current), text_content(reply), reply.tool_calls
                )
            }
        )
        ledger = charge_retries(
            ledger,
            limits,
            planned_input=planned,
            attempts=reply.response_metadata.get("agentcraft2_attempts"),
        )
        ledger = reconcile(ledger, limits, planned_input=planned, usage=reply.usage_metadata)

        visibility_id = (
            "vis-" + hashlib.sha256(f"{reply.id}|{ledger['model_calls']}".encode()).hexdigest()[:20]
        )
        await evidence.put_visibility(
            scope,
            visibility_id,
            {
                "reply_id": reply.id,
                "model_call": ledger["model_calls"],
                "memo_ids": selection["memo_ids"],
                "visible_refs": context.visible_refs,
                "basis": context.basis.to_record(),
                "evicted_tool_message_ids": context.diagnostics["evicted_tool_message_ids"],
                "planned_input_tokens": planned,
            },
        )
        attempt = {
            "reply": reply,
            "messages": context.messages,
            "selection": selection,
            "diagnostics": {**context.diagnostics, "visibility_id": visibility_id},
            "limit": words,
            "needs_citation": context.needs_citation,
            "memo_texts": [str(memo["content"]) for memo in memos],
            "fresh_data": any(
                tool_payload(m) and m.name not in EVIDENCE_TOOL_NAMES
                for m in current
                if isinstance(m, ToolMessage)
            ),
        }
        return Command(update={"attempt": attempt, "ledger": ledger}, goto="validate")

    async def validate(
        state: QnAState,
    ) -> Command[Any]:
        attempt = state.get("attempt")
        ledger = state["ledger"]
        if attempt is None:
            # A resume lost the draft, which is deliberately not checkpointed: ask again.
            return Command(goto="generate")
        reply: AIMessage = attempt["reply"]
        selection = attempt["selection"]
        # What the answer may cite is what the final model call was shown, as recorded then.
        issues = response_issues(reply, attempt["messages"][1:], attempt["limit"], citations=False)
        released: list[dict[str, str]] = []
        if not reply.tool_calls:
            scope = evidence_scope()
            record = await evidence.get_visibility(scope, attempt["diagnostics"]["visibility_id"])
            if record is None:
                return stop("unverifiable", ledger)
            basis = CitationBasis.from_record(record["basis"])
            text = text_content(reply)
            findings = resolve(text, basis)
            unknown = {f.ref for f in findings if f.status == "unknown"}
            if unknown and (
                other := await other_version_refs(
                    evidence, scope, state.get("memos", []), selection["memo_ids"], unknown
                )
            ):
                findings = resolve(text, basis.with_other_versions(other))
            issues += citation_issues(findings, needs_citation=attempt["needs_citation"])
            released = [{"ref": f.ref, "status": f.status} for f in findings if f.status == "valid"]
        issues += tool_context_issues(
            reply, selection, state.get("memos", []), now().date().isoformat()
        )
        if selection["mode"] == "refresh" and not reply.tool_calls and not attempt["fresh_data"]:
            issues.append(
                "Retrieve current evidence before describing what has changed; otherwise state "
                "that current evidence is unavailable."
            )
        if issues:
            if ledger["revisions"] >= limits.max_revisions:
                return stop("validation_failed", ledger)
            return Command(
                update={
                    "issues": issues,
                    "ledger": {**ledger, "revisions": ledger["revisions"] + 1},
                },
                goto="generate",
            )
        if not reply.tool_calls:
            return Command(
                update={"issues": [], "attempt": {**attempt, "released": released}},
                goto="commit",
            )
        if reason := tool_gate(ledger, limits, requested=len(reply.tool_calls)):
            return stop(reason, ledger)
        ledger = note_tool_calls(
            ledger, [(call["name"], call["args"]) for call in reply.tool_calls]
        )
        return Command(update={"messages": [reply], "ledger": ledger, "issues": []}, goto="tools")

    async def commit(state: QnAState) -> dict[str, Any]:
        attempt = state["attempt"]
        reply: AIMessage = attempt["reply"]
        messages = await hydrate_messages(state["messages"], evidence, evidence_scope())
        text = text_content(reply)
        # Unsupported figures are caveats, not blocks: they travel with the text, so copying or
        # exporting the answer keeps them, and they are recorded beside it for a reader to inspect.
        warnings = numeric_warnings(text, attempt["messages"][1:], memo_texts=attempt["memo_texts"])
        sources = await _sources(attempt["released"], state, attempt["selection"])
        reply = reply.model_copy(
            update={
                "content": text + warning_footer(warnings) if warnings else reply.content,
                "additional_kwargs": {
                    **reply.additional_kwargs,
                    "rmprep_warnings": warnings,
                    "rmprep_citations": sources,
                },
            }
        )
        diagnostics = {
            **attempt["diagnostics"],
            "ledger": state["ledger"],
            "review_advisories": response_advisories(reply, attempt["messages"][1:]),
            "warnings": len(warnings),
        }
        return _accepted_update(
            cast(State, {**state, "messages": messages}),
            reply,
            None,
            attempt["selection"],
            diagnostics,
        )

    async def _sources(
        released: Sequence[Mapping[str, str]], state: QnAState, selection: Mapping[str, Any]
    ) -> list[dict[str, Any]]:
        """For each citation released, which memo version and card it points at, for inspection."""
        memos = [m for m in state.get("memos", []) if m["memo_id"] in selection["memo_ids"]]
        owner = {s: m["memo_id"] for m in memos for s in m.get("source_ids", [])}
        refs = [r["ref"] for r in released if not r["ref"].startswith("calculation[")]
        wanted = {card_id(source, ref): (source, ref) for source in owner for ref in refs}
        found = await evidence.get_cards(evidence_scope(), list(owner), list(wanted))
        cards = {wanted[c["card_id"]][1]: c for c in found if c["card_id"] in wanted}
        return [
            {
                "ref": r["ref"],
                "status": r["status"],
                "kind": "calculation" if r["ref"].startswith("calculation[") else "record",
                "memo_id": owner[cards[r["ref"]]["source_id"]] if r["ref"] in cards else None,
                "card_id": cards[r["ref"]]["card_id"] if r["ref"] in cards else None,
            }
            for r in released
        ]

    async def limitation(state: QnAState) -> dict[str, Any]:
        reason = state.get("outcome") or "unknown"
        current = turns(state["messages"])[-1]
        text = limitation_text(reason)
        reply = AIMessage(
            text,
            id=stable_id(str(current[-1].id), len(current), text, []),
            additional_kwargs={"rmprep_limitation": reason},
        )
        earlier = state.get("context_diagnostics") or {}
        diagnostics = {
            **{k: v for k, v in earlier.items() if k in ("error", "planned_input_tokens")},
            "limitation": reason,
            "ledger": state.get("ledger"),
        }
        return {"messages": [reply], "context_diagnostics": diagnostics}

    builder = StateGraph(QnAState, context_schema=RequestContext)
    builder.add_node("select", select, destinations=("prefetch", END))
    builder.add_node("prefetch", prefetch, destinations=("tools", "generate"))
    builder.add_node(
        "generate",
        generate,
        error_handler=recover,
        timeout=limits.timeout_seconds,
        destinations=("validate", "limitation"),
    )
    builder.add_node(
        "validate", validate, destinations=("generate", "tools", "commit", "limitation")
    )
    builder.add_node(
        "tools",
        cast(Any, offloading(ToolNode(tools), evidence)),
        error_handler=recover,
        timeout=limits.timeout_seconds,
        # A locked or briefly unavailable evidence database is worth another try. The platform
        # already retries a transient failure of a governed model or tool call.
        retry_policy=RetryPolicy(
            max_attempts=3, initial_interval=0.05, retry_on=sqlite3.OperationalError
        ),
    )
    builder.add_node("commit", commit)
    builder.add_node("limitation", limitation)
    builder.add_edge(START, "select")
    builder.add_edge("tools", "generate")
    builder.add_edge("commit", END)
    builder.add_edge("limitation", END)
    return builder.compile().with_config(recursion_limit=limits.recursion_limit)
