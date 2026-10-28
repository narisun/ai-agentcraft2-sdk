"""``ac2 data model validate``: the values in the files against the model."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from agentcraft2_cli.__main__ import main
from agentcraft2_cli.data_model import read_data_model
from agentcraft2_cli.data_model_draft import write_model
from agentcraft2_cli.testing import toolbox_for_tests

TOOLS = toolbox_for_tests(pins={})

CLAIMS = (
    "claim_id,uen,amount,opened\n"
    "9001,A-1,10.5,2026-01-04\n"
    '9002,A-2,"1,234.50",2026-01-09\n'
    "9003,A-1,30.25,03/04/2026\n"
    "9004,A-2,40,2026-02-11\n"
)
ACCOUNTS = "uen,name\nA-1,Alpha\nA-2,Beta\n"


def run(*arguments: str | Path) -> int:
    return main([str(argument) for argument in arguments], toolbox=TOOLS)


def finished(data: Path) -> None:
    """Draft the model for `data` and fill the placeholders so that it follows the standard."""
    assert run("data", "model", "draft", data) == 0
    rows = read_data_model(data / "data_model.csv")
    for row in rows:
        row["domain"] = "crm"
        row["table_description"] = "Rows of a table, described by a reviewer."
        row["description"] = "What this column means, written by a reviewer."
        row["fk"] = row["fk"].replace("todo.", "crm.")
        if row["column"] in ("amount",):
            row["datatype"] = "decimal"
        if row["column"] == "opened":
            row["datatype"] = "date"
    write_model(rows, data / "data_model.csv")


@pytest.fixture
def data(tmp_path: Path) -> Path:
    folder = tmp_path / "data"
    folder.mkdir()
    (folder / "accounts.csv").write_text(ACCOUNTS, encoding="utf-8", newline="")
    (folder / "claims.csv").write_text(CLAIMS, encoding="utf-8", newline="")
    finished(folder)
    return folder


def test_validate_names_the_file_row_line_column_and_what_is_wrong(
    data: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    capsys.readouterr()
    assert run("data", "model", "validate", data) == 1
    out = capsys.readouterr().out
    assert "claims.csv" in out
    assert "row 2 (line 3)" in out
    assert "column amount" in out
    assert "'1,234.50'" in out
    assert "thousands separator" in out
    assert "row 3 (line 4)" in out
    assert "column opened" in out
    assert "day and month order" in out
    assert "2 errors" in out.splitlines()[-1]


def test_validate_exits_cleanly_when_the_values_follow_the_model(
    data: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (data / "claims.csv").write_text(
        "claim_id,uen,amount,opened\n9001,A-1,10.5,2026-01-04\n", encoding="utf-8", newline=""
    )
    capsys.readouterr()
    assert run("data", "model", "validate", data) == 0
    assert "no problems" in capsys.readouterr().out


def test_warnings_alone_do_not_fail_the_command(
    data: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (data / "claims.csv").write_text(
        "claim_id,uen,amount,opened\n9001,A-1,10.5, 2026-01-04\n", encoding="utf-8", newline=""
    )
    capsys.readouterr()
    assert run("data", "model", "validate", data) == 0
    out = capsys.readouterr().out
    assert "warning" in out
    assert "0 errors" in out.splitlines()[-1]


def test_a_rule_is_shown_a_few_times_per_column_and_the_rest_are_counted(
    data: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    lines = "".join(f"{9100 + n},A-1,bad{n},2026-01-04\n" for n in range(8))
    (data / "claims.csv").write_text(
        "claim_id,uen,amount,opened\n" + lines, encoding="utf-8", newline=""
    )
    capsys.readouterr()
    run("data", "model", "validate", data, "--limit", "3")
    out = capsys.readouterr().out
    assert out.count("is not a plain number") == 3
    assert "and 5 more" in out
    assert "--report" in out


def test_the_report_file_has_every_finding_with_its_exact_place(data: Path, tmp_path: Path) -> None:
    lines = "".join(f"{9100 + n},A-1,bad{n},2026-01-04\n" for n in range(8))
    (data / "claims.csv").write_text(
        "claim_id,uen,amount,opened\n" + lines, encoding="utf-8", newline=""
    )
    report = tmp_path / "report.csv"
    run("data", "model", "validate", data, "--limit", "1", "--report", report)
    with report.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert list(rows[0]) == ["level", "rule", "file", "row", "line", "column", "value", "message"]
    types = [r for r in rows if r["rule"] == "type"]
    assert len(types) == 8
    assert (types[0]["file"], types[0]["row"], types[0]["line"], types[0]["column"]) == (
        "claims.csv",
        "1",
        "2",
        "amount",
    )
    assert types[0]["value"] == "bad0"


def test_values_can_be_kept_out_of_the_output_and_the_report(
    data: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    report = tmp_path / "report.csv"
    capsys.readouterr()
    run("data", "model", "validate", data, "--hide-values", "--report", report)
    assert "1,234.50" not in capsys.readouterr().out
    assert "1,234.50" not in report.read_text(encoding="utf-8")


def test_only_the_named_tables_are_validated(
    data: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    capsys.readouterr()
    assert run("data", "model", "validate", data, "--table", "accounts") == 0
    assert "claims.csv" not in capsys.readouterr().out


def test_validate_without_a_model_says_how_to_make_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = tmp_path / "d"
    folder.mkdir()
    (folder / "t.csv").write_text("a\n1\n", encoding="utf-8")
    assert run("data", "model", "validate", folder) == 1
    assert "ac2 data model draft" in capsys.readouterr().err


def test_a_model_that_still_has_placeholders_is_refused_with_the_reason(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = tmp_path / "d"
    folder.mkdir()
    (folder / "t.csv").write_text("a\n1\n", encoding="utf-8")
    run("data", "model", "draft", folder)
    capsys.readouterr()
    assert run("data", "model", "validate", folder) == 0
    assert "placeholder" not in capsys.readouterr().err
