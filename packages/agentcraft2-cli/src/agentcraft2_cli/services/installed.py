"""Whether an installed service is usable by this SDK, and whether its files fit on Windows."""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

from agentcraft2_cli import __version__
from agentcraft2_cli.errors import CliError
from agentcraft2_cli.services import bootstrap, packages
from agentcraft2_cli.services.registry import INSTALLED, ServiceSpec
from agentcraft2_cli.workspace import LibrarySource
from agentcraft2_core.config import sdk_variable

__all__ = [
    "WINDOWS_PATH_LIMIT",
    "check_path_length",
    "fingerprint",
    "installed_problem",
    "outdated",
]

WINDOWS_PATH_LIMIT = 259
"""The longest path Windows opens, unless an administrator has enabled long paths."""


def installed_problem(spec: ServiceSpec, installed: Path, version: str = __version__) -> str | None:
    """Say what is wrong with the installed service for this SDK, or ``None`` if it is usable."""
    if spec.requirements is None:
        return None
    why = packages.problem(installed)
    if why is not None:
        return why
    try:
        record = json.loads((installed / INSTALLED).read_text("utf-8"))
    except (OSError, ValueError):
        return f"{installed / INSTALLED} is missing or unreadable"
    if record.get("sdk_version") != version:
        return f"it was installed by SDK {record.get('sdk_version')}, and this is SDK {version}"
    return None


def fingerprint(spec: ServiceSpec, library: LibrarySource) -> str:
    """Return what an install of ``spec`` from ``library`` depends on, as one short string.

    It covers the pip arguments, the Python, and the ``pyproject.toml`` of each package taken from
    a checkout, which is where its dependencies are declared. Source files are left out on
    purpose: a checkout is installed editable, so an edit to the code takes effect without an
    install. The string changes exactly when a new install would install something else.
    """
    if spec.requirements is None:
        return ""
    requirements = spec.requirements(library)
    projects = {}
    for argument in requirements:
        folder = Path(re.sub(r"\[[^\]]*\]$", "", argument))
        pyproject = folder / "pyproject.toml"
        if pyproject.is_file():
            # A checkout whose line endings differ is the same checkout.
            projects[str(folder)] = hashlib.sha256(
                pyproject.read_bytes().replace(b"\r\n", b"\n")
            ).hexdigest()
    basis = {
        "requirements": requirements,
        "projects": projects,
        "python": bootstrap.runtime_identity(),
    }
    return hashlib.sha256(json.dumps(basis, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def outdated(
    spec: ServiceSpec, installed: Path, library: LibrarySource, version: str = __version__
) -> str | None:
    """Say why an install of ``spec`` must be redone, or ``None`` if it is current.

    It is current when it is usable by this SDK (see :func:`installed_problem`) and was made from
    the same requirements, dependencies and Python as an install would use now.
    """
    why = installed_problem(spec, installed, version)
    if why is not None:
        return why
    record = json.loads((installed / INSTALLED).read_text("utf-8"))
    if record.get("fingerprint") != fingerprint(spec, library):
        return "its requirements, dependencies or Python have changed since it was installed"
    return None


def check_path_length(
    spec: ServiceSpec, installed: Path, *, windows: bool = sys.platform == "win32"
) -> None:
    """Refuse to install packages whose paths Windows would not open.

    Raises:
        CliError: If the folder is so deep that the longest file of the service would pass the
            limit, with the length found and what to do.
    """
    if not windows or spec.requirements is None:
        return
    folder = packages.site_packages(installed)
    needed = len(str(folder)) + 1 + spec.longest_path
    if needed > WINDOWS_PATH_LIMIT:
        raise CliError(
            f"the path to the packages of {spec.name} is too long for Windows",
            expected=f"paths of at most {WINDOWS_PATH_LIMIT} characters",
            actual=f"{needed} characters for the longest file below {folder}",
            fix=(
                f"set {sdk_variable()} to a shorter folder on a local disk, such as "
                "C:\\ac2-sdk; or ask your administrators to turn on long paths "
                "(the LongPathsEnabled setting)"
            ),
        )
