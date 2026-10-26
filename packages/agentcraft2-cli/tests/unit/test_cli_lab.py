"""Agent Lab as a service: configured from the workspace, started with ``ac2 services``."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

import pytest

from agentcraft2_cli.__main__ import main
from agentcraft2_cli.errors import CliError
from agentcraft2_cli.services import lab
from agentcraft2_cli.services.control import write_status
from agentcraft2_cli.services.manager import ServiceTools, start
from agentcraft2_cli.services.registry import LAB, SERVICES, Paths, paths_of
from agentcraft2_cli.testing import toolbox_for_tests
from agentcraft2_cli.workspace import LibrarySource


class Runner:
    """Stands in for the command that writes Agent Lab's configuration."""

    def __init__(self, code: int = 0, output: str = "") -> None:
        self.code = code
        self.output = output
        self.ran: list[tuple[list[str], Path]] = []

    def __call__(self, command: Sequence[str], folder: Path) -> tuple[int, str]:
        self.ran.append((list(command), folder))
        return self.code, self.output


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    tools = toolbox_for_tests(pins={})
    assert main(["init", "demo", "--dir", str(tmp_path), "--owner", "team"], tools) == 0
    root = tmp_path / "demo"
    assert main(["new", "agent", "helper", "--workspace", str(root)], tools) == 0
    return root


@pytest.fixture
def paths(tmp_path: Path, workspace: Path) -> Paths:
    return paths_of(workspace / ".agentcraft2", tmp_path / "sdk", "lab")


def arguments_of(runner: Runner) -> list[str]:
    command, _ = runner.ran[0]
    return command[command.index("agentcraft2_ui") + 1 :]


def test_lab_is_a_service_that_starts_after_phoenix_on_its_own_port() -> None:
    assert list(SERVICES) == ["phoenix", "lab"]
    assert LAB.port == 8765
    assert LAB.url == "http://127.0.0.1:8765"
    assert LAB.health_path == "/healthz"


def test_lab_is_installed_with_its_chat_libraries_and_the_sdk_it_needs() -> None:
    assert LAB.requirements is not None
    from_a_checkout = LAB.requirements(LibrarySource(kind="path", path="/sdk"))
    assert from_a_checkout.count("--editable") == 2
    assert any(
        item.replace("\\", "/").endswith("packages/agentcraft2-core") for item in from_a_checkout
    )
    assert any(
        item.replace("\\", "/").endswith("packages/agentcraft2-ui[chat]")
        for item in from_a_checkout
    )
    assert LAB.requirements(LibrarySource(kind="index", version=">=0.1,<0.2")) == [
        "agentcraft2-ui[chat]>=0.1,<0.2"
    ]


def test_lab_is_run_from_its_own_packages_with_its_configuration_in_the_workspace(
    paths: Paths,
) -> None:
    command = LAB.command(paths)
    assert command[command.index("-m") + 1 :] == [
        "agentcraft2_ui",
        "serve",
        "--config",
        str(paths.data / "config.json"),
    ]
    assert "-I" in command  # nothing of the machine's own Python is loaded


# ------------------------------------------------------------------------------- prepare


def test_the_configuration_is_written_from_the_agents_and_their_addresses(
    workspace: Path,
    paths: Paths,
) -> None:
    runner = Runner()
    assert lab.prepare(workspace, paths, runner) == []
    arguments = arguments_of(runner)
    assert arguments[0] == "init"
    assert arguments[arguments.index("--config") + 1] == str(paths.data / "config.json")
    assert arguments[arguments.index("--env-file") + 1].endswith("helper\\.env") or arguments[
        arguments.index("--env-file") + 1
    ].endswith("helper/.env")
    assert arguments[arguments.index("--agent") + 1] == "helper"
    assert arguments[arguments.index("--artifacts-agent") + 1] == "helper"
    assert arguments[arguments.index("--allow-url") + 1] == "http://127.0.0.1:8000"
    assert arguments[arguments.index("--port") + 1] == "8765"
    assert "--overwrite" not in arguments
    assert runner.ran[0][1] == paths.base


def test_a_configuration_that_exists_is_refreshed_and_keeps_the_conversations(
    workspace: Path,
    paths: Paths,
) -> None:
    paths.data.mkdir(parents=True)
    (paths.data / "config.json").write_text("{}", encoding="utf-8")
    runner = Runner()
    lab.prepare(workspace, paths, runner)
    assert "--overwrite" in arguments_of(runner)


