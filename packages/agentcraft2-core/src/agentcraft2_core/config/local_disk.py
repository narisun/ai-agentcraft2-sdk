"""Whether a folder is on a disk of this machine, and not on the network, synced or roamed.

Phoenix and Agent Lab are installed in a folder that must stay on the machine, and Phoenix keeps
a SQLite database in the workspace. A network drive makes both slow and unreliable, a synced
folder such as OneDrive copies hundreds of megabytes and locks files, and a roaming profile
carries them to every machine the person signs in to.

The installer script runs before the SDK exists and loads this file by its path, so it uses
only the standard library.
"""

from __future__ import annotations

import ctypes
import os
import sys
from collections.abc import Callable, Mapping
from pathlib import Path

__all__ = ["DRIVE_REMOTE", "free_bytes", "local_disk_problem"]

DRIVE_REMOTE = 4
"""What Windows reports for a drive that is on the network."""

_SYNCED = ("OneDrive", "OneDriveCommercial", "OneDriveConsumer")
_ROAMING = "APPDATA"


def _drive_type(root: str) -> int:
    """Ask Windows what kind of drive ``root``, a drive letter and a backslash, is."""
    windll = getattr(ctypes, "windll", None)
    if windll is None:
        return 0
    return int(windll.kernel32.GetDriveTypeW(root))


def local_disk_problem(
    folder: Path,
    *,
    windows: bool = sys.platform == "win32",
    environment: Mapping[str, str] | None = None,
    drive_type: Callable[[str], int] = _drive_type,
) -> str | None:
    """Say why ``folder`` is not on a disk of this machine, or ``None`` if it is.

    Only Windows is checked: that is where a profile is roamed, a drive mapped to a share and a
    folder synced as a matter of policy.

    Args:
        folder: The folder, which need not exist yet.
        windows: Whether this is Windows.
        environment: Where the names of the synced and roaming folders are read from.
        drive_type: Asks Windows for the type of a drive; a test passes its own.
    """
    if not windows:
        return None
    found = os.environ if environment is None else environment
    for candidate in dict.fromkeys((folder, folder.resolve())):
        drive, _ = os.path.splitdrive(str(candidate))
        if drive.startswith(("\\\\", "//")):
            return f"{candidate} is a network path ({drive})"
        if drive and drive_type(drive + "\\") == DRIVE_REMOTE:
            return f"{candidate} is on a network drive ({drive})"
        for name in _SYNCED:
            synced = (found.get(name) or "").strip()
            if synced and candidate.is_relative_to(Path(synced)):
                return f"{candidate} is inside OneDrive, which synchronizes it ({synced})"
        roaming = (found.get(_ROAMING) or "").strip()
        if roaming and candidate.is_relative_to(Path(roaming)):
            return f"{candidate} is in the roaming profile ({roaming}), which follows the user"
    return None


def free_bytes(folder: Path) -> int:
    """Return the free space of the disk that holds ``folder``, which need not exist yet."""
    import shutil

    existing = folder
    while not existing.exists() and existing != existing.parent:
        existing = existing.parent
    return shutil.disk_usage(existing).free
