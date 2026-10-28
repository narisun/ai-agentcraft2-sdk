"""The SQL of this service lives beside the sections and the tool it serves, not in a folder.

``get_client_records`` and ``get_reference_data`` read a section each; a section is one entry that
holds the SQL, the table it comes from, the keys that identify a row and what to tell about it.
``find_clients`` keeps its query in ``server.py``, above the tool. ``rmprep_v2_data.queries``
turns them into the text the data source loads.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from agentcraft2_core.adapters.sql import QueryCatalog
from agentcraft2_core.contracts import Classification

from rmprep_v2_data.client_sections import CLIENT_SECTIONS
from rmprep_v2_data.queries import QUERIES
from rmprep_v2_data.reference_sections import REFERENCE_SECTIONS
from rmprep_v2_data.sections import ClientSection, ReferenceSection

SERVICE = Path(__file__).resolve().parents[1]
SECTIONS = {**CLIENT_SECTIONS, **REFERENCE_SECTIONS}

CLIENT_PARAMETERS = ["uen", "start_date", "end_date", "row_offset"]
REFERENCE_PARAMETERS = ["search", "start_date", "end_date", "row_offset"]


@pytest.fixture(scope="module")
def catalog() -> QueryCatalog:
    return QueryCatalog.from_texts(QUERIES, dialect="redshift")


def parameters(catalog: QueryCatalog, name: str) -> list[str]:
    return [p.name for p in catalog.describe()[name].parameters]


# --- every section has its SQL, and nothing else does --------------------------------------------


def test_every_section_of_both_tools_has_a_query_and_there_is_one_more_for_find_clients() -> None:
    expected = {s.value for s in ClientSection} | {s.value for s in ReferenceSection}
    assert set(SECTIONS) == expected
    assert len(SECTIONS) == len(CLIENT_SECTIONS) + len(REFERENCE_SECTIONS), "a name is used twice"
    assert set(QUERIES) == expected | {"find_clients"}


def test_each_tool_has_the_sections_of_its_enum() -> None:
    assert set(CLIENT_SECTIONS) == {s.value for s in ClientSection}
    assert set(REFERENCE_SECTIONS) == {s.value for s in ReferenceSection}


def test_the_service_has_no_folder_of_query_files_any_more() -> None:
    assert not (SERVICE / "queries").exists()
    settings = (SERVICE / ".env.example").read_text(encoding="utf-8")
    prefix = "AC2_DATA_SOURCES="
    line = next(text for text in settings.splitlines() if text.startswith(prefix))
    options = json.loads(line[len(prefix) :])["rmprep_v2"]
    assert options["queries_module"] == "rmprep_v2_data.queries"
    assert "queries_dir" not in options


# --- the text loads as the data source loads it ----------------------------------------------------


def test_the_queries_load_and_are_checked_like_files(catalog: QueryCatalog) -> None:
    assert sorted(catalog.describe()) == sorted(QUERIES)


def test_a_client_section_takes_a_client_and_a_window_and_a_page(catalog: QueryCatalog) -> None:
    for name in CLIENT_SECTIONS:
        assert parameters(catalog, name) == CLIENT_PARAMETERS, name


def test_a_reference_section_takes_a_search_and_a_window_and_a_page(catalog: QueryCatalog) -> None:
    for name in REFERENCE_SECTIONS:
        assert parameters(catalog, name) == REFERENCE_PARAMETERS, name


def test_find_clients_takes_only_the_search(catalog: QueryCatalog) -> None:
    assert parameters(catalog, "find_clients") == ["search"]


def test_every_query_is_capped_and_classified(catalog: QueryCatalog) -> None:
    for name, query in catalog.describe().items():
        assert query.max_rows == 50, name
        assert query.classification in (Classification.CONFIDENTIAL, Classification.INTERNAL), name


def test_client_evidence_is_confidential_and_reference_data_is_internal(
    catalog: QueryCatalog,
) -> None:
    described = catalog.describe()
    assert {described[n].classification for n in CLIENT_SECTIONS} == {Classification.CONFIDENTIAL}
    assert {described[n].classification for n in REFERENCE_SECTIONS} == {Classification.INTERNAL}
    assert described["find_clients"].classification is Classification.CONFIDENTIAL


# --- what a record cites ------------------------------------------------------------------------------


def test_the_keys_that_identify_a_row_are_columns_the_query_returns(catalog: QueryCatalog) -> None:
    """A key that the query does not return would make every source_ref say 'key=None'."""
    for name, spec in SECTIONS.items():
        assert set(spec.keys) <= set(catalog.get(name).outputs), name


def test_every_section_says_where_its_rows_come_from() -> None:
    for name, spec in SECTIONS.items():
        assert spec.table and spec.keys, name
        assert spec.table in spec.sql, f"{name}: the SQL never mentions {spec.table}"
