"""Build the SDK from this checkout and install it into the current Python environment.

Run with your selected Python: ``python tools/install_sdk.py``. The script uses only that
Python and its pip. It never creates an environment and never installs anything else
than the SDK (and, with ``--dev``, the tools used to work on it).

Steps, each reported as it starts and again when it is complete:

1. Check the environment, and stop at the first problem.
2. Find the SDK, and the tools of the SDK, that are already installed.
3. Build a wheel of each package. This comes before the uninstall, so that a build that
   fails does not leave the environment without an SDK.
4. Uninstall the installed SDK and its tools, and confirm that they are gone.
5. Install the new SDK. This starts only after step 4 has succeeded.
6. Install the SDK's tools, Phoenix now and Agent Lab next, into the SDK's own folder.
7. Verify the installation.

The tools go in ``AC2_SDK_PATH``: by default ``agentcraft2-sdk`` in ``%LOCALAPPDATA%`` on
Windows, which belongs to the machine and is not roamed, and ``~/.agentcraft2_sdk`` elsewhere. A
folder on a network drive, in OneDrive or in the roaming profile is refused. What a tool writes,
such as traces, goes to the workspace, not there.

Options: ``--dev`` also installs every optional dependency and the development tools;
``--editable`` installs the checkout itself instead of the built wheels, so edits to the
source take effect at once; ``--no-services`` leaves the tools out. Any other argument is
passed to pip, for example ``--no-index --find-links=/path/to/wheels``. pip prints only
problems, so the steps stay readable; ``--verbose`` shows everything pip prints.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import importlib.util
import json
import os
import shutil
import site
import subprocess
import sys
import sysconfig
import tomllib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"

PACKAGES = ("agentcraft2-core", "agentcraft2-aws", "agentcraft2-cli")
"""The distributions this script builds and installs, in installation order."""

SDK_DISTRIBUTIONS = (*PACKAGES, "agentcraft2-ui")
"""Every distribution of the SDK. Any of them found in the environment is removed first."""

MINIMUM_PYTHON = (3, 11)
STEPS = (
    "Checking the environment",
    "Looking for an installed SDK",
    "Building the SDK",
    "Uninstalling the installed SDK",
    "Installing the SDK",
    "Installing the SDK's tools",
    "Verifying the installation",
)

CORE_CONFIG = ROOT / "packages" / "agentcraft2-core" / "src" / "agentcraft2_core" / "config"
"""Two files there use only the standard library, so this script can load them by their paths."""

CONTROL = (
    ROOT / "packages" / "agentcraft2-cli" / "src" / "agentcraft2_cli" / "services" / "control.py"
)
"""Standard-library only too: it says whether a tool's supervisor is alive."""

SERVICE_NAMES = ("phoenix",)
"""The tools installed with the SDK."""
SERVICE_LONGEST_PATH = 150
SERVICE_MEGABYTES = 650
"""What the tools need; a test compares them with ``ServiceSpec.longest_path`` and ``megabytes``."""
WINDOWS_PATH_LIMIT = 259
INSTALLED_RECORD = "installed.json"


class SetupError(Exception):
    """A problem that stops the script, with what to do about it."""

    def __init__(self, message: str, fix: str = "") -> None:
        super().__init__(message)
        self.fix = fix
        self.step = ""


@dataclass(frozen=True)
class Installed:
    """One SDK distribution found in the environment."""

    name: str
    version: str
    editable: bool
    location: str

    def describe(self) -> str:
        """Return the line printed for it."""
        kind = "editable" if self.editable else "installed"
        return f"{self.name} {self.version} ({kind}, {self.location})"


def say(text: str = "") -> None:
    """Print one line."""
    sys.stdout.write(text + "\n")
    sys.stdout.flush()


def module_name(distribution: str) -> str:
    """Return the import name of a distribution."""
    return distribution.replace("-", "_")


def pip(arguments: Sequence[str], *, capture: bool = False) -> subprocess.CompletedProcess[str]:
    """Run pip with the Python that runs this script."""
    return subprocess.run(  # noqa: S603 - the current interpreter and explicit arguments
        [sys.executable, "-m", "pip", "--disable-pip-version-check", *arguments],
        cwd=ROOT,
        capture_output=capture,
        text=True,
        check=False,
    )


def quiet(verbose: bool) -> list[str]:
    """Return pip's option for printing only problems, unless everything was asked for.

    pip lists every requirement that is already satisfied, which buries the steps of this
    script and what failed.
    """
    return [] if verbose else ["-q"]


