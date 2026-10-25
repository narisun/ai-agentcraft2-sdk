"""Run a module with a service's own package folder, and none of this Python's other packages.

A service such as Phoenix needs packages that conflict with those of the SDK, so its packages
live in a folder of their own, installed by ``pip --target``. This file starts a module with
that folder on its path. It runs under ``python -I -S``, which leaves out the user's and the
site's packages and ``PYTHONPATH``, so it imports only the standard library, and it can be run
by its path without ``agentcraft2_cli`` being importable.

Usage::

    python -I -S bootstrap.py --packages FOLDER -m MODULE [ARGUMENTS...]
    python -I -S bootstrap.py --packages FOLDER --check PIP_MODULE_FILE
"""

from __future__ import annotations

import importlib.util
import json
import runpy
import signal
import site
import sys
import sysconfig
from collections.abc import Sequence
from pathlib import Path

MANIFEST = ".python-runtime.json"
"""The file in a package folder that records the Python it was installed for."""


def runtime_identity() -> dict[str, object]:
    """Identify the Python that installs a package folder: its kind, version and platform."""
    return {
        "implementation": sys.implementation.name,
        "version": list(sys.version_info[:2]),
        "platform": sysconfig.get_platform(),
    }


def problem_with(folder: Path) -> str | None:
    """Say what is wrong with a package folder for this Python, or ``None`` if it is usable."""
    if not folder.is_dir():
        return f"the service's packages are not installed ({folder} does not exist)"
    manifest = folder / MANIFEST
    if not manifest.is_file():
        return f"the service's packages have no record of their Python ({manifest})"
    try:
        recorded = json.loads(manifest.read_text("utf-8"))
    except (OSError, ValueError):
        return (
            f"the record of the Python the packages were installed for is unreadable ({manifest})"
        )
    if recorded != runtime_identity():
        return (
            f"the service's packages were installed for a different Python ({recorded}), "
            f"and this is {runtime_identity()}"
        )
    return None


def main(argv: Sequence[str] | None = None) -> int:
    """Put the package folder on the path, then run the module or the check."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not sys.flags.isolated or not sys.flags.no_site:
        sys.stderr.write("the package bootstrap must run under python -I -S\n")
        return 2
    if len(arguments) < 4 or arguments[0] != "--packages":
        sys.stderr.write(
            "usage: bootstrap.py --packages FOLDER (-m MODULE [ARGS...] | --check PIP)\n"
        )
        return 2
    folder = Path(arguments[1]).resolve()
    problem = problem_with(folder)
    if problem is not None:
        sys.stderr.write(f"{problem}\n")
        return 3
    # -I -S leave out the global and user site packages and PYTHONPATH. addsitedir also reads
    # the .pth files that an editable install writes.
    site.addsitedir(str(folder))
    command, *rest = arguments[2:]
    if command == "--check":
        # Load the pip that installed the folder, without exposing its own site packages.
        spec = importlib.util.spec_from_file_location("pip", rest[0])
        if spec is None or spec.loader is None:
            sys.stderr.write(f"cannot load pip from {rest[0]}\n")
            return 2
        module = importlib.util.module_from_spec(spec)
        sys.modules["pip"] = module
        spec.loader.exec_module(module)
        sys.argv = ["pip", "check"]
        runpy.run_module("pip", run_name="__main__", alter_sys=True)
        return 0
    if command != "-m" or not rest:
        sys.stderr.write(f"unknown option {command}\n")
        return 2
    # Uvicorn restores and replays SIGBREAK once it has closed its lifespan, and Python's
    # default handler then ends the process with exit 3. The interrupt handler lets the
    # service finish its cleanup first.
    windows_break = getattr(signal, "SIGBREAK", None)
    if windows_break is not None and signal.getsignal(windows_break) == signal.SIG_DFL:
        signal.signal(windows_break, signal.default_int_handler)
    sys.argv = [rest[0], *rest[1:]]
    try:
        runpy.run_module(rest[0], run_name="__main__", alter_sys=True)
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
