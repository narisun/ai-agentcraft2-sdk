"""The data model standard: what a data_model.csv must say about every table and column."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from agentcraft2_cli.data_model import (
    COLUMNS,
    DATATYPES,
    Issue,
    check_data_model,
    read_data_model,
    tables_of,
)

TABLE_TEXT = "Accounts of the bank's clients, one row per client."


def row(**changes: str) -> dict[str, str]:
    base = {
        "domain": "crm",
        "table": "accounts",
        "table_description": TABLE_TEXT,
        "column": "uen",
        "datatype": "string",
        "nullable": "N",
        "pk": "Y",
        "uk": "",
        "fk": "",
        "description": "The unique entity number that identifies a client.",
    }
    return {**base, **changes}


def model(*extra: dict[str, str]) -> list[dict[str, str]]:
    return [
        row(),
        row(column="name", pk="", nullable="N", description="The client's legal name."),
        *extra,
    ]


def messages(issues: list[Issue], level: str = "error") -> list[str]:
    return [f"{i.where}: {i.message}" for i in issues if i.level == level]


# --- a model that follows the standard ----------------------------------------------------


def test_a_model_that_follows_the_standard_has_no_issues() -> None:
    assert check_data_model(model()) == []


def test_the_standard_names_its_columns_in_order() -> None:
    assert COLUMNS == (
        "domain",
        "table",
        "table_description",
        "column",
        "datatype",
        "nullable",
        "pk",
        "uk",
        "fk",
        "description",
    )
    assert {"string", "integer", "decimal", "boolean", "date", "timestamp"} == set(DATATYPES)


# --- required information -----------------------------------------------------------------


@pytest.mark.parametrize(
    "field",
    ["domain", "table", "table_description", "column", "datatype", "nullable", "description"],
)
def test_every_required_field_must_be_filled(field: str) -> None:
    rows = model()
    rows[1][field] = "  "
    assert any(field in m for m in messages(check_data_model(rows)))


def test_a_table_without_a_description_is_named_in_the_error() -> None:
    rows = [row(table_description=""), row(table_description="", column="name", pk="")]
    errors = messages(check_data_model(rows))
    assert any("accounts" in m and "table_description" in m for m in errors)


def test_every_row_of_a_table_must_carry_the_same_description() -> None:
    rows = model()
    rows[1]["table_description"] = "Something else entirely, written once more."
    assert any("same table_description" in m for m in messages(check_data_model(rows)))


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        ("Too short.", "at least 20"),
        ("x" * 301 + ".", "at most 300"),
        ("Accounts of the bank's clients, one row per client", "end with a full stop"),
        ("accounts", "at least 20"),
    ],
)
def test_a_table_description_has_a_useful_length_and_ends_in_a_full_stop(
    text: str, problem: str
) -> None:
    rows = [row(table_description=text), row(table_description=text, column="name", pk="")]
    assert any(problem in m for m in messages(check_data_model(rows)))


def test_a_table_description_must_say_more_than_the_table_name() -> None:
    text = "Accounts accounts accounts accounts."
    rows = [row(table_description=text), row(table_description=text, column="name", pk="")]
    assert any("more than the table name" in m for m in messages(check_data_model(rows)))


# --- values -------------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["Accounts", "has space", "1st", "a-b", ""])
def test_names_are_lower_case_words_joined_by_underscores(name: str) -> None:
    rows = [row(column=name), *model()[1:]]
    assert messages(check_data_model(rows))


def test_a_datatype_outside_the_standard_is_an_error() -> None:
    rows = model()
    rows[1]["datatype"] = "varchar"
    assert any("datatype" in m and "varchar" in m for m in messages(check_data_model(rows)))


@pytest.mark.parametrize(("field", "value"), [("nullable", "Yes"), ("pk", "yes"), ("uk", "1")])
def test_flags_are_y_or_blank(field: str, value: str) -> None:
    rows = model()
    rows[1][field] = value
    assert any(field in m for m in messages(check_data_model(rows)))


def test_nullable_is_y_or_n_never_blank() -> None:
    rows = model()
    rows[1]["nullable"] = ""
    assert any("nullable" in m for m in messages(check_data_model(rows)))


# --- keys and structure -------------------------------------------------------------------


def test_every_table_has_a_primary_key() -> None:
    rows = [row(pk=""), row(column="name", pk="")]
    assert any("primary key" in m for m in messages(check_data_model(rows)))


def test_a_primary_key_column_cannot_be_nullable() -> None:
    rows = [row(nullable="Y"), row(column="name", pk="")]
    assert any("primary key" in m and "nullable" in m for m in messages(check_data_model(rows)))


def test_a_column_appears_once_in_a_table() -> None:
    rows = model(row(column="name", pk=""))
    assert any("more than once" in m for m in messages(check_data_model(rows)))


def test_a_table_belongs_to_one_domain() -> None:
    rows = model(row(domain="other", column="extra", pk=""))
    assert any("one domain" in m for m in messages(check_data_model(rows)))


def test_a_table_name_is_used_by_one_table_only() -> None:
    rows = model(row(domain="other", column="uen"), row(domain="other", column="name", pk=""))
    assert messages(check_data_model(rows))


# --- foreign keys -------------------------------------------------------------------------


def test_a_foreign_key_names_an_existing_column() -> None:
    contacts = [
        row(table="contacts", column="uen", fk="crm.accounts.uen"),
        row(table="contacts", column="email", pk="", description="The contact's address."),
    ]
    assert messages(check_data_model(model(*contacts))) == []


def test_a_foreign_key_may_carry_a_note_in_brackets() -> None:
    contacts = [
        row(table="contacts", column="uen", fk="crm.accounts.uen (each entry; may be outside)"),
    ]
    assert messages(check_data_model(model(*contacts))) == []


@pytest.mark.parametrize("fk", ["crm.accounts.nothing", "crm.nothing.uen", "other.accounts.uen"])
def test_a_foreign_key_to_something_that_does_not_exist_is_an_error(fk: str) -> None:
    contacts = [row(table="contacts", column="uen", fk=fk)]
    assert any("fk" in m for m in messages(check_data_model(model(*contacts))))


def test_a_foreign_key_to_a_table_without_a_column_is_a_warning_not_an_error() -> None:
    contacts = [row(table="contacts", column="uen", fk="crm.accounts (consistency)")]
    issues = check_data_model(model(*contacts))
    assert messages(issues) == []
    assert any("no column" in m for m in messages(issues, "warning"))


def test_a_measure_in_a_primary_key_is_a_warning() -> None:
    rows = [
        row(),
        row(column="amount", datatype="decimal", pk="Y", description="An amount of money."),
    ]
    issues = check_data_model(rows)
    assert messages(issues) == []
    assert any("amount" in m and "decimal" in m for m in messages(issues, "warning"))


def test_a_very_short_column_description_is_a_warning() -> None:
    rows = model()
    rows[1]["description"] = "Name."
    assert any("name" in m for m in messages(check_data_model(rows), "warning"))


# --- against the data folder --------------------------------------------------------------


def write_csv(path: Path, header: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        csv.writer(handle).writerow(header)


def test_the_tables_and_columns_must_match_the_csv_files(tmp_path: Path) -> None:
    write_csv(tmp_path / "accounts.csv", ["uen", "name"])
    assert check_data_model(model(), tmp_path) == []


def test_a_table_without_a_file_is_an_error(tmp_path: Path) -> None:
    assert any("accounts.csv" in m for m in messages(check_data_model(model(), tmp_path)))


def test_a_file_without_a_table_is_an_error(tmp_path: Path) -> None:
    write_csv(tmp_path / "accounts.csv", ["uen", "name"])
    write_csv(tmp_path / "orphan.csv", ["a"])
    assert any("orphan" in m for m in messages(check_data_model(model(), tmp_path)))


def test_a_file_may_be_the_model_itself(tmp_path: Path) -> None:
    write_csv(tmp_path / "accounts.csv", ["uen", "name"])
    write_csv(tmp_path / "data_model.csv", list(COLUMNS))
    assert check_data_model(model(), tmp_path) == []


@pytest.mark.parametrize("header", [["uen"], ["uen", "name", "extra"], ["name", "uen"]])
def test_columns_must_match_the_file_header_in_name_and_order(
    tmp_path: Path, header: list[str]
) -> None:
    write_csv(tmp_path / "accounts.csv", header)
    assert any("header" in m for m in messages(check_data_model(model(), tmp_path)))


# --- reading ------------------------------------------------------------------------------


def test_a_file_with_other_columns_is_rejected_with_the_expected_header(tmp_path: Path) -> None:
    path = tmp_path / "data_model.csv"
    write_csv(path, ["domain", "table", "column"])
    with pytest.raises(ValueError, match="table_description"):
        read_data_model(path)


def test_tables_are_grouped_in_file_order() -> None:
    rows = model(row(table="contacts", column="uen", pk="Y"))
    grouped = tables_of(rows)
    assert [(t.domain, t.name, len(t.columns)) for t in grouped] == [
        ("crm", "accounts", 2),
        ("crm", "contacts", 1),
    ]
    assert grouped[0].description == TABLE_TEXT


# --- placeholders -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("domain", "todo"),
        ("table_description", "TODO: describe this table. It has 4 rows and 8 columns."),
        ("description", "TODO: describe this column."),
    ],
)
def test_a_placeholder_from_a_draft_is_an_error_until_it_is_replaced(
    field: str, value: str
) -> None:
    rows = model()
    for each in rows:
        each[field] = value
    assert any("placeholder" in m for m in messages(check_data_model(rows)))
