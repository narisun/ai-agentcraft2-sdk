"""Reading the files a command is given, with the real reason when it cannot."""

from __future__ import annotations

from pathlib import Path

from agentcraft2_cli.errors import CliError
from agentcraft2_core.contracts import describe

__all__ = ["read_text"]


def read_text(path: Path, *, what: str, fix: str | None = None) -> str:
    """Return the text of ``path``, which is UTF-8 with or without a byte order mark.

    A byte order mark is accepted because Windows PowerShell 5.1 writes one, and
    the TOML and JSON parsers refuse it.

    Args:
        path: The file to read.
        what: What the file is, for the message: "the workspace file".
        fix: What to do about it, when more than the message helps.

    Raises:
        CliError: If it cannot be read. The message says why, as the operating
            system or the decoder put it: a missing file, a permission, a byte
            that is not UTF-8.
    """
    try:
        return path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as problem:
        raise CliError(
            f"{what} ({path}) cannot be read ({describe(problem)})", fix=fix
        ) from problem
