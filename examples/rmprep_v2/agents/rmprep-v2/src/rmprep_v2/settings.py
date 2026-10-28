"""Load service-specific context limits at startup using the library's config sources."""

from __future__ import annotations

from dataclasses import dataclass, fields
from pathlib import Path

from agentcraft2_core.config import DotenvConfigSource, EnvironConfigSource, LayeredConfigSource
from agentcraft2_core.contracts import ConfigSource, ConfigurationError

from rmprep_v2.context import ContextBudget

__all__ = ["ENV_FILE", "QnaLimits", "load_context_budget", "load_qna_limits"]

ENV_FILE = Path(__file__).resolve().parents[2] / ".env"
"""The service's ``.env`` file, beside ``pyproject.toml``.

A deployed service has no such file and is configured by its environment.
"""

_CONTEXT_SETTINGS = {
    "RMPREP_CONTEXT_INPUT_TOKENS": "input_tokens",
    "RMPREP_CONTEXT_QUESTION_TOKENS": "question_tokens",
    "RMPREP_CONTEXT_RECENT_TOKENS": "recent_tokens",
    "RMPREP_CONTEXT_TOOL_SCHEMA_TOKENS": "tool_schema_tokens",
}


def load_context_budget(
    dotenv_path: Path | None = ENV_FILE, *, source: ConfigSource | None = None
) -> ContextBudget:
    """Snapshot context settings; process variables override the agent's .env.

    Pass a ConfigSource for isolated tests or another configuration backend.
    Missing settings use ContextBudget defaults. Invalid or misspelled context
    settings fail startup, before any model calls. Reading never changes os.environ.
    """
    if source is None:
        layers: list[ConfigSource] = [EnvironConfigSource()]
        if dotenv_path is not None:
            layers.append(DotenvConfigSource(dotenv_path))
        source = LayeredConfigSource(*layers)
    unknown = sorted(
        name
        for name in source.names()
        if name.startswith("RMPREP_CONTEXT_") and name not in _CONTEXT_SETTINGS
    )
    if unknown:
        raise ConfigurationError("Unknown rmprep-v2 context setting: " + ", ".join(unknown))
    values: dict[str, int] = {}
    for name, field in _CONTEXT_SETTINGS.items():
        raw = source.get(name)
        if raw is None:
            continue
        if not raw.strip().isascii() or not raw.strip().isdecimal() or int(raw) <= 0:
            raise ConfigurationError(f"{name} must be a positive whole number of tokens")
        values[field] = int(raw)
    try:
        return ContextBudget(**values)
    except ValueError as error:
        raise ConfigurationError(f"Invalid RMPREP_CONTEXT_* settings: {error}") from None


_MAY_BE_ZERO = frozenset({"prefetch_cards", "max_revisions"})


@dataclass(frozen=True)
class QnaLimits:
    """What one Q&A question may use. All are planning limits, not provider-exact counts.

    The token limits apply to the estimator in ``rmprep_v2.context``. The call limits count
    logical operations: a tool call counts once however it was served, and every model
    attempt, including a revision or a retry, counts against ``max_model_calls``.
    """

    input_tokens: int = 40_000
    """The most one model request may hold, tool definitions included."""
    output_tokens: int = 4_000
    """The most one model reply may use. A ceiling, not a target."""
    request_tokens: int = 200_000
    """Input plus output reserved across every attempt of one question."""
    recent_tokens: int = 8_000
    """Recent complete exchanges kept in a request."""
    question_tokens: int = 2_000
    directory_tokens: int = 2_000
    working_evidence_tokens: int = 10_000
    """Tool results of the current question kept whole in a request."""
    max_tool_calls: int = 6
    max_model_calls: int = 8
    max_revisions: int = 2
    timeout_seconds: int = 60
    max_no_progress_repeats: int = 2
    prefetch_cards: int = 8
    """Search hits read automatically before the model is asked; zero turns this off."""
    max_steps: int | None = None
    """Graph steps allowed for one question. Normally derived from the call limits."""

    def __post_init__(self) -> None:
        for field in fields(self):
            value = getattr(self, field.name)
            if value is None and field.name == "max_steps":
                continue
            if (
                type(value) is not int
                or value < 0
                or (value == 0 and field.name not in _MAY_BE_ZERO)
            ):
                raise ValueError(f"{field.name} must be a positive whole number")
        if self.output_tokens >= self.input_tokens:
            raise ValueError("the output allowance must be smaller than the input ceiling")
        if self.request_tokens < self.input_tokens + self.output_tokens:
            raise ValueError("the request budget must hold at least one full input and output")
        if self.question_tokens + self.directory_tokens >= self.input_tokens:
            raise ValueError("the question and directory allowances must leave room in the input")

    @property
    def recursion_limit(self) -> int:
        """Graph steps to allow: each model attempt can take a generate, a validate and a tool step."""
        return self.max_steps or 3 * self.max_model_calls + self.max_tool_calls + 8


_QNA_SETTINGS = {
    "RMPREP_QNA_INPUT_TOKENS": "input_tokens",
    "RMPREP_QNA_OUTPUT_TOKENS": "output_tokens",
    "RMPREP_QNA_REQUEST_TOKENS": "request_tokens",
    "RMPREP_QNA_RECENT_TOKENS": "recent_tokens",
    "RMPREP_QNA_QUESTION_TOKENS": "question_tokens",
    "RMPREP_QNA_DIRECTORY_TOKENS": "directory_tokens",
    "RMPREP_QNA_WORKING_EVIDENCE_TOKENS": "working_evidence_tokens",
    "RMPREP_QNA_MAX_TOOL_CALLS": "max_tool_calls",
    "RMPREP_QNA_MAX_MODEL_CALLS": "max_model_calls",
    "RMPREP_QNA_MAX_REVISIONS": "max_revisions",
    "RMPREP_QNA_TIMEOUT_SECONDS": "timeout_seconds",
    "RMPREP_QNA_MAX_NO_PROGRESS_REPEATS": "max_no_progress_repeats",
    "RMPREP_QNA_MAX_STEPS": "max_steps",
    "RMPREP_QNA_PREFETCH_CARDS": "prefetch_cards",
}


def load_qna_limits(
    dotenv_path: Path | None = ENV_FILE, *, source: ConfigSource | None = None
) -> QnaLimits:
    """Snapshot the Q&A limits at startup; process variables override the agent's .env.

    Missing settings use the defaults. A misspelled or malformed setting fails startup,
    before any model call, and so does a set of limits that cannot work together.
    """
    if source is None:
        layers: list[ConfigSource] = [EnvironConfigSource()]
        if dotenv_path is not None:
            layers.append(DotenvConfigSource(dotenv_path))
        source = LayeredConfigSource(*layers)
    unknown = sorted(
        name
        for name in source.names()
        if name.startswith("RMPREP_QNA_") and name not in _QNA_SETTINGS
    )
    if unknown:
        raise ConfigurationError("Unknown rmprep Q&A setting: " + ", ".join(unknown))
    values: dict[str, int] = {}
    for name, field in _QNA_SETTINGS.items():
        raw = source.get(name)
        if raw is None:
            continue
        zero_ok = field in _MAY_BE_ZERO
        if (
            not raw.strip().isascii()
            or not raw.strip().isdecimal()
            or int(raw) < (0 if zero_ok else 1)
        ):
            raise ConfigurationError(
                f"{name} must be a {'whole number' if zero_ok else 'positive whole number'}"
            )
        values[field] = int(raw)
    try:
        return QnaLimits(**values)
    except ValueError as error:
        raise ConfigurationError(f"Invalid RMPREP_QNA_* settings: {error}") from None
