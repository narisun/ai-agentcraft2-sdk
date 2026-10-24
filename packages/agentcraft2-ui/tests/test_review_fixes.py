"""Behaviors found wrong in review: each test fails on the code it replaced."""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from pydantic import BaseModel, ValidationError

from agentcraft2_core.contracts import ConfigurationError, Principal, Scope
from agentcraft2_ui.__main__ import _runtimes, _safe_message
from agentcraft2_ui.adapters.http import HttpDispatcher, default_credentials
from agentcraft2_ui.adapters.inspection import fetch_inspection
from agentcraft2_ui.config.shell import private_secret
from agentcraft2_ui.contracts.catalog import AgentBinding, AgentCoreRuntime, PresentationManifest
from agentcraft2_ui.contracts.conversations import RunRecord, TurnAdmission
from agentcraft2_ui.contracts.queries import ContractError, ErrorCode, ViewContext

SCOPE = Scope("tenant", "user", "memo")
VIEW = ViewContext(principal=Principal(tenant="tenant", subject="user"), scope=SCOPE)
ARN = "arn:aws:bedrock-agentcore:eu-west-1:123456789012:runtime/memo-abc123"


def _binding(runtime: AgentCoreRuntime | None = None) -> AgentBinding:
    return AgentBinding(
        base_url="http://127.0.0.1:8000",
        manifest=PresentationManifest(
            agent_id="memo",
            agent_version="1",
            registry_revision="1",
            title="Memo",
            api_audience="memo",
        ),
        runtime=runtime,
    )


def _run() -> RunRecord:
    return RunRecord(
        scope=SCOPE,
        thread_id="thread",
        request_id="request",
        run_id="run",
        idempotency_key="send",
        state="completed",
        answer="answer",
        accepted_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


def _expired_session(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make boto3 fail the way it does when an SSO session has expired."""
    import boto3
    from botocore.exceptions import TokenRetrievalError

    class Expired:
        def get_credentials(self) -> object:
            raise TokenRetrievalError(provider="sso", error_msg="the session has expired")

    monkeypatch.setattr(boto3, "Session", Expired)


# ------------------------------------------------------ AWS sign-in that cannot be used


def test_an_expired_aws_sign_in_is_a_known_failure_not_an_unexpected_error(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _expired_session(monkeypatch)
    with caplog.at_level("ERROR"), pytest.raises(ContractError) as raised:
        default_credentials()
    assert raised.value.code == ErrorCode.UNAVAILABLE
    # The log says what kind of failure it was, and not its text.
    record = next(r for r in caplog.records if "could not be resolved" in r.getMessage())
    assert record.error_type == "TokenRetrievalError"  # type: ignore[attr-defined]
    assert "expired" not in caplog.text


async def test_an_expired_aws_sign_in_fails_the_run_without_sending_and_frees_the_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _expired_session(monkeypatch)
    sent: list[httpx.Request] = []

    def nowhere(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200)

    turn = TurnAdmission(scope=SCOPE, thread_id="thread", question="hello", idempotency_key="send")
    async with httpx.AsyncClient(transport=httpx.MockTransport(nowhere)) as client:
        dispatcher = HttpDispatcher(client, 10, credentials=default_credentials)
        binding = _binding(AgentCoreRuntime(arn=ARN))
        result = await dispatcher.dispatch(VIEW, binding, turn, _run())
    assert sent == []
    # "failed" is a known outcome. "unknown" would hold the thread for good.
    assert (result.state, result.failure_code) == ("failed", "unauthorized")


# ------------------------------------------------ the inspector never calls AWS unsigned


async def test_a_runtime_hosted_agent_is_unsupported_without_any_request() -> None:
    sent: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        result = await fetch_inspection(client, VIEW, _binding(AgentCoreRuntime(arn=ARN)), _run())
    assert sent == []
    assert result.model_inputs.availability == "unsupported"
    assert result.checkpoints.availability == "unsupported"


# ----------------------------------------------------------- startup messages say why


class _Settings(BaseModel):
    port: int


def test_a_startup_message_names_the_invalid_fields_and_never_their_values() -> None:
    with pytest.raises(ValidationError) as raised:
        _Settings(port="my-secret-value")
    message = _safe_message(raised.value)
    assert "port (int_parsing)" in message
    assert "my-secret-value" not in message


def test_a_startup_message_gives_the_line_where_json_stopped_parsing() -> None:
    with pytest.raises(json.JSONDecodeError) as raised:
        json.loads('{\n  "a": 1,\n  oops\n}')
    assert "line 3, column 3" in _safe_message(raised.value)


def test_a_startup_message_gives_the_operating_system_reason_and_the_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError) as raised:
        (tmp_path / "config.json").read_text(encoding="utf-8")
    message = _safe_message(raised.value)
    assert "config.json" in message
    assert "required file could not be read or written (" in message


def test_a_startup_message_gives_the_librarys_own_message() -> None:
    message = _safe_message(ConfigurationError("the registry names no agents"))
    assert message == "the registry names no agents"


# --------------------------------------------- options that would be ignored are refused


class _Args:
    def __init__(self, **values: object) -> None:
        self.agent: list[str] = []
        self.agentcore_runtime: list[str] | None = None
        self.agentcore_auth = "iam"
        self.agentcore_token_file: Path | None = None
        self.__dict__.update(values)


def test_a_token_file_without_a_runtime_is_refused_instead_of_ignored(tmp_path: Path) -> None:
    args = _Args(agentcore_auth="bearer", agentcore_token_file=tmp_path / "token.txt")
    with pytest.raises(ValueError, match="apply to --agentcore-runtime"):
        _runtimes(args)  # type: ignore[arg-type]


def test_bearer_without_a_token_file_is_refused_even_with_no_runtime() -> None:
    with pytest.raises(ValueError, match="needs --agentcore-token-file"):
        _runtimes(_Args(agentcore_auth="bearer"))  # type: ignore[arg-type]


# ----------------------------------------------------------------- local secret files


@pytest.mark.skipif(sys.platform == "win32", reason="Windows has no owner-only file mode here")
def test_generated_secrets_are_readable_by_their_owner_alone(tmp_path: Path) -> None:
    private_secret(tmp_path / "access-code")
    assert (tmp_path / "access-code").stat().st_mode & 0o777 == 0o600
