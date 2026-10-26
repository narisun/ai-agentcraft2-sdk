"""The one script that builds and installs the SDK stops at the first problem, in order."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from typing import Any

import pytest
from tools import install_sdk

ROOT = Path(__file__).resolve().parents[2]
OLD = install_sdk.Installed("agentcraft2-core", "0.0.9", editable=False, location="site-packages")


def arguments(
    *, dev: bool = False, editable: bool = False, verbose: bool = False
) -> argparse.Namespace:
    return argparse.Namespace(dev=dev, editable=editable, verbose=verbose)


def completed(returncode: int = 0, stdout: str = "", stderr: str = "") -> Any:
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Replace every step with a recorder, so the order of the steps can be read."""
    made: list[str] = []

    def record(name: str, result: Any = None) -> Callable[..., Any]:
        def step(*_: Any, **__: Any) -> Any:
            made.append(name)
            return result

        return step

    monkeypatch.setattr(install_sdk, "check_environment", record("check", ["Python 3.13"]))
    monkeypatch.setattr(install_sdk, "find_installed", record("find", [OLD]))
    monkeypatch.setattr(install_sdk, "build_wheels", record("build", [Path("core.whl")] * 3))
    monkeypatch.setattr(install_sdk, "uninstall", record("uninstall"))
    monkeypatch.setattr(install_sdk, "install", record("install"))
    monkeypatch.setattr(install_sdk, "verify", record("verify", ["agentcraft2-core 0.1.0"]))
    monkeypatch.setattr(install_sdk, "command_note", record("note", ["Check it with: ac2"]))
    return made


