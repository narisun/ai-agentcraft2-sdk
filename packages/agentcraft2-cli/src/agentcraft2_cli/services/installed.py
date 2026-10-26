"""Whether an installed service is usable by this SDK, and whether its files fit on Windows."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from agentcraft2_cli import __version__
from agentcraft2_cli.errors import CliError
from agentcraft2_cli.services import packages
from agentcraft2_cli.services.registry import INSTALLED, ServiceSpec
from agentcraft2_core.config import sdk_variable

__all__ = ["WINDOWS_PATH_LIMIT", "check_path_length", "installed_problem"]

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
