"""``install-sdk.bat`` and ``install-sdk.sh`` run the one installer with the chosen Python."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BATCH = ROOT / "install-sdk.bat"
SHELL = ROOT / "install-sdk.sh"

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="runs a shell script")


def launcher() -> list[str]:
    return ["cmd", "/c", str(BATCH)] if sys.platform == "win32" else ["sh", str(SHELL)]


def run(*arguments: str, python: str | None) -> subprocess.CompletedProcess[str]:
    environment = {key: value for key, value in os.environ.items() if key != "PYTHON"}
    if python is not None:
        environment["PYTHON"] = python
    return subprocess.run(  # noqa: S603 - the launcher under test
        [*launcher(), *arguments],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
        env=environment,
    )


def test_each_launcher_runs_the_one_installer() -> None:
    for path in (BATCH, SHELL):
        text = path.read_text(encoding="utf-8")
        assert "install_sdk.py" in text, path
        assert "-m pip" not in text, f"{path.name} must leave installing to the installer"


def test_the_line_endings_suit_the_shell_that_reads_them() -> None:
    assert b"\r\n" in BATCH.read_bytes()
    assert b"\n" not in BATCH.read_bytes().replace(b"\r\n", b"")
    assert b"\r" not in SHELL.read_bytes()


@posix_only
def test_the_shell_launcher_is_executable() -> None:
    assert os.access(SHELL, os.X_OK)


def test_the_installer_gets_the_options_and_runs_with_the_chosen_python() -> None:
    result = run("--help", python=sys.executable)
    assert result.returncode == 0, result.stderr
    assert "--dev" in result.stdout
    assert "--editable" in result.stdout


def test_a_python_that_cannot_run_is_named_with_the_fix(tmp_path: Path) -> None:
    missing = str(tmp_path / "no python (x86)" / "python")
    result = run("--help", python=missing)
    assert result.returncode == 1
    assert "cannot run" in result.stderr
    assert "no python (x86)" in result.stderr
    assert "fix:" in result.stderr
    assert result.stdout == ""


def test_it_finds_the_installer_from_another_folder(tmp_path: Path) -> None:
    environment = {key: value for key, value in os.environ.items() if key != "PYTHON"}
    environment["PYTHON"] = sys.executable
    result = subprocess.run(  # noqa: S603 - the launcher under test
        [*launcher(), "--help"],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
        env=environment,
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert "--editable" in result.stdout