def project(package: str) -> dict[str, Any]:
    """Read the ``[project]`` table of one package of this checkout."""
    path = ROOT / "packages" / package / "pyproject.toml"
    if not path.is_file():
        raise SetupError(
            f"the package folder is missing: {path}",
            "run this script from a complete checkout of the SDK repository",
        )
    try:
        table: dict[str, Any] = tomllib.loads(path.read_text(encoding="utf-8")).get("project", {})
    except tomllib.TOMLDecodeError as problem:
        raise SetupError(f"{path} is not valid TOML ({problem})") from problem
    if "version" not in table:
        raise SetupError(f"{path} does not declare a version")
    return table


def extras_of(package: str) -> list[str]:
    """Return the names of the optional dependency groups a package declares."""
    return sorted(project(package).get("optional-dependencies", {}))


def development_tools() -> list[str]:
    """Return the requirements of the ``dev`` dependency group of the root pyproject.toml."""
    path = ROOT / "pyproject.toml"
    try:
        groups = tomllib.loads(path.read_text(encoding="utf-8")).get("dependency-groups", {})
    except (OSError, tomllib.TOMLDecodeError) as problem:
        raise SetupError(f"cannot read the development tools from {path} ({problem})") from problem
    tools = groups.get("dev")
    if not tools:
        raise SetupError(
            f"{path} has no 'dev' dependency group", "restore the group, or omit --dev"
        )
    return [str(tool) for tool in tools]


def site_folders() -> list[Path]:
    """Return the folders this Python installs packages into."""
    folders = {sysconfig.get_path("purelib"), sysconfig.get_path("platlib")}
    folders.update(site.getsitepackages())
    return sorted(Path(folder) for folder in folders if folder)


def leftovers() -> list[Path]:
    """Return what pip left behind when an uninstall of the SDK was interrupted.

    pip renames what it is about to remove by replacing the first letter with ``~``. When the
    uninstall stops half way (on Windows, a running ``ac2`` locks its own file) the renamed
    folder stays. pip then warns about it on every run and never removes it.
    """
    ours = {module_name(name)[1:] for name in SDK_DISTRIBUTIONS}
    found = []
    for folder in site_folders():
        if not folder.is_dir():
            continue
        for entry in sorted(folder.iterdir()):
            normal = entry.name[1:].lower().replace("-", "_")
            if entry.name.startswith("~") and any(normal.startswith(name) for name in ours):
                found.append(entry)
    return found


def removal_command(path: Path) -> str:
    """Return the command that deletes ``path`` on this system."""
    if os.name == "nt":
        return f'rmdir /s /q "{path}"' if path.is_dir() else f'del "{path}"'
    return f"rm -rf '{path}'"


def load_module(path: Path) -> Any:
    """Load one of the SDK's standard-library-only files by its path, before it is installed."""
    spec = importlib.util.spec_from_file_location(path.stem, path)
    if spec is None or spec.loader is None:
        raise SetupError(f"cannot load {path}", "run this script from a complete checkout")
    module = importlib.util.module_from_spec(spec)
    sys.modules[path.stem] = module
    spec.loader.exec_module(module)
    return module


def sdk_folder() -> Path:
    """Return the folder the SDK's tools go in: ``AC2_SDK_PATH``, or the default for this system."""
    chosen = os.environ.get("AC2_SDK_PATH", "").strip()
    if not chosen:
        found: Path = load_module(CORE_CONFIG / "sdk_location.py").default_sdk_path()
        return found
    folder = Path(chosen).expanduser()
    if not folder.is_absolute():
        raise SetupError(
            f"AC2_SDK_PATH is not an absolute path ({chosen!r})",
            "write the whole path in AC2_SDK_PATH, such as C:\\ac2-sdk or /opt/ac2-sdk",
        )
    return folder


