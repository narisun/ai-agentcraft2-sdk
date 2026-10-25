"""The services the SDK provides for developers, and what each one needs to run."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from agentcraft2_cli.logbook import LOG_FOLDER
from agentcraft2_cli.services import packages
from agentcraft2_cli.workspace import LibrarySource

__all__ = ["PHOENIX", "SERVICES", "Paths", "ServiceSpec", "paths_of"]

SERVICE_FOLDER = "services"
"""The folder of the state folder that holds one folder per service."""


@dataclass(frozen=True, slots=True)
class Paths:
    """Where a service keeps its files, all under the state folder of the workspace.

    Attributes:
        base: The service's folder: its packages, its data and its run folder.
        run: The folder the supervisor and ``ac2 services`` talk through.
        data: The service's own data, such as Phoenix's database of traces.
        log: The service's log, beside the logs of the agents, so ``ac2 logs`` reads it.
    """

    base: Path
    run: Path
    data: Path
    log: Path


def paths_of(state: Path, name: str) -> Paths:
    """Return the paths of the service ``name`` under the state folder ``state``."""
    base = state / SERVICE_FOLDER / name
    return Paths(base, base / "run", base / "data", state / LOG_FOLDER / f"{name}.log")


@dataclass(frozen=True, slots=True)
class ServiceSpec:
    """One service: what it is, where it listens and how it is installed and started.

    Attributes:
        name: The name used on the command line.
        title: A line that says what it is.
        port: The loopback port it listens on.
        health_path: The path that answers 200 when it is ready.
        command: Builds the command that runs it, given its paths.
        requirements: Builds the pip arguments that install its packages, given where the
            workspace takes the SDK from; ``None`` for a service that has no packages of its
            own.
        longest_path: The length of the longest path inside its installed packages, which a
            Windows path limit must leave room for.
    """

    name: str
    title: str
    port: int
    health_path: str
    command: Callable[[Paths], list[str]]
    requirements: Callable[[LibrarySource], list[str]] | None = None
    longest_path: int = 0

    @property
    def url(self) -> str:
        """The address of the service."""
        return f"http://127.0.0.1:{self.port}"


def _phoenix_requirements(library: LibrarySource) -> list[str]:
    if library.kind == "path":
        checkout = Path(library.path or "")
        return ["--editable", f"{checkout / 'packages' / 'agentcraft2-phoenix'}[viewer]"]
    return [f"agentcraft2-phoenix[viewer]{library.version}"]


PHOENIX = ServiceSpec(
    name="phoenix",
    title="Phoenix: the viewer for the traces of your agents",
    port=6006,
    health_path="/healthz",
    command=lambda paths: packages.command(
        paths.base, "agentcraft2_phoenix", "--port", "6006", "--data-dir", str(paths.data)
    ),
    requirements=_phoenix_requirements,
    # Measured on Phoenix 20.21.1: 145, in a generated evaluator under phoenix/evals.
    longest_path=150,
)

SERVICES = {spec.name: spec for spec in (PHOENIX,)}
"""Every service, by name, in the order they start."""
