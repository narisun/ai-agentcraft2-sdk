"""Agent Lab as a service: the configuration it needs from the workspace, and what to tell.

Agent Lab chats with the agents of a workspace over HTTP. Its own configuration (which agents,
at which addresses, as which developer) is written from the workspace's registry and the first
agent's settings every time it starts, so it never disagrees with the registry. What it keeps,
such as conversations and the sign-in code, stays in the workspace, beside the other state of
the service. Nothing here installs it: the SDK's installer does.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from agentcraft2_cli.errors import CliError, invocation
from agentcraft2_cli.services import packages
from agentcraft2_cli.shared import read_agents
from agentcraft2_cli.workspace import AGENTS_FOLDER, load_answers

if TYPE_CHECKING:
    from agentcraft2_cli.services.registry import Paths

__all__ = ["PORT", "Runner", "access_code", "after_start", "config_file", "prepare"]

PORT = 8765
"""The loopback port Agent Lab listens on."""

Runner = Callable[[Sequence[str], Path], tuple[int, str]]
"""Runs a command in a folder and returns its exit code and what it printed."""


def run_command(command: Sequence[str], folder: Path) -> tuple[int, str]:
    """Run a command and return its exit code and what it printed."""
    done = subprocess.run(  # noqa: S603 - the command of an installed service
        list(command),
        cwd=folder,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return done.returncode, (done.stderr or done.stdout).strip()


def config_file(paths: Paths) -> Path:
    """Return the file that configures Agent Lab."""
    return paths.data / "config.json"


def access_code(paths: Paths) -> Path:
    """Return the file that holds the code that signs a person in."""
    return paths.data / "access-code"


def _addressed(root: Path) -> list[tuple[str, int, str]]:
    """Return ``(name, port, address)`` of each agent of the workspace that has an address."""
    answers = load_answers(root)
    registered = {entry.id: entry for entry in read_agents(root)}
    found = []
    for agent in answers.agents:
        entry = registered.get(agent.name)
        if entry is not None and entry.url:
            found.append((agent.name, agent.port, entry.url))
    return found


def prepare(root: Path, paths: Paths, run: Runner = run_command) -> list[str]:
    """Write Agent Lab's configuration for the agents of the workspace.

    Returns:
        Notes to show: the agents that were left out, and why.

    Raises:
        CliError: If there is no agent Agent Lab could talk to, or its configuration could not
            be written.
    """
    answers = load_answers(root)
    if not answers.agents:
        raise CliError(
            "Agent Lab has no agent to chat with",
            expected="at least one agent in this workspace",
            actual="the workspace has none",
            fix=f"add one: {invocation()} new agent NAME --mcp SERVER",
        )
    addressed = _addressed(root)
    if not addressed:
        raise CliError(
            "no agent of this workspace has an address Agent Lab may call",
            expected="'url: http://127.0.0.1:PORT' under each agent in registry/agents.yaml",
            actual="none of the agents has one",
            fix=(
                "add the line under the agent in registry/agents.yaml, at the indentation of "
                f"'owner' (for {answers.agents[0].name}: url: http://127.0.0.1:"
                f"{answers.agents[0].port}); agents made by '{invocation()} new agent' have it"
            ),
        )
    left_out = sorted({agent.name for agent in answers.agents} - {name for name, _, _ in addressed})
    notes = [
        f"{name} has no address in registry/agents.yaml, so Agent Lab leaves it out"
        for name in left_out
    ]
    env_file = root / AGENTS_FOLDER / addressed[0][0] / ".env"
    if not env_file.is_file():
        env_file = env_file.with_name(".env.example")
    if not env_file.is_file():
        raise CliError(
            f"cannot read the settings of {addressed[0][0]}",
            expected=f"{env_file.with_name('.env')} (or .env.example)",
            actual="neither exists",
            fix=f"restore the file; {invocation()} update shows what the workspace expects",
        )
    config = config_file(paths)
    arguments = ["init", "--config", str(config), "--env-file", str(env_file)]
    for name, _, address in addressed:
        # An agent that returns documents (memos) shows them beside the chat; one that does not
        # returns none, and nothing is shown.
        arguments += ["--agent", name, "--artifacts-agent", name, "--allow-url", address]
    arguments += ["--port", str(PORT)]
    if config.exists():
        # The registry may have changed since the last start; conversations and secrets stay.
        arguments.append("--overwrite")
    paths.data.mkdir(parents=True, exist_ok=True)
    code, output = run(packages.command(paths.installed, "agentcraft2_ui", *arguments), paths.base)
    if code != 0:
        raise CliError(
            "Agent Lab could not be configured for this workspace",
            expected="a configuration written from the registry and the agent's settings",
            actual=output.splitlines()[-1] if output else f"exit code {code}",
            fix=(
                f"fix what it names, then run '{invocation()} services start lab' again. All "
                "the agents must run as the same developer (the identity in their .env files)"
            ),
        )
    return notes


def after_start(root: Path, paths: Paths) -> list[str]:
    """Return what the person needs to know now that Agent Lab answers."""
    lines = [f"Sign in with the code in: {access_code(paths)}"]
    started = [(name, port) for name, port, _ in _addressed(root)]
    lines.append("Agent Lab talks to your agents; start each one, with its MCP servers, to chat:")
    lines += [f"  {invocation()} run {name} --port {port}" for name, port in started]
    return lines