def check_tools_folder(sdk: Path) -> list[str]:
    """Check the folder of the SDK's tools: on a local disk, short enough, with room."""
    disks = load_module(CORE_CONFIG / "local_disk.py")
    reason = disks.local_disk_problem(sdk)
    if reason is not None:
        raise SetupError(
            f"the folder for the SDK's tools is not on a local disk: {reason}",
            "set AC2_SDK_PATH to a folder on a local disk, such as C:\\ac2-sdk",
        )
    folder = sdk / "services" / SERVICE_NAMES[0] / "lib"
    needed = len(str(folder)) + 1 + SERVICE_LONGEST_PATH
    if sys.platform == "win32" and needed > WINDOWS_PATH_LIMIT:
        raise SetupError(
            f"the path to the SDK's tools is too long for Windows: {needed} characters needed, "
            f"{WINDOWS_PATH_LIMIT} allowed (below {sdk})",
            "set AC2_SDK_PATH to a shorter folder on a local disk, such as C:\\ac2-sdk",
        )
    room = disks.free_bytes(sdk) // 1024 // 1024
    if room < int(SERVICE_MEGABYTES * 1.5):
        raise SetupError(
            f"there is not enough room for the SDK's tools in {sdk}: {room} MB free, "
            f"about {int(SERVICE_MEGABYTES * 1.5)} MB needed",
            "free some space, or set AC2_SDK_PATH to a folder on a larger local disk",
        )
    refuse_while_running(sdk)
    return [f"Tools go in {sdk} (a local disk, {room} MB free), and none is running"]


def check_environment(arguments: argparse.Namespace, pip_arguments: Sequence[str]) -> list[str]:
    """Check everything that can be checked before anything is changed.

    Raises:
        SetupError: At the first problem found.

    Returns:
        The lines to print for what was checked.
    """
    lines = []
    if sys.version_info < MINIMUM_PYTHON:
        found = ".".join(map(str, sys.version_info[:3]))
        raise SetupError(
            f"Python {found} is too old: the SDK needs Python "
            f"{'.'.join(map(str, MINIMUM_PYTHON))} or newer",
            "run the script with a newer Python, for example: py -3.11 tools/install_sdk.py",
        )
    lines.append(f"Python {'.'.join(map(str, sys.version_info[:3]))} at {sys.executable}")
    found_pip = pip(["--version"], capture=True)
    if found_pip.returncode != 0:
        raise SetupError(
            f"pip does not run with this Python ({found_pip.stderr.strip() or 'no output'})",
            "ask your package administrators for a Python that includes pip",
        )
    lines.append(found_pip.stdout.strip().splitlines()[0])
    versions = {package: str(project(package)["version"]) for package in PACKAGES}
    if len(set(versions.values())) != 1:
        listed = ", ".join(f"{name} {version}" for name, version in versions.items())
        raise SetupError(
            f"the packages do not have one version ({listed})",
            "set the same version in each packages/*/pyproject.toml",
        )
    lines.append(f"SDK version {next(iter(versions.values()))} from {ROOT / 'packages'}")
    shadowed = [
        entry
        for entry in os.environ.get("PYTHONPATH", "").split(os.pathsep)
        if entry and any(Path(entry).resolve() == ROOT / "packages" / p / "src" for p in PACKAGES)
    ]
    if shadowed:
        raise SetupError(
            f"PYTHONPATH names the checkout's own source ({shadowed[0]}), which would load "
            "the SDK from the checkout instead of the installed copy",
            "unset PYTHONPATH, then run the script again",
        )
    if arguments.dev:
        development_tools()
        lines.append("Development tools are declared in pyproject.toml")
    isolated = "--no-build-isolation" not in pip_arguments
    if not arguments.editable and not isolated and importlib.util.find_spec("hatchling") is None:
        raise SetupError(
            "--no-build-isolation needs hatchling, and this Python does not have it",
            "install hatchling with pip, or leave out --no-build-isolation",
        )
    try:
        DIST.mkdir(exist_ok=True)
    except OSError as problem:
        raise SetupError(f"cannot create the build folder {DIST} ({problem})") from problem
    if not os.access(DIST, os.W_OK):
        raise SetupError(f"the build folder {DIST} is not writable")
    lines.append(f"Wheels will be built in {DIST}")
    for entry in leftovers():
        lines.append(
            f"warning: {entry} is left over from an interrupted uninstall. pip ignores it and "
            f"warns about it on every run. Delete it: {removal_command(entry)}"
        )
    if not arguments.no_services:
        lines.extend(check_tools_folder(sdk_folder()))
    return lines


def find_installed() -> list[Installed]:
    """Return the SDK distributions installed in this environment."""
    found = []
    for name in SDK_DISTRIBUTIONS:
        try:
            distribution = importlib.metadata.distribution(name)
        except importlib.metadata.PackageNotFoundError:
            continue
        editable = False
        direct = distribution.read_text("direct_url.json")
        if direct:
            editable = bool(json.loads(direct).get("dir_info", {}).get("editable"))
        found.append(
            Installed(name, distribution.version, editable, str(distribution.locate_file("")))
        )
    return found