def test_the_steps_run_in_order_and_each_is_marked_complete(
    calls: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert install_sdk.main([]) == 0
    assert calls == ["check", "find", "build", "uninstall", "install", "verify", "note"]
    lines = capsys.readouterr().out.splitlines()
    began = [line for line in lines if re.fullmatch(r"\[\d/6\] [^:]+", line)]
    done = [line for line in lines if line.endswith(": complete")]
    assert [line[:5] for line in began] == [f"[{n}/6]" for n in range(1, 7)]
    assert [line[:5] for line in done] == [f"[{n}/6]" for n in range(1, 7)]
    # Each step is complete before the next one begins.
    order = [
        line[:5] + (" done" if line in done else " begin") for line in lines if line[:1] == "["
    ]
    assert order == [f"[{n}/6] {kind}" for n in range(1, 7) for kind in ("begin", "done")]
    assert "removed agentcraft2-core" in "\n".join(lines)


def test_the_install_waits_for_a_successful_uninstall(
    calls: list[str], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def refuse(*_: Any) -> None:
        raise install_sdk.SetupError("pip could not uninstall agentcraft2-core: locked", "close it")

    monkeypatch.setattr(install_sdk, "uninstall", refuse)
    assert install_sdk.main([]) == 1
    assert calls == ["check", "find", "build"]
    error = capsys.readouterr().err
    assert "error: stopped at step 4 of 6, uninstalling the installed sdk:" in error
    assert "pip could not uninstall agentcraft2-core: locked" in error
    assert "fix: close it" in error


def test_a_failed_build_leaves_the_installed_sdk_alone(
    calls: list[str], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fail(*_: Any) -> None:
        raise install_sdk.SetupError("pip could not build agentcraft2-aws")

    monkeypatch.setattr(install_sdk, "build_wheels", fail)
    assert install_sdk.main([]) == 1
    assert "uninstall" not in calls
    assert "step 3 of 6, building the sdk" in capsys.readouterr().err


def test_a_failed_check_stops_before_anything_is_done(
    calls: list[str], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fail(*_: Any) -> None:
        raise install_sdk.SetupError("Python 3.9.0 is too old", "use a newer Python")

    monkeypatch.setattr(install_sdk, "check_environment", fail)
    assert install_sdk.main([]) == 1
    assert calls == []
    assert "stopped at step 1 of 6, checking the environment: Python 3.9.0 is too old" in (
        capsys.readouterr().err
    )


def test_nothing_is_uninstalled_when_nothing_is_installed(
    calls: list[str], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(install_sdk, "find_installed", lambda: [])
    assert install_sdk.main([]) == 0
    assert "uninstall" not in calls
    assert "nothing to uninstall" in capsys.readouterr().out


def test_an_editable_install_builds_nothing(
    calls: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert install_sdk.main(["--editable"]) == 0
    assert "build" not in calls
    assert "there is nothing to build" in capsys.readouterr().out


# ------------------------------------------------------------------------------ the checks


@pytest.fixture
def environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(install_sdk, "DIST", tmp_path / "dist")
    monkeypatch.setattr(
        install_sdk, "pip", lambda *_, **__: completed(stdout="pip 24.2 from somewhere\n")
    )
    monkeypatch.delenv("PYTHONPATH", raising=False)


@pytest.mark.usefixtures("environment")
def test_the_checks_pass_for_this_checkout(tmp_path: Path) -> None:
    lines = install_sdk.check_environment(arguments(dev=True), [])
    assert any(line.startswith("SDK version") for line in lines)
    assert (tmp_path / "dist").is_dir()


@pytest.mark.usefixtures("environment")
def test_an_old_python_is_refused_with_the_version_it_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "version_info", (3, 9, 1, "final", 0))
    with pytest.raises(install_sdk.SetupError, match=r"Python 3.9.1 is too old") as caught:
        install_sdk.check_environment(arguments(), [])
    assert "3.11 or newer" in str(caught.value)


def test_a_pip_that_does_not_run_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(install_sdk, "DIST", tmp_path)
    monkeypatch.setattr(
        install_sdk, "pip", lambda *_, **__: completed(1, stderr="No module named pip")
    )
    with pytest.raises(install_sdk.SetupError, match="No module named pip"):
        install_sdk.check_environment(arguments(), [])


@pytest.mark.usefixtures("environment")
def test_a_pythonpath_that_shadows_the_install_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PYTHONPATH", str(ROOT / "packages" / "agentcraft2-core" / "src"))
    with pytest.raises(install_sdk.SetupError, match="PYTHONPATH names the checkout") as caught:
        install_sdk.check_environment(arguments(), [])
    assert caught.value.fix == "unset PYTHONPATH, then run the script again"


@pytest.mark.usefixtures("environment")
def test_packages_with_different_versions_are_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    versions = {"agentcraft2-core": "0.1.0", "agentcraft2-aws": "0.1.0", "agentcraft2-cli": "0.2.0"}
    monkeypatch.setattr(install_sdk, "project", lambda name: {"version": versions[name]})
    with pytest.raises(install_sdk.SetupError, match="do not have one version") as caught:
        install_sdk.check_environment(arguments(), [])
    assert "agentcraft2-cli 0.2.0" in str(caught.value)


@pytest.mark.usefixtures("environment")
def test_a_missing_package_folder_is_named(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(install_sdk, "ROOT", tmp_path)
    with pytest.raises(install_sdk.SetupError, match="package folder is missing") as caught:
        install_sdk.check_environment(arguments(), [])
    assert str(tmp_path) in str(caught.value)


# ----------------------------------------------------------------------------- the work


def test_an_editable_installation_is_found_and_described(monkeypatch: pytest.MonkeyPatch) -> None:
    direct = json.dumps({"dir_info": {"editable": True}, "url": "file:///repo"})
    distribution = SimpleNamespace(
        version="0.1.0",
        read_text=lambda name: direct if name == "direct_url.json" else None,
        locate_file=lambda _: Path("/repo/packages/agentcraft2-core/src"),
    )

    def distribution_of(name: str) -> Any:
        if name != "agentcraft2-core":
            raise importlib.metadata.PackageNotFoundError(name)
        return distribution

    monkeypatch.setattr(importlib.metadata, "distribution", distribution_of)
    (found,) = install_sdk.find_installed()
    assert found.editable
    assert found.describe().startswith("agentcraft2-core 0.1.0 (editable, ")


def test_a_failed_uninstall_names_the_package_and_pips_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        install_sdk, "pip", lambda *_, **__: completed(1, stderr="PermissionError: ac2.exe")
    )
    with pytest.raises(install_sdk.SetupError) as caught:
        install_sdk.uninstall([OLD])
    assert "pip could not uninstall agentcraft2-core: PermissionError: ac2.exe" in str(caught.value)
    assert "Nothing was installed" in caught.value.fix


def test_an_uninstall_that_leaves_the_sdk_importable_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(install_sdk, "pip", lambda *_, **__: completed())
    monkeypatch.setattr(subprocess, "run", lambda *_, **__: completed(stdout="agentcraft2_core\n"))
    with pytest.raises(install_sdk.SetupError, match="still importable") as caught:
        install_sdk.uninstall([OLD])
    assert "agentcraft2_core" in str(caught.value)


def installation(
    monkeypatch: pytest.MonkeyPatch, *, dev: bool, editable: bool, wheels: list[Path]
) -> list[str]:
    made: list[list[str]] = []

    def record(arguments: list[str], **_: Any) -> Any:
        made.append(list(arguments))
        return completed()

    monkeypatch.setattr(install_sdk, "pip", record)
    install_sdk.install(arguments(dev=dev, editable=editable), wheels, ["--no-index"])
    (call,) = made
    return call


def test_wheels_are_installed_together_with_pips_own_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wheels = [Path(f"dist/{name}.whl") for name in ("core", "aws", "cli")]
    call = installation(monkeypatch, dev=False, editable=False, wheels=wheels)
    assert call == [
        "install",
        "-q",
        "--no-warn-script-location",
        *(str(wheel) for wheel in wheels),
        "--no-index",
    ]


def test_a_development_install_is_editable_with_every_extra_and_the_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    call = installation(monkeypatch, dev=True, editable=True, wheels=[])
    assert call.count("--editable") == 3
    core = next(
        item
        for item in call
        if item.endswith(
            "agentcraft2-core[" + ",".join(install_sdk.extras_of("agentcraft2-core")) + "]"
        )
    )
    assert "otel" in core
    assert "pytest>=8.2" in call
    assert call[-1] == "--no-index"


def test_a_failed_install_says_the_earlier_sdk_is_gone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(install_sdk, "pip", lambda *_, **__: completed(1))
    with pytest.raises(install_sdk.SetupError) as caught:
        install_sdk.install(arguments(editable=True), [], [])
    assert "already uninstalled" in caught.value.fix


def test_pip_prints_only_problems_unless_everything_is_asked_for(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(install_sdk, "DIST", tmp_path)
    made: list[list[str]] = []

    def record(call: list[str], **_: Any) -> Any:
        made.append(call)
        if call[0] == "wheel":
            name = call[-1].replace("\\", "/").rsplit("/", 1)[-1].replace("-", "_")
            (tmp_path / f"{name}-0.1.0-py3-none-any.whl").write_bytes(b"")
        return completed()

    monkeypatch.setattr(install_sdk, "pip", record)
    install_sdk.build_wheels()
    install_sdk.build_wheels(verbose=True)
    quiet, loud = made[:3], made[3:]
    assert all("-q" in call for call in quiet)
    assert not any("-q" in call for call in loud)
    assert install_sdk.quiet(False) == ["-q"]
    assert install_sdk.quiet(True) == []


def test_verbose_installs_with_all_of_pips_output(monkeypatch: pytest.MonkeyPatch) -> None:
    made: list[list[str]] = []

    def record(call: list[str], **_: Any) -> Any:
        made.append(call)
        return completed()

    monkeypatch.setattr(install_sdk, "pip", record)
    install_sdk.install(arguments(editable=True, verbose=True), [], [])
    assert "-q" not in made[0]


# -------------------------------------------------------- leftovers of a stopped uninstall


@pytest.fixture
def site_packages(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    folder = tmp_path / "site-packages"
    folder.mkdir()
    monkeypatch.setattr(install_sdk, "site_folders", lambda: [folder])
    return folder


def test_what_a_stopped_uninstall_left_is_found_by_name(site_packages: Path) -> None:
    (site_packages / "~gentcraft2_cli-0.1.0.dist-info").mkdir()
    (site_packages / "~gentcraft2-core").mkdir()
    (site_packages / "~ytest").mkdir()  # not ours
    (site_packages / "agentcraft2_cli").mkdir()  # installed, not left over
    names = [entry.name for entry in install_sdk.leftovers()]
    assert names == ["~gentcraft2-core", "~gentcraft2_cli-0.1.0.dist-info"]


@pytest.mark.usefixtures("environment")
def test_the_check_warns_about_a_leftover_and_says_how_to_delete_it(site_packages: Path) -> None:
    (site_packages / "~gentcraft2_cli-0.1.0.dist-info").mkdir()
    lines = install_sdk.check_environment(arguments(), [])
    (warning,) = [line for line in lines if line.startswith("warning:")]
    assert "~gentcraft2_cli-0.1.0.dist-info" in warning
    assert "interrupted uninstall" in warning
    assert any(command in warning for command in ("rmdir /s /q", "rm -rf"))


@pytest.mark.usefixtures("environment")
def test_no_warning_without_a_leftover(site_packages: Path) -> None:
    del site_packages
    lines = install_sdk.check_environment(arguments(), [])
    assert not [line for line in lines if line.startswith("warning:")]


# ------------------------------------------------------------------------- its own scope


def test_the_script_knows_nothing_about_the_examples_or_the_other_tools() -> None:
    source = (ROOT / "tools" / "install_sdk.py").read_text(encoding="utf-8")
    for word in ("rmprep", "rmqna", "examples/", "Agent Lab", "Phoenix"):
        assert word not in source


def test_the_packages_it_installs_all_exist() -> None:
    for package in install_sdk.PACKAGES:
        assert (ROOT / "packages" / package / "pyproject.toml").is_file()


# ------------------------------------------------------------------- how to run ac2 afterwards


@pytest.fixture
def scripts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """A scripts folder holding ac2, as the installed distribution's file list names it."""
    folder = tmp_path / "Scripts"
    folder.mkdir()
    (folder / "ac2.exe").write_bytes(b"")
    installed = SimpleNamespace(
        files=[PurePosixPath("agentcraft2_cli/__init__.py"), PurePosixPath("ac2.exe")],
        locate_file=lambda entry: folder / entry,
    )
    monkeypatch.setattr(importlib.metadata, "distribution", lambda _: installed)
    monkeypatch.setattr(install_sdk, "python_command", lambda: "python")
    monkeypatch.setattr(shutil, "which", lambda _: None)
    return folder


def test_ac2_on_path_is_checked_by_its_own_name(
    monkeypatch: pytest.MonkeyPatch, scripts: Path
) -> None:
    monkeypatch.setenv("PATH", str(scripts))
    monkeypatch.setattr(shutil, "which", lambda _: str(scripts / "ac2.exe"))
    assert install_sdk.command_note() == [
        "Check it with: ac2 --version (or python -m agentcraft2_cli --version)"
    ]


def test_ac2_off_path_names_the_folder_and_the_command_that_works(
    monkeypatch: pytest.MonkeyPatch, scripts: Path, tmp_path: Path
) -> None:
    monkeypatch.setenv("PATH", str(tmp_path / "elsewhere"))
    lines = install_sdk.command_note()
    assert str(scripts) in lines[0]
    assert "not on your PATH" in lines[0]
    assert "python -m agentcraft2_cli" in lines[1]
    assert lines[2] == "Check it with: python -m agentcraft2_cli --version"


def test_an_older_ac2_that_comes_first_is_named(
    monkeypatch: pytest.MonkeyPatch, scripts: Path, tmp_path: Path
) -> None:
    older = tmp_path / "old" / "ac2.exe"
    monkeypatch.setenv("PATH", os.pathsep.join([str(older.parent), str(scripts)]))
    monkeypatch.setattr(shutil, "which", lambda _: str(older))
    lines = install_sdk.command_note()
    assert str(older) in lines[0]
    assert str(scripts) in lines[0]
    assert "python -m agentcraft2_cli" in lines[1]


def test_a_distribution_without_the_command_gets_the_module_form(
    monkeypatch: pytest.MonkeyPatch, scripts: Path
) -> None:
    del scripts
    installed = SimpleNamespace(files=None, locate_file=lambda entry: Path(entry))
    monkeypatch.setattr(importlib.metadata, "distribution", lambda _: installed)
    assert install_sdk.command_note() == ["Check it with: python -m agentcraft2_cli --version"]


def test_a_missing_distribution_gets_the_module_form(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(_: str) -> Any:
        raise importlib.metadata.PackageNotFoundError

    monkeypatch.setattr(importlib.metadata, "distribution", missing)
    monkeypatch.setattr(install_sdk, "python_command", lambda: "python")
    assert install_sdk.command_note() == ["Check it with: python -m agentcraft2_cli --version"]


def test_python_is_called_python_only_when_that_is_this_python(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shutil, "which", lambda _: sys.executable)
    assert install_sdk.python_command() == "python"
    monkeypatch.setattr(shutil, "which", lambda _: None)
    assert install_sdk.python_command() == f'"{sys.executable}"'


def test_the_note_is_printed_after_the_install_is_reported(
    calls: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert install_sdk.main([]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[-2:] == ["The SDK is installed.", "Check it with: ac2"]


def test_the_folder_is_named_without_dots_in_it(
    monkeypatch: pytest.MonkeyPatch, scripts: Path, tmp_path: Path
) -> None:
    del scripts
    installed = SimpleNamespace(
        files=[PurePosixPath("../../Scripts/ac2.exe")],
        locate_file=lambda entry: tmp_path / "Lib" / "site-packages" / entry,
    )
    monkeypatch.setattr(importlib.metadata, "distribution", lambda _: installed)
    monkeypatch.setenv("PATH", str(tmp_path / "elsewhere"))
    assert str(tmp_path / "Scripts") in install_sdk.command_note()[0]
    assert ".." not in install_sdk.command_note()[0]
