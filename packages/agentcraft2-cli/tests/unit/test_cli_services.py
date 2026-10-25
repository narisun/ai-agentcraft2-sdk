"""``ac2 services``: the package folder, the manager, the supervisor and the command."""

from __future__ import annotations

import socket
import subprocess
import sys
import textwrap
import time
from collections.abc import Callable, Sequence
from contextlib import closing
from pathlib import Path

import pytest

from agentcraft2_cli.__main__ import main
from agentcraft2_cli.errors import CliError
from agentcraft2_cli.services import bootstrap, packages
from agentcraft2_cli.services.control import (
    active,
    hold_lock,
    read_status,
    request_stop,
    stop_requested,
    write_status,
)
from agentcraft2_cli.services.manager import (
    ServiceTools,
    check_path_length,
    launch_supervisor,
    start,
    status,
    stop,
)
from agentcraft2_cli.services.registry import PHOENIX, ServiceSpec, paths_of
from agentcraft2_cli.testing import toolbox_for_tests
from agentcraft2_cli.workspace import LibrarySource

CHECKOUT = LibrarySource(kind="path", path="/work/sdk")
INDEX = LibrarySource(kind="index", version=">=0.1,<0.2")


def say_to(lines: list[str]) -> Callable[[str], None]:
    return lines.append


