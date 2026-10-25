"""The environment the Phoenix server runs in: fixed, local, and free of the agent's secrets.

Phoenix reads its settings from environment variables. A developer's shell may hold the
agent's credentials and settings, and none of them has any use here, so they are removed
before Phoenix starts. What Phoenix is then told is decided here, in one place.
"""

from __future__ import annotations

import os
from collections.abc import MutableMapping
from pathlib import Path

__all__ = ["apply_environment", "apply_to_process", "server_environment"]

_REMOVED_PREFIXES = (
    "AC2_",
    "EAP_",
    "PHOENIX_",
    "OTEL_",
    "LANGFUSE_",
    "ANTHROPIC_",
    "OPENAI_",
    "AWS_",
    "RMPREP_",
)
"""Variables of the agent and of the services it talks to: not for the viewer to see."""


def server_environment(data_dir: Path, port: int) -> dict[str, str]:
    """Return the settings Phoenix runs with.

    Args:
        data_dir: Where Phoenix keeps its SQLite database of traces.
        port: The loopback port it listens on.
    """
    return {
        "PHOENIX_WORKING_DIR": str(data_dir),
        "PHOENIX_SQL_DATABASE_URL": "sqlite:///" + (data_dir / "phoenix.db").as_posix(),
        "PHOENIX_HOST": "127.0.0.1",
        "PHOENIX_PORT": str(port),
        # Nothing leaves the machine: no usage telemetry, no tracking pixel, no web fonts.
        "PHOENIX_TELEMETRY_ENABLED": "false",
        "PHOENIX_ALLOW_EXTERNAL_RESOURCES": "false",
        "PHOENIX_DISABLE_AGENT_ASSISTANT": "true",
        # Viewing traces runs no code and calls no model. Agents own their model and MCP calls.
        "PHOENIX_ALLOWED_SANDBOX_PROVIDERS": "NONE",
        "PHOENIX_ALLOWED_PROVIDERS": "NONE",
        "PHOENIX_ENABLE_MCP_SERVER": "false",
        "PHOENIX_ENABLE_MCP_CODE_MODE": "false",
        "PHOENIX_ENABLE_PROMETHEUS": "false",
    }


def apply_environment(environment: MutableMapping[str, str], data_dir: Path, port: int) -> None:
    """Remove the agent's variables from ``environment``, then set the viewer's own."""
    for name in [name for name in environment if name.startswith(_REMOVED_PREFIXES)]:
        del environment[name]
    environment.update(server_environment(data_dir, port))


def apply_to_process(data_dir: Path, port: int) -> None:
    """Set up the environment of this process for the viewer; call it before Phoenix is imported."""
    apply_environment(os.environ, data_dir, port)