def test_a_workspace_without_an_agent_says_how_to_add_one(tmp_path: Path, paths: Paths) -> None:
    tools = toolbox_for_tests(pins={})
    assert main(["init", "empty", "--dir", str(tmp_path), "--owner", "t"], tools) == 0
    with pytest.raises(CliError, match="no agent to chat with") as caught:
        lab.prepare(tmp_path / "empty", paths, Runner())
    assert "new agent" in (caught.value.fix or "")


def test_an_agent_without_an_address_is_named_with_the_line_to_add(
    workspace: Path,
    paths: Paths,
) -> None:
    registry = workspace / "registry" / "agents.yaml"
    text = registry.read_text(encoding="utf-8")
    registry.write_text(
        "\n".join(line for line in text.splitlines() if "url:" not in line) + "\n",
        encoding="utf-8",
    )
    runner = Runner()
    with pytest.raises(CliError, match="no agent of this workspace has an address") as caught:
        lab.prepare(workspace, paths, runner)
    assert "url: http://127.0.0.1:8000" in (caught.value.fix or "")
    assert runner.ran == []


def test_an_agent_that_has_no_address_is_left_out_and_the_others_are_not(
    workspace: Path,
    paths: Paths,
) -> None:
    tools = toolbox_for_tests(pins={})
    assert (
        main(["new", "agent", "second", "--port", "8001", "--workspace", str(workspace)], tools)
        == 0
    )
    registry = workspace / "registry" / "agents.yaml"
    text = registry.read_text(encoding="utf-8").replace("  url: http://127.0.0.1:8001\n", "")
    registry.write_text(text, encoding="utf-8")
    runner = Runner()
    notes = lab.prepare(workspace, paths, runner)
    assert notes == ["second has no address in registry/agents.yaml, so Agent Lab leaves it out"]
    assert arguments_of(runner).count("--agent") == 1


def test_a_failure_to_configure_is_reported_with_its_last_line(
    workspace: Path,
    paths: Paths,
) -> None:
    runner = Runner(2, "UI error: something small\nUI error: the agents differ (ValueError)")
    with pytest.raises(CliError, match="could not be configured") as caught:
        lab.prepare(workspace, paths, runner)
    assert "the agents differ" in (caught.value.actual or "")
    assert "same developer" in (caught.value.fix or "")


def test_what_to_do_next_names_the_code_and_each_agent_to_start(
    workspace: Path,
    paths: Paths,
) -> None:
    lines = lab.after_start(workspace, paths)
    assert lines[0] == f"Sign in with the code in: {paths.data / 'access-code'}"
    assert any(line.endswith("run helper --port 8000") for line in lines)


# ------------------------------------------------------------------------- the manager


def test_a_service_is_prepared_before_it_starts_and_told_after(
    tmp_path: Path, workspace: Path
) -> None:
    events: list[str] = []

    def prepare(root: Path, paths: Paths) -> list[str]:
        events.append("prepare")
        return ["a note"]

    spec = replace(LAB, prepare=prepare, after_start=lambda root, paths: ["a hint"])
    sdk = tmp_path / "sdk"
    from agentcraft2_cli.services import bootstrap, packages
    from agentcraft2_cli.services.registry import INSTALLED, installed_dir

    folder = installed_dir(sdk, "lab")
    site = packages.site_packages(folder)
    site.mkdir(parents=True)
    (site / bootstrap.MANIFEST).write_text(
        json.dumps(bootstrap.runtime_identity()), encoding="utf-8"
    )
    (folder / INSTALLED).write_text(json.dumps({"sdk_version": "1.0"}), encoding="utf-8")

    def launch(run: Path) -> None:
        events.append("launch")
        write_status(run, "running", port=8765)

    tools = ServiceTools(
        launch=launch,
        port_free=lambda port: True,
        is_ready=lambda port, path: True,
        sleep=lambda seconds: None,
        disk_problem=lambda folder: None,
        version="1.0",
    )
    said: list[str] = []
    start(spec, workspace / ".agentcraft2", sdk, tools, said.append, workspace=workspace)
    assert events == ["prepare", "launch"]
    assert said[0] == "a note"
    assert said[-2:] == ["lab is ready: http://127.0.0.1:8765", "a hint"]
