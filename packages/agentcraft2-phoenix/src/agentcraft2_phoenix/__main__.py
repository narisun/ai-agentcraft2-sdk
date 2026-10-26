"""``python -m agentcraft2_phoenix``: run the trace viewer, on loopback."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from agentcraft2_phoenix.config import apply_to_process
from agentcraft2_phoenix.server import run

__all__ = ["main"]


def main(argv: Sequence[str] | None = None) -> int:
    """Set up the viewer's environment, then run it. Return the exit code."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=6006, help="the loopback port (default 6006)")
    parser.add_argument(
        "--data-dir", type=Path, required=True, help="the folder for the database of traces"
    )
    arguments = parser.parse_args(argv)
    if not 1 <= arguments.port <= 65535:
        parser.error(f"--port must be between 1 and 65535, not {arguments.port}")
    arguments.data_dir.mkdir(parents=True, exist_ok=True)
    apply_to_process(arguments.data_dir.resolve(), arguments.port)
    sys.stdout.write(
        f"Phoenix on http://127.0.0.1:{arguments.port}; traces in {arguments.data_dir.resolve()}\n"
    )
    sys.stdout.flush()
    try:
        run(arguments.port)
    except RuntimeError as problem:
        sys.stderr.write(f"error: {problem}\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
