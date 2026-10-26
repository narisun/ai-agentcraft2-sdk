"""The server shims, and the refusal to run with a Phoenix other than the pinned one."""

import asyncio
import sys
import types
from importlib.metadata import PackageNotFoundError
from typing import Any

import jinja2
import pytest

from agentcraft2_phoenix import PINNED_VERSION, server

PAGE = (
    '<title>Phoenix</title><meta name="title" content="Arize Phoenix" />'
    '<meta name="description" content="AI Observability & Evaluation" />'
    '<meta property="og:title" content="Arize Phoenix" />'
    '<meta property="og:description" content="AI Observability & Evaluation" />'
    '<meta property="og:image" content="https://raw.githubusercontent.com/Arize-ai/phoenix-assets/'
    'main/images/socal/social-preview-horizontal.jpg" /></head>'
)


def test_the_grpc_listener_is_switched_off_and_the_original_start_still_runs() -> None:
    entered: list[bool] = []

    class GrpcServer:
        _disabled = False

        async def __aenter__(self) -> None:
            entered.append(self._disabled)

    server.http_only(GrpcServer)
    asyncio.run(GrpcServer().__aenter__())
    assert entered == [True]


class _App:
    def __init__(self, providers: set[str] | None) -> None:
        self.providers = providers
        self.checked = 0

    def get_env_allowed_providers(self) -> set[str] | None:
        return self.providers

    def _warn_if_missing_aioboto3(self) -> None:
        self.checked += 1


@pytest.mark.parametrize(("providers", "checked"), [(set(), 0), ({"AWS"}, 1), (None, 1)])
def test_the_bedrock_dependency_is_checked_only_when_aws_is_allowed(
    providers: set[str] | None, checked: int
) -> None:
    app = _App(providers)
    server.check_enabled_playground_dependencies(app)
    app._warn_if_missing_aioboto3()
    assert app.checked == checked


def _fake_phoenix(monkeypatch: pytest.MonkeyPatch, started: list[list[str]]) -> Any:
    """Put stand-ins for the modules of Phoenix that ``run`` imports into ``sys.modules``."""
    grpc: Any = types.ModuleType("phoenix.server.grpc_server")
    grpc.GrpcServer = type("GrpcServer", (), {"__aenter__": lambda self: None})
    app: Any = types.ModuleType("phoenix.server.app")
    app.templates = types.SimpleNamespace(
        env=jinja2.Environment(loader=jinja2.DictLoader({"index.html": PAGE}), autoescape=True)
    )
    app._warn_if_missing_aioboto3 = lambda: None
    app.get_env_allowed_providers = lambda: set()
    main: Any = types.ModuleType("phoenix.server.main")
    main.main = lambda: started.append(list(sys.argv))
    for name, module in (
        ("phoenix", types.ModuleType("phoenix")),
        ("phoenix.server", types.ModuleType("phoenix.server")),
        ("phoenix.server.grpc_server", grpc),
        ("phoenix.server.app", app),
        ("phoenix.server.main", main),
    ):
        monkeypatch.setitem(sys.modules, name, module)
    return app


def test_run_starts_the_pinned_phoenix_on_loopback_with_the_branded_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started: list[list[str]] = []
    app = _fake_phoenix(monkeypatch, started)
    monkeypatch.setattr(server, "version", lambda _: PINNED_VERSION)
    monkeypatch.setattr(sys, "argv", ["before"])
    server.run(6123)
    assert started == [["phoenix", "serve", "--host", "127.0.0.1", "--port", "6123"]]
    assert "AgentCraft2 Traces" in app.templates.env.get_template("index.html").render()


def test_run_refuses_another_release_of_phoenix(monkeypatch: pytest.MonkeyPatch) -> None:
    started: list[list[str]] = []
    _fake_phoenix(monkeypatch, started)
    monkeypatch.setattr(server, "version", lambda _: "99.0.0")
    with pytest.raises(RuntimeError, match=f"99.0.0 is installed.*made for {PINNED_VERSION}"):
        server.run(6006)
    assert started == []


def test_run_says_how_to_install_phoenix_when_it_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing(_: str) -> str:
        raise PackageNotFoundError

    monkeypatch.setattr(server, "version", missing)
    with pytest.raises(RuntimeError, match="'viewer' extra"):
        server.run(6006)
