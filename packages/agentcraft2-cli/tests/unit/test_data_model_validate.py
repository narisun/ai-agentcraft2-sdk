"""Cell-level validation of CSV files against the data model, run through DuckDB."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentcraft2_cli.data_model import COLUMNS
from agentcraft2_cli.data_model_validate import Finding, describe_load_mismatch, validate_data

Spec = tuple[str, str, str, str, str, str]  # column, datatype, nullable, pk, uk, fk


def model(table: str, *columns: Spec) -> list[dict[str, str]]:
    return [
        dict(
            zip(
                COLUMNS,
                (
                    "crm",
                    table,
                    "Rows of a table, described for the test.",
                    name,
                    kind,
                    null,
                    pk,
                    uk,
                    fk,
                    "A description.",
                ),
                strict=True,
            )
        )
        for name, kind, null, pk, uk, fk in columns
    ]


def write(tmp_path: Path, name: str, text: str) -> None:
    (tmp_path / f"{name}.csv").write_text(text, encoding="utf-8", newline="")


def one(kind: str, text: str, tmp_path: Path, show_values: bool = True) -> list[Finding]:
    """Validate a table `t` with a key `id` and one column `v` of the declared type."""
    columns = model("t", ("id", "string", "N", "Y", "", ""), ("v", kind, "Y", "", "", ""))
    write(tmp_path, "t", text)
    return validate_data(columns, tmp_path, show_values=show_values)


def errors(found: list[Finding], rule: str | None = None) -> list[Finding]:
    return [f for f in found if f.level == "error" and (rule is None or f.rule == rule)]


def warnings(found: list[Finding], rule: str | None = None) -> list[Finding]:
    return [f for f in found if f.level == "warning" and (rule is None or f.rule == rule)]


# --- a clean folder -----------------------------------------------------------------------


def test_a_folder_that_follows_its_model_has_no_findings(tmp_path: Path) -> None:
    columns = model(
        "t",
        ("id", "integer", "N", "Y", "", ""),
        ("amount", "decimal", "N", "", "", ""),
        ("opened", "date", "N", "", "", ""),
        ("at", "timestamp", "Y", "", "", ""),
        ("active", "boolean", "N", "", "", ""),
        ("name", "string", "N", "", "", ""),
    )
    write(
        tmp_path,
        "t",
        "id,amount,opened,at,active,name\n"
        "1,10.50,2026-01-02,2026-01-02 10:00:00,Yes,Alpha\n"
        "2,-3,2026-02-03,,No,Beta\n",
    )
    assert validate_data(columns, tmp_path) == []


# --- where a finding is -------------------------------------------------------------------


def test_a_finding_names_the_file_row_line_column_and_value(tmp_path: Path) -> None:
    found = one("integer", "id,v\na,1\nb,2\nc,x7\nd,4\n", tmp_path)
    (finding,) = errors(found, "type")
    assert finding.file == "t.csv"
    assert (finding.row, finding.line) == (3, 4)
    assert finding.column == "v"
    assert finding.value == "x7"
    assert "x7" in finding.message


def test_the_line_follows_a_quoted_value_that_spans_lines(tmp_path: Path) -> None:
    columns = model(
        "t",
        ("id", "string", "N", "Y", "", ""),
        ("note", "string", "Y", "", "", ""),
        ("v", "integer", "Y", "", "", ""),
    )
    write(tmp_path, "t", 'id,note,v\na,"two\nlines",1\nb,plain,oops\n')
    (finding,) = errors(validate_data(columns, tmp_path), "type")
    assert (finding.row, finding.line) == (2, 4)


def test_values_can_be_left_out_of_the_findings(tmp_path: Path) -> None:
    found = one("integer", "id,v\na,secret-123\n", tmp_path, show_values=False)
    (finding,) = errors(found, "type")
    assert finding.value is None
    assert "secret-123" not in finding.message


# --- integers and decimals ----------------------------------------------------------------


@pytest.mark.parametrize("value", ["12.5", "1,234", "abc", "1e3", "$5", "12%", "--4", "٣"])
def test_an_integer_column_holds_only_whole_numbers(tmp_path: Path, value: str) -> None:
    found = one("integer", f'id,v\na,"{value}"\n', tmp_path)
    assert [f.value for f in errors(found, "type")] == [value]


@pytest.mark.parametrize("value", ["0", "7", "-3", "+4", "007"])
def test_whole_numbers_are_integers(tmp_path: Path, value: str) -> None:
    assert errors(one("integer", f"id,v\na,{value}\n", tmp_path)) == []


@pytest.mark.parametrize(
    ("value", "hint"),
    [
        ("$1,234.50", "currency"),
        ("1,234.50", "thousands separator"),
        ("12%", "percent"),
        ("(5.00)", "brackets"),
        ("1.2.3", "more than one decimal point"),
        ("N/A", "missing-value"),
        ("abc", "not a number"),
    ],
)
def test_a_decimal_column_rejects_formatted_numbers_and_says_why(
    tmp_path: Path, value: str, hint: str
) -> None:
    found = one("decimal", f'id,v\na,"{value}"\n', tmp_path)
    (finding,) = errors(found, "type")
    assert hint in finding.message


@pytest.mark.parametrize("value", ["0", "5", "-3.20", ".5", "10.", "+1.5"])
def test_plain_numbers_are_decimals(tmp_path: Path, value: str) -> None:
    assert errors(one("decimal", f"id,v\na,{value}\n", tmp_path)) == []


# --- booleans -----------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["maybe", "2", "yep", "T"])
def test_a_boolean_column_rejects_other_words(tmp_path: Path, value: str) -> None:
    found = one("boolean", f"id,v\na,{value}\n", tmp_path)
    assert [f.value for f in errors(found, "type")] == [value]


@pytest.mark.parametrize("value", ["Yes", "no", "Y", "n", "TRUE", "false", "1", "0"])
def test_the_usual_spellings_are_booleans(tmp_path: Path, value: str) -> None:
    assert errors(one("boolean", f"id,v\na,{value}\n", tmp_path)) == []


def test_a_column_that_spells_its_booleans_several_ways_is_a_warning(tmp_path: Path) -> None:
    found = one("boolean", "id,v\na,Yes\nb,Y\nc,true\nd,No\ne,N\n", tmp_path)
    assert errors(found) == []
    (finding,) = warnings(found, "mixed")
    assert (finding.row, finding.line) == (None, None)
    assert finding.column == "v"
    for spelling in ("Yes", "Y", "true"):
        assert spelling in finding.message


def test_one_spelling_for_each_answer_is_consistent(tmp_path: Path) -> None:
    assert warnings(one("boolean", "id,v\na,Yes\nb,No\nc,Yes\n", tmp_path), "mixed") == []


# --- dates and times ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "hint"),
    [
        ("03/04/2026", "day and month order"),
        ("2026/03/04", "YYYY-MM-DD"),
        ("2026-3-4", "YYYY-MM-DD"),
        ("20260304", "YYYY-MM-DD"),
        ("4 Mar 2026", "YYYY-MM-DD"),
        ("2026-02-30", "not a real date"),
        ("2026-13-01", "not a real date"),
        ("2026-01-02 10:00:00", "has a time"),
    ],
)
def test_a_date_column_takes_only_iso_dates_and_says_what_is_wrong(
    tmp_path: Path, value: str, hint: str
) -> None:
    found = one("date", f'id,v\na,"{value}"\n', tmp_path)
    (finding,) = errors(found, "type")
    assert hint in finding.message


def test_a_leap_day_is_a_date(tmp_path: Path) -> None:
    assert errors(one("date", "id,v\na,2028-02-29\nb,2026-12-31\n", tmp_path)) == []
    assert len(errors(one("date", "id,v\na,2027-02-29\n", tmp_path), "type")) == 1


@pytest.mark.parametrize(
    "value",
    [
        "2026-01-02 10:00:00",
        "2026-01-02T10:00:00",
        "2026-01-02T10:00",
        "2026-01-02 10:00:00.123456",
        "2026-01-02T10:00:00Z",
        "2026-01-02T10:00:00+02:00",
        "2026-01-02",
    ],
)
def test_iso_timestamps_are_timestamps(tmp_path: Path, value: str) -> None:
    assert errors(one("timestamp", f"id,v\na,{value}\n", tmp_path)) == []


@pytest.mark.parametrize(
    "value", ["2026-01-02 25:00:00", "01/02/2026 10:00", "2026-01-02 10:61:00", "10:00:00", "now"]
)
def test_other_timestamps_are_errors(tmp_path: Path, value: str) -> None:
    assert [
        f.value for f in errors(one("timestamp", f'id,v\na,"{value}"\n', tmp_path), "type")
    ] == [value]


def test_timestamps_with_and_without_a_zone_in_one_column_are_a_warning(tmp_path: Path) -> None:
    found = one("timestamp", "id,v\na,2026-01-02T10:00:00Z\nb,2026-01-02 11:00:00\n", tmp_path)
    assert errors(found) == []
    (finding,) = warnings(found, "mixed")
    assert "time zone" in finding.message


# --- strings ------------------------------------------------------------------------------


def test_a_string_column_accepts_anything(tmp_path: Path) -> None:
    assert one("string", 'id,v\na,"1,234"\nb,03/04/2026\nc,maybe\n', tmp_path) == []


# --- empty and padded values --------------------------------------------------------------


def test_an_empty_value_in_a_column_that_is_never_empty_is_an_error(tmp_path: Path) -> None:
    columns = model("t", ("id", "string", "N", "Y", "", ""), ("v", "string", "N", "", "", ""))
    write(tmp_path, "t", "id,v\na,x\nb,\nc,   \n")
    found = errors(validate_data(columns, tmp_path), "empty")
    assert [(f.row, f.line, f.column) for f in found] == [(2, 3, "v"), (3, 4, "v")]


def test_an_empty_value_where_the_model_allows_it_is_fine(tmp_path: Path) -> None:
    assert one("integer", "id,v\na,\nb,\n", tmp_path) == []


def test_a_padded_value_is_a_warning_and_is_still_checked_for_its_type(tmp_path: Path) -> None:
    found = one("integer", "id,v\na, 12\nb,13 \nc,x \n", tmp_path)
    assert [(f.row, f.column) for f in warnings(found, "whitespace")] == [
        (1, "v"),
        (2, "v"),
        (3, "v"),
    ]
    assert [f.value for f in errors(found, "type")] == ["x"]


def test_padding_in_a_string_column_is_a_warning(tmp_path: Path) -> None:
    found = one("string", "id,v\na, Alpha\n", tmp_path)
    assert [(f.rule, f.row) for f in found] == [("whitespace", 1)]


# --- keys ---------------------------------------------------------------------------------


def test_a_repeated_primary_key_points_at_the_first_row_that_has_it(tmp_path: Path) -> None:
    columns = model("t", ("id", "string", "N", "Y", "", ""), ("v", "string", "Y", "", "", ""))
    write(tmp_path, "t", "id,v\na,1\nb,2\na,3\n")
    (finding,) = errors(validate_data(columns, tmp_path), "duplicate")
    assert (finding.row, finding.line, finding.column) == (3, 4, "id")
    assert "row 1" in finding.message


def test_a_composite_primary_key_is_checked_as_a_whole(tmp_path: Path) -> None:
    columns = model("t", ("a", "string", "N", "Y", "", ""), ("b", "string", "N", "Y", "", ""))
    write(tmp_path, "t", "a,b\nx,1\nx,2\ny,1\nx,2\n")
    (finding,) = errors(validate_data(columns, tmp_path), "duplicate")
    assert finding.row == 4
    assert finding.column == "a, b"


def test_a_unique_column_with_a_repeat_is_an_error_but_empties_do_not_repeat(
    tmp_path: Path,
) -> None:
    columns = model("t", ("id", "string", "N", "Y", "", ""), ("code", "string", "Y", "", "Y", ""))
    write(tmp_path, "t", "id,code\na,x\nb,\nc,\nd,x\n")
    found = errors(validate_data(columns, tmp_path), "duplicate")
    assert [(f.row, f.column) for f in found] == [(4, "code")]


# --- joins --------------------------------------------------------------------------------


def accounts_and_claims(tmp_path: Path, fk: str = "crm.accounts.uen") -> list[dict[str, str]]:
    write(tmp_path, "accounts", "uen\nA-1\nA-2\n")
    return model("accounts", ("uen", "string", "N", "Y", "", "")) + model(
        "claims", ("id", "string", "N", "Y", "", ""), ("uen", "string", "Y", "", "", fk)
    )


def test_a_foreign_key_value_with_no_match_is_reported_at_its_row(tmp_path: Path) -> None:
    columns = accounts_and_claims(tmp_path)
    write(tmp_path, "claims", "id,uen\n1,A-1\n2,Z-9\n3,\n4,A-2\n")
    (finding,) = errors(validate_data(columns, tmp_path), "orphan")
    assert (finding.file, finding.row, finding.line, finding.column) == ("claims.csv", 2, 3, "uen")
    assert "accounts.uen" in finding.message


def test_a_join_the_model_qualifies_in_brackets_is_not_checked_row_by_row(tmp_path: Path) -> None:
    """A reconciliation or a soft reference is not promised to match; diff counts its misses."""
    columns = accounts_and_claims(tmp_path, "crm.accounts.uen (soft reference)")
    write(tmp_path, "claims", "id,uen\n1,Z-9\n")
    assert [f for f in validate_data(columns, tmp_path) if f.rule == "orphan"] == []


def test_a_foreign_key_to_a_table_that_is_not_there_is_skipped(tmp_path: Path) -> None:
    columns = accounts_and_claims(tmp_path)
    (tmp_path / "accounts.csv").unlink()
    write(tmp_path, "claims", "id,uen\n1,Z-9\n")
    assert errors(validate_data(columns, tmp_path), "orphan") == []


# --- the files themselves -----------------------------------------------------------------


def test_a_row_with_too_few_or_too_many_fields_is_reported_at_its_line(tmp_path: Path) -> None:
    columns = model(
        "t",
        ("id", "string", "N", "Y", "", ""),
        ("a", "string", "Y", "", "", ""),
        ("v", "integer", "Y", "", "", ""),
    )
    write(tmp_path, "t", "id,a,v\n1,x,10\n2,y\n3,z,30,extra\n4,w,oops\n")
    found = errors(validate_data(columns, tmp_path), "structure")
    assert sorted(f.line or 0 for f in found) == [3, 4]
    assert all(f.file == "t.csv" and "3" in f.message for f in found)


def test_cells_after_a_malformed_row_are_still_checked_with_the_right_row_and_line(
    tmp_path: Path,
) -> None:
    columns = model(
        "t",
        ("id", "string", "N", "Y", "", ""),
        ("a", "string", "Y", "", "", ""),
        ("v", "integer", "Y", "", "", ""),
    )
    write(tmp_path, "t", "id,a,v\n1,x,10\n2,y\n3,z,30,extra\n4,w,oops\n")
    (finding,) = errors(validate_data(columns, tmp_path), "type")
    assert (finding.row, finding.line, finding.value) == (4, 5, "oops")


def test_a_file_duckdb_cannot_read_is_reported_as_such(tmp_path: Path) -> None:
    columns = model("t", ("id", "string", "N", "Y", "", ""), ("v", "string", "Y", "", "", ""))
    (tmp_path / "t.csv").write_bytes(b"id,v\n1,\xff\xfe\n")
    found = validate_data(columns, tmp_path)
    assert any(f.file == "t.csv" and f.rule in ("load", "structure") for f in errors(found))


def test_a_file_with_no_header_row_is_reported(tmp_path: Path) -> None:
    columns = model("t", ("id", "string", "N", "Y", "", ""))
    (tmp_path / "t.csv").write_text("", encoding="utf-8")
    assert any(f.rule == "load" for f in errors(validate_data(columns, tmp_path)))


def test_a_column_the_model_has_and_the_file_lacks_is_an_error(tmp_path: Path) -> None:
    columns = model("t", ("id", "string", "N", "Y", "", ""), ("v", "integer", "Y", "", "", ""))
    write(tmp_path, "t", "id\na\n")
    (finding,) = errors(validate_data(columns, tmp_path), "columns")
    assert (finding.file, finding.column) == ("t.csv", "v")


def test_a_file_with_no_table_in_the_model_is_a_warning_and_is_not_validated(
    tmp_path: Path,
) -> None:
    columns = model("t", ("id", "string", "N", "Y", "", ""))
    write(tmp_path, "t", "id\na\n")
    write(tmp_path, "extra", "x\n1\n")
    found = validate_data(columns, tmp_path)
    assert [(f.file, f.rule, f.level) for f in found] == [("extra.csv", "columns", "warning")]


def test_only_the_named_tables_are_validated(tmp_path: Path) -> None:
    columns = model("t", ("id", "string", "N", "Y", "", ""), ("v", "integer", "Y", "", "", ""))
    write(tmp_path, "t", "id,v\na,x\n")
    assert validate_data(columns, tmp_path, tables=["other"]) == []
    assert errors(validate_data(columns, tmp_path, tables=["t"]), "type")


# --- what DuckDB would load ---------------------------------------------------------------


def test_a_column_the_model_calls_text_that_duckdb_loads_as_a_date_is_a_warning(
    tmp_path: Path,
) -> None:
    found = one("string", "id,v\na,2026-01-02\nb,2026-01-03\n", tmp_path)
    (finding,) = warnings(found, "runtime_type")
    assert finding.column == "v"
    assert "DATE" in finding.message
    assert (finding.row, finding.line) == (None, None)


def test_a_column_the_model_types_that_duckdb_loads_as_text_points_at_the_cause(
    tmp_path: Path,
) -> None:
    found = one("integer", "id,v\na,1\nb,x\n", tmp_path)
    assert len(errors(found, "type")) == 1
    (finding,) = warnings(found, "runtime_type")
    assert "VARCHAR" in finding.message


def test_a_column_that_loads_as_the_type_the_model_declares_is_quiet(tmp_path: Path) -> None:
    assert one("integer", "id,v\na,1\nb,2\n", tmp_path) == []
    assert one("decimal", "id,v\na,1\nb,2.5\n", tmp_path) == []
    assert one("date", "id,v\na,2026-01-02\n", tmp_path) == []
    assert one("boolean", "id,v\na,Yes\nb,No\n", tmp_path) == []


# --- saying what DuckDB made of a file it misread ------------------------------------------


def test_a_file_read_as_one_column_says_the_delimiter_was_lost() -> None:
    message = describe_load_mismatch(["a,b,c"], ["a", "b", "c"])
    assert "single column" in message
    assert "delimiter" in message


def test_generated_column_names_say_the_header_was_not_recognised() -> None:
    message = describe_load_mismatch(["column0", "column1", "column2"], ["a", "b", "c"])
    assert "header row" in message
    assert "column0" in message


def test_other_differences_name_both_counts() -> None:
    message = describe_load_mismatch(["a", "b"], ["a", "b", "c"])
    assert "2 columns" in message
    assert "3" in message


def test_a_ragged_file_that_duckdb_misreads_is_a_load_error_that_names_the_header(
    tmp_path: Path,
) -> None:
    columns = model(
        "t",
        ("id", "string", "N", "Y", "", ""),
        ("a", "string", "Y", "", "", ""),
        ("v", "integer", "Y", "", "", ""),
    )
    write(tmp_path, "t", "id,a,v\n1,x,10\n2,y\n3,z,30,extra\n4,w,5\n")
    found = errors(validate_data(columns, tmp_path), "load")
    assert found
    assert all(f.row is None and "header" in f.message for f in found)
