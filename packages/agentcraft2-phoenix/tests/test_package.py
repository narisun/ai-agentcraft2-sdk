"""The package's metadata agrees with the release its code was written for."""

import tomllib
from pathlib import Path

from agentcraft2_phoenix import PINNED_VERSION

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def test_the_viewer_extra_pins_the_release_the_code_names() -> None:
    project = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]
    assert project["optional-dependencies"]["viewer"] == [f"arize-phoenix=={PINNED_VERSION}"]


def test_nothing_but_the_page_tooling_is_a_base_dependency() -> None:
    project = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]
    assert [item.split(">")[0] for item in project["dependencies"]] == ["jinja2"]