def build_wheels(verbose: bool = False) -> list[Path]:
    """Build one wheel of each package into the build folder, replacing earlier ones."""
    for old in DIST.glob("agentcraft2_*.whl"):
        old.unlink()
    wheels = []
    for package in PACKAGES:
        say(f"      building {package}")
        built = pip(
            [
                "wheel",
                *quiet(verbose),
                "--no-deps",
                "--wheel-dir",
                str(DIST),
                str(ROOT / "packages" / package),
            ]
        )
        if built.returncode != 0:
            raise SetupError(
                f"pip could not build {package} (exit code {built.returncode}); "
                "its output is above",
                "fix the problem it names, then run the script again. Nothing was uninstalled",
            )
        produced = sorted(DIST.glob(f"{module_name(package)}-*.whl"))
        if len(produced) != 1:
            raise SetupError(
                f"expected one wheel of {package} in {DIST}, found {len(produced)}",
                f"delete the contents of {DIST}, then run the script again",
            )
        wheels.append(produced[0])
        say(f"      built {produced[0].name}")
    return wheels


def uninstall(installed: Sequence[Installed]) -> None:
    """Uninstall the SDK, and confirm that none of it is left."""
    names = [item.name for item in installed]
    removed = pip(["uninstall", "--yes", *names], capture=True)
    if removed.returncode != 0:
        raise SetupError(
            f"pip could not uninstall {', '.join(names)}: "
            f"{(removed.stderr or removed.stdout).strip() or 'no output'}",
            "close every program that runs the SDK (on Windows, a running ac2 command locks "
            "its own file), then run the script again. Nothing was installed",
        )
    check = (
        "import importlib.metadata as m, importlib.util as u\n"
        "left = []\n"
        f"for n in {list(SDK_DISTRIBUTIONS)!r}:\n"
        "    try:\n"
        "        m.distribution(n)\n"
        "        left.append(n)\n"
        "    except m.PackageNotFoundError:\n"
        "        pass\n"
        "    if u.find_spec(n.replace('-', '_')):\n"
        "        left.append(n.replace('-', '_'))\n"
        "print(' '.join(left))\n"
    )
    left = subprocess.run(  # noqa: S603 - the current interpreter and a fixed program
        [sys.executable, "-I", "-c", check], capture_output=True, text=True, check=False
    )
    if left.returncode != 0 or left.stdout.strip():
        raise SetupError(
            "the SDK is still importable after the uninstall "
            f"({left.stdout.strip() or left.stderr.strip()})",
            "remove the leftover files it names, then run the script again. Nothing was installed",
        )


def install(arguments: argparse.Namespace, wheels: Sequence[Path], extra: Sequence[str]) -> None:
    """Install the SDK, with the development tools when asked."""
    requirements: list[str] = []
    for index, package in enumerate(PACKAGES):
        names = extras_of(package) if arguments.dev else []
        extras = f"[{','.join(names)}]" if names else ""
        if arguments.editable:
            requirements.extend(("--editable", f"{ROOT / 'packages' / package}{extras}"))
        else:
            requirements.append(f"{wheels[index]}{extras}")
    if arguments.dev:
        requirements.extend(development_tools())
    # The scripts-folder warning is left out: the last line of this script says whether ac2
    # can be typed, which is what the warning is about.
    installed = pip(
        [
            "install",
            *quiet(getattr(arguments, "verbose", False)),
            "--no-warn-script-location",
            *requirements,
            *extra,
        ]
    )
    if installed.returncode != 0:
        raise SetupError(
            f"pip could not install the SDK (exit code {installed.returncode}); "
            "its output is above",
            "fix the problem it names, then run the script again. The earlier SDK is already "
            "uninstalled",
        )


def verify() -> list[str]:
    """Check the installed SDK, and return the lines to print for it."""
    checked = pip(["check"], capture=True)
    if checked.returncode != 0:
        raise SetupError(
            "pip reports broken requirements: " + (checked.stdout or checked.stderr).strip(),
            "install what it names, then run 'python -m pip check'",
        )
    program = (
        "import importlib, importlib.metadata as m\n"
        f"for name in {list(PACKAGES)!r}:\n"
        "    importlib.import_module(name.replace('-', '_'))\n"
        "    print(name, m.version(name))\n"
    )
    imported = subprocess.run(  # noqa: S603 - the current interpreter and a fixed program
        [sys.executable, "-I", "-c", program], capture_output=True, text=True, check=False
    )
    if imported.returncode != 0:
        raise SetupError(
            "the installed SDK does not import: "
            + (imported.stderr.strip().splitlines() or [""])[-1],
            "run 'python -m pip check' and read the error above",
        )
    return ["pip check found no broken requirements", *imported.stdout.strip().splitlines()]


