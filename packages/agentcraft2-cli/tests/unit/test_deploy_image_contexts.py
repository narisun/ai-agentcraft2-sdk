"""Every image the deploy step writes can be built: each COPY source is in its build context.

A Dockerfile that copies a folder its own ``.dockerignore`` leaves out fails to build, and the
obvious way round it, deleting the ignore file, sends the whole workspace (its ``.env`` files
included) into the build. These tests read both files of each image and check that they agree.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath

import pytest

from agentcraft2_cli.render import TemplateRenderer

# A folder name the workspace could hold, with files of the kinds an image must never carry.
CONTEXT = {
    "name": "helper",
    "kind": "agent",
    "folder": "agents/helper",
    "package": "helper",
    "entry_module": "helper.service",
    "protocol": "HTTP",
    "port": 8000,
    "command_json": '["helper-serve"]',
    "python": "3.13",
    "adot": "aws-opentelemetry-distro>=0.18.0",
    "artifact": "container",
    "jwt": False,
    "v": {
        "PREFIX": "P_",
        "ARTIFACT": "A",
        "AUTH": "B",
        "DISCOVERY_URL": "C",
        "MEMORY": "D",
        "GATEWAY": "E",
    },
}
LEAVES = ("anything.txt", "pyproject.toml", "data.yaml", "src/module.py", "agents.yaml")
SECRET_FILES = (
    ".env",
    "agents/helper/.env",
    "policies/.env.local",
    "deploy/helper/prod.env",
    "policies/server.key",
    "policies/server.pem",
    "agents/helper/.agentcraft2/audit.jsonl",
    "policies/audit.jsonl",
    "policies/state.sqlite",
)


def _regex(pattern: str) -> re.Pattern[str]:
    """Translate a dockerignore pattern: ``**`` crosses folders, ``*`` and ``?`` do not."""
    out, index = "", 0
    while index < len(pattern):
        if pattern.startswith("**/", index):
            out += "(?:.*/)?"
            index += 3
        elif pattern.startswith("**", index):
            out += ".*"
            index += 2
        elif pattern[index] == "*":
            out += "[^/]*"
            index += 1
        elif pattern[index] == "?":
            out += "[^/]"
            index += 1
        else:
            out += re.escape(pattern[index])
            index += 1
    return re.compile(out)


def _sent(path: str, ignore: str) -> bool:
    """Whether a build context built with these patterns carries ``path``: the last match wins."""
    sent = True
    for line in ignore.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        negated = line.startswith("!")
        pattern = line.removeprefix("!").rstrip("/")
        if _regex(pattern).fullmatch(path) or any(
            _regex(pattern).fullmatch("/".join(parts)) for parts in _parents(path)
        ):
            sent = negated
    return sent


def _parents(path: str) -> list[list[str]]:
    parts = path.split("/")
    return [parts[:end] for end in range(1, len(parts))]


def _images() -> list[tuple[str, dict[str, str]]]:
    renderer = TemplateRenderer()
    return [
        ("deploy-opa", {str(p): t for p, t in renderer.render("deploy-opa", {}).items()}),
        (
            "deploy-service",
            {str(p): t for p, t in renderer.render("deploy-service", CONTEXT).items()},
        ),
        (
            "deploy-agentcore",
            {str(p): t for p, t in renderer.render("deploy-agentcore", CONTEXT).items()},
        ),
    ]


def _copied(dockerfile: str) -> list[str]:
    sources = []
    for line in dockerfile.splitlines():
        parts = line.split()
        if parts[:1] == ["COPY"] and not any(part.startswith("--from") for part in parts):
            sources.append(parts[1])
    return sources


@pytest.mark.parametrize(("template", "files"), _images(), ids=lambda value: str(value)[:20])
def test_every_copy_source_is_in_the_build_context(template: str, files: dict[str, str]) -> None:
    dockerfile = files["Dockerfile"]
    ignore = files["Dockerfile.dockerignore"]
    sources = _copied(dockerfile)
    assert sources, template
    for source in sources:
        # A folder is in the context when at least one file such a folder holds is sent; a file
        # stands for itself. Contexts admit what they need by name, so one probe is not enough.
        probes = (
            [source]
            if "." in PurePosixPath(source).name
            else [f"{source}/{leaf}" for leaf in LEAVES]
        )
        assert any(_sent(probe, ignore) for probe in probes), (
            f"{template}: COPY {source} is left out by its dockerignore"
        )


@pytest.mark.parametrize(("template", "files"), _images(), ids=lambda value: str(value)[:20])
def test_no_image_carries_a_secret_or_local_state(template: str, files: dict[str, str]) -> None:
    ignore = files["Dockerfile.dockerignore"]
    carried = [path for path in SECRET_FILES if _sent(path, ignore)]
    assert carried == [], f"{template}: the build context would carry {carried}"


def test_the_opa_image_carries_the_workspaces_rules_and_the_bundle() -> None:
    ignore = dict(_images())["deploy-opa"]["Dockerfile.dockerignore"]
    assert _sent("policies/agentcraft2/rules/data.yaml", ignore)
    assert _sent("deploy/opa/bundle/agentcraft2/authz/authz.rego", ignore)
    assert not _sent("agents/helper/src/helper/service.py", ignore)
