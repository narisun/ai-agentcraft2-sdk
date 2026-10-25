"""``ac2 logs`` and ``ac2 errors``: a service's log kept while it runs, and read back."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from agentcraft2_cli.__main__ import main
from agentcraft2_cli.commands.observe import follow
from agentcraft2_cli.logbook import (
    LogWriter,
    errors_in,
    format_entry,
    format_error,
    log_file,
    parse_line,
    tail_lines,
)
from agentcraft2_cli.processes import run_service
from agentcraft2_cli.testing import toolbox_for_tests


def _line(**fields: object) -> str:
    base = {"time": "2026-10-24T12:03:41.250+00:00", "level": "INFO", "service": "helper"}
    return json.dumps({**base, **fields})


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.delenv("AC2_HOME", raising=False)
    tools = toolbox_for_tests()
    assert main(["init", "demo", "--dir", str(tmp_path)], tools) == 0
    root = tmp_path / "demo"
    assert main(["new", "agent", "helper", "--workspace", str(root)], tools) == 0
    return root


def _write_log(root: Path, *lines: str, service: str = "helper") -> Path:
    path = log_file(root / "agents" / service / ".agentcraft2", service)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# ------------------------------------------------------------------------- reading lines


def test_a_log_line_is_read_back_with_its_facts() -> None:
    entry = parse_line(
        _line(message="request ended", request_id="r-1", outcome="ok", duration_ms=12), "helper"
    )
    assert (entry.level, entry.service, entry.message) == ("INFO", "helper", "request ended")
    assert entry.fields == {"request_id": "r-1", "outcome": "ok", "duration_ms": 12}
    assert "INFO" in format_entry(entry)
    assert "request_id=r-1 outcome=ok duration_ms=12" in format_entry(entry)


def test_a_line_that_is_not_json_is_shown_as_the_output_it_is() -> None:
    entry = parse_line("some print statement", "helper")
    assert (entry.level, entry.message) == ("OUTPUT", "some print statement")
    assert not entry.is_error
    assert format_entry(entry).endswith("helper  some print statement")


def test_tail_reads_the_last_lines_of_a_large_file_from_its_end(tmp_path: Path) -> None:
    path = tmp_path / "big.log"
    path.write_text("".join(f"line {number}\n" for number in range(20_000)), encoding="utf-8")
    assert tail_lines(path, 3) == ["line 19997", "line 19998", "line 19999"]
    assert tail_lines(path, 0) == []
    assert tail_lines(tmp_path / "missing.log", 5) == []


# ------------------------------------------------------------------------ what failed


def test_a_failure_shows_the_exact_message_the_facts_and_the_stack() -> None:
    entry = parse_line(
        _line(
            level="ERROR",
            message="tool_call",
            error_type="ConfigurationError",
            error="the audit option 'fsync' is not valid",
            error_expected="true or false",
            error_actual="'maybe'",
            error_fix="set it to true",
            error_at=["agentcraft2_core/x.py:10 in a", "agentcraft2_core/y.py:20 in b"],
            error_traceback=(
                "Traceback (most recent call last):\n  File x\nConfigurationError: boom"
            ),
            request_id="r-9",
        ),
        "helper",
    )
    text = format_error(entry)
    assert "ConfigurationError" in text
    assert "the audit option 'fsync' is not valid" in text
    assert "expected: true or false" in text
    assert "found: 'maybe'" in text
    assert "fix: set it to true" in text
    assert "raised at: agentcraft2_core/y.py:20 in b" in text
    assert "request: r-9" in text
    assert "    Traceback (most recent call last):" in text
    assert "    ConfigurationError: boom" in text
    assert "stack trace" not in format_error(entry, trace=False)


def test_the_frames_stand_in_when_no_full_stack_was_written() -> None:
    entry = parse_line(
        _line(level="ERROR", error_type="X", error="m", error_at=["a.py:1 in f"]), "s"
    )
    assert "stack (innermost last):" in format_error(entry)
    assert "    a.py:1 in f" in format_error(entry)


def test_only_failures_are_listed() -> None:
    entries = [
        parse_line(_line(message="fine"), "s"),
        parse_line(_line(level="ERROR", message="bad"), "s"),
        parse_line(_line(level="WARNING", error_type="Odd", message="odd"), "s"),
        parse_line(_line(level="WARNING", message="careful"), "s"),
    ]
    assert [entry.message for entry in errors_in(entries)] == ["bad", "odd"]


# ------------------------------------------------------------------------- the writer


def test_a_crash_is_one_entry_with_its_type_message_and_stack(tmp_path: Path) -> None:
    path = tmp_path / "logs" / "helper.log"
    writer = LogWriter(path, "helper")
    writer.note("starting")
    writer.line(_line(message="started"))
    for row in (
        "Traceback (most recent call last):",
        '  File "x.py", line 3, in main',
        "    raise ValueError('bad port')",
        "ValueError: bad port",
        "helper-serve: ValueError: bad port",
        "  raised at: x.py:3 in main",
    ):
        writer.line(row + "\n")
    writer.finish(1)
    entries = [parse_line(row, "helper") for row in path.read_text(encoding="utf-8").splitlines()]
    failures = errors_in(entries)
    crash = failures[0]
    assert crash.fields["error_type"] == "ValueError"
    assert crash.fields["error"] == "bad port"
    assert "Traceback (most recent call last):" in crash.fields["error_traceback"]
    assert crash.message == "ValueError: bad port"
    # The crash says why it stopped, so the exit code is a note and not a second failure.
    assert entries[-1].message == "helper stopped with exit code 1"
    assert entries[-1].level == "INFO"
    assert len(failures) == 1
    assert entries[0].message == "starting"


def test_plain_output_of_a_service_that_ends_well_is_not_a_failure(tmp_path: Path) -> None:
    path = tmp_path / "helper.log"
    writer = LogWriter(path, "helper")
    writer.line("hello from print\n")
    writer.finish(0)
    entries = [parse_line(row, "helper") for row in path.read_text(encoding="utf-8").splitlines()]
    assert errors_in(entries) == []
    assert entries[0].level == "OUTPUT"
    assert entries[0].message == "hello from print"


def test_a_log_over_five_megabytes_is_set_aside_when_the_next_run_starts(tmp_path: Path) -> None:
    path = tmp_path / "helper.log"
    path.write_bytes(b"x" * 5_000_001)
    LogWriter(path, "helper").finish(0)
    assert path.with_name("helper.log.1").stat().st_size == 5_000_001
    assert path.stat().st_size < 1_000


# --------------------------------------------------------------------- ac2 run keeps it


def _service(root: Path, body: str) -> Path:
    folder = root / "agents" / "helper"
    (folder / "src" / "demo_agent").mkdir(parents=True, exist_ok=True)
    (folder / "src" / "demo_agent" / "__init__.py").write_text("", encoding="utf-8")
    (folder / "src" / "demo_agent" / "service.py").write_text(body, encoding="utf-8")
    return folder


def test_running_a_service_shows_its_output_and_keeps_it_in_its_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("AC2_HOME", raising=False)
    body = (
        "import json, sys\n"
        "print(json.dumps({'time': '2026-10-24T12:00:00+00:00', 'level': 'INFO',"
        " 'service': 'helper', 'message': 'ready'}), flush=True)\n"
        "print('a plain line')\n"
        "raise RuntimeError('port 8000 is taken')\n"
    )
    folder = _service(tmp_path, body)
    assert run_service(folder, "demo_agent.service", []) == 1
    shown = capfd.readouterr().out
    assert "ready" in shown
    assert "RuntimeError: port 8000 is taken" in shown  # the terminal gets it as it was written
    entries = [
        parse_line(row, "helper")
        for row in log_file(folder / ".agentcraft2", "helper")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    crash = next(entry for entry in errors_in(entries) if "error_traceback" in entry.fields)
    assert crash.fields["error_type"] == "RuntimeError"
    assert crash.fields["error"] == "port 8000 is taken"
    assert any(entry.message == "ready" for entry in entries)
    plain = next(entry for entry in entries if entry.message == "a plain line")
    assert plain.level == "OUTPUT"
    assert "a plain line" not in crash.fields["error_traceback"]


def test_the_log_goes_under_the_project_home_when_there_is_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("AC2_HOME", str(home))
    folder = _service(tmp_path, "print('hi')\n")
    assert run_service(folder, "demo_agent.service", []) == 0
    assert (home / ".agentcraft2" / "logs" / "helper.log").is_file()
    assert not (folder / ".agentcraft2").exists()


# ------------------------------------------------------------------------ the commands


def test_logs_show_the_latest_lines_in_order(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_log(
        workspace,
        _line(time="2026-10-24T12:00:01+00:00", message="first"),
        _line(time="2026-10-24T12:00:02+00:00", message="second", request_id="r-2"),
        _line(time="2026-10-24T12:00:03+00:00", level="ERROR", message="third"),
    )
    tools = toolbox_for_tests()
    assert main(["logs", "helper", "-n", "2", "--workspace", str(workspace)], tools) == 0
    out = capsys.readouterr().out.splitlines()
    assert [("second" in row, "third" in row) for row in out] == [(True, False), (False, True)]
    assert main(["logs", "--level", "error", "--workspace", str(workspace)], tools) == 0
    only = capsys.readouterr().out
    assert "third" in only
    assert "second" not in only
    assert main(["logs", "--request", "r-2", "--workspace", str(workspace)], tools) == 0
    assert "second" in capsys.readouterr().out
    assert main(["logs", "helper", "--raw", "--workspace", str(workspace)], tools) == 0
    assert json.loads(capsys.readouterr().out.splitlines()[0])["message"] == "first"


def test_logs_with_nothing_logged_say_how_to_get_a_log(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["logs", "--workspace", str(workspace)], toolbox_for_tests()) == 1
    err = capsys.readouterr().err
    assert "there are no logs yet" in err
    assert "ac2 run <service>" in err


def test_an_unknown_service_is_named_with_the_known_ones(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["logs", "nope", "--workspace", str(workspace)], toolbox_for_tests()) == 1
    assert "there is no service called 'nope'" in capsys.readouterr().err


def test_errors_list_the_failures_with_their_stacks(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_log(
        workspace,
        _line(message="fine"),
        _line(
            level="ERROR",
            message="tool_call",
            error_type="PolicyDenied",
            error="no rule covers it",
            error_traceback="Traceback (most recent call last):\n  File a\nPolicyDenied: x",
        ),
    )
    tools = toolbox_for_tests()
    assert main(["errors", "--workspace", str(workspace)], tools) == 0
    out = capsys.readouterr().out
    assert "PolicyDenied" in out
    assert "no rule covers it" in out
    assert "    Traceback (most recent call last):" in out
    assert "1 failure shown." in out
    assert main(["errors", "--no-trace", "--workspace", str(workspace)], tools) == 0
    assert "Traceback" not in capsys.readouterr().out


def test_errors_say_so_when_nothing_failed(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_log(workspace, _line(message="fine"))
    assert main(["errors", "--workspace", str(workspace)], toolbox_for_tests()) == 0
    assert capsys.readouterr().out.strip() == "No errors in the logs."


def test_follow_shows_what_is_added_and_starts_again_after_a_rotation(tmp_path: Path) -> None:
    path = tmp_path / "helper.log"
    path.write_text(_line(message="old") + "\n", encoding="utf-8")
    shown: list[str] = []
    steps = iter(
        [
            lambda: path.write_text(path.read_text() + _line(message="new") + "\n"),
            lambda: path.write_text(_line(message="after rotation") + "\n"),
        ]
    )

    def sleep(_seconds: float) -> None:
        next(steps, lambda: None)()

    follow([("helper", path)], lambda entry: shown.append(entry.message), sleep=sleep, cycles=3)
    assert shown == ["new", "after rotation"]


# --------------------------------------------------------------------------- the help


def test_the_help_lists_commands_by_what_they_are_for(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--help"], toolbox_for_tests()) == 0
    out = capsys.readouterr().out
    for section in ("Start:", "Verify:", "Run and watch:", "Keep and ship:"):
        assert section in out
    for command in ("init", "new", "doctor", "run", "logs", "errors"):
        assert f"\n  {command} " in out
    assert out.index("Start:") < out.index("Verify:") < out.index("Run and watch:")
    assert "A first session:" in out


def test_every_command_has_help_with_examples_or_a_plain_description(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from agentcraft2_cli.app import cli

    for name, command in cli.commands.items():
        assert command.help, f"{name} has no help"
        assert main([name, "--help"], toolbox_for_tests()) == 0, name
        assert "Usage:" in capsys.readouterr().out


def test_the_program_is_named_as_it_was_started(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", ["/usr/bin/ac2"])
    assert main(["--help"], toolbox_for_tests()) == 0
    assert "Usage: ac2 " in capsys.readouterr().out
    monkeypatch.setattr(sys, "argv", ["agentcraft2"])
    assert main(["--help"], toolbox_for_tests()) == 0
    assert "Usage: agentcraft2 " in capsys.readouterr().out
