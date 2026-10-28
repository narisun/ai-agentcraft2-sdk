"""``ac2 data model``: draft a data_model.csv from CSV files, compare it, check it."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentcraft2_cli.__main__ import main
from agentcraft2_cli.data_model import check_data_model, read_data_model
from agentcraft2_cli.testing import toolbox_for_tests

TOOLS = toolbox_for_tests(pins={})

ACCOUNTS = "uen,name\nA-1,Alpha\nA-2,Beta\nA-3,Gamma\n"
CLAIMS = "claim_id,uen,amount\n9001,A-1,10.5\n9002,A-2,20.0\n9003,A-1,30.25\n"


def run(*arguments: str | Path) -> int:
    return main([str(argument) for argument in arguments], toolbox=TOOLS)


@pytest.fixture
def data(tmp_path: Path) -> Path:
    folder = tmp_path / "data"
    folder.mkdir()
    (folder / "accounts.csv").write_text(ACCOUNTS, encoding="utf-8")
    (folder / "claims.csv").write_text(CLAIMS, encoding="utf-8")
    return folder


def finish(data: Path) -> None:
    """Fill the placeholders of a draft so that it follows the standard."""
    rows = read_data_model(data / "data_model.csv")
    for row in rows:
        row["domain"] = "crm"
        row["table_description"] = "Rows of a table, described by a reviewer."
        row["description"] = "What this column means, written by a reviewer."
        row["fk"] = row["fk"].replace("todo.", "crm.")
    from agentcraft2_cli.data_model_draft import write_model

    write_model(rows, data / "data_model.csv")


# --- draft --------------------------------------------------------------------------------


def test_draft_writes_the_model_beside_the_data_and_says_what_to_fill_in(
    data: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run("data", "model", "draft", data) == 0
    out = capsys.readouterr().out
    rows = read_data_model(data / "data_model.csv")
    assert {r["table"] for r in rows} == {"accounts", "claims"}
    assert "2 tables" in out
    assert "5 columns" in out
    assert "TODO" in out
    assert "ac2 data model check" in out


def test_draft_writes_to_the_file_it_is_told_to(data: Path, tmp_path: Path) -> None:
    target = tmp_path / "elsewhere" / "model.csv"
    assert run("data", "model", "draft", data, "--output", target) == 0
    assert target.is_file()
    assert not (data / "data_model.csv").exists()


def test_draft_never_overwrites_a_model_unless_told_to(
    data: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (data / "data_model.csv").write_text("kept", encoding="utf-8")
    assert run("data", "model", "draft", data) == 1
    err = capsys.readouterr().err
    assert "already exists" in err
    assert "ac2 data model diff" in err
    assert (data / "data_model.csv").read_text(encoding="utf-8") == "kept"
    assert run("data", "model", "draft", data, "--force") == 0
    assert (data / "data_model.csv").read_text(encoding="utf-8") != "kept"


def test_draft_of_an_empty_folder_is_an_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run("data", "model", "draft", tmp_path) == 1
    assert "no CSV files" in capsys.readouterr().err


def test_draft_of_a_missing_folder_is_an_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run("data", "model", "draft", tmp_path / "nothing") == 1
    assert "folder" in capsys.readouterr().err


# --- check --------------------------------------------------------------------------------


def test_check_fails_on_a_fresh_draft_and_passes_once_it_is_filled_in(
    data: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run("data", "model", "draft", data)
    capsys.readouterr()
    assert run("data", "model", "check", data) == 1
    assert "placeholder" in capsys.readouterr().out
    finish(data)
    assert run("data", "model", "check", data) == 0
    assert "follows the standard" in capsys.readouterr().out


def test_check_without_a_model_says_how_to_make_one(
    data: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run("data", "model", "check", data) == 1
    assert "ac2 data model draft" in capsys.readouterr().err


def test_check_reads_a_model_from_anywhere(data: Path, tmp_path: Path) -> None:
    run("data", "model", "draft", data, "--output", tmp_path / "m.csv")
    assert run("data", "model", "check", data, "--model", tmp_path / "m.csv") == 1


def test_check_refuses_a_file_with_the_wrong_columns(
    data: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (data / "data_model.csv").write_text(
        "domain,table,column\ncrm,accounts,uen\n", encoding="utf-8"
    )
    assert run("data", "model", "check", data) == 1
    assert "table_description" in capsys.readouterr().err


# --- diff ---------------------------------------------------------------------------------


def test_diff_of_a_model_that_matches_exits_cleanly(
    data: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run("data", "model", "draft", data)
    finish(data)
    capsys.readouterr()
    assert run("data", "model", "diff", data) == 0
    assert "no differences" in capsys.readouterr().out


def test_diff_lists_each_difference_by_table_and_exits_one(
    data: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run("data", "model", "draft", data)
    finish(data)
    (data / "claims.csv").write_text(CLAIMS + "9004,A-9,5.0\n", encoding="utf-8")
    (data / "extra.csv").write_text("id\n1\n", encoding="utf-8")
    capsys.readouterr()
    assert run("data", "model", "diff", data) == 1
    out = capsys.readouterr().out
    assert "extra" in out
    assert "table_not_in_model" in out
    assert "claims.uen" in out
    assert "foreign_key" in out
    assert "differences" in out.splitlines()[-1] or "difference" in out.splitlines()[-1]


def test_diff_prints_notes_apart_from_differences(
    data: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run("data", "model", "draft", data)
    finish(data)
    rows = read_data_model(data / "data_model.csv")
    for row in rows:
        if row["table"] == "claims" and row["column"] == "uen":
            row["fk"] = ""
    from agentcraft2_cli.data_model_draft import write_model

    write_model(rows, data / "data_model.csv")
    capsys.readouterr()
    assert run("data", "model", "diff", data) == 0
    out = capsys.readouterr().out
    assert "Notes" in out
    assert "foreign_key_candidate" in out


def test_diff_without_a_model_says_how_to_make_one(
    data: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run("data", "model", "diff", data) == 1
    assert "ac2 data model draft" in capsys.readouterr().err


def test_a_draft_compared_with_its_own_data_has_no_differences(data: Path) -> None:
    run("data", "model", "draft", data)
    from agentcraft2_cli.data_model_diff import compare_model

    found = compare_model(read_data_model(data / "data_model.csv"), data)
    assert [d for d in found if d.level == "difference"] == []
    assert check_data_model(read_data_model(data / "data_model.csv"), data)