def same_place(first: str | Path, second: str | Path) -> bool:
    """Whether two paths name the same file or folder, ignoring case on Windows."""
    return os.path.normcase(os.path.realpath(first)) == os.path.normcase(os.path.realpath(second))


def python_command() -> str:
    """Return how to run this Python: ``python`` when that is this one, else its full path."""
    found = shutil.which("python")
    if found and same_place(found, sys.executable):
        return "python"
    return f'"{sys.executable}"'


def command_note() -> list[str]:
    """Return the lines that say how to run ``ac2`` now that the SDK is installed.

    pip puts the command in its scripts folder, which is not always on PATH (a base Python
    on Windows usually has it off PATH), and an older ``ac2`` elsewhere on PATH can come
    first. Either way typing ``ac2`` would fail or run the wrong one, so say which it is.
    """
    module = f"{python_command()} -m agentcraft2_cli"
    importlib.invalidate_caches()
    try:
        installed = importlib.metadata.distribution("agentcraft2-cli")
    except importlib.metadata.PackageNotFoundError:
        return [f"Check it with: {module} --version"]
    command = next(
        (
            Path(str(installed.locate_file(entry)))
            for entry in installed.files or []
            if entry.name in {"ac2", "ac2.exe"}
        ),
        None,
    )
    if command is None:
        return [f"Check it with: {module} --version"]
    command = Path(os.path.normpath(command))
    folder = command.parent
    on_path = [entry for entry in os.environ.get("PATH", "").split(os.pathsep) if entry]
    if not any(same_place(entry, folder) for entry in on_path):
        return [
            f"The 'ac2' command is in {folder}, which is not on your PATH, so typing 'ac2' fails.",
            f"Until you add that folder to PATH, run it as: {module}",
            f"Check it with: {module} --version",
        ]
    first = shutil.which("ac2")
    if first and not same_place(first, command):
        return [
            f"Typing 'ac2' runs {first}, not the one just installed in {folder}.",
            f"Remove the old one or put {folder} first on PATH. Until then, run: {module}",
            f"Check it with: {module} --version",
        ]
    return [f"Check it with: ac2 --version (or {module} --version)"]


def running_tools(sdk: Path) -> list[tuple[str, Path]]:
    """Return the tools that are running, with the state folder of the workspace that started them.

    The SDK folder records where each was started; the run folder there holds a lock that only a
    live supervisor holds.
    """
    control = load_module(CONTROL)
    found = []
    for folder in installed_tools(sdk):
        try:
            recorded = json.loads((sdk / "running" / f"{folder.name}.json").read_text("utf-8"))
            state = Path(str(recorded["state"]))
        except (OSError, ValueError, KeyError):
            continue
        if control.active(state / "services" / folder.name / "run"):
            found.append((folder.name, state))
    return found


def refuse_while_running(sdk: Path) -> None:
    """Stop before anything is changed if a tool is running: its files are in use.

    Raises:
        SetupError: Naming the tool and the workspace it was started from.
    """
    for name, state in running_tools(sdk):
        raise SetupError(
            f"the tool {name} is running, started from the workspace {state.parent}",
            "stop it with 'ac2 services stop' (from any workspace), then run this script again. "
            "Nothing was changed",
        )


def installed_tools(sdk: Path) -> list[Path]:
    """Return the folders of the tools this script, or an earlier run of it, installed."""
    folder = sdk / "services"
    if not folder.is_dir():
        return []
    return sorted(item for item in folder.iterdir() if (item / INSTALLED_RECORD).is_file())


def remove_tools(sdk: Path) -> list[str]:
    """Remove the tools of an earlier install, and nothing else in the folder.

    Raises:
        SetupError: If a tool cannot be removed, which is usually because it is running.
    """
    refuse_while_running(sdk)
    removed = []
    # A tool is moved aside before it is deleted. Moving a folder fails at once, and changes
    # nothing, while the tool runs; deleting it would first remove every file that is not in use
    # and leave the running tool broken.
    for leftover in sorted((sdk / "services").glob(".*-removing")):
        shutil.rmtree(leftover, ignore_errors=True)
    for folder in installed_tools(sdk):
        aside = folder.with_name(f".{folder.name}-removing")
        try:
            folder.rename(aside)
        except OSError as problem:
            raise SetupError(
                f"cannot remove {folder} ({problem})",
                "stop the tool with 'ac2 services stop', close what uses its files, then run "
                "the script again. Nothing was changed",
            ) from problem
        shutil.rmtree(aside, ignore_errors=True)
        (sdk / "running" / f"{folder.name}.json").unlink(missing_ok=True)
        removed.append(folder.name)
    return removed


