"""Prompt caching through the Converse API: cache points on the wire, and what the usage says."""

from __future__ import annotations

from typing import Any, Literal

import pytest
from botocore.stub import Stubber
from langchain_aws import ChatBedrockConverse
from langchain_core.messages import HumanMessage, SystemMessage

from agentcraft2_aws.model_bedrock import BedrockChatModelProvider, BedrockModelOptions
from agentcraft2_aws.pack import register_aws_adapters
from agentcraft2_aws.testing import offline_sessions
from agentcraft2_core import Principal, RequestContext, bind_request_context
from agentcraft2_core.config import ConfigResolver, MappingConfigSource
from agentcraft2_core.contracts import ConfigurationError, ProviderSelection, Section
from agentcraft2_core.di import ServiceContainer
from agentcraft2_core.integrations.langgraph import static_system_prompt
from agentcraft2_core.testing import Fakes

MODEL = "anthropic.claude-sonnet-4-5-20250929-v1:0"
CONTEXT = RequestContext(
    principal=Principal(subject="u-1", tenant="t-9"),
    application="accounts-agent",
    request_id="r-1",
    thread_id="th-1",
)
POINT = {"cachePoint": {"type": "default"}}


def provider(enabled: bool = True, ttl: Literal["5m", "1h"] = "5m") -> BedrockChatModelProvider:
    return BedrockChatModelProvider(
        offline_sessions(), options=BedrockModelOptions(prompt_caching=enabled, cache_ttl=ttl)
    )


def static(*blocks: str) -> SystemMessage:
    return static_system_prompt(blocks[0], "\n".join(blocks[1:]))


# --- the options ----------------------------------------------------------------------------------


def test_caching_is_off_unless_asked_for() -> None:
    options = BedrockModelOptions()
    assert (options.prompt_caching, options.cache_ttl) == (False, "5m")


@pytest.mark.parametrize(
    "options", [{"prompt_cache": True}, {"cache_ttl": "10m"}, {"prompt_caching": "yes"}]
)
def test_invalid_cache_settings_are_rejected(options: dict[str, object]) -> None:
    with pytest.raises(ConfigurationError):
        ProviderSelection("bedrock", options).parse_options(BedrockModelOptions)


# --- the request ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("ttl", "point"),
    [("5m", POINT), ("1h", {"cachePoint": {"type": "default", "ttl": "1h"}})],
)
def test_the_static_prefix_is_followed_by_a_cache_point_and_the_rest_is_not_cached(
    ttl: Literal["5m", "1h"], point: dict[str, Any]
) -> None:
    messages = [static("Fixed instructions", "Client and date"), HumanMessage("q")]
    before = [m.model_dump() for m in messages]
    prepared, options = provider(ttl=ttl).prepare_request(messages, {})
    blocks = prepared[0].content
    assert blocks[0] == {"type": "text", "text": "Fixed instructions"}
    assert blocks[1] == point
    assert blocks[2] == {"type": "text", "text": "Client and date"}
    assert prepared[0].additional_kwargs == messages[0].additional_kwargs
    assert [m.model_dump() for m in messages] == before, "the saved messages are not changed"
    # The conversation's own tail is cached by langchain-aws from this setting.
    assert options == {"cache_control": {"ttl": ttl}}


def test_a_five_minute_cache_never_names_its_ttl() -> None:
    """A model that supports only five minutes can reject a ttl field."""
    prepared, options = provider(ttl="5m").prepare_request([static("Fixed")], {})
    assert "ttl" not in str(prepared[0].content[1])
    assert options == {"cache_control": {"ttl": "5m"}}


def test_caching_off_changes_nothing() -> None:
    messages = [static("Fixed", "Changing"), HumanMessage("q")]
    prepared, options = provider(enabled=False).prepare_request(messages, {"stop": ["x"]})
    assert prepared == messages
    assert options == {"stop": ["x"]}


def test_a_system_message_with_no_static_boundary_is_left_alone() -> None:
    messages = [SystemMessage("Possibly dynamic"), HumanMessage("q")]
    prepared, options = provider().prepare_request(messages, {})
    assert prepared == messages
    assert options == {"cache_control": {"ttl": "5m"}}


def test_only_the_first_static_message_gets_a_cache_point() -> None:
    messages = [static("One"), static("Two"), HumanMessage("q")]
    prepared, _ = provider().prepare_request(messages, {})
    assert POINT in prepared[0].content
    assert POINT not in prepared[1].content


def test_a_request_that_already_asks_for_a_cache_setting_keeps_it() -> None:
    _, options = provider(ttl="1h").prepare_request(
        [static("Fixed")], {"cache_control": {"ttl": "5m"}}
    )
    assert options == {"cache_control": {"ttl": "5m"}}


# --- what is sent to Bedrock ------------------------------------------------------------


class Recorder:
    """Stands in for the runtime client: remembers the request and answers it."""

    def __init__(self, usage: dict[str, int]) -> None:
        self.requests: list[dict[str, Any]] = []
        self._usage = usage

    def converse(self, **request: Any) -> dict[str, Any]:
        self.requests.append(request)
        return {
            "output": {"message": {"role": "assistant", "content": [{"text": "ok"}]}},
            "stopReason": "end_turn",
            "usage": self._usage,
            "metrics": {"latencyMs": 1},
        }


