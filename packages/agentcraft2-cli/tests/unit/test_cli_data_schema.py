"""``ac2 data model schema``: the tables, columns and joins, for a model that writes SQL."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentcraft2_cli.__main__ import main
from agentcraft2_cli.data_model import COLUMNS
from agentcraft2_cli.data_model_draft import write_model
from agentcraft2_cli.testing import toolbox_for_tests

TOOLS = toolbox_for_tests(pins={})


def run(*arguments: str | Path) -> int:
    return main([str(argument) for argument in arguments], toolbox=TOOLS)


def row(table: str, column: str, **changes: str) -> dict[str, str]:
    base = dict.fromkeys(COLUMNS, "")
    base.update(
        domain="crm",
        table=table,
        table_description=f"The {table} of the bank, one row for each of them.",
        column=column,
        datatype="string",
        nullable="Y",
        description=f"The {column} of a row.",
    )
    return {**base, **changes}


@pytest.fixture
def data(tmp_path: Path) -> Path:
    folder = tmp_path / "data"
    folder.mkdir()
    (folder / "accounts.csv").write_text(
        "uen,segment\nA-1,Retail\nA-2,Retail\nA-3,Corporate\n", encoding="utf-8"
    )
    (folder / "claims.csv").write_text("claim_id,uen\n1,A-1\n2,A-2\n", encoding="utf-8")
    (folder / "regions.csv").write_text("code\nEU\n", encoding="utf-8")
    write_model(
        [
            row("accounts", "uen", pk="Y", nullable="N"),
            row("accounts", "segment"),
            row("claims", "claim_id", pk="Y", nullable="N", datatype="integer"),
            row("claims", "uen", fk="crm.accounts.uen", nullable="N"),
            row("regions", "code", pk="Y", nullable="N"),
        ],
        folder / "data_model.csv",
    )
    return folder


def test_the_schema_is_printed_as_markdown_and_its_size_is_said_on_the_other_stream(
    data: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run("data", "model", "schema", data) == 0
    captured = capsys.readouterr()
    assert captured.out.startswith("# Data schema\n")
    assert "- claims.uen = accounts.uen (many-to-one)" in captured.out
    assert "3 tables" in captured.err
    assert "tokens" in captured.err
    assert "# Data schema" not in captured.err


def test_json_is_asked_for_by_name(data: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run("data", "model", "schema", data, "--format", "json") == 0
    parsed = json.loads(capsys.readouterr().out)
    assert [t["name"] for t in parsed["tables"]] == ["accounts", "claims", "regions"]


def test_the_schema_can_be_written_to_a_file(
    data: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "out" / "schema.md"
    target.parent.mkdir()
    assert run("data", "model", "schema", data, "-o", target) == 0
    assert target.read_text(encoding="utf-8").startswith("# Data schema\n")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "schema.md" in captured.err


def test_tables_can_be_chosen_and_their_neighbours_added(
    data: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run("data", "model", "schema", data, "--table", "claims", "--related") == 0
    out = capsys.readouterr().out
    assert "## claims\n" in out
    assert "## accounts\n" in out
    assert "## regions\n" not in out
    assert "2 of 3 tables" in out


def test_an_unknown_table_says_what_was_meant(
    data: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run("data", "model", "schema", data, "--table", "claim") == 1
    assert "Did you mean claims?" in capsys.readouterr().err


def test_related_without_a_table_is_a_usage_error(
    data: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run("data", "model", "schema", data, "--related") == 2
    assert "--related" in capsys.readouterr().err


def test_values_are_read_from_the_files_only_when_asked_for(
    data: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run("data", "model", "schema", data) == 0
    assert "Values of" not in capsys.readouterr().out
    assert run("data", "model", "schema", data, "--values", "5") == 0
    out = capsys.readouterr().out
    assert "Values of segment: Retail | Corporate" in out
    assert "Values were read from the data files." in out


def test_the_dialect_can_be_named(data: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run("data", "model", "schema", data, "--dialect", "duckdb") == 0
    assert "SQL dialect: duckdb." in capsys.readouterr().out


def test_a_model_that_does_not_follow_the_standard_is_not_put_in_a_prompt(
    data: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rows = [row("accounts", "uen", pk="Y", nullable="N", description="TODO: describe it")]
    rows[0]["domain"] = "todo"
    write_model(rows, data / "data_model.csv")
    assert run("data", "model", "schema", data) == 1
    err = capsys.readouterr().err
    assert "does not follow the standard" in err
    assert f"ac2 data model check {data}" in err
    assert "--allow-problems" in err


def test_a_model_with_problems_can_still_be_written_when_it_is_asked_for(
    data: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rows = [row("accounts", "uen", pk="Y", nullable="N", description="TODO: describe it")]
    write_model(rows, data / "data_model.csv")
    assert run("data", "model", "schema", data, "--allow-problems") == 0
    assert "## accounts" in capsys.readouterr().out


def test_a_missing_model_points_at_draft(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = tmp_path / "empty"
    folder.mkdir()
    assert run("data", "model", "schema", folder) == 1
    assert "ac2 data model draft" in capsys.readouterr().err


def test_a_model_elsewhere_can_be_named(
    data: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    elsewhere = tmp_path / "other_model.csv"
    (data / "data_model.csv").replace(elsewhere)
    assert run("data", "model", "schema", data, "--model", elsewhere) == 0
    assert "## accounts" in capsys.readouterr().out


def test_the_command_is_listed_beside_the_others(capsys: pytest.CaptureFixture[str]) -> None:
    assert run("data", "model", "--help") == 0
    out = capsys.readouterr().out
    assert "schema" in out
    assert all(name in out for name in ("draft", "diff", "check", "validate"))


def test_tables_can_be_left_out(data: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run("data", "model", "schema", data, "--exclude", "regions") == 0
    captured = capsys.readouterr()
    assert "## regions" not in captured.out
    assert "## accounts" in captured.out
    assert "2 of 3 tables" in captured.err


def test_leaving_out_a_table_that_is_not_there_says_what_was_meant(
    data: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run("data", "model", "schema", data, "--exclude", "region") == 1
    assert "Did you mean regions?" in capsys.readouterr().err
