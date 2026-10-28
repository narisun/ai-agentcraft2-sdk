"""Where the files in a data folder disagree with the data_model.csv that describes them."""

from __future__ import annotations

from pathlib import Path

from agentcraft2_cli.data_model import COLUMNS
from agentcraft2_cli.data_model_diff import Difference, compare_model

ACCOUNTS = """\
uen,name,employees
A-1,Alpha,10
A-2,Beta,20
A-3,Gamma,30
"""
CLAIMS = """\
claim_id,uen,amount,note
9001,A-1,10.5,
9002,A-2,20.0,late
9003,A-1,30.25,
"""

Spec = tuple[str, str, str, str, str, str]  # column, datatype, nullable, pk, uk, fk


def rows(table: str, *columns: Spec, domain: str = "crm") -> list[dict[str, str]]:
    return [
        dict(
            zip(
                COLUMNS,
                (
                    domain,
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


ACCOUNTS_MODEL = rows(
    "accounts",
    ("uen", "string", "N", "Y", "", ""),
    ("name", "string", "N", "", "", ""),
    ("employees", "integer", "N", "", "", ""),
)
CLAIMS_MODEL = rows(
    "claims",
    ("claim_id", "integer", "N", "Y", "", ""),
    ("uen", "string", "N", "", "", "crm.accounts.uen"),
    ("amount", "decimal", "N", "", "", ""),
    ("note", "string", "Y", "", "", ""),
)


def folder(tmp_path: Path, **tables: str) -> Path:
    for name, text in tables.items():
        (tmp_path / f"{name}.csv").write_text(text, encoding="utf-8")
    return tmp_path


def compare(tmp_path: Path, model: list[dict[str, str]], **tables: str) -> list[Difference]:
    return compare_model(model, folder(tmp_path, **tables))


def kinds(found: list[Difference], level: str = "difference") -> set[tuple[str, str]]:
    return {(d.kind, d.where) for d in found if d.level == level}


# --- a model that matches -----------------------------------------------------------------


def test_a_model_that_matches_its_files_has_no_differences(tmp_path: Path) -> None:
    found = compare(tmp_path, ACCOUNTS_MODEL + CLAIMS_MODEL, accounts=ACCOUNTS, claims=CLAIMS)
    assert [d for d in found if d.level == "difference"] == []


# --- tables and columns -------------------------------------------------------------------


def test_a_file_with_no_table_in_the_model(tmp_path: Path) -> None:
    found = compare(tmp_path, ACCOUNTS_MODEL, accounts=ACCOUNTS, claims=CLAIMS)
    assert ("table_not_in_model", "claims") in kinds(found)


def test_a_table_in_the_model_with_no_file(tmp_path: Path) -> None:
    found = compare(tmp_path, ACCOUNTS_MODEL + CLAIMS_MODEL, accounts=ACCOUNTS)
    assert ("table_not_in_files", "claims") in kinds(found)


def test_a_column_only_in_the_file_and_one_only_in_the_model(tmp_path: Path) -> None:
    text = "uen,name,employees,region\nA-1,Alpha,10,N\nA-2,Beta,20,S\nA-3,Gamma,30,N\n"
    found = compare(tmp_path, ACCOUNTS_MODEL, accounts=text)
    assert ("column_not_in_model", "accounts.region") in kinds(found)
    smaller = "uen,name\nA-1,Alpha\nA-2,Beta\nA-3,Gamma\n"
    found = compare(tmp_path, ACCOUNTS_MODEL, accounts=smaller)
    assert ("column_not_in_files", "accounts.employees") in kinds(found)


def test_the_same_columns_in_another_order(tmp_path: Path) -> None:
    text = "name,uen,employees\nAlpha,A-1,10\nBeta,A-2,20\nGamma,A-3,30\n"
    assert ("column_order", "accounts") in kinds(compare(tmp_path, ACCOUNTS_MODEL, accounts=text))


# --- data types ---------------------------------------------------------------------------


def test_a_type_the_data_does_not_fit_is_a_difference(tmp_path: Path) -> None:
    text = ACCOUNTS.replace("10", "ten")
    found = compare(tmp_path, ACCOUNTS_MODEL, accounts=text)
    assert ("datatype", "accounts.employees") in kinds(found)
    message = next(d.message for d in found if d.kind == "datatype")
    assert "integer" in message
    assert "string" in message


def test_whole_numbers_in_a_decimal_column_are_expected(tmp_path: Path) -> None:
    model = rows("t", ("id", "string", "N", "Y", "", ""), ("v", "decimal", "N", "", "", ""))
    assert kinds(compare(tmp_path, model, t="id,v\na,1\nb,2\n")) == set()


def test_a_string_column_that_holds_decimals_is_only_a_note(tmp_path: Path) -> None:
    model = rows("t", ("id", "string", "N", "Y", "", ""), ("v", "string", "N", "", "", ""))
    found = compare(tmp_path, model, t="id,v\na,1.5\nb,2.5\n")
    assert kinds(found) == set()
    assert ("datatype", "t.v") in kinds(found, "note")


def test_a_string_column_of_whole_number_codes_is_not_reported(tmp_path: Path) -> None:
    """Codes such as an industry number are text on purpose."""
    model = rows("t", ("id", "string", "N", "Y", "", ""), ("v", "string", "N", "", "", ""))
    assert compare(tmp_path, model, t="id,v\na,11\nb,22\n") == []


# --- nullable -----------------------------------------------------------------------------


def test_a_column_the_model_forbids_to_be_empty_but_is_empty(tmp_path: Path) -> None:
    text = "uen,name,employees\nA-1,Alpha,10\nA-2,,20\nA-3,Gamma,30\n"
    found = compare(tmp_path, ACCOUNTS_MODEL, accounts=text)
    assert ("nullable", "accounts.name") in kinds(found)
    assert "1 of 3" in next(d.message for d in found if d.kind == "nullable")


def test_a_nullable_column_that_is_never_empty_is_not_reported(tmp_path: Path) -> None:
    """Most columns that allow empty values have none in a given delivery; that is not news."""
    model = rows("t", ("id", "string", "N", "Y", "", ""), ("v", "string", "Y", "", "", ""))
    assert compare(tmp_path, model, t="id,v\na,x\nb,y\n") == []


# --- keys ---------------------------------------------------------------------------------


def test_a_primary_key_with_duplicates_is_a_difference(tmp_path: Path) -> None:
    text = ACCOUNTS.replace("A-2", "A-1")
    found = compare(tmp_path, ACCOUNTS_MODEL, accounts=text)
    assert ("primary_key", "accounts") in kinds(found)
    assert "duplicate" in next(d.message for d in found if d.kind == "primary_key")


def test_a_primary_key_column_with_empty_values_is_a_difference(tmp_path: Path) -> None:
    text = ACCOUNTS.replace("A-2", "")
    assert ("primary_key", "accounts") in kinds(compare(tmp_path, ACCOUNTS_MODEL, accounts=text))


def test_a_primary_key_that_is_larger_than_it_needs_to_be_is_a_note(tmp_path: Path) -> None:
    model = rows(
        "t",
        ("id", "string", "N", "Y", "", ""),
        ("kind", "string", "N", "Y", "", ""),
        ("v", "decimal", "N", "Y", "", ""),
    )
    found = compare(tmp_path, model, t="id,kind,v\na,x,1.5\nb,x,2.5\nc,y,3.5\n")
    assert kinds(found) == set()
    assert ("primary_key", "t") in kinds(found, "note")
    assert "id" in next(d.message for d in found if d.level == "note" and d.kind == "primary_key")


def test_a_table_without_a_primary_key_in_the_model_is_not_reported_here(tmp_path: Path) -> None:
    """The standard check reports that; this compares only what the model says."""
    model = rows("t", ("id", "string", "N", "", "", ""))
    assert kinds(compare(tmp_path, model, t="id\na\nb\n")) == set()


def test_a_unique_column_with_duplicates_is_a_difference(tmp_path: Path) -> None:
    model = rows("t", ("id", "string", "N", "Y", "", ""), ("code", "string", "N", "", "Y", ""))
    found = compare(tmp_path, model, t="id,code\na,x\nb,x\n")
    assert ("unique", "t.code") in kinds(found)


# --- foreign keys -------------------------------------------------------------------------


def test_a_foreign_key_value_with_no_match_is_a_difference(tmp_path: Path) -> None:
    claims = CLAIMS.replace("A-2", "Z-9")
    found = compare(tmp_path, ACCOUNTS_MODEL + CLAIMS_MODEL, accounts=ACCOUNTS, claims=claims)
    assert ("foreign_key", "claims.uen") in kinds(found)
    assert "Z-9" not in next(d.message for d in found if d.kind == "foreign_key"), (
        "values are counted, never quoted"
    )
    assert "1 distinct value" in next(d.message for d in found if d.kind == "foreign_key")


def test_a_foreign_key_the_model_calls_soft_is_a_note_not_a_difference(tmp_path: Path) -> None:
    soft = [
        {**r, "fk": "crm.accounts.uen (soft reference)"} if r["column"] == "uen" else r
        for r in CLAIMS_MODEL
    ]
    claims = CLAIMS.replace("A-2", "Z-9")
    found = compare(tmp_path, ACCOUNTS_MODEL + soft, accounts=ACCOUNTS, claims=claims)
    assert ("foreign_key", "claims.uen") not in kinds(found)
    assert ("foreign_key", "claims.uen") in kinds(found, "note")


def test_empty_values_are_not_orphans(tmp_path: Path) -> None:
    claims = CLAIMS.replace("A-2", "")
    model = ACCOUNTS_MODEL + [
        {**r, "nullable": "Y"} if r["column"] == "uen" else r for r in CLAIMS_MODEL
    ]
    found = compare(tmp_path, model, accounts=ACCOUNTS, claims=claims)
    assert ("foreign_key", "claims.uen") not in kinds(found)


def test_a_join_the_data_shows_that_the_model_does_not_is_a_note(tmp_path: Path) -> None:
    claims_without_fk = [{**r, "fk": ""} for r in CLAIMS_MODEL]
    found = compare(tmp_path, ACCOUNTS_MODEL + claims_without_fk, accounts=ACCOUNTS, claims=CLAIMS)
    assert ("foreign_key_candidate", "claims.uen") in kinds(found, "note")
    assert "accounts.uen" in next(d.message for d in found if d.kind == "foreign_key_candidate")


def test_a_foreign_key_to_a_table_with_no_file_is_skipped_not_guessed(tmp_path: Path) -> None:
    found = compare(tmp_path, ACCOUNTS_MODEL + CLAIMS_MODEL, claims=CLAIMS)
    assert ("foreign_key", "claims.uen") not in kinds(found)


# --- reading ------------------------------------------------------------------------------


def test_every_finding_says_where_it_is_and_what_differs(tmp_path: Path) -> None:
    model = rows("t", ("id", "string", "N", "Y", "", ""), ("v", "string", "N", "", "", ""))
    found = compare(tmp_path, model, t="id,v\na,1\nb,\n")
    assert found
    for d in found:
        assert d.where
        assert d.message
        assert str(d).startswith(d.level)