def install_tools(sdk: Path) -> list[str]:
    """Install the SDK's tools with the SDK that was just installed."""
    environment = {**os.environ, "AC2_SDK_PATH": str(sdk)}
    done = subprocess.run(  # noqa: S603 - this Python, running the SDK's own installer
        [sys.executable, "-m", "agentcraft2_cli.sdk", "install", "--checkout", str(ROOT)],
        env=environment,
        cwd=ROOT,
        check=False,
    )
    if done.returncode != 0:
        raise SetupError(
            f"the SDK's tools could not be installed (exit code {done.returncode}); the message "
            "above says why",
            "fix what it names, then run this script again. The SDK itself is installed",
        )
    return [f"installed in {sdk}"]


def check_tools(sdk: Path) -> list[str]:
    """Check that the installed tools are usable by the installed SDK."""
    checked = subprocess.run(
        [sys.executable, "-m", "agentcraft2_cli.sdk", "check"],
        env={**os.environ, "AC2_SDK_PATH": str(sdk)},
        capture_output=True,
        text=True,
        check=False,
    )
    if checked.returncode != 0:
        raise SetupError(
            "the SDK's tools are not usable: " + (checked.stderr or checked.stdout).strip(),
            "run this script again",
        )
    return checked.stdout.strip().splitlines()


def run(arguments: argparse.Namespace, pip_arguments: Sequence[str]) -> None:
    """Run every step in order. The first problem stops the run."""
    total = len(STEPS)
    step = 0

    def begin() -> None:
        say(f"[{step + 1}/{total}] {STEPS[step]}")

    def complete(lines: Sequence[str] = ()) -> None:
        nonlocal step
        for line in lines:
            say(f"      {line}")
        say(f"[{step + 1}/{total}] {STEPS[step]}: complete")
        step += 1

    try:
        steps(arguments, pip_arguments, begin, complete)
    except SetupError as problem:
        problem.step = f"step {step + 1} of {total}, {STEPS[step].lower()}"
        raise


def steps(
    arguments: argparse.Namespace,
    pip_arguments: Sequence[str],
    begin: Callable[[], None],
    complete: Callable[[Sequence[str]], None],
) -> None:
    """Do the work of every step, in order."""
    begin()
    complete(check_environment(arguments, pip_arguments))

    sdk = None if arguments.no_services else sdk_folder()
    begin()
    installed = find_installed()
    tools = installed_tools(sdk) if sdk is not None else []
    complete(
        [item.describe() for item in installed]
        + [f"tool {folder.name} in {folder}" for folder in tools]
        or ["no SDK is installed"]
    )

    begin()
    wheels: list[Path] = []
    if arguments.editable:
        complete(["editable install: there is nothing to build"])
    else:
        wheels = build_wheels(getattr(arguments, "verbose", False))
        complete([])

    begin()
    gone = remove_tools(sdk) if sdk is not None else []
    if installed:
        uninstall(installed)
    removed = [f"removed {', '.join(item.name for item in installed)}"] if installed else []
    removed += [f"removed the tool {name}" for name in gone]
    complete(removed or ["nothing to uninstall"])

    begin()
    install(arguments, wheels, pip_arguments)
    complete([])

    begin()
    complete(install_tools(sdk) if sdk is not None else ["skipped: --no-services"])

    begin()
    complete([*verify(), *(check_tools(sdk) if sdk is not None else [])])


def main(argv: Sequence[str] | None = None) -> int:
    """Build and install the SDK. Return the exit code."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dev", action="store_true", help="also install the development tools")
    parser.add_argument(
        "--editable", action="store_true", help="install the checkout itself, not built wheels"
    )
    parser.add_argument(
        "--verbose", action="store_true", help="show everything pip prints, not only problems"
    )
    parser.add_argument(
        "--no-services", action="store_true", help="leave out Phoenix and the other tools"
    )
    arguments, pip_arguments = parser.parse_known_args(argv)
    try:
        run(arguments, pip_arguments)
    except SetupError as problem:
        sys.stderr.write(f"error: stopped at {problem.step}: {problem}\n")
        if problem.fix:
            sys.stderr.write(f"fix: {problem.fix}\n")
        return 1
    say("The SDK is installed.")
    for line in command_note():
        say(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
