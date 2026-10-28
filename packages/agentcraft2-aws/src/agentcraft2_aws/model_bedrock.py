"""The Amazon Bedrock model provider.

The ``langchain-aws`` package is an optional dependency; install the
``bedrock`` extra of ``agentcraft2-aws`` to use this provider.

A developer reaches Bedrock with an SSO profile and, behind a TLS-inspecting
proxy, the enterprise CA file. Both come from configuration; nothing else
changes between the Anthropic API and Bedrock.

Prompt caching uses the Converse API's own cache points, not the Anthropic wire format. See
``BedrockModelOptions``.
"""

from __future__ import annotations

from typing import Any, Literal

from langchain_core.messages import BaseMessage, SystemMessage
from pydantic import Field

from agentcraft2_aws.session import AwsSessionFactory
from agentcraft2_core.contracts import (
    AgentCraft2Error,
    ConfigurationError,
    ModelCapabilities,
    OptionsModel,
)

__all__ = ["BedrockChatModelProvider", "BedrockModelOptions"]

_INSTALL_HINT = "install it with: pip install 'agentcraft2-aws[bedrock]'"
_WHAT = "the bedrock model provider"
_STATIC_PREFIX = "agentcraft2_static_prefix"


class BedrockModelOptions(OptionsModel):
    """Bedrock prompt caching through the Converse API; every call still runs the model pipeline.

    Set these under ``bedrock`` in the model options map. Caching is off by default. A request
    needs a prompt prefix of at least the model's minimum (1,024 tokens for Claude Sonnet 4.5,
    4,096 for Claude Opus 4.5 and Haiku 4.5) before a cache point takes effect; a shorter prefix
    still succeeds but is not cached. Whether a call hit the cache is in its usage, not here.
    """

    prompt_caching: bool = Field(
        default=False,
        strict=True,
        description="Cache the static prompt prefix and the conversation tail.",
    )
    cache_ttl: Literal["5m", "1h"] = Field(
        default="5m",
        description="Cache lifetime. The model must support a one-hour cache to use 1h.",
    )


class BedrockChatModelProvider:
    """Builds chat models served by Amazon Bedrock through the Converse API.

    The SDK clients are injected into every model, so a model never builds a
    client of its own from the process environment.

    Args:
        sessions: Builds the Bedrock clients.
        options: Prompt caching settings. Disabled by default.

    Raises:
        ConfigurationError: If the optional dependency is missing, or the
            clients cannot be built.
    """

    def __init__(
        self, sessions: AwsSessionFactory, *, options: BedrockModelOptions | None = None
    ) -> None:
        try:
            from langchain_aws import ChatBedrockConverse
        except ImportError as exc:
            raise ConfigurationError(
                f"{_WHAT} is not installed; {_INSTALL_HINT}",
                fix=(
                    'in a workspace, add "agentcraft2-aws[bedrock]" to the service\'s '
                    "dependencies in its pyproject.toml, then run 'agentcraft2 install'"
                ),
            ) from exc
        self._chat_model = ChatBedrockConverse
        self._sessions = sessions
        self._options = options or BedrockModelOptions()
        # The model pipeline's resilience stage owns retries; one SDK attempt each.
        self._runtime = sessions.client("bedrock-runtime", retried_by_pipeline=True)
        # The control plane client is used only to resolve an inference profile to its model.
        self._control = sessions.client("bedrock")

    def __repr__(self) -> str:
        return f"BedrockChatModelProvider(region={self._sessions.region!r})"

    @property
    def capabilities(self) -> ModelCapabilities:
        """What Bedrock offers through the Converse API."""
        return ModelCapabilities(
            tool_calling=True,
            structured_output=True,
            prompt_caching=True,
            token_counting=True,
        )

    def prepare_request(
        self, messages: list[BaseMessage], options: dict[str, Any]
    ) -> tuple[list[BaseMessage], dict[str, Any]]:
        """Mark the static prefix of the prompt for the Converse cache, and cache the tail.

        A system message built with ``static_system_prompt`` marks its first block as stable. A
        cache point follows that block, so the instructions (and the tool definitions, which the
        API reads first) stay cached while the request context after it changes. The
        ``cache_control`` setting lets ``langchain-aws`` add its own points for the conversation's
        tail, within Bedrock's limit of four. A five-minute cache never names its TTL: a model
        that supports only five minutes can reject the field. Copies keep these directives out of
        the durable checkpoint.
        """
        if not self._options.prompt_caching:
            return list(messages), dict(options)
        prepared_options = dict(options)
        prepared_options.setdefault("cache_control", {"ttl": self._options.cache_ttl})
        point: dict[str, str] = {"type": "default"}
        if self._options.cache_ttl != "5m":
            point["ttl"] = self._options.cache_ttl
        prepared: list[BaseMessage] = []
        marked = False
        for message in messages:
            if (
                not marked
                and isinstance(message, SystemMessage)
                and message.additional_kwargs.get(_STATIC_PREFIX) is True
                and isinstance(message.content, list)
                and message.content
            ):
                blocks = [message.content[0], {"cachePoint": point}, *message.content[1:]]
                message = message.model_copy(update={"content": blocks})
                marked = True
            prepared.append(message)
        return prepared, prepared_options

    def create(self, model_id: str) -> object:
        """Return a ``ChatBedrockConverse`` model for a model or inference-profile ID."""
        return self._chat_model(
            model_id=model_id, client=self._runtime, bedrock_client=self._control
        )

    def classify_error(self, error: BaseException) -> AgentCraft2Error | None:
        """Map AWS SDK errors to the taxonomy.

        An expired SSO sign-in becomes ``CredentialsExpiredError`` and names
        the command that renews it. The SDK's message is not copied.
        """
        return self._sessions.classify(error, what=_WHAT)