def send(adapter: BedrockChatModelProvider, messages: list[Any], usage: dict[str, int]) -> Any:
    native = adapter.create(MODEL)
    assert isinstance(native, ChatBedrockConverse)
    recorder = Recorder(usage)
    native.client = recorder
    prepared, options = adapter.prepare_request(messages, {})
    reply = native.invoke(prepared, **options)
    return recorder.requests[0], reply


PLAIN_USAGE = {"inputTokens": 12, "outputTokens": 3, "totalTokens": 15}


def test_the_cache_point_sits_after_the_static_instructions_in_the_converse_system_field() -> None:
    request, _ = send(
        provider(),
        [static("Fixed instructions", "Client and date"), HumanMessage("q")],
        PLAIN_USAGE,
    )
    assert request["system"][:3] == [
        {"text": "Fixed instructions"},
        POINT,
        {"text": "Client and date"},
    ]
    points = [b for b in request["system"] if "cachePoint" in b]
    assert len(points) == 1, "langchain-aws adds no second system point beside ours"


def test_a_one_hour_cache_names_its_ttl_on_the_wire() -> None:
    request, _ = send(
        provider(ttl="1h"), [static("Fixed", "Moving"), HumanMessage("q")], PLAIN_USAGE
    )
    assert request["system"][1] == {"cachePoint": {"type": "default", "ttl": "1h"}}


def test_the_conversations_tail_is_cached_and_the_total_stays_within_bedrocks_limit() -> None:
    request, _ = send(provider(), [static("Fixed", "Moving"), HumanMessage("q")], PLAIN_USAGE)
    messages = request["messages"]
    tail = messages[-1]["content"]
    assert tail[-1] == POINT
    count = sum("cachePoint" in b for b in request["system"]) + sum(
        "cachePoint" in b for m in messages for b in m["content"]
    )
    assert count <= 4


def test_nothing_is_added_to_the_wire_when_caching_is_off() -> None:
    request, _ = send(
        provider(enabled=False), [static("Fixed", "Moving"), HumanMessage("q")], PLAIN_USAGE
    )
    assert "cachePoint" not in str(request)


# --- what the usage says ----------------------------------------------------------------------


def test_cached_tokens_are_part_of_the_input_total_and_counted_once() -> None:
    usage = {
        "inputTokens": 50,
        "outputTokens": 20,
        "totalTokens": 1070,
        "cacheReadInputTokens": 900,
        "cacheWriteInputTokens": 100,
    }
    _, reply = send(provider(), [static("Fixed", "Moving"), HumanMessage("q")], usage)
    reported = reply.usage_metadata
    # Bedrock's inputTokens are only the uncached ones; the total is what the call really sent.
    assert reported["input_tokens"] == 50 + 900 + 100
    assert reported["input_token_details"]["cache_read"] == 900
    assert reported["input_token_details"]["cache_creation"] == 100


async def test_the_governed_model_audits_the_full_input_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sessions, fakes = offline_sessions(), Fakes()
    config = ConfigResolver(
        MappingConfigSource(
            {
                "AC2_MODEL_PROVIDER": "bedrock",
                "AC2_MODEL_ID": MODEL,
                "AWS_REGION": "eu-west-1",
                "AC2_LIMITS": '{"model": {"retries": 0}}',
                "AC2_MODEL_OPTIONS": '{"bedrock":{"prompt_caching":true,"cache_ttl":"1h"}}',
            }
        )
    ).resolve()
    for section in Section:
        config = config.with_section(section, ProviderSelection("fake"))
    providers = register_aws_adapters(fakes.providers(), sessions=sessions)
    seen: list[dict[str, Any]] = []
    client = sessions.client("bedrock-runtime", retried_by_pipeline=True)
    client.meta.events.register(
        "before-parameter-build.bedrock-runtime.Converse",
        lambda params, **_: seen.append(dict(params)),
    )
    usage = {
        "inputTokens": 50,
        "outputTokens": 20,
        "totalTokens": 1070,
        "cacheReadInputTokens": 900,
        "cacheWriteInputTokens": 100,
    }
    with Stubber(client) as bedrock:
        bedrock.add_response(
            "converse",
            {
                "output": {"message": {"role": "assistant", "content": [{"text": "ok"}]}},
                "stopReason": "end_turn",
                "usage": usage,
                "metrics": {"latencyMs": 1},
            },
        )
        async with ServiceContainer(
            config, providers, clock=fakes.clock, ids=fakes.ids
        ) as services:
            with bind_request_context(CONTEXT):
                reply = await services.model().ainvoke(
                    [static("Fixed", "Moving"), HumanMessage("q")]
                )
    assert seen[0]["system"][1] == {"cachePoint": {"type": "default", "ttl": "1h"}}
    assert reply.usage_metadata["input_tokens"] == 1050
    (record,) = fakes.audit.records
    assert (record.attributes["input_tokens"], record.attributes["output_tokens"]) == (1050, 20)
