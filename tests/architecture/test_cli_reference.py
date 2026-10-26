"""docs/cli.md names every command of the command line, and nothing that is not there."""

from __future__ import annotations

import re
from pathlib import Path

import click

from agentcraft2_cli.app import cli

REFERENCE = (Path(__file__).resolve().parents[2] / "docs" / "cli.md").read_text(encoding="utf-8")


def _commands(group: click.Group, prefix: str = "ac2") -> list[str]:
    found = []
    for name, command in group.commands.items():
        full = f"{prefix} {name}"
        found.append(full)
        if isinstance(command, click.Group):
            found += _commands(command, full)
    return found


def test_every_command_is_in_the_reference() -> None:
    missing = [name for name in _commands(cli) if f"`{name}" not in REFERENCE]
    assert missing == [], f"docs/cli.md does not name: {missing}"


def test_the_reference_names_only_commands_that_exist() -> None:
    known = set(_commands(cli))
    named = set(re.findall(r"`(ac2 [a-z]+(?: [a-z]+)?)(?: [A-Z\[\-][^`]*)?`", REFERENCE))
    invented = sorted(
        name for name in named if name not in known and name.rsplit(" ", 1)[0] not in known
    )
    assert invented == [], f"docs/cli.md names commands that do not exist: {invented}"
