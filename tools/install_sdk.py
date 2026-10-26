"""Build the SDK from this checkout and install it into the current Python environment.

Run with your selected Python: ``python tools/install_sdk.py``. The script uses only that
Python and its pip. It never creates an environment and never installs anything else
than the SDK (and, with ``--dev``, the tools used to work on it).

Steps, each reported as it starts and again when it is complete:

1. Check the environment, and stop at the first problem.
2. Find the SDK distributions that are already installed.
3. Build a wheel of each package. This comes before the uninstall, so that a build that
   fails does not leave the environment without an SDK.
4. Uninstall the installed SDK, and confirm that it is gone.
5. Install the new SDK. This starts only after step 4 has succeeded.
6. Verify the installation.

Options: ``--dev`` also installs every optional dependency and the development tools;
``--editable`` installs the checkout itself instead of the built wheels, so edits to the
source take effect at once. Any other argument is passed to pip, for example
``--no-index --find-links=/path/to/wheels``.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import importlib.util
import json
import os
import shutil
import subprocess
import sys
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
    "Verifying the installation",
)


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


def build_wheels() -> list[Path]:
    """Build one wheel of each package into the build folder, replacing earlier ones."""
    for old in DIST.glob("agentcraft2_*.whl"):
        old.unlink()
    wheels = []
    for package in PACKAGES:
        say(f"      building {package}")
        built = pip(
            ["wheel", "--no-deps", "--wheel-dir", str(DIST), str(ROOT / "packages" / package)]
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
    installed = pip(["install", *requirements, *extra])
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

    begin()
    installed = find_installed()
    complete([item.describe() for item in installed] or ["no SDK is installed"])

    begin()
    wheels: list[Path] = []
    if arguments.editable:
        complete(["editable install: there is nothing to build"])
    else:
        wheels = build_wheels()
        complete([])

    begin()
    if installed:
        uninstall(installed)
        complete([f"removed {', '.join(item.name for item in installed)}"])
    else:
        complete(["nothing to uninstall"])

    begin()
    install(arguments, wheels, pip_arguments)
    complete([])

    begin()
    complete(verify())


def main(argv: Sequence[str] | None = None) -> int:
    """Build and install the SDK. Return the exit code."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dev", action="store_true", help="also install the development tools")
    parser.add_argument(
        "--editable", action="store_true", help="install the checkout itself, not built wheels"
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
