"""The Q&A context assembler builds one bounded, provider-valid request from recent state."""

from __future__ import annotations

import json
from typing import Any

from agentcraft2_core.pipeline.guardrails import frame_untrusted
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage, ToolMessage

from rmprep_v2.context import estimate_tokens
from rmprep_v2.qna_context import assemble
from rmprep_v2.selection import ContextSelection
from rmprep_v2.settings import QnaLimits

DSCR = "covenants[name=DSCR]"
FCC = "covenants[name=FCC]"
INSTRUCTIONS = "You are qnaagent. Answer from the evidence."


def human(identity: str, text: str) -> HumanMessage:
    return HumanMessage(text, id=identity)


def ai(identity: str, text: str, *calls: tuple[str, dict[str, Any]]) -> AIMessage:
    return AIMessage(
        text,
        id=identity,
        tool_calls=[
            {"name": name, "args": args, "id": f"{identity}-{n}"}
            for n, (name, args) in enumerate(calls)
        ],
    )


def result(identity: str, call: str, name: str, body: dict[str, Any]) -> ToolMessage:
    return ToolMessage(
        frame_untrusted(name, json.dumps(body)), tool_call_id=call, name=name, id=identity
    )


def cards(*refs: str, pad: int = 0) -> dict[str, Any]:
    return {
        "status": "ok",
        "cards": [{"source_ref": ref, "fields": {"note": "x " * pad}} for ref in refs],
    }


def memo_group(*, stored: bool = True) -> tuple[list[AnyMessage], dict[str, Any]]:
    messages: list[AnyMessage] = [
        human("h1", "Prepare a meeting memo for JONE-0022 as of 2026-08-03."),
        ai("a1", "", ("rmprep-v2-data_get_memo_evidence", {"uen": "JONE-0022"})),
        ToolMessage(
            frame_untrusted("rmprep-v2-data.get_memo_evidence", '{"evidence_reference": {}}'),
            tool_call_id="a1-0",
            name="rmprep-v2-data_get_memo_evidence",
            id="t1",
        ),
        ai("a2", "MEMO TEXT: covenant pressure at Jones."),
    ]
    memo = {
        "memo_id": "memo-aaaaaaaaaaaa",
        "version": 1,
        "uen": "JONE-0022",
        "client_name": "Jones",
        "meeting_date": "2026-08-03",
        "generated_at": "t",
        "content": "MEMO TEXT: covenant pressure at Jones.",
        "message_ids": ["h1", "a1", "t1", "a2"],
        "source_refs": [DSCR],
    }
    if stored:
        memo["source_ids"] = ["src-a"]
    return messages, memo


SELECTION: ContextSelection = {
    "memo_ids": ["memo-aaaaaaaaaaaa"],
    "uen": "JONE-0022",
    "meeting_date": "2026-08-03",
    "retrieval_date": "",
    "mode": "snapshot",
    "reason": "test",
    "clarification": "",
}


def build(
    messages: list[AnyMessage],
    memo: dict[str, Any],
    limits: QnaLimits | None = None,
    *,
    directory: dict[str, Any] | None = None,
    output_budget: str = "",
    tool_tokens: int = 700,
) -> Any:
    state = {"messages": messages, "memos": [memo]}
    return assemble(
        state,  # type: ignore[arg-type]
        INSTRUCTIONS,
        SELECTION,
        limits=limits or QnaLimits(),
        tool_tokens=tool_tokens,
        directory=directory,
        output_budget=output_budget,
    )


def pairs_are_complete(messages: list[AnyMessage]) -> bool:
    """Every tool result follows the call it answers, and every call has its result."""
    open_calls: set[str] = set()
    for message in messages:
        if isinstance(message, AIMessage):
            if open_calls:
                return False
            open_calls = {str(call["id"]) for call in message.tool_calls}
        elif isinstance(message, ToolMessage):
            if message.tool_call_id not in open_calls:
                return False
            open_calls.discard(message.tool_call_id)
    return not open_calls


