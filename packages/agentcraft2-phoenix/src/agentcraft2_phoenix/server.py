"""Run the pinned Phoenix with the lifecycle changes that suit a developer's machine.

The pinned Phoenix starts an optional gRPC collector on every interface, even when its web
host is loopback. Agents export over HTTP, so that listener is switched off. Phoenix also checks
for an optional Bedrock dependency even when it allows no provider. Keep these shims covered
when changing ``PINNED_VERSION``: neither changes database queries, warning filters or the
installed packages.
"""

from __future__ import annotations

import sys
from functools import wraps
from importlib import import_module
from importlib.metadata import PackageNotFoundError, version
from typing import Any

from agentcraft2_phoenix import PINNED_VERSION, page

__all__ = ["check_enabled_playground_dependencies", "http_only", "run"]


def http_only(grpc_server: Any) -> None:
    """Skip only the unused gRPC listener; HTTP ingestion remains enabled."""
    enter = grpc_server.__aenter__

    async def disabled_enter(self: Any) -> None:
        self._disabled = True
        await enter(self)

    grpc_server.__aenter__ = disabled_enter


def check_enabled_playground_dependencies(app: Any) -> None:
    """Check the Bedrock dependency only when Phoenix permits the AWS provider.

    The pinned Phoenix runs this check even when no provider is allowed. Keep the original
    check, with its warnings and exceptions, whenever AWS is allowed. Trace ingestion never
    needs a model client.
    """
    check = app._warn_if_missing_aioboto3

    @wraps(check)
    def check_when_enabled() -> None:
        providers = app.get_env_allowed_providers()
        if providers is None or "AWS" in providers:
            check()

    app._warn_if_missing_aioboto3 = check_when_enabled


def run(port: int) -> None:
    """Run the Phoenix server on loopback at ``port``, until it is stopped.

    Raises:
        RuntimeError: If Phoenix is missing or is not the release this package was made for.
    """
    try:
        installed = version("arize-phoenix")
    except PackageNotFoundError:
        raise RuntimeError(
            "arize-phoenix is not installed; install this package with its 'viewer' extra"
        ) from None
    if installed != PINNED_VERSION:
        raise RuntimeError(
            f"arize-phoenix {installed} is installed, and this package was made for "
            f"{PINNED_VERSION}; install agentcraft2-phoenix[viewer] again"
        )
    http_only(import_module("phoenix.server.grpc_server").GrpcServer)
    app = import_module("phoenix.server.app")
    check_enabled_playground_dependencies(app)
    page.install(app)
    sys.argv = ["phoenix", "serve", "--host", "127.0.0.1", "--port", str(port)]
    import_module("phoenix.server.main").main()
