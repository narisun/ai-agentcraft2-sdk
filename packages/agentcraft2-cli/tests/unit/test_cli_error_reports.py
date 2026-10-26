"""A failure of a command shows the exact message and the stack trace, and nothing is hidden."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from agentcraft2_cli.__main__ import main
from agentcraft2_cli.errors import EXIT_FAILED, EXIT_INTERRUPTED, CliError
from agentcraft2_cli.testing import toolbox_for_tests
from agentcraft2_cli.toolbox import Toolbox
from agentcraft2_core.contracts import ConfigurationError


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    tools = toolbox_for_tests()
    assert main(["init", "demo", "--dir", str(tmp_path)], tools) == 0
    root = tmp_path / "demo"
    assert main(["new", "agent", "helper", "--workspace", str(root)], tools) == 0
    return root


def _failing_with(error: BaseException) -> Toolbox:
    def read_graph(folder: Path, package: str) -> str:
        raise error

    return replace(toolbox_for_tests(), read_graph=read_graph)


def _graph(root: Path, tools: Toolbox, *options: str) -> int:
    return main([*options, "graph", "helper", "--workspace", str(root)], tools)


def test_a_mistake_of_the_person_at_the_keyboard_gets_its_facts_and_no_stack(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    error = CliError("there is no graph", expected="a graph", actual="none", fix="add one")
    assert _graph(workspace, _failing_with(error)) == EXIT_FAILED
    err = capsys.readouterr().err
    assert "error: there is no graph" in err
    assert "Traceback" not in err


def test_trace_adds_the_stack_to_a_mistake_of_the_person_at_the_keyboard(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    error = CliError("there is no graph")
    assert _graph(workspace, _failing_with(error), "--trace") == EXIT_FAILED
    err = capsys.readouterr().err
    assert err.startswith("Traceback (most recent call last):")
    assert "agentcraft2_cli.errors.CliError: there is no graph" in err or "CliError" in err
    assert "error: there is no graph" in err


def test_an_error_from_deeper_down_shows_its_exact_message_and_its_stack(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    error = ConfigurationError("the audit option 'fsync' is not valid", expected="true or false")
    assert _graph(workspace, _failing_with(error)) == EXIT_FAILED
    err = capsys.readouterr().err
    assert err.startswith("Traceback (most recent call last):")
    assert "the audit option 'fsync' is not valid" in err
    assert "expected: true or false" in err
    assert "error: ConfigurationError:" in err


def test_a_fault_in_the_command_is_reported_whole_with_a_pointer_to_the_stack(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _graph(workspace, _failing_with(ZeroDivisionError("division by zero"))) == EXIT_FAILED
    err = capsys.readouterr().err
    assert err.startswith("Traceback (most recent call last):")
    assert "ZeroDivisionError: division by zero" in err
    assert "unexpected error: ZeroDivisionError: division by zero" in err
    assert "fault in the command" in err


def test_ctrl_c_stops_quietly_with_the_conventional_code(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _graph(workspace, _failing_with(KeyboardInterrupt())) == EXIT_INTERRUPTED
    err = capsys.readouterr().err
    assert "stopped" in err
    assert "Traceback" not in err


def test_the_trace_option_does_not_leak_into_the_next_command_line(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    error = CliError("there is no graph")
    _graph(workspace, _failing_with(error), "--trace")
    capsys.readouterr()
    _graph(workspace, _failing_with(error))
    assert "Traceback" not in capsys.readouterr().err
