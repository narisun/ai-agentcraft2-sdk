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
from agentcraft2_cli.services.installed import check_path_length, installed_problem
from agentcraft2_cli.services.registry import INSTALLED, PHOENIX, installed_dir
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
    install(tmp_path / "sdk", pip=writes_packages(asked), say=said.append)
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
    assert "975 MB free" in (caught.value.expected or "")
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
        install(sdk, pip=lambda arguments, folder: 1)
    assert sorted(path.name for path in packages.site_packages(folder).iterdir()) == before
    assert installed_problem(PHOENIX, folder) is None


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
        installer, "install_services", lambda sdk, library: seen.append((sdk, library))
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
