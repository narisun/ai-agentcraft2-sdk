"""The command line of the viewer: its settings are applied before Phoenix starts."""

import os
from pathlib import Path

import pytest

from agentcraft2_phoenix import __main__ as entry


def test_main_applies_the_local_environment_then_runs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    seen: dict[str, object] = {}
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-private")
    monkeypatch.setenv("PHOENIX_HOST", "0.0.0.0")  # noqa: S104 - an unsafe setting, to be replaced

    def run(port: int) -> None:
        seen["port"] = port
        seen["host"] = os.environ["PHOENIX_HOST"]
        seen["key"] = os.environ.get("ANTHROPIC_API_KEY")

    monkeypatch.setattr(entry, "run", run)
    data = tmp_path / "data"
    assert entry.main(["--port", "6111", "--data-dir", str(data)]) == 0
    assert seen == {"port": 6111, "host": "127.0.0.1", "key": None}
    assert data.is_dir()
    assert f"http://127.0.0.1:6111; traces in {data.resolve()}" in capsys.readouterr().out


def test_main_reports_a_refusal_of_phoenix_and_exits_with_1(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def refuse(_: int) -> None:
        raise RuntimeError("arize-phoenix 99 is installed")

    monkeypatch.setattr(entry, "run", refuse)
    assert entry.main(["--data-dir", str(tmp_path)]) == 1
    assert "error: arize-phoenix 99 is installed" in capsys.readouterr().err


@pytest.mark.parametrize("port", ["0", "70000"])
def test_main_refuses_a_port_that_cannot_exist(tmp_path: Path, port: str) -> None:
    with pytest.raises(SystemExit) as caught:
        entry.main(["--port", port, "--data-dir", str(tmp_path)])
    assert caught.value.code == 2