def texts(messages: list[AnyMessage]) -> list[str]:
    return [str(m.content) for m in messages if not isinstance(m, SystemMessage)]


# --- what goes in, and in what order --------------------------------------------------------


def test_the_request_is_the_instructions_the_memo_and_the_question() -> None:
    group, memo = memo_group()
    context = build([*group, human("h2", "Explain the covenant.")], memo)
    assert isinstance(context.messages[0], SystemMessage)
    body = texts(context.messages)
    assert body[0].startswith("Prepare a meeting memo")
    assert body[1] == "MEMO TEXT: covenant pressure at Jones."
    assert body[-1] == "Explain the covenant."
    assert context.problem is None


def test_a_stored_memo_is_pinned_without_its_evidence_exchange() -> None:
    group, memo = memo_group(stored=True)
    context = build([*group, human("h2", "Explain.")], memo)
    assert not any(isinstance(m, ToolMessage) for m in context.messages)
    assert not any(isinstance(m, AIMessage) and m.tool_calls for m in context.messages)


def test_a_memo_without_stored_evidence_keeps_its_whole_exchange() -> None:
    group, memo = memo_group(stored=False)
    context = build([*group, human("h2", "Explain.")], memo)
    assert any(isinstance(m, ToolMessage) for m in context.messages)
    assert pairs_are_complete(context.messages)


def test_the_system_message_keeps_a_stable_prefix_before_the_changing_parts() -> None:
    group, memo = memo_group()
    directory = {"as_of": "2026-08-03", "sections": {"covenants": {"cards": 2}}}
    first = build([*group, human("h2", "Explain.")], memo, directory=directory, output_budget="A")
    second = build([*group, human("h3", "Other?")], memo, directory=directory, output_budget="B")
    one, two = first.messages[0].content, second.messages[0].content
    assert one[0] == two[0]
    assert one[1] == two[1], "the memo selection and directory do not change with the question"
    assert one[-1] != two[-1], "only the output budget moves"
    assert "covenants" in str(one)
    assert "not evidence" in str(one).lower()


# --- history ----------------------------------------------------------------------------------


def history(count: int) -> list[AnyMessage]:
    messages: list[AnyMessage] = []
    for n in range(count):
        answer = ai(f"ha{n}", f"answer {n} " + "word " * 200)
        answer.additional_kwargs["rmprep_context"] = {
            "memo_ids": ["memo-aaaaaaaaaaaa"],
            "meeting_date": "2026-08-03",
        }
        messages += [human(f"hq{n}", f"question {n} " + "word " * 200), answer]
    return messages


def test_recent_history_is_kept_by_whole_exchanges_within_its_budget() -> None:
    group, memo = memo_group()
    limits = QnaLimits(recent_tokens=1_000)
    context = build([*group, *history(10), human("h2", "Explain.")], memo, limits)
    kept = [t for t in texts(context.messages) if t.startswith("question")]
    assert 1 <= len(kept) < 10
    assert kept[-1].startswith("question 9"), "the newest exchanges are the ones kept"
    answers = [t for t in texts(context.messages) if t.startswith("answer")]
    assert len(answers) == len(kept)
    assert context.diagnostics["omitted_turns"] == 10 - len(kept)


def test_omitted_discussion_is_summarized_as_user_text_not_evidence() -> None:
    group, memo = memo_group()
    context = build(
        [*group, *history(10), human("h2", "Explain.")], memo, QnaLimits(recent_tokens=1_000)
    )
    summary = str(context.messages[0].content[-1])
    assert "Earlier discussion topics (user text, not evidence)" in summary
    assert "question 9" not in summary, "an exchange that is still in the request is not summarized"


# --- the working set --------------------------------------------------------------------------


