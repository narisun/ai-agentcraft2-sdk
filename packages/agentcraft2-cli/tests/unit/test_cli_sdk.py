"""The SDK installs its tools, once, into its own folder: ``python -m agentcraft2_cli.sdk``."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

import pytest

from agentcraft2_cli import __version__
from agentcraft2_cli import sdk as installer
from agentcraft2_cli.errors import CliError
from agentcraft2_cli.services import bootstrap, packages
from agentcraft2_cli.services.installed import (
    check_path_length,
    fingerprint,
    installed_problem,
    outdated,
)
from agentcraft2_cli.services.registry import (
    INSTALLED,
    LAB,
    PHOENIX,
    SERVICES,
    installed_dir,
)
from agentcraft2_cli.workspace import LibrarySource

CHECKOUT = LibrarySource(kind="path", path="/work/sdk")
INDEX = LibrarySource(kind="index", version=">=0.1,<0.2")
PLENTY = 10**12


def writes_packages(asked: list[Sequence[str]]):  # type: ignore[no-untyped-def]
    def pip(arguments: Sequence[str], folder: Path) -> int:
        asked.append(arguments)
        target = Path(arguments[arguments.index("--target") + 1])
        target.mkdir(parents=True)
        return 0

    return pip


def install(sdk: Path, library: LibrarySource = CHECKOUT, **changes: object) -> None:
    arguments: dict[str, object] = {
        "pip": writes_packages([]),
        "say": lambda text: None,
        "disk_problem": lambda folder: None,
        "free_space": lambda folder: PLENTY,
        "check_paths": lambda spec, folder: None,
        **changes,
    }
    installer.install_services(sdk, library, **arguments)  # type: ignore[arg-type]


def test_phoenix_is_installed_into_its_own_folder_with_a_record_of_the_sdk(tmp_path: Path) -> None:
    asked: list[Sequence[str]] = []
    said: list[str] = []
    install(tmp_path / "sdk", pip=writes_packages(asked), say=said.append, specs=[PHOENIX])
    folder = installed_dir(tmp_path / "sdk", "phoenix")
    assert said == [f"Installing phoenix into {folder}", "  phoenix installed"]
    assert asked[0][-2] == "--editable"
    assert asked[0][-1].endswith("agentcraft2-phoenix[viewer]")
    record = json.loads((folder / INSTALLED).read_text("utf-8"))
    assert record["sdk_version"] == __version__
    assert record["service"] == "phoenix"
    assert installed_problem(PHOENIX, folder) is None


def test_from_an_index_the_requirement_is_the_version_the_workspace_asks_for(
    tmp_path: Path,
) -> None:
    asked: list[Sequence[str]] = []
    install(tmp_path / "sdk", INDEX, pip=writes_packages(asked))
    assert asked[0][-1] == "agentcraft2-phoenix[viewer]>=0.1,<0.2"


def test_agent_lab_is_installed_beside_phoenix_with_the_sdk_it_needs(tmp_path: Path) -> None:
    asked: list[Sequence[str]] = []
    install(tmp_path / "sdk", pip=writes_packages(asked))
    lab = next(call for call in asked if call[-1].endswith("agentcraft2-ui[chat]"))
    assert lab.count("--editable") == 2
    assert installed_problem(LAB, installed_dir(tmp_path / "sdk", "lab")) is None


def test_a_folder_that_is_not_on_a_local_disk_is_refused_before_anything_is_installed(
    tmp_path: Path,
) -> None:
    def pip(arguments: Sequence[str], folder: Path) -> int:
        raise AssertionError("pip must not run")

    with pytest.raises(CliError, match="not on a local disk") as caught:
        install(
            tmp_path / "sdk",
            pip=pip,
            disk_problem=lambda folder: r"\\server\share is a network path (\\server)",
        )
    assert "network path" in (caught.value.actual or "")
    assert "AC2_SDK_PATH" in (caught.value.fix or "")
    assert not (tmp_path / "sdk").exists()


def test_not_enough_room_is_refused_with_both_numbers(tmp_path: Path) -> None:
    with pytest.raises(CliError, match="not enough room") as caught:
        install(tmp_path / "sdk", free_space=lambda folder: 100 * 1024 * 1024)
    needed = int(sum(spec.megabytes for spec in SERVICES.values()) * 1.5)
    assert f"{needed} MB free" in (caught.value.expected or "")
    assert "100 MB free" in (caught.value.actual or "")


def test_a_folder_so_deep_that_windows_would_not_open_the_files_is_refused(
    tmp_path: Path,
) -> None:
    deep = installed_dir(tmp_path / ("d" * 70) / ("e" * 70) / ("f" * 70), "phoenix")
    with pytest.raises(CliError, match="too long for Windows") as caught:
        check_path_length(PHOENIX, deep, windows=True)
    assert caught.value.expected == "paths of at most 259 characters"
    assert "characters for the longest file below" in (caught.value.actual or "")
    assert "AC2_SDK_PATH" in (caught.value.fix or "")
    assert "LongPathsEnabled" in (caught.value.fix or "")


def test_a_short_folder_and_other_systems_are_not_refused(tmp_path: Path) -> None:
    check_path_length(
        PHOENIX, installed_dir(Path("C:/Users/jane.doe/AppData/Local/x"), "phoenix"), windows=True
    )
    check_path_length(PHOENIX, installed_dir(tmp_path / ("d" * 200), "phoenix"), windows=False)


def test_the_default_folder_leaves_room_for_a_long_user_name() -> None:
    default = Path("C:/Users") / ("a" * 40) / "AppData" / "Local" / "agentcraft2-sdk"
    check_path_length(PHOENIX, installed_dir(default, "phoenix"), windows=True)


def test_a_failed_install_keeps_what_was_there(tmp_path: Path) -> None:
    sdk = tmp_path / "sdk"
    install(sdk)
    folder = installed_dir(sdk, "phoenix")
    before = sorted(path.name for path in packages.site_packages(folder).iterdir())
    with pytest.raises(CliError, match="exit code 1"):
        install(sdk, pip=lambda arguments, folder: 1, force=True)
    assert sorted(path.name for path in packages.site_packages(folder).iterdir()) == before
    assert installed_problem(PHOENIX, folder) is None


# ------------------------------------------- tools that are current are not installed again


def checkout_of(tmp_path: Path, text: str = "[project]\nname = 'x'\n") -> LibrarySource:
    """A checkout whose tools have a pyproject.toml, as a stand-in for the real packages."""
    for name in ("agentcraft2-phoenix", "agentcraft2-core", "agentcraft2-ui"):
        folder = tmp_path / "checkout" / "packages" / name
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "pyproject.toml").write_text(text, encoding="utf-8")
    return LibrarySource(kind="path", path=str(tmp_path / "checkout"))


def run_install(
    sdk: Path, library: LibrarySource, **changes: object
) -> tuple[list[str], list[str]]:
    """Install; return what pip was asked to install (by service) and what was said."""
    asked: list[Sequence[str]] = []
    said: list[str] = []
    install(sdk, library, pip=writes_packages(asked), say=said.append, **changes)
    return [call[-1].rsplit("agentcraft2-", 1)[-1] for call in asked], said


def test_a_second_install_leaves_current_tools_alone(tmp_path: Path) -> None:
    sdk, library = tmp_path / "sdk", checkout_of(tmp_path)
    first, _ = run_install(sdk, library)
    assert first == ["phoenix[viewer]", "ui[chat]"]
    marker = packages.site_packages(installed_dir(sdk, "phoenix")) / "marker.txt"
    marker.write_text("kept", encoding="utf-8")

    asked, said = run_install(sdk, library)

    assert asked == []
    assert marker.read_text(encoding="utf-8") == "kept"
    assert len(said) == 2
    assert all("installed and current" in line and "skipped" in line for line in said)
    assert "--force-services" in said[0]


def test_force_installs_every_tool_again_even_when_current(tmp_path: Path) -> None:
    sdk, library = tmp_path / "sdk", checkout_of(tmp_path)
    run_install(sdk, library)
    asked, said = run_install(sdk, library, force=True)
    assert asked == ["phoenix[viewer]", "ui[chat]"]
    assert not any("skipped" in line for line in said)


def test_a_changed_dependency_installs_the_tool_that_uses_it_and_only_that_one(
    tmp_path: Path,
) -> None:
    sdk, library = tmp_path / "sdk", checkout_of(tmp_path)
    run_install(sdk, library)
    # Phoenix is installed from its own package only; Agent Lab also from the core package.
    core = tmp_path / "checkout" / "packages" / "agentcraft2-core" / "pyproject.toml"
    core.write_text("[project]\nname = 'x'\ndependencies = ['y']\n", encoding="utf-8")

    asked, said = run_install(sdk, library)

    assert asked == ["ui[chat]"]
    assert any(line.startswith("phoenix is installed and current") for line in said)
    assert any(line.startswith("lab must be installed again") for line in said)
    assert any("have changed since it was installed" in line for line in said)


def test_a_change_to_the_code_alone_is_not_a_reason_to_install(tmp_path: Path) -> None:
    sdk, library = tmp_path / "sdk", checkout_of(tmp_path)
    run_install(sdk, library)
    source = tmp_path / "checkout" / "packages" / "agentcraft2-ui" / "module.py"
    source.write_text("print('edited')\n", encoding="utf-8")
    asked, _ = run_install(sdk, library)
    assert asked == []


def test_line_endings_of_a_checkout_do_not_change_the_fingerprint(tmp_path: Path) -> None:
    library = checkout_of(tmp_path, "[project]\nname = 'x'\n")
    before = fingerprint(PHOENIX, library)
    # write_text translates newlines on Windows; write the bytes to be sure of CRLF
    pyproject = tmp_path / "checkout" / "packages" / "agentcraft2-phoenix" / "pyproject.toml"
    pyproject.write_bytes(b"[project]\r\nname = 'x'\r\n")
    assert fingerprint(PHOENIX, library) == before


def test_another_version_of_the_sdk_or_a_missing_fingerprint_installs_again(
    tmp_path: Path,
) -> None:
    sdk, library = tmp_path / "sdk", checkout_of(tmp_path)
    run_install(sdk, library)
    folder = installed_dir(sdk, "phoenix")
    assert outdated(PHOENIX, folder, library) is None
    assert "installed by SDK" in (outdated(PHOENIX, folder, library, "9.9") or "")
    record = folder / INSTALLED
    old = json.loads(record.read_text("utf-8"))
    del old["fingerprint"]  # a record made before fingerprints existed
    record.write_text(json.dumps(old), encoding="utf-8")
    asked, _ = run_install(sdk, library)
    assert asked == ["phoenix[viewer]"]


def test_a_damaged_tool_is_installed_again_without_force(tmp_path: Path) -> None:
    sdk, library = tmp_path / "sdk", checkout_of(tmp_path)
    run_install(sdk, library)
    (installed_dir(sdk, "lab") / INSTALLED).unlink()
    asked, _ = run_install(sdk, library)
    assert asked == ["ui[chat]"]


def test_when_everything_is_current_no_disk_or_room_check_is_made(tmp_path: Path) -> None:
    sdk, library = tmp_path / "sdk", checkout_of(tmp_path)
    run_install(sdk, library)

    def fail(folder: Path) -> object:
        raise AssertionError("nothing is installed, so nothing needs room")

    run_install(sdk, library, disk_problem=fail, free_space=fail)


def test_a_tool_in_use_is_named_instead_of_failing_with_a_system_error(tmp_path: Path) -> None:
    sdk, library = tmp_path / "sdk", checkout_of(tmp_path)

    def locked(arguments: Sequence[str], folder: Path) -> int:
        raise PermissionError("the file is in use")

    with pytest.raises(CliError, match="could not be replaced") as caught:
        install(sdk, library, pip=locked)
    assert "ac2 services stop" in (caught.value.fix or "")


def test_main_passes_force_on(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[object] = []
    monkeypatch.setenv("AC2_SDK_PATH", str(tmp_path / "sdk"))
    monkeypatch.setattr(
        installer, "install_services", lambda sdk, library, **given: seen.append(given["force"])
    )
    checkout = tmp_path / "checkout"
    (checkout / "packages" / "agentcraft2-core").mkdir(parents=True)
    (checkout / "packages" / "agentcraft2-core" / "pyproject.toml").write_text("", encoding="utf-8")
    assert installer.main(["install", "--checkout", str(checkout)]) == 0
    assert installer.main(["install", "--checkout", str(checkout), "--force"]) == 0
    assert seen == [False, True]


# ----------------------------------------------------------- what makes an install usable


def test_an_install_by_another_version_of_the_sdk_is_not_usable(tmp_path: Path) -> None:
    install(tmp_path / "sdk")
    folder = installed_dir(tmp_path / "sdk", "phoenix")
    assert installed_problem(PHOENIX, folder, "9.9.9") == (
        f"it was installed by SDK {__version__}, and this is SDK 9.9.9"
    )


def test_a_missing_record_and_missing_packages_are_named(tmp_path: Path) -> None:
    folder = installed_dir(tmp_path / "sdk", "phoenix")
    assert "does not exist" in (installed_problem(PHOENIX, folder) or "")
    packages_folder = packages.site_packages(folder)
    packages_folder.mkdir(parents=True)
    (packages_folder / bootstrap.MANIFEST).write_text(
        json.dumps(bootstrap.runtime_identity()), encoding="utf-8"
    )
    assert INSTALLED in (installed_problem(PHOENIX, folder) or "")


# ------------------------------------------------------------------------ the command line


def test_main_installs_from_a_checkout_into_ac2_sdk_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[tuple[Path, LibrarySource]] = []
    monkeypatch.setenv("AC2_SDK_PATH", str(tmp_path / "sdk"))
    monkeypatch.setattr(
        installer, "install_services", lambda sdk, library, **_: seen.append((sdk, library))
    )
    checkout = tmp_path / "checkout"
    (checkout / "packages" / "agentcraft2-core").mkdir(parents=True)
    (checkout / "packages" / "agentcraft2-core" / "pyproject.toml").write_text("", encoding="utf-8")
    assert installer.main(["install", "--checkout", str(checkout)]) == 0
    assert seen[0][0] == tmp_path / "sdk"
    assert seen[0][1].kind == "path"
    assert installer.main(["install", "--version", ">=0.1"]) == 0
    assert seen[1][1] == LibrarySource(kind="index", version=">=0.1")


def test_main_says_what_to_give_when_it_cannot_tell_where_to_install_from(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AC2_SDK_PATH", str(tmp_path / "sdk"))
    monkeypatch.setattr(
        installer, "library_source", lambda *_: (_ for _ in ()).throw(CliError("x"))
    )
    assert installer.main(["install"]) == 1
    err = capsys.readouterr().err
    assert "cannot tell where to install the SDK's tools from" in err
    assert "--checkout DIR" in err
    assert "--version SPECIFIER" in err


def test_check_names_what_is_installed_and_what_is_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AC2_SDK_PATH", str(tmp_path / "sdk"))
    assert installer.main(["check"]) == 1
    err = capsys.readouterr().err
    assert "the SDK's tools are not all installed" in err
    assert "phoenix:" in err
    install(tmp_path / "sdk")
    capsys.readouterr()
    assert installer.main(["check"]) == 0
    assert f"phoenix: installed in {installed_dir(tmp_path / 'sdk', 'phoenix')}" in (
        capsys.readouterr().out
    )
