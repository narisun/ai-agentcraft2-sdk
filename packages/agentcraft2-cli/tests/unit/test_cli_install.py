"""``ac2 install`` installs the workspace's own services, and only checks that the SDK is there."""

from __future__ import annotations

import itertools
from collections.abc import Sequence
from pathlib import Path

import pytest

from agentcraft2_cli.__main__ import main
from agentcraft2_cli.commands.install import installation_arguments, missing_sdk_packages
from agentcraft2_cli.testing import toolbox_for_tests


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    tools = toolbox_for_tests(pins={})
    assert main(["init", "demo", "--dir", str(tmp_path), "--owner", "demo-team"], tools) == 0
    root = tmp_path / "demo"
    assert main(["new", "agent", "helper", "--workspace", str(root)], tools) == 0
    return root


def editables(arguments: Sequence[str]) -> list[str]:
    return [
        Path(value).name for flag, value in itertools.pairwise(arguments) if flag == "--editable"
    ]


def test_only_the_workspaces_own_services_are_installed_never_the_sdk(workspace: Path) -> None:
    arguments = installation_arguments(workspace)
    assert editables(arguments) == ["helper"]
    assert not any("agentcraft2-core" in item or "packages" in item for item in arguments)
    assert any(item.startswith("pytest") for item in arguments)


def test_without_the_development_tools_it_is_just_the_services(workspace: Path) -> None:
    assert installation_arguments(workspace, dev=False) == (
        "install",
        "--editable",
        str(workspace / "agents" / "helper"),
    )


def test_a_workspace_from_a_checkout_needs_the_sdk_installed(workspace: Path) -> None:
    assert missing_sdk_packages(workspace, lambda package: False) == ["agentcraft2-core"]
    assert missing_sdk_packages(workspace, lambda package: True) == []


def test_a_workspace_from_the_index_lets_pip_resolve_the_sdk(workspace: Path) -> None:
    toml = workspace / "agentcraft2.toml"
    text = toml.read_text(encoding="utf-8")
    start = text.index("[library]")
    end = text.index("\n[", start + 1) if "\n[" in text[start + 1 :] else len(text)
    text = text[:start] + '[library]\nsource = "index"\nversion = ">=0.1,<0.2"\n' + text[end:]
    toml.write_text(text, encoding="utf-8")
    assert missing_sdk_packages(workspace, lambda package: False) == []


def test_the_command_stops_with_the_fix_when_the_sdk_is_not_installed(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def run(module: str, arguments: Sequence[str], folder: Path) -> int:
        raise AssertionError("pip must not start")

    tools = toolbox_for_tests(pins={}, run_tool=run, is_installed=lambda package: False)
    capsys.readouterr()
    assert main(["install", "--workspace", str(workspace)], tools) == 1
    err = capsys.readouterr().err
    assert "the SDK is not installed in this Python" in err
    assert "agentcraft2-core installed with the SDK, before the workspace" in err
    assert "python tools/install_sdk.py" in err
    assert "installs only this workspace's own services" in err


def test_the_command_hands_pip_only_the_workspace(workspace: Path) -> None:
    ran: list[Sequence[str]] = []

    def run(module: str, arguments: Sequence[str], folder: Path) -> int:
        ran.append(arguments)
        return 0

    tools = toolbox_for_tests(pins={}, run_tool=run)
    assert main(["install", "--workspace", str(workspace)], tools) == 0
    assert editables(ran[0]) == ["helper"]


def test_the_help_says_what_it_installs_and_what_it_does_not(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["install", "--help"], toolbox_for_tests()) == 0
    out = capsys.readouterr().out
    assert "Install the workspace's own agents and MCP servers" in out
    assert "never installs" in out