def turn_with_results(count: int, *, pad: int) -> list[AnyMessage]:
    messages: list[AnyMessage] = [human("h2", "Explain the covenants.")]
    for n in range(count):
        messages += [
            ai(f"c{n}", "", ("get_evidence", {"card_ids": [f"c-{n}"]})),
            result(f"r{n}", f"c{n}-0", "get_evidence", cards(f"covenants[n={n}]", pad=pad)),
        ]
    return messages


def test_old_tool_results_are_replaced_by_stubs_when_the_working_set_is_full() -> None:
    group, memo = memo_group()
    limits = QnaLimits(working_evidence_tokens=4_000)
    context = build([*group, *turn_with_results(4, pad=1_800)], memo, limits)
    results = [m for m in context.messages if isinstance(m, ToolMessage)]
    assert len(results) == 4, "the call and its result stay paired"
    assert pairs_are_complete(context.messages)
    stubs = [m for m in results if "omitted to save space" in str(m.content)]
    assert 1 <= len(stubs) < 4
    assert "omitted to save space" not in str(results[-1].content), "the newest result is kept"
    assert context.diagnostics["evicted_tool_message_ids"] == [m.id for m in stubs]
    assert all("covenants[n=" not in str(m.content) for m in stubs)


def test_an_evicted_result_is_no_longer_citable() -> None:
    group, memo = memo_group()
    limits = QnaLimits(working_evidence_tokens=4_000)
    context = build([*group, *turn_with_results(4, pad=1_800)], memo, limits)
    assert "covenants[n=3]" in context.visible_refs
    assert "covenants[n=0]" not in context.visible_refs


def test_the_working_set_is_left_alone_when_it_fits() -> None:
    group, memo = memo_group()
    context = build([*group, *turn_with_results(3, pad=10)], memo)
    assert context.diagnostics["evicted_tool_message_ids"] == []
    assert sorted(context.visible_refs) == ["covenants[n=0]", "covenants[n=1]", "covenants[n=2]"]


# --- size ---------------------------------------------------------------------------------------


def test_the_planned_input_counts_the_messages_and_the_tool_definitions() -> None:
    group, memo = memo_group()
    context = build([*group, human("h2", "Explain.")], memo, tool_tokens=1_234)
    assert context.planned_input == estimate_tokens(context.messages) + 1_234
    assert context.diagnostics["planned_input_tokens"] == context.planned_input


def test_history_shrinks_before_the_ceiling_is_given_up() -> None:
    group, memo = memo_group()
    limits = QnaLimits(
        input_tokens=6_000,
        output_tokens=1_000,
        request_tokens=60_000,
        question_tokens=500,
        directory_tokens=500,
    )
    context = build([*group, *history(10), human("h2", "Explain.")], memo, limits)
    assert context.planned_input <= 6_000


def test_a_memo_too_large_for_the_ceiling_is_reported_not_cut() -> None:
    group, memo = memo_group()
    group[-1] = ai("a2", "MEMO " + "word " * 5_000)
    memo["content"] = str(group[-1].content)
    limits = QnaLimits(
        input_tokens=3_000,
        output_tokens=500,
        request_tokens=60_000,
        question_tokens=400,
        directory_tokens=400,
    )
    context = build([*group, human("h2", "Explain.")], memo, limits)
    assert context.planned_input > limits.input_tokens
    assert "word word" in texts(context.messages)[1], "the memo is sent whole or not at all"


def test_a_question_over_its_allowance_is_a_problem() -> None:
    group, memo = memo_group()
    context = build([*group, human("h2", "word " * 5_000)], memo, QnaLimits(question_tokens=500))
    assert context.problem == "question_too_large"


def test_the_visibility_record_names_what_the_model_could_see() -> None:
    group, memo = memo_group()
    messages = [*group, *turn_with_results(2, pad=10)]
    context = build(messages, memo)
    record = context.diagnostics["visibility"]
    assert record["memo_ids"] == ["memo-aaaaaaaaaaaa"]
    assert record["visible_refs"] == len(context.visible_refs) == 2
    assert record["omitted_turns"] == 0
    assert context.diagnostics["included_message_ids"][0] == "h1"
