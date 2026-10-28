"""The supplied data model and CSV files follow the SDK's data model standard.

The standard and its checks belong to the SDK (``ac2 data model check | diff | validate``, and
``docs/data-model.md`` there); this file only runs them on this example's own files, so a broken
model or a bad value stops the build.
"""

from __future__ import annotations

from pathlib import Path

from agentcraft2_cli.data_model import check_data_model, read_data_model, tables_of
from agentcraft2_cli.data_model_diff import compare_model
from agentcraft2_cli.data_model_validate import validate_data

DATA = Path(__file__).resolve().parents[3] / "data"


def rows() -> list[dict[str, str]]:
    return read_data_model(DATA / "data_model.csv")


def test_the_supplied_data_model_follows_the_standard() -> None:
    issues = check_data_model(rows(), DATA)
    assert [str(issue) for issue in issues if issue.level == "error"] == []


def test_every_table_of_the_supplied_model_has_a_description() -> None:
    tables = tables_of(rows())
    assert len(tables) == 32
    assert all(len(t.description) >= 20 for t in tables)


def test_the_supplied_model_describes_the_csv_files_it_sits_beside() -> None:
    differences = compare_model(rows(), DATA)
    assert [d.message for d in differences if d.level == "difference"] == []


def test_every_value_of_the_supplied_files_is_what_the_model_says() -> None:
    findings = validate_data(rows(), DATA)
    assert [f"{f.where()}: {f.message}" for f in findings if f.level == "error"] == []
