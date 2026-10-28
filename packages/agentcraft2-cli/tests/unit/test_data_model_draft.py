"""A draft data_model.csv inferred from a folder of CSV files."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentcraft2_cli.data_model import COLUMNS, check_data_model, read_data_model
from agentcraft2_cli.data_model_draft import draft_model, write_model
from agentcraft2_cli.errors import CliError

ACCOUNTS = """\
uen,name,segment,employees,revenue,founded,active,zip
A-1,Alpha,Retail,10,1500.50,2001-02-03,Yes,01234
A-2,Beta,Retail,20,200.00,1999-12-31,No,02345
A-3,Gamma,Business,30,3000.75,2010-06-15,Yes,03456
A-4,Delta,Business,40,40.10,2015-01-01,Yes,04567
"""
CLAIMS = """\
claim_id,uen,status,amount,opened,note
9001,A-1,open,1200.50,2026-01-04,
9002,A-2,closed,80.00,2026-01-09,late
9003,A-1,open,455.10,2026-02-11,
9004,A-3,review,9900.00,2026-03-02,
"""


def folder(tmp_path: Path, **tables: str) -> Path:
    for name, text in tables.items():
        (tmp_path / f"{name}.csv").write_text(text, encoding="utf-8")
    return tmp_path


def draft(tmp_path: Path, **tables: str) -> dict[str, dict[str, dict[str, str]]]:
    """The draft as {table: {column: row}}."""
    rows = draft_model(folder(tmp_path, **tables))
    result: dict[str, dict[str, dict[str, str]]] = {}
    for row in rows:
        result.setdefault(row["table"], {})[row["column"]] = row
    return result


# --- the shape of the draft ---------------------------------------------------------------


def test_the_draft_has_one_row_per_column_in_file_order(tmp_path: Path) -> None:
    rows = draft_model(folder(tmp_path, accounts=ACCOUNTS, claims=CLAIMS))
    assert [(r["table"], r["column"]) for r in rows[:3]] == [
        ("accounts", "uen"),
        ("accounts", "name"),
        ("accounts", "segment"),
    ]
    assert len(rows) == 8 + 6
    assert all(tuple(r) == COLUMNS for r in rows)


def test_tables_follow_the_file_names_and_the_model_itself_is_left_out(tmp_path: Path) -> None:
    tables = folder(tmp_path, claims=CLAIMS, accounts=ACCOUNTS)
    (tables / "data_model.csv").write_text(",".join(COLUMNS) + "\n", encoding="utf-8")
    (tables / "notes.txt").write_text("not a table", encoding="utf-8")
    assert next(r["table"] for r in draft_model(tables)) == "accounts"
    assert {r["table"] for r in draft_model(tables)} == {"accounts", "claims"}


def test_what_cannot_be_inferred_is_a_marked_placeholder(tmp_path: Path) -> None:
    rows = draft(tmp_path, accounts=ACCOUNTS)["accounts"]
    for row in rows.values():
        assert row["domain"] == "todo"
        assert row["table_description"].startswith("TODO")
        assert row["description"].startswith("TODO")
        assert row["uk"] == ""
    assert len({r["table_description"] for r in rows.values()}) == 1


def test_a_byte_order_mark_and_a_missing_final_newline_are_read(tmp_path: Path) -> None:
    (tmp_path / "t.csv").write_bytes("﻿id,x\n1,a\n2,b".encode())
    rows = draft_model(tmp_path)
    assert [r["column"] for r in rows] == ["id", "x"]


# --- data types ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("column", "expected"),
    [
        ("employees", "integer"),
        ("revenue", "decimal"),
        ("founded", "date"),
        ("active", "boolean"),
        ("zip", "string"),
        ("uen", "string"),
        ("name", "string"),
    ],
)
def test_the_type_is_the_narrowest_one_every_value_fits(
    tmp_path: Path, column: str, expected: str
) -> None:
    assert draft(tmp_path, accounts=ACCOUNTS)["accounts"][column]["datatype"] == expected


def test_a_timestamp_is_not_a_date(tmp_path: Path) -> None:
    text = "id,at\n1,2026-01-01T10:00:00\n2,2026-01-02 11:30:00\n"
    assert draft(tmp_path, t=text)["t"]["at"]["datatype"] == "timestamp"


def test_whole_numbers_and_decimals_together_are_decimal(tmp_path: Path) -> None:
    assert draft(tmp_path, t="id,v\n1,10\n2,2.5\n")["t"]["v"]["datatype"] == "decimal"


def test_mixed_values_are_a_string(tmp_path: Path) -> None:
    assert draft(tmp_path, t="id,v\n1,10\n2,abc\n")["t"]["v"]["datatype"] == "string"
    assert draft(tmp_path, t="id,v\n1,10\n2,2026-01-01\n")["t"]["v"]["datatype"] == "string"


def test_a_number_with_a_leading_zero_is_an_identifier_not_a_number(tmp_path: Path) -> None:
    assert draft(tmp_path, t="id,v\n1,007\n2,010\n")["t"]["v"]["datatype"] == "string"


def test_a_column_with_no_values_is_a_nullable_string(tmp_path: Path) -> None:
    row = draft(tmp_path, t="id,v\n1,\n2,\n")["t"]["v"]
    assert (row["datatype"], row["nullable"]) == ("string", "Y")


# --- nullable -----------------------------------------------------------------------------


def test_only_a_key_column_is_drafted_as_never_empty(tmp_path: Path) -> None:
    """Never empty in these rows is evidence, not a promise, so only a key claims it."""
    rows = draft(tmp_path, claims=CLAIMS)["claims"]
    assert rows["claim_id"]["nullable"] == "N"
    assert rows["status"]["nullable"] == "Y"
    assert rows["note"]["nullable"] == "Y"


def test_a_column_never_empty_in_the_rows_says_so_for_the_reviewer(tmp_path: Path) -> None:
    rows = draft(tmp_path, claims=CLAIMS)["claims"]
    assert "Never empty in the 4 rows." in rows["amount"]["description"]
    assert "Never empty" not in rows["note"]["description"]
    assert "Never empty" not in rows["claim_id"]["description"]


def test_a_table_with_no_rows_has_nullable_columns(tmp_path: Path) -> None:
    assert draft(tmp_path, t="id,v\n")["t"]["id"]["nullable"] == "Y"


# --- primary keys -------------------------------------------------------------------------


def test_a_unique_column_named_like_an_identifier_is_the_key(tmp_path: Path) -> None:
    rows = draft(tmp_path, claims=CLAIMS)["claims"]
    assert [c for c, r in rows.items() if r["pk"] == "Y"] == ["claim_id"]


def test_without_such_a_name_the_first_unique_column_is_the_key(tmp_path: Path) -> None:
    rows = draft(tmp_path, accounts=ACCOUNTS)["accounts"]
    assert [c for c, r in rows.items() if r["pk"] == "Y"] == ["uen"]


def test_a_composite_key_is_found_when_no_single_column_is_unique(tmp_path: Path) -> None:
    text = (
        "uen,as_of,account,balance\n"
        "A,2026-01,op,10.5\nA,2026-01,sv,3.0\nA,2026-02,op,11.0\nB,2026-01,op,7.7\n"
    )
    rows = draft(tmp_path, balances=text)["balances"]
    assert [c for c, r in rows.items() if r["pk"] == "Y"] == ["uen", "as_of", "account"]


def test_a_measure_is_never_part_of_a_key(tmp_path: Path) -> None:
    text = "g,amount\nx,1.5\nx,2.5\ny,3.5\n"
    rows = draft(tmp_path, t=text)["t"]
    assert all(r["pk"] == "" for r in rows.values())


def test_a_column_with_empty_values_is_never_part_of_a_key(tmp_path: Path) -> None:
    rows = draft(tmp_path, t="a,b\n1,\n2,x\n3,y\n")["t"]
    assert rows["a"]["pk"] == "Y"
    assert rows["b"]["pk"] == ""


def test_a_table_with_no_unique_combination_gets_no_key_and_the_check_says_so(
    tmp_path: Path,
) -> None:
    folder(tmp_path, t="a,b\nx,y\nx,y\n")
    rows = draft_model(tmp_path)
    assert all(r["pk"] == "" for r in rows)
    assert any("primary key" in str(i) for i in check_data_model(rows) if i.level == "error")


# --- foreign keys -------------------------------------------------------------------------


def test_a_column_whose_values_all_exist_in_another_tables_key_is_a_foreign_key(
    tmp_path: Path,
) -> None:
    rows = draft(tmp_path, accounts=ACCOUNTS, claims=CLAIMS)["claims"]
    assert rows["uen"]["fk"] == "todo.accounts.uen"
    assert rows["claim_id"]["fk"] == ""


def test_a_name_that_matches_but_a_value_that_does_not_is_not_a_foreign_key(
    tmp_path: Path,
) -> None:
    claims = CLAIMS.replace("A-3", "Z-9")
    assert draft(tmp_path, accounts=ACCOUNTS, claims=claims)["claims"]["uen"]["fk"] == ""


def test_a_tables_own_key_is_not_a_foreign_key_to_itself(tmp_path: Path) -> None:
    assert draft(tmp_path, accounts=ACCOUNTS)["accounts"]["uen"]["fk"] == ""


def test_empty_values_do_not_count_against_a_foreign_key(tmp_path: Path) -> None:
    claims = CLAIMS.replace("A-2", "")
    assert (
        draft(tmp_path, accounts=ACCOUNTS, claims=claims)["claims"]["uen"]["fk"]
        == "todo.accounts.uen"
    )


# --- joins when several tables share a key ----------------------------------------------


def test_the_widest_table_with_the_key_is_the_parent(tmp_path: Path) -> None:
    accounts = "uen,name,industry\nA-1,Alpha,x\nA-2,Beta,y\n"
    relationship = "uen,coverage,priority\nA-1,east,high\nA-2,west,low\n"
    credit = "uen,rating\nA-1,3\nA-2,4\n"
    rows = draft(tmp_path, accounts=accounts, relationship=relationship, credit=credit)
    assert rows["credit"]["uen"]["fk"] == "todo.accounts.uen"
    assert rows["relationship"]["uen"]["fk"] == "todo.accounts.uen"
    assert rows["accounts"]["uen"]["fk"] == ""


def test_a_key_column_can_be_a_foreign_key_too(tmp_path: Path) -> None:
    rows = draft(tmp_path, accounts=ACCOUNTS, extra="uen,score\nA-1,5\nA-2,6\n")
    assert rows["extra"]["uen"]["pk"] == "Y"
    assert rows["extra"]["uen"]["fk"] == "todo.accounts.uen"


def test_a_key_of_up_to_six_columns_is_found(tmp_path: Path) -> None:
    lines = ["a,b,c,d,e,f,v"]
    for index in range(64):
        bits = [str((index >> n) & 1) for n in range(6)]
        lines.append(",".join([*bits, "1.5"]))
    rows = draft(tmp_path, t="\n".join(lines) + "\n")["t"]
    assert [c for c, r in rows.items() if r["pk"] == "Y"] == ["a", "b", "c", "d", "e", "f"]


# --- hints for the reviewer ---------------------------------------------------------------


def test_a_column_with_a_few_repeated_values_lists_them(tmp_path: Path) -> None:
    rows = draft(tmp_path, claims=CLAIMS)["claims"]
    assert rows["status"]["description"].startswith(
        "TODO: describe this column. Values seen: closed | open | review."
    )


def test_an_identifier_column_lists_no_values(tmp_path: Path) -> None:
    rows = draft(tmp_path, accounts=ACCOUNTS)["accounts"]
    assert rows["uen"]["description"] == "TODO: describe this column."


# --- the draft against the standard -------------------------------------------------------


def test_the_only_errors_in_a_draft_are_the_placeholders_to_fill_in(tmp_path: Path) -> None:
    rows = draft_model(folder(tmp_path, accounts=ACCOUNTS, claims=CLAIMS))
    errors = [i for i in check_data_model(rows, tmp_path) if i.level == "error"]
    assert errors
    assert all("placeholder" in i.message for i in errors)


def test_a_draft_with_its_placeholders_filled_in_follows_the_standard(tmp_path: Path) -> None:
    rows = draft_model(folder(tmp_path, accounts=ACCOUNTS, claims=CLAIMS))
    for row in rows:
        row["domain"] = "crm"
        row["table_description"] = "Accounts of clients, one row per client entity."
        row["description"] = "What this column holds and how to read it."
        if row["fk"]:
            row["fk"] = row["fk"].replace("todo.", "crm.")
    assert [i for i in check_data_model(rows, tmp_path) if i.level == "error"] == []


# --- writing ------------------------------------------------------------------------------


def test_the_file_written_reads_back_to_the_same_rows(tmp_path: Path) -> None:
    rows = draft_model(folder(tmp_path, accounts=ACCOUNTS, claims=CLAIMS))
    target = tmp_path / "out" / "data_model.csv"
    write_model(rows, target)
    assert read_data_model(target) == rows


def test_a_folder_with_no_csv_files_is_a_clear_error(tmp_path: Path) -> None:
    with pytest.raises(CliError, match="no CSV files"):
        draft_model(tmp_path)
