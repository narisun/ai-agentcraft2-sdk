"""Where the SDK keeps its tools, and the refusal of a folder that is not on a local disk."""

from __future__ import annotations

from pathlib import Path, PureWindowsPath

import pytest

from agentcraft2_core.config import MappingConfigSource, load_service_config, sdk_path, sdk_variable
from agentcraft2_core.config.local_disk import DRIVE_REMOTE, free_bytes, local_disk_problem
from agentcraft2_core.config.sdk_location import default_sdk_path
from agentcraft2_core.contracts import ConfigurationError

WINDOWS_ENVIRONMENT = {
    "LOCALAPPDATA": r"C:\Users\jane.doe\AppData\Local",
    "APPDATA": r"C:\Users\jane.doe\AppData\Roaming",
    "OneDrive": r"C:\Users\jane.doe\OneDrive - Bank",
}


def test_on_windows_the_default_is_the_local_application_data_folder_never_the_roaming_one() -> (
    None
):
    found = default_sdk_path(WINDOWS_ENVIRONMENT, windows=True, home=Path("C:/Users/jane.doe"))
    assert PureWindowsPath(found) == PureWindowsPath(
        r"C:\Users\jane.doe\AppData\Local\agentcraft2-sdk"
    )
    assert "Roaming" not in str(found)


def test_without_localappdata_windows_falls_back_to_the_same_folder_in_the_home() -> None:
    found = default_sdk_path({}, windows=True, home=Path("C:/Users/jane.doe"))
    assert Path(found) == Path("C:/Users/jane.doe") / "AppData" / "Local" / "agentcraft2-sdk"


def test_elsewhere_the_default_is_a_hidden_folder_in_the_home(tmp_path: Path) -> None:
    assert default_sdk_path({}, windows=False, home=tmp_path) == tmp_path / ".agentcraft2_sdk"


def test_the_variable_names_the_folder_and_must_be_absolute(tmp_path: Path) -> None:
    name = sdk_variable()
    assert name == "AC2_SDK_PATH"
    assert sdk_path(MappingConfigSource({name: str(tmp_path / "tools")})) == tmp_path / "tools"
    with pytest.raises(ConfigurationError, match="AC2_SDK_PATH is not an absolute path") as caught:
        sdk_path(MappingConfigSource({name: "tools/sdk"}))
    assert caught.value.fix == "write the whole path in AC2_SDK_PATH"


def test_without_the_variable_the_default_applies() -> None:
    assert sdk_path(MappingConfigSource({})) == default_sdk_path()
    assert sdk_path(MappingConfigSource({"AC2_SDK_PATH": "  "})) == default_sdk_path()


def test_a_service_is_not_refused_for_the_variable_in_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AC2_SDK_PATH", str(tmp_path))
    assert load_service_config(tmp_path / ".env") is not None


def test_the_variable_in_an_env_file_is_refused_because_it_belongs_to_the_machine(
    tmp_path: Path,
) -> None:
    env = tmp_path / ".env"
    env.write_text(f"AC2_SDK_PATH={tmp_path}\n", encoding="utf-8")
    with pytest.raises(ConfigurationError, match="a setting of the machine") as caught:
        load_service_config(env)
    assert "process environment" in (caught.value.expected or "")


# ------------------------------------------------------------------------ a local disk


def fixed(root: str) -> int:
    return 3


def remote(root: str) -> int:
    return DRIVE_REMOTE


def check(folder: str, **changes: object) -> str | None:
    arguments: dict[str, object] = {
        "windows": True,
        "environment": WINDOWS_ENVIRONMENT,
        "drive_type": fixed,
        **changes,
    }
    return local_disk_problem(Path(folder), **arguments)  # type: ignore[arg-type]


def test_a_folder_on_a_fixed_local_drive_is_accepted() -> None:
    assert check(r"C:\Users\jane.doe\AppData\Local\agentcraft2-sdk") is None
    assert check(r"D:\ac2-sdk") is None


def test_a_unc_path_is_a_network_path() -> None:
    assert "network path" in (check(r"\\fileserver\home\jane\sdk") or "")


def test_a_mapped_network_drive_is_refused() -> None:
    reason = check(r"H:\sdk", drive_type=remote)
    assert reason is not None
    assert "network drive (H:)" in reason


def test_a_folder_inside_onedrive_is_refused() -> None:
    reason = check(r"C:\Users\jane.doe\OneDrive - Bank\sdk")
    assert reason is not None
    assert "OneDrive" in reason


def test_a_folder_in_the_roaming_profile_is_refused() -> None:
    reason = check(r"C:\Users\jane.doe\AppData\Roaming\agentcraft2")
    assert reason is not None
    assert "roaming profile" in reason


def test_other_systems_are_not_asked() -> None:
    assert check("/home/jane/sdk", windows=False) is None


def test_free_space_is_read_from_the_nearest_folder_that_exists(tmp_path: Path) -> None:
    assert free_bytes(tmp_path / "not" / "yet" / "made") > 0
