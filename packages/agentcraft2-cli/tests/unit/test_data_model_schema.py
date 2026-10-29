"""The schema of a data model, written for a model that writes SQL: what it may use, and how."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentcraft2_cli.data_model import COLUMNS
from agentcraft2_cli.data_model_schema import (
    build_schema,
    estimate_tokens,
    render_json,
    render_markdown,
)


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


def model() -> list[dict[str, str]]:
    return [
        row("accounts", "uen", pk="Y", nullable="N", description="Unique entity number."),
        row("accounts", "name", description="Legal name of the client."),
        row("accounts", "segment", description="Segment the client is served in."),
        row("accounts", "revenue", datatype="decimal", description="Annual revenue in USD."),
        row("accounts", "since", datatype="date", description="First day as a client."),
        row("accounts", "email", uk="Y", description="Contact address, unique."),
        row("claims", "claim_id", pk="Y", nullable="N", datatype="integer"),
        row("claims", "uen", fk="crm.accounts.uen", nullable="N"),
        row("claims", "status", description="Where the claim stands."),
        row("claims", "amount", datatype="decimal"),
        row("profiles", "uen", pk="Y", nullable="N", fk="crm.accounts.uen"),
        row("profiles", "tier"),
        row("balances", "uen", pk="Y", nullable="N"),
        row("balances", "as_of", pk="Y", nullable="N", datatype="date"),
        row("balances", "amount", datatype="decimal"),
        row("snapshots", "snapshot_id", pk="Y", nullable="N"),
        row("snapshots", "uen", fk="crm.balances.uen"),
        row("notes", "note_id", pk="Y", nullable="N"),
        row("notes", "uen", fk="crm.accounts.uen (each entry; may be outside)"),
        row("audits", "audit_id", pk="Y", nullable="N"),
        row("audits", "uen", fk="crm.accounts (consistency)"),
        row("audits", "owner", fk="crm.accounts (consistency)"),
        row("audits", "total", datatype="decimal", fk="crm.accounts.revenue (reconciliation)"),
        row("regions", "code", pk="Y", nullable="N", domain="ref"),
    ]


def joins_of(schema, child: str) -> list:
    return [j for j in schema.joins if j.child_table == child]


# --- the tables and their columns ---------------------------------------------------------------


def test_tables_follow_the_model_with_their_keys_and_columns() -> None:
    schema = build_schema(model())
    assert [t.name for t in schema.tables] == [
        "accounts",
        "claims",
        "profiles",
        "balances",
        "snapshots",
        "notes",
        "audits",
        "regions",
    ]
    accounts = schema.tables[0]
    assert accounts.domain == "crm"
    assert accounts.primary_key == ("uen",)
    assert accounts.unique == ("email",)
    assert accounts.description == "The accounts of the bank, one row for each of them."
    uen, name = accounts.columns[0], accounts.columns[1]
    assert (uen.name, uen.type, uen.nullable, uen.primary_key) == ("uen", "string", False, True)
    assert (name.nullable, name.primary_key, name.unique) == (True, False, False)
    assert accounts.columns[5].unique is True


def test_a_composite_key_lists_every_column_in_order() -> None:
    balances = next(t for t in build_schema(model()).tables if t.name == "balances")
    assert balances.primary_key == ("uen", "as_of")


def test_a_column_that_points_elsewhere_says_where() -> None:
    claims = next(t for t in build_schema(model()).tables if t.name == "claims")
    uen = next(c for c in claims.columns if c.name == "uen")
    assert (uen.references.table, uen.references.column) == ("accounts", "uen")


def test_building_twice_gives_the_same_schema() -> None:
    assert build_schema(model()) == build_schema(model())


# --- the join graph -----------------------------------------------------------------------------


def test_a_foreign_key_to_a_unique_column_is_many_to_one() -> None:
    (join,) = joins_of(build_schema(model()), "claims")
    assert (join.parent_table, join.parent_column, join.child_column) == ("accounts", "uen", "uen")
    assert join.kind == "reference"
    assert join.cardinality == "many-to-one"
    assert join.condition == "claims.uen = accounts.uen"


def test_a_foreign_key_that_is_the_childs_whole_key_is_one_to_one() -> None:
    (join,) = joins_of(build_schema(model()), "profiles")
    assert join.cardinality == "one-to-one"


def test_a_parent_column_that_is_not_unique_alone_is_said_to_be_unknown() -> None:
    (join,) = joins_of(build_schema(model()), "snapshots")
    assert join.cardinality == "unknown"
    assert "not unique on its own" in join.warning


def test_a_foreign_key_with_a_note_in_brackets_is_soft() -> None:
    (join,) = joins_of(build_schema(model()), "notes")
    assert join.kind == "soft"
    assert join.note == "each entry; may be outside"
    assert "may match nothing" in join.warning


def test_a_foreign_key_without_a_column_relates_tables_only() -> None:
    join = joins_of(build_schema(model()), "audits")[0]
    assert join.kind == "table"
    assert join.parent_column is None
    assert join.condition == ""
    assert join.for_joining is False


def test_a_soft_reference_to_a_column_that_is_not_a_key_is_not_for_joining() -> None:
    schema = build_schema(model())
    total = next(j for j in joins_of(schema, "audits") if j.child_column == "total")
    assert (total.kind, total.cardinality, total.for_joining) == ("soft", "unknown", False)
    audits = next(t for t in schema.tables if t.name == "audits")
    column = next(c for c in audits.columns if c.name == "total")
    assert column.references.kind == "relation"


def test_references_that_are_keys_are_for_joining() -> None:
    schema = build_schema(model())
    assert all(j.for_joining for j in joins_of(schema, "claims") + joins_of(schema, "notes"))
    snapshots = joins_of(schema, "snapshots")[0]
    assert snapshots.for_joining, "a part of a composite key is still a join, with a warning"


def test_joins_come_in_model_order() -> None:
    schema = build_schema(model())
    assert [j.child_table for j in schema.joins] == [
        "claims",
        "profiles",
        "snapshots",
        "notes",
        "audits",
        "audits",
        "audits",
    ]


# --- choosing tables ----------------------------------------------------------------------------


def test_named_tables_are_all_that_is_returned_and_only_the_joins_between_them() -> None:
    schema = build_schema(model(), tables=["claims", "accounts"])
    assert [t.name for t in schema.tables] == ["accounts", "claims"]
    assert [j.child_table for j in schema.joins] == ["claims"]


def test_a_table_that_points_outside_the_choice_loses_the_join() -> None:
    schema = build_schema(model(), tables=["claims"])
    assert schema.joins == ()
    claims = schema.tables[0]
    uen = next(c for c in claims.columns if c.name == "uen")
    assert uen.references.table == "accounts", "the column still says what it points at"


def test_related_adds_the_tables_one_join_away_in_either_direction() -> None:
    schema = build_schema(model(), tables=["claims"], related=True)
    assert [t.name for t in schema.tables] == ["accounts", "claims"]
    schema = build_schema(model(), tables=["accounts"], related=True)
    assert {t.name for t in schema.tables} == {
        "accounts",
        "claims",
        "profiles",
        "notes",
        "audits",
    }


def test_excluded_tables_and_the_joins_to_them_are_left_out() -> None:
    schema = build_schema(model(), exclude=["claims"])
    assert "claims" not in [t.name for t in schema.tables]
    assert "claims" not in [j.child_table for j in schema.joins]
    assert schema.tables_in_model == 8


def test_an_excluded_table_is_not_added_by_related() -> None:
    schema = build_schema(model(), tables=["accounts"], related=True, exclude=["claims"])
    assert "claims" not in [t.name for t in schema.tables]
    assert "profiles" in [t.name for t in schema.tables]


def test_an_excluded_table_that_does_not_exist_is_refused_like_a_chosen_one() -> None:
    with pytest.raises(ValueError, match="no table called 'claim'"):
        build_schema(model(), exclude=["claim"])


def test_a_table_cannot_be_chosen_and_excluded() -> None:
    with pytest.raises(ValueError, match="both chosen and excluded: claims"):
        build_schema(model(), tables=["claims"], exclude=["claims"])


def test_an_unknown_table_is_refused_with_the_closest_name() -> None:
    with pytest.raises(ValueError, match="no table called 'claim'") as caught:
        build_schema(model(), tables=["claim"])
    assert "claims" in str(caught.value)


def test_an_unknown_table_with_nothing_close_lists_the_tables() -> None:
    with pytest.raises(ValueError, match="the tables are: accounts, claims"):
        build_schema(model(), tables=["zzz"])


# --- values from the data -----------------------------------------------------------------------


def write_data(folder: Path) -> Path:
    folder.mkdir()
    (folder / "accounts.csv").write_text(
        "uen,name,segment,revenue,since,email\n"
        "A-1,Alpha,Retail,1.5,2020-01-01,a@x\n"
        "A-2,Beta,Retail,2.5,2020-01-02,b@x\n"
        "A-3,Gamma,Corporate,3.5,2020-01-03,c@x\n"
        "A-4,Delta,Retail,4.5,2020-01-04,d@x\n"
        "A-5,Eps,,5.5,2020-01-05,e@x\n",
        encoding="utf-8",
    )
    (folder / "claims.csv").write_text(
        "claim_id,uen,status,amount\n1,A-1,open,1\n2,A-1,closed,2\n3,A-2,open,3\n",
        encoding="utf-8",
    )
    return folder


def values_of(schema, table: str, column: str) -> tuple[str, ...]:
    found = next(t for t in schema.tables if t.name == table)
    return next(c for c in found.columns if c.name == column).values


def test_values_are_left_out_unless_asked_for(tmp_path: Path) -> None:
    schema = build_schema(model(), data_dir=write_data(tmp_path / "data"))
    assert all(not column.values for table in schema.tables for column in table.columns)
    assert schema.values_from_data is False


def test_a_text_column_with_few_values_lists_them_most_common_first(tmp_path: Path) -> None:
    schema = build_schema(model(), data_dir=write_data(tmp_path / "data"), values=4)
    assert values_of(schema, "accounts", "segment") == ("Retail", "Corporate")
    assert values_of(schema, "claims", "status") == ("open", "closed")
    assert schema.values_from_data is True


def test_a_column_with_more_values_than_the_limit_lists_none(tmp_path: Path) -> None:
    schema = build_schema(model(), data_dir=write_data(tmp_path / "data"), values=1)
    assert values_of(schema, "accounts", "segment") == ()
    assert values_of(schema, "accounts", "name") == ()


def test_keys_numbers_and_dates_never_list_values(tmp_path: Path) -> None:
    schema = build_schema(model(), data_dir=write_data(tmp_path / "data"), values=50)
    assert values_of(schema, "accounts", "uen") == ()  # a primary key
    assert values_of(schema, "accounts", "email") == ()  # a unique column
    assert values_of(schema, "claims", "uen") == ()  # a foreign key
    assert values_of(schema, "accounts", "revenue") == ()
    assert values_of(schema, "accounts", "since") == ()


def test_a_long_value_is_cut_and_one_with_a_line_break_is_left_out(tmp_path: Path) -> None:
    data = write_data(tmp_path / "data")
    (data / "claims.csv").write_text(
        'claim_id,uen,status,amount\n1,A-1,"' + "x" * 80 + '",1\n2,A-1,"two\nlines",2\n',
        encoding="utf-8",
    )
    found = values_of(build_schema(model(), data_dir=data, values=5), "claims", "status")
    assert found == ("x" * 37 + "...",)


def test_values_need_the_data_folder() -> None:
    with pytest.raises(ValueError, match="values come from the data files"):
        build_schema(model(), values=3)


def test_a_table_without_a_file_just_lists_no_values(tmp_path: Path) -> None:
    data = write_data(tmp_path / "data")
    (data / "claims.csv").unlink()
    schema = build_schema(model(), data_dir=data, values=5)
    assert values_of(schema, "claims", "status") == ()
    assert values_of(schema, "accounts", "segment") == ("Retail", "Corporate")


# --- writing it for a model that writes SQL -----------------------------------------------------


def test_the_markdown_gives_the_rules_the_index_the_joins_and_a_card_per_table() -> None:
    text = render_markdown(build_schema(model()))
    assert text.startswith("# Data schema\n")
    assert "SQL dialect: redshift." in text
    assert "Use only the tables, columns and joins listed here." in text
    assert "| accounts | crm | The accounts of the bank, one row for each of them. | uen |" in text
    assert "## Joins" in text
    assert "- claims.uen = accounts.uen (many-to-one)" in text
    assert "## accounts\n" in text
    assert "Primary key: uen. Unique: email." in text
    assert "| uen | string | no | pk | Unique entity number. |" in text
    assert "| uen | string | no | fk accounts.uen | The uen of a row. |" in text


def test_the_joins_say_what_to_watch_for() -> None:
    text = render_markdown(build_schema(model()))
    assert "- snapshots.uen = balances.uen (unknown): balances.uen is not unique on its own" in text
    assert "- notes.uen = accounts.uen (many-to-one, soft): may match nothing" in text


def test_relations_that_are_not_keys_are_listed_apart_and_never_as_a_condition() -> None:
    text = render_markdown(build_schema(model()))
    before, _, others = text.partition("## Other relations")
    joins = before.partition("## Joins")[2]
    assert "audits" not in joins, "a relation that is not a key is not in the join list"
    assert "do not join on them" in others
    assert "- audits.total relates to accounts.revenue (reconciliation)" in others


def test_table_level_relations_that_repeat_are_said_once() -> None:
    text = render_markdown(build_schema(model()))
    lines = [x for x in text.splitlines() if "relates to accounts (consistency)" in x]
    assert lines == ["- audits.uen, audits.owner relates to accounts (consistency)"]


def test_without_other_relations_the_section_is_left_out() -> None:
    text = render_markdown(build_schema(model(), tables=["claims", "accounts"]))
    assert "## Other relations" not in text


def test_a_column_that_only_relates_is_marked_so_and_not_as_a_foreign_key() -> None:
    text = render_markdown(build_schema(model()))
    assert "| total | decimal | yes | relates accounts.revenue |" in text
    assert "| owner | string | yes | relates accounts |" in text
    assert "| uen | string | yes | soft fk accounts.uen |" in text


def test_the_dialect_is_whatever_was_asked_for() -> None:
    assert "SQL dialect: duckdb." in render_markdown(build_schema(model(), dialect="duckdb"))


def test_a_subset_says_so_and_has_only_its_own_cards() -> None:
    text = render_markdown(build_schema(model(), tables=["claims", "accounts"]))
    assert "2 of 8 tables" in text
    assert "## claims\n" in text
    assert "## profiles\n" not in text


def test_values_appear_under_the_table_and_say_where_they_came_from(tmp_path: Path) -> None:
    schema = build_schema(model(), data_dir=write_data(tmp_path / "data"), values=4)
    text = render_markdown(schema)
    assert "Values of segment: Retail | Corporate" in text
    assert "values were read from the data files" in text.lower()
    assert "values were read" not in render_markdown(build_schema(model())).lower()


def test_a_pipe_in_a_description_does_not_break_the_table() -> None:
    rows = model()
    rows[1]["description"] = "Legal name | as registered\nwith a second line."
    text = render_markdown(build_schema(rows))
    line = next(x for x in text.splitlines() if x.startswith("| name |"))
    assert line.count("|") == 6
    assert "\n" not in line


def test_the_markdown_is_the_same_every_time_and_has_no_trailing_spaces() -> None:
    first = render_markdown(build_schema(model()))
    assert first == render_markdown(build_schema(model()))
    assert all(line == line.rstrip() for line in first.splitlines())
    assert first.endswith("\n")
    assert not first.endswith("\n\n")


def test_the_json_carries_the_same_facts() -> None:
    data = json.loads(render_json(build_schema(model())))
    assert data["dialect"] == "redshift"
    assert [t["name"] for t in data["tables"]][:2] == ["accounts", "claims"]
    accounts = data["tables"][0]
    assert accounts["primary_key"] == ["uen"]
    assert accounts["columns"][0] == {
        "name": "uen",
        "type": "string",
        "nullable": False,
        "primary_key": True,
        "unique": False,
        "references": None,
        "description": "Unique entity number.",
        "values": [],
    }
    join = next(j for j in data["joins"] if j["child_table"] == "claims")
    assert join["condition"] == "claims.uen = accounts.uen"
    assert join["cardinality"] == "many-to-one"
    assert join["for_joining"] is True
    total = next(j for j in data["joins"] if j["child_column"] == "total")
    assert total["for_joining"] is False


def test_the_json_is_the_same_every_time() -> None:
    assert render_json(build_schema(model())) == render_json(build_schema(model()))


def test_the_size_estimate_grows_with_the_text() -> None:
    small = estimate_tokens(render_markdown(build_schema(model(), tables=["regions"])))
    large = estimate_tokens(render_markdown(build_schema(model())))
    assert 0 < small < large
    assert estimate_tokens("") == 1