@pytest.fixture(autouse=True)
def no_home(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AC2_HOME", raising=False)


# ------------------------------------------------------------------ the package folder


def test_the_packages_go_in_a_short_folder_below_the_service(tmp_path: Path) -> None:
    # Short, because Windows refuses long paths unless an administrator allows them.
    assert packages.site_packages(tmp_path / "phoenix") == tmp_path / "phoenix" / "lib"


def test_the_command_runs_the_module_isolated_from_the_other_packages_of_this_python(
    tmp_path: Path,
) -> None:
    command = packages.command(tmp_path, "some.module", "--port", "1")
    assert command[:5] == [sys.executable, "-I", "-S", "-u", "-X"]
    assert command[5] == "utf8"
    assert command[6] == str(packages.BOOTSTRAP)
    assert command[7:] == [
        "--packages",
        str(packages.site_packages(tmp_path)),
        "-m",
        "some.module",
        "--port",
        "1",
    ]


def test_packages_that_are_missing_have_no_record_or_are_for_another_python_are_not_usable(
    tmp_path: Path,
) -> None:
    folder = tmp_path / "site-packages"
    assert "do not exist" in (bootstrap.problem_with(folder) or "") or "does not exist" in (
        bootstrap.problem_with(folder) or ""
    )
    folder.mkdir()
    assert "no record" in (bootstrap.problem_with(folder) or "")
    (folder / bootstrap.MANIFEST).write_text('{"implementation": "other"}', encoding="utf-8")
    assert "different Python" in (bootstrap.problem_with(folder) or "")
    (folder / bootstrap.MANIFEST).write_text("not json", encoding="utf-8")
    assert "unreadable" in (bootstrap.problem_with(folder) or "")
    (folder / bootstrap.MANIFEST).write_text(
        __import__("json").dumps(bootstrap.runtime_identity()), encoding="utf-8"
    )
    assert bootstrap.problem_with(folder) is None


def test_the_bootstrap_refuses_to_run_without_isolation() -> None:
    assert bootstrap.main(["--packages", "x", "-m", "y"]) == 2


def test_a_module_runs_from_the_package_folder_and_sees_nothing_else(tmp_path: Path) -> None:
    folder = packages.site_packages(tmp_path)
    folder.mkdir(parents=True)
    (folder / bootstrap.MANIFEST).write_text(
        __import__("json").dumps(bootstrap.runtime_identity()), encoding="utf-8"
    )
    (folder / "viewer_stub.py").write_text(
        textwrap.dedent(
            """
            import sys
            try:
                import pytest
            except ImportError:
                print("isolated", *sys.argv[1:])
            else:
                print("not isolated")
            """
        ),
        encoding="utf-8",
    )
    ran = subprocess.run(  # noqa: S603 - the command of a service, built by the package folder
        packages.command(tmp_path, "viewer_stub", "a", "b"),
        capture_output=True,
        text=True,
        check=False,
    )
    assert ran.returncode == 0, ran.stderr
    assert ran.stdout.strip() == "isolated a b"


def test_a_module_does_not_run_from_packages_that_were_not_installed(tmp_path: Path) -> None:
    ran = subprocess.run(  # noqa: S603 - the command of a service, built by the package folder
        packages.command(tmp_path, "viewer_stub"), capture_output=True, text=True, check=False
    )
    assert ran.returncode == 3
    assert "not installed" in ran.stderr


def test_an_install_builds_aside_checks_and_then_replaces_the_old_packages(
    tmp_path: Path,
) -> None:
    base = tmp_path / "phoenix"
    old = packages.site_packages(base)
    old.mkdir(parents=True)
    (old / "old.txt").write_text("old", encoding="utf-8")
    asked: list[Sequence[str]] = []

    def pip(arguments: Sequence[str], folder: Path) -> int:
        asked.append(arguments)
        target = Path(arguments[arguments.index("--target") + 1])
        target.mkdir(parents=True)
        (target / "new.txt").write_text("new", encoding="utf-8")
        assert folder == base
        return 0

    packages.install(base, ["--editable", "x[viewer]"], pip=pip)
    assert asked[0][0] == "install"
    assert "--no-user" in asked[0]
    assert asked[0][-2:] == ["--editable", "x[viewer]"]
    assert (old / "new.txt").is_file()
    assert not (old / "old.txt").exists()
    assert bootstrap.problem_with(old) is None
    assert [item.name for item in old.parent.iterdir()] == ["lib"]


def test_a_failed_install_keeps_the_old_packages_and_says_what_to_do(tmp_path: Path) -> None:
    base = tmp_path / "phoenix"
    old = packages.site_packages(base)
    old.mkdir(parents=True)
    (old / "old.txt").write_text("old", encoding="utf-8")
    with pytest.raises(CliError, match="exit code 1") as caught:
        packages.install(base, ["x"], pip=lambda *_: 1)
    assert "your index" in (caught.value.fix or "")
    assert (old / "old.txt").is_file()
    assert [item.name for item in old.parent.iterdir()] == ["lib"]


# --------------------------------------------------------------------------- the registry


def test_phoenix_installs_from_the_checkout_or_from_the_index() -> None:
    assert PHOENIX.requirements is not None
    assert PHOENIX.requirements(CHECKOUT) == [
        "--editable",
        f"{Path('/work/sdk') / 'packages' / 'agentcraft2-phoenix'}[viewer]",
    ]
    assert PHOENIX.requirements(INDEX) == ["agentcraft2-phoenix[viewer]>=0.1,<0.2"]


def test_phoenix_runs_its_module_on_its_port_with_its_data_folder(tmp_path: Path) -> None:
    paths = paths_of(tmp_path, "phoenix")
    command = PHOENIX.command(paths)
    assert command[-5:] == ["agentcraft2_phoenix", "--port", "6006", "--data-dir", str(paths.data)]
    assert PHOENIX.url == "http://127.0.0.1:6006"
    assert PHOENIX.health_path == "/healthz"


def test_a_service_keeps_everything_in_the_state_folder_and_its_log_beside_the_agents(
    tmp_path: Path,
) -> None:
    paths = paths_of(tmp_path, "phoenix")
    assert paths.base == tmp_path / "services" / "phoenix"
    assert paths.run == paths.base / "run"
    assert paths.data == paths.base / "data"
    assert paths.log == tmp_path / "logs" / "phoenix.log"


# ----------------------------------------------------------------------- the run folder


def test_a_held_lock_shows_a_supervisor_that_is_alive(tmp_path: Path) -> None:
    assert not active(tmp_path)
    with hold_lock(tmp_path):
        assert active(tmp_path)
        with pytest.raises(RuntimeError, match="already runs"), hold_lock(tmp_path):
            pass
    assert not active(tmp_path)


def test_the_state_is_published_whole_and_read_back(tmp_path: Path) -> None:
    assert read_status(tmp_path) == {}
    write_status(tmp_path, "running", port=6006)
    published = read_status(tmp_path)
    assert published["state"] == "running"
    assert published["port"] == 6006
    (tmp_path / "status.json").write_text("{broken", encoding="utf-8")
    assert read_status(tmp_path) == {}


def test_a_stop_is_a_file_in_the_run_folder(tmp_path: Path) -> None:
    assert not stop_requested(tmp_path)
    request_stop(tmp_path)
    assert stop_requested(tmp_path)


# ------------------------------------------------------------------------------ status


def fake_tools(**changes: object) -> ServiceTools:
    defaults: dict[str, object] = {
        "launch": lambda run: None,
        "pip": lambda arguments, folder: 0,
        "port_free": lambda port: True,
        "is_ready": lambda port, path: True,
        "sleep": lambda seconds: None,
        # The folders of a test run are deeper than a workspace; the check has tests of its own.
        "check_paths": lambda spec, paths: None,
    }
    return ServiceTools(**{**defaults, **changes})  # type: ignore[arg-type]


def installed(state: Path) -> None:
    """Make the packages of Phoenix look installed."""
    folder = packages.site_packages(paths_of(state, "phoenix").base)
    folder.mkdir(parents=True)
    (folder / bootstrap.MANIFEST).write_text(
        __import__("json").dumps(bootstrap.runtime_identity()), encoding="utf-8"
    )


def test_a_service_that_was_never_installed_says_how_to_install_it(tmp_path: Path) -> None:
    current = status(PHOENIX, tmp_path, fake_tools())
    assert current.state == "not installed"
    assert "ac2 services start" in current.detail


def test_installed_packages_that_are_for_another_python_are_not_installed(tmp_path: Path) -> None:
    installed(tmp_path)
    manifest = packages.site_packages(paths_of(tmp_path, "phoenix").base) / bootstrap.MANIFEST
    manifest.write_text('{"implementation": "other"}', encoding="utf-8")
    current = status(PHOENIX, tmp_path, fake_tools())
    assert current.state == "not installed"
    assert "different Python" in current.detail


def test_installed_and_not_running_is_stopped(tmp_path: Path) -> None:
    installed(tmp_path)
    assert status(PHOENIX, tmp_path, fake_tools()).state == "stopped"


def test_a_running_supervisor_with_an_answering_service_is_running(tmp_path: Path) -> None:
    installed(tmp_path)
    run = paths_of(tmp_path, "phoenix").run
    run.mkdir(parents=True)
    write_status(run, "running", port=6006)
    with hold_lock(run):
        current = status(PHOENIX, tmp_path, fake_tools())
    assert (current.state, current.address) == ("running", "http://127.0.0.1:6006")


def test_a_running_service_that_no_longer_answers_is_failed(tmp_path: Path) -> None:
    installed(tmp_path)
    run = paths_of(tmp_path, "phoenix").run
    run.mkdir(parents=True)
    write_status(run, "running", port=6006)
    with hold_lock(run):
        current = status(PHOENIX, tmp_path, fake_tools(is_ready=lambda port, path: False))
    assert current.state == "failed"
    assert "nothing answers on port 6006" in current.detail


def test_a_supervisor_that_died_without_saying_how_is_failed_and_points_at_the_log(
    tmp_path: Path,
) -> None:
    installed(tmp_path)
    run = paths_of(tmp_path, "phoenix").run
    run.mkdir(parents=True)
    write_status(run, "running", port=6006)
    current = status(PHOENIX, tmp_path, fake_tools())
    assert current.state == "failed"
    assert str(paths_of(tmp_path, "phoenix").log) in current.detail


def test_a_recorded_failure_is_shown_with_its_reason(tmp_path: Path) -> None:
    installed(tmp_path)
    run = paths_of(tmp_path, "phoenix").run
    run.mkdir(parents=True)
    write_status(run, "failed", detail="the service ended with exit code 1 before it was ready")
    current = status(PHOENIX, tmp_path, fake_tools())
    assert current.state == "failed"
    assert "exit code 1 before it was ready" in current.detail


# ------------------------------------------------------------------------------- start


def writes_running(state: Path) -> Callable[[Path], None]:
    def launch(run: Path) -> None:
        write_status(run, "running", port=6006)

    return launch


def test_starting_installs_what_is_missing_then_starts_and_waits(tmp_path: Path) -> None:
    lines: list[str] = []
    asked: list[Sequence[str]] = []

    def pip(arguments: Sequence[str], folder: Path) -> int:
        asked.append(arguments)
        target = Path(arguments[arguments.index("--target") + 1])
        target.mkdir(parents=True)
        return 0

    tools = fake_tools(pip=pip, launch=writes_running(tmp_path))
    start(PHOENIX, tmp_path, CHECKOUT, tools, say_to(lines))
    assert asked[0][-2] == "--editable"
    assert any(line.startswith("Installing the packages of phoenix") for line in lines)
    assert lines[-1] == "phoenix is ready: http://127.0.0.1:6006"
    spec = (paths_of(tmp_path, "phoenix").run / "spec.json").read_text(encoding="utf-8")
    assert '"port": 6006' in spec
    assert paths_of(tmp_path, "phoenix").data.is_dir()


def test_starting_what_is_installed_does_not_install_again(tmp_path: Path) -> None:
    installed(tmp_path)
    lines: list[str] = []

    def pip(arguments: Sequence[str], folder: Path) -> int:
        raise AssertionError("pip must not run")

    start(
        PHOENIX,
        tmp_path,
        CHECKOUT,
        fake_tools(pip=pip, launch=writes_running(tmp_path)),
        say_to(lines),
    )
    assert not any("Installing" in line for line in lines)


def test_starting_a_service_that_is_running_does_nothing(tmp_path: Path) -> None:
    installed(tmp_path)
    run = paths_of(tmp_path, "phoenix").run
    run.mkdir(parents=True)
    write_status(run, "running", port=6006)
    lines: list[str] = []

    def launch(_: Path) -> None:
        raise AssertionError("must not start a second one")

    with hold_lock(run):
        start(PHOENIX, tmp_path, CHECKOUT, fake_tools(launch=launch), say_to(lines))
    assert lines == ["phoenix is already running: http://127.0.0.1:6006"]


def test_a_port_that_is_in_use_stops_the_start_before_anything_is_installed(
    tmp_path: Path,
) -> None:
    def pip(arguments: Sequence[str], folder: Path) -> int:
        raise AssertionError("nothing is installed when the port is taken")

    with pytest.raises(CliError, match="port 6006 is already in use") as caught:
        start(PHOENIX, tmp_path, CHECKOUT, fake_tools(port_free=lambda port: False, pip=pip))
    assert caught.value.expected == "port 6006 free on 127.0.0.1"
    assert "ac2 services start phoenix" in (caught.value.fix or "")


def test_a_failed_install_stops_the_start_with_pips_exit_code(tmp_path: Path) -> None:
    with pytest.raises(CliError, match="exit code 1"):
        start(PHOENIX, tmp_path, CHECKOUT, fake_tools(pip=lambda arguments, folder: 1))
    assert not (paths_of(tmp_path, "phoenix").run / "spec.json").exists()


def test_a_service_that_fails_on_start_shows_why_and_the_end_of_its_log(tmp_path: Path) -> None:
    installed(tmp_path)
    paths = paths_of(tmp_path, "phoenix")
    paths.log.parent.mkdir(parents=True)
    paths.log.write_text("loading\nRuntimeError: arize-phoenix 99 is installed\n", encoding="utf-8")

    def launch(run: Path) -> None:
        write_status(run, "failed", detail="the service ended with exit code 1 before it was ready")

    with pytest.raises(
        CliError, match="phoenix did not start: the service ended with exit code 1"
    ) as caught:
        start(PHOENIX, tmp_path, CHECKOUT, fake_tools(launch=launch))
    assert "RuntimeError: arize-phoenix 99 is installed" in (caught.value.fix or "")
    assert str(paths.log) in (caught.value.fix or "")


def test_a_supervisor_that_never_starts_is_reported(tmp_path: Path) -> None:
    installed(tmp_path)
    ticks = iter(range(0, 1000, 5))
    with pytest.raises(CliError, match="supervisor of phoenix did not start"):
        start(PHOENIX, tmp_path, CHECKOUT, fake_tools(clock=lambda: float(next(ticks))))


# ------------------------------------------------------------------------------- stop


def test_stopping_what_is_not_running_says_so(tmp_path: Path) -> None:
    lines: list[str] = []
    stop(PHOENIX, tmp_path, fake_tools(), say_to(lines))
    assert lines == ["phoenix is not running"]


# ---------------------------------------------------------- real processes (slow): a stub


STUB = textwrap.dedent(
    """
    import signal, sys
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200 if self.path == "/healthz" else 404)
            self.end_headers()
            self.wfile.write(b"ok")
        def log_message(self, *args):
            pass

    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, signal.default_int_handler)
    server = HTTPServer(("127.0.0.1", int(sys.argv[1])), Handler)
    print("stub listening", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("stub stopping", flush=True)
    """
)


def free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def stub_spec(port: int) -> ServiceSpec:
    return ServiceSpec(
        name="stub",
        title="A stub that answers the health path",
        port=port,
        health_path="/healthz",
        command=lambda paths: [sys.executable, "-u", "-c", STUB, str(port)],
    )


@pytest.mark.slow
def test_a_service_starts_in_the_background_answers_and_stops_gracefully(tmp_path: Path) -> None:
    port = free_port()
    spec = stub_spec(port)
    lines: list[str] = []
    tools = ServiceTools(launch=launch_supervisor)
    try:
        start(spec, tmp_path, CHECKOUT, tools, say_to(lines))
        assert lines[-1] == f"stub is ready: http://127.0.0.1:{port}"
        current = status(spec, tmp_path, tools)
        assert (current.state, current.address) == ("running", f"http://127.0.0.1:{port}")
        again: list[str] = []
        start(spec, tmp_path, CHECKOUT, tools, say_to(again))
        assert again == [f"stub is already running: http://127.0.0.1:{port}"]
        with pytest.raises(CliError, match="already in use"):
            start(stub_spec(port), tmp_path / "other", CHECKOUT, tools)
        stopped: list[str] = []
        stop(spec, tmp_path, tools, say_to(stopped))
        assert stopped == ["Stopping stub...", "stub stopped"]
        assert status(spec, tmp_path, tools).state == "stopped"
        assert read_status(paths_of(tmp_path, "stub").run)["state"] == "stopped"
        log = paths_of(tmp_path, "stub").log.read_text(encoding="utf-8")
        assert "stub listening" in log
        assert "stub stopping" in log
    finally:
        _stop_quietly(spec, tmp_path)


@pytest.mark.slow
def test_a_service_that_dies_on_start_is_reported_with_its_output(tmp_path: Path) -> None:
    spec = ServiceSpec(
        name="dies",
        title="A service that cannot start",
        port=free_port(),
        health_path="/healthz",
        command=lambda paths: [
            sys.executable,
            "-c",
            "import sys; print('RuntimeError: no packages', file=sys.stderr); sys.exit(3)",
        ],
    )
    with pytest.raises(
        CliError, match="dies did not start: the service ended with exit code 3"
    ) as caught:
        start(spec, tmp_path, CHECKOUT, ServiceTools(launch=launch_supervisor))
    assert "RuntimeError: no packages" in (caught.value.fix or "")
    assert status(spec, tmp_path, ServiceTools()).state == "failed"


def _stop_quietly(spec: ServiceSpec, state: Path) -> None:
    paths = paths_of(state, spec.name)
    if active(paths.run):
        request_stop(paths.run)
        deadline = time.monotonic() + 30
        while active(paths.run) and time.monotonic() < deadline:
            time.sleep(0.2)


# -------------------------------------------------------------------------- the command


def make_workspace(tmp_path: Path) -> Path:
    toolbox = toolbox_for_tests()
    assert main(["init", "demo", "--dir", str(tmp_path), "--owner", "demo-team"], toolbox) == 0
    return tmp_path / "demo"


def test_services_lists_each_service_with_its_state_and_how_to_start(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = make_workspace(tmp_path)
    tools = toolbox_for_tests(services=fake_tools())
    assert main(["services", "--workspace", str(root)], tools) == 0
    out = capsys.readouterr().out
    assert f"Services for {root}" in out
    assert f"state: {root / '.agentcraft2'}" in out
    assert "phoenix" in out
    assert "not installed" in out
    assert "Start them with: ac2 services start" in out


def test_services_list_is_the_same_as_services(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = make_workspace(tmp_path)
    capsys.readouterr()
    tools = toolbox_for_tests(services=fake_tools())
    main(["services", "--workspace", str(root)], tools)
    plain = capsys.readouterr().out
    main(["services", "list", "--workspace", str(root)], tools)
    assert capsys.readouterr().out == plain


def test_services_start_and_stop_name_the_services_that_exist(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = make_workspace(tmp_path)
    tools = toolbox_for_tests(services=fake_tools())
    assert main(["services", "start", "nope", "--workspace", str(root)], tools) == 1
    err = capsys.readouterr().err
    assert "there is no service called 'nope'" in err
    assert "one of: phoenix" in err


def test_services_start_installs_starts_and_reports(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = make_workspace(tmp_path)
    state = root / ".agentcraft2"
    installed(state)

    tools = toolbox_for_tests(services=fake_tools(launch=writes_running(state)))
    assert main(["services", "start", "--workspace", str(root)], tools) == 0
    assert "phoenix is ready: http://127.0.0.1:6006" in capsys.readouterr().out


def test_services_outside_a_workspace_say_so(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["services", "--workspace", str(tmp_path)], toolbox_for_tests()) == 1
    assert "agentcraft2.toml" in capsys.readouterr().err


def test_the_help_teaches_the_commands(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["services", "--help"], toolbox_for_tests()) == 0
    out = capsys.readouterr().out
    for text in ("ac2 services start", "ac2 services stop", "ac2 logs phoenix", ".agentcraft2"):
        assert text in out


def test_logs_reads_the_log_of_a_service(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = make_workspace(tmp_path)
    log = paths_of(root / ".agentcraft2", "phoenix").log
    log.parent.mkdir(parents=True)
    log.write_text("Phoenix on http://127.0.0.1:6006\nlistening\n", encoding="utf-8")
    capsys.readouterr()
    tools = toolbox_for_tests()
    assert main(["logs", "phoenix", "--workspace", str(root)], tools) == 0
    assert "Phoenix on http://127.0.0.1:6006" in capsys.readouterr().out
    assert main(["logs", "--workspace", str(root)], tools) == 0
    assert "listening" in capsys.readouterr().out


def test_logs_of_a_service_that_never_ran_say_so(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = make_workspace(tmp_path)
    capsys.readouterr()
    assert main(["logs", "phoenix", "--workspace", str(root)], toolbox_for_tests()) == 1
    assert "there are no logs yet" in capsys.readouterr().err


# ------------------------------------------------------------------- Windows path length


def test_a_folder_so_deep_that_windows_would_not_open_the_files_is_refused_before_installing(
    tmp_path: Path,
) -> None:
    deep = paths_of(tmp_path / ("d" * 60) / ("e" * 60) / ("f" * 60), "phoenix")
    with pytest.raises(CliError, match="too long for Windows") as caught:
        check_path_length(PHOENIX, deep, windows=True)
    assert caught.value.expected == "paths of at most 259 characters"
    assert "characters for the longest file below" in (caught.value.actual or "")
    assert "shorter folder" in (caught.value.fix or "")
    assert "LongPathsEnabled" in (caught.value.fix or "")


def test_a_shallow_folder_and_other_systems_are_not_refused(tmp_path: Path) -> None:
    check_path_length(PHOENIX, paths_of(Path("C:/work/demo/.agentcraft2"), "phoenix"), windows=True)
    deep = paths_of(tmp_path / ("d" * 200), "phoenix")
    check_path_length(PHOENIX, deep, windows=False)


def test_the_longest_file_of_phoenix_was_measured_with_room_to_spare() -> None:
    assert PHOENIX.longest_path >= 145
