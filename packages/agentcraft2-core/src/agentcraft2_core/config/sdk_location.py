"""Where the SDK keeps the tools it installs: one decision, made without the SDK.

The installer script runs before any of the SDK is installed, so this file uses only the
standard library, and the script loads it by its path. The SDK reads the same function, so
the installer and ``ac2`` can never disagree about where the tools are.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from pathlib import Path

__all__ = ["default_sdk_path"]


def default_sdk_path(
    environment: Mapping[str, str] | None = None,
    *,
    windows: bool = sys.platform == "win32",
    home: Path | None = None,
) -> Path:
    """Return where the SDK keeps its tools when nothing says otherwise.

    On Windows that is the local application data folder, which belongs to the machine and
    is never roamed with the profile; the roaming folder is never used. Elsewhere it is a
    hidden folder in the home folder.

    Args:
        environment: Where ``LOCALAPPDATA`` is read from; by default the process environment.
        windows: Whether this is Windows.
        home: The user's home folder; by default the system's.
    """
    found = os.environ if environment is None else environment
    base = home if home is not None else Path.home()
    if windows:
        local = (found.get("LOCALAPPDATA") or "").strip()
        return (Path(local) if local else base / "AppData" / "Local") / "agentcraft2-sdk"
    return base / ".agentcraft2_sdk"
