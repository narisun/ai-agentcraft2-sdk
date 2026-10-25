"""The small things a developer meets first: hints, prompts, early stops, and where help points."""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import click
import pytest

from agentcraft2_cli.__main__ import main
from agentcraft2_cli.errors import invocation
from agentcraft2_cli.testing import toolbox_for_tests
from agentcraft2_cli.toolbox import terminal_prompt


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    tools = toolbox_for_tests()
    assert main(["init", "demo", "--dir", str(tmp_path), "--owner", "t"], tools) == 0
    root = tmp_path / "demo"
    assert main(["new", "agent", "helper", "--workspace", str(root)], tools) == 0
    return root


# ------------------------------------------------------------------ the command's own name


@pytest.mark.parametrize(
    ("started", "expected"),
    [
        ("/usr/local/bin/ac2", "ac2"),
        ("C:\\env\\Scripts\\ac2.exe", "ac2"),
        ("/usr/local/bin/agentcraft2", "agentcraft2"),
        ("/site-packages/agentcraft2_cli/__main__.py", "python -m agentcraft2_cli"),
        ("/site-packages/pytest/__main__.py", "agentcraft2"),
    ],
)
def test_a_hint_names_the_command_the_way_it_was_started(
    monkeypatch: pytest.MonkeyPatch, started: str, expected: str
) -> None:
    monkeypatch.setattr(sys, "argv", [started])
    assert invocation() == expected


def test_the_hints_after_init_use_the_short_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", ["ac2"])
    assert main(["init", "demo", "--dir", str(tmp_path), "--owner", "t"], toolbox_for_tests()) == 0
    out = capsys.readouterr().out
    for step in ("ac2 new mcp hello-mcp", "ac2 install", "ac2 doctor", "ac2 logs hello-agent"):
        assert step in out
    assert "python -m agentcraft2_cli" not in out


def test_the_error_for_a_folder_that_is_no_workspace_uses_the_short_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", ["ac2"])
    assert main(["doctor", "--workspace", str(tmp_path)], toolbox_for_tests()) == 1
    assert "Create one with 'ac2 init <name>'" in capsys.readouterr().err


# ----------------------------------------------------------------------------- prompts


def test_a_prompt_with_nobody_to_answer_takes_the_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)

    def ended(*_args: object, **_kwargs: object) -> str:
        try:
            raise EOFError
        except EOFError:
            raise click.Abort from None

    monkeypatch.setattr(click, "prompt", ended)
    assert terminal_prompt("Which team?", "platform-team") is None


def test_ctrl_c_at_a_prompt_still_stops(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)

    def interrupted(*_args: object, **_kwargs: object) -> str:
        try:
            raise KeyboardInterrupt
        except KeyboardInterrupt:
            raise click.Abort from None

    monkeypatch.setattr(click, "prompt", interrupted)
    with pytest.raises(click.Abort):
        terminal_prompt("Which team?", "platform-team")


# ------------------------------------------------------------------------------- verify


def test_config_check_is_doctor(workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
    tools = toolbox_for_tests()
    assert main(["doctor", "helper", "--no-pins", "--workspace", str(workspace)], tools) == 0
    first = capsys.readouterr().out
    assert (
        main(["config", "check", "helper", "--no-pins", "--workspace", str(workspace)], tools) == 0
    )
    assert capsys.readouterr().out == first
    assert "Everything checked is usable." in first


def test_check_stops_at_once_when_a_service_is_not_installed(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ran: list[str] = []

    def run_tool(module: str, arguments: object, folder: Path) -> int:
        ran.append(module)
        return 0

    tools = replace(toolbox_for_tests(), is_installed=lambda package: False, run_tool=run_tool)
    assert main(["check", "--workspace", str(workspace)], tools) == 1
    err = capsys.readouterr().err
    assert "helper is not installed here" in err
    assert "not importable: helper" in err
    assert "run 'agentcraft2 install'" in err
    assert ran == []  # no tool was started to fail on an import


def test_a_failed_install_on_windows_names_the_way_round_a_locked_launcher(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    tools = replace(toolbox_for_tests(), run_tool=lambda module, arguments, folder: 1)
    monkeypatch.setattr(sys, "platform", "win32")
    assert main(["install", "--workspace", str(workspace)], tools) == 1
    assert "python -m agentcraft2_cli install" in capsys.readouterr().err
    monkeypatch.setattr(sys, "platform", "linux")
    assert main(["install", "--workspace", str(workspace)], tools) == 1
    assert "another process" not in capsys.readouterr().err
