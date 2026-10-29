"""``ac2 new mcp --from-model``: tools made from a data model and a folder of query files."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from agentcraft2_cli.__main__ import main
from agentcraft2_cli.data_model import COLUMNS
from agentcraft2_cli.data_model_draft import write_model
from agentcraft2_cli.dataplan import DataPlan, PlannedParameter, PlannedQuery
from agentcraft2_cli.errors import CliError
from agentcraft2_cli.readers import from_model
from agentcraft2_cli.testing import toolbox_for_tests
from agentcraft2_cli.toolbox import Toolbox
from agentcraft2_cli.workspace import load_answers

TOOLS = toolbox_for_tests(pins={})

ACCOUNTS = (
    "uen,name,segment,email,annual_revenue\n"
    "A-1,Alpha,Retail,a@x.test,1000.50\n"
    "A-2,Beta,Retail,b@x.test,2000.00\n"
    "A-3,Gamma,Corporate,c@x.test,3000.75\n"
)
CLAIMS = (
    "claim_id,uen,status,amount,opened\n"
    "9001,A-1,open,10.50,2026-01-04\n"
    "9002,A-2,closed,20.00,2026-01-09\n"
    "9003,A-1,open,30.25,2026-02-11\n"
)

CLAIMS_BY_UEN = """\
-- description: Claims opened by one client, newest first.
-- param uen: string
-- max_rows: 50
-- classification: confidential
SELECT claim_id, uen, status, amount, opened
FROM claims
WHERE uen = :uen
ORDER BY opened DESC
"""
OPEN_CLAIMS = """\
-- description: Claims of one client in one status, with the client's name and email.
-- param uen: string
-- param status: string
-- max_rows: 20
-- classification: confidential
SELECT a.name, a.email, c.claim_id, c.status
FROM accounts a
JOIN claims c ON c.uen = a.uen
WHERE c.uen = :uen AND c.status = :status
ORDER BY claim_id
"""
BY_SEGMENT = """\
-- description: Clients served in one segment, with their annual revenue.
-- param segment: string
-- max_rows: 100
SELECT uen, name, annual_revenue
FROM accounts
WHERE segment = :segment
ORDER BY name
"""


def row(table: str, column: str, **changes: str) -> dict[str, str]:
    base = dict.fromkeys(COLUMNS, "")
    base.update(
        domain="crm",
        table=table,
        column=column,
        datatype="string",
        nullable="Y",
        description=f"The {column} of a row, as the bank records it.",
    )
    return {**base, **changes}


def model_rows() -> list[dict[str, str]]:
    accounts = "Accounts of the bank's clients, one row for each client."
    claims = "Insurance claims opened by clients, one row for each claim."

    def of(table: str, about: str, column: str, text: str, **changes: str) -> dict[str, str]:
        return row(table, column, table_description=about, description=text, **changes)

    return [
        of(
            "accounts",
            accounts,
            "uen",
            "Client key, the unique entity number.",
            pk="Y",
            nullable="N",
        ),
        of("accounts", accounts, "name", "Legal name of the client."),
        of("accounts", accounts, "segment", "Segment the client is served in."),
        of("accounts", accounts, "email", "Contact address of the client."),
        of(
            "accounts",
            accounts,
            "annual_revenue",
            "Annual revenue of the client in USD.",
            datatype="decimal",
        ),
        of(
            "claims",
            claims,
            "claim_id",
            "Identifier of the claim.",
            datatype="integer",
            pk="Y",
            nullable="N",
        ),
        of(
            "claims",
            claims,
            "uen",
            "Client the claim belongs to.",
            fk="crm.accounts.uen",
            nullable="N",
        ),
        of("claims", claims, "status", "Where the claim stands: open or closed."),
        of("claims", claims, "amount", "Amount claimed in USD.", datatype="decimal"),
        of("claims", claims, "opened", "Day the claim was opened.", datatype="date"),
    ]


@pytest.fixture
def data(tmp_path: Path) -> Path:
    folder = tmp_path / "data"
    folder.mkdir()
    (folder / "accounts.csv").write_text(ACCOUNTS, encoding="utf-8")
    (folder / "claims.csv").write_text(CLAIMS, encoding="utf-8")
    write_model(model_rows(), folder / "data_model.csv")
    return folder


@pytest.fixture
def queries(tmp_path: Path) -> Path:
    folder = tmp_path / "queries"
    folder.mkdir()
    (folder / "claims_by_uen.sql").write_text(CLAIMS_BY_UEN, encoding="utf-8")
    (folder / "open_claims.sql").write_text(OPEN_CLAIMS, encoding="utf-8")
    (folder / "accounts_in_segment.sql").write_text(BY_SEGMENT, encoding="utf-8")
    return folder


def by_name(plan: DataPlan) -> dict[str, PlannedQuery]:
    return {query.name: query for query in plan.queries}


# --- the plan the reader makes -------------------------------------------------------------------


async def test_a_plan_is_made_from_the_query_files_and_ran_over_the_files(
    data: Path, queries: Path
) -> None:
    proposal = await from_model(data, queries, source="claims")
    plan = proposal.plan
    assert (plan.origin, plan.source, plan.kind) == ("model", "claims", "duckdb_csv")
    assert [q.name for q in plan.queries] == ["accounts_in_segment", "claims_by_uen", "open_claims"]
    assert all(q.returns_rows for q in plan.queries)
    claims = by_name(plan)["claims_by_uen"]
    assert claims.columns == ("claim_id", "uen", "status", "amount", "opened")
    assert (claims.classification, claims.max_rows) == ("confidential", 50)
    assert claims.definition == CLAIMS_BY_UEN, "the file is kept as it was written"


async def test_a_description_is_the_header_and_says_what_one_row_is(
    data: Path, queries: Path
) -> None:
    queries_ = by_name((await from_model(data, queries, source="claims")).plan)
    assert queries_["claims_by_uen"].description == (
        "Claims opened by one client, newest first. One row per claim_id."
    )
    assert queries_["accounts_in_segment"].description.endswith("One row per uen.")
    # A query over two tables has no single kind of row to name.
    assert queries_["open_claims"].description == (
        "Claims of one client in one status, with the client's name and email."
    )


async def test_an_argument_is_described_by_the_column_it_is_compared_with(
    data: Path, queries: Path
) -> None:
    plan = (await from_model(data, queries, source="claims")).plan
    claims = by_name(plan)["claims_by_uen"]
    assert claims.parameters == (
        PlannedParameter("uen", "string", "A-1", "Client the claim belongs to."),
    )
    # Through an alias, and with the example taken from the data.
    uen, status = by_name(plan)["open_claims"].parameters
    assert (uen.description, uen.example) == ("Client the claim belongs to.", "A-1")
    assert (status.description, status.example) == (
        "Where the claim stands: open or closed.",
        "open",
    )
    assert by_name(plan)["accounts_in_segment"].parameters[0].example == "Retail"


async def test_columns_that_look_personal_are_masked_and_others_can_be_named(
    data: Path, queries: Path
) -> None:
    plan = (await from_model(data, queries, source="claims")).plan
    assert by_name(plan)["open_claims"].masked == ("email",)
    assert by_name(plan)["claims_by_uen"].masked == ()
    named = (
        await from_model(data, queries, source="claims", mask=["amount", "annual_revenue"])
    ).plan
    assert by_name(named)["claims_by_uen"].masked == ("amount",)
    assert by_name(named)["accounts_in_segment"].masked == ("annual_revenue",)
    assert named.masked == ("amount", "annual_revenue", "email")


async def test_a_column_to_mask_that_no_query_returns_is_refused(data: Path, queries: Path) -> None:
    with pytest.raises(CliError, match="no query returns a column called emial"):
        await from_model(data, queries, source="claims", mask=["emial"])


async def test_a_query_without_a_classification_is_confidential_and_says_so(
    data: Path, queries: Path
) -> None:
    proposal = await from_model(data, queries, source="claims")
    assert by_name(proposal.plan)["accounts_in_segment"].classification == "confidential"
    assert any("accounts_in_segment has no classification" in note for note in proposal.notes)


async def test_the_files_and_the_model_are_copied_into_the_server(
    data: Path, queries: Path
) -> None:
    proposal = await from_model(data, queries, source="claims")
    assert sorted(proposal.data_files) == ["accounts.csv", "claims.csv", "data_model.csv"]
    assert proposal.data_files["claims.csv"] == data / "claims.csv"
    assert "2 tables" in proposal.summary
    assert "3 queries" in proposal.summary
    assert proposal.description == "Read-only tools over the claims data."


async def test_queries_can_be_chosen_by_name(data: Path, queries: Path) -> None:
    proposal = await from_model(data, queries, source="claims", only=["open_claims"])
    assert [q.name for q in proposal.plan.queries] == ["open_claims"]
    with pytest.raises(CliError, match="not among the proposed queries: nothing_like_it"):
        await from_model(data, queries, source="claims", only=["nothing_like_it"])


async def test_an_argument_with_no_column_to_describe_it_is_still_a_tool_argument(
    data: Path, tmp_path: Path
) -> None:
    folder = tmp_path / "own"
    folder.mkdir()
    (folder / "recent.sql").write_text(
        "-- description: Claims opened on or after a day, newest first.\n"
        "-- param since: date\n-- max_rows: 10\n-- classification: internal\n"
        "SELECT claim_id, opened FROM claims WHERE opened >= :since ORDER BY opened DESC\n",
        encoding="utf-8",
    )
    (since,) = (await from_model(data, folder, source="claims")).plan.queries[0].parameters
    assert since.description == "Day the claim was opened."
    assert since.example == "2026-01-04"


async def test_a_declared_type_that_differs_from_the_column_is_a_note_not_an_error(
    data: Path, tmp_path: Path
) -> None:
    folder = tmp_path / "own"
    folder.mkdir()
    (folder / "by_claim.sql").write_text(
        "-- description: One claim by its identifier, as text.\n-- param claim_id: string\n"
        "-- max_rows: 5\n-- classification: internal\n"
        "SELECT claim_id, status FROM claims WHERE CAST(claim_id AS VARCHAR) = :claim_id\n"
        "ORDER BY claim_id\n",
        encoding="utf-8",
    )
    proposal = await from_model(data, folder, source="claims")
    assert len(proposal.plan.queries) == 1


# --- what the reader refuses ---------------------------------------------------------------------


async def test_a_folder_without_a_data_model_points_at_draft(tmp_path: Path, queries: Path) -> None:
    (tmp_path / "bare").mkdir()
    (tmp_path / "bare" / "claims.csv").write_text(CLAIMS, encoding="utf-8")
    with pytest.raises(CliError, match="no data model") as caught:
        await from_model(tmp_path / "bare", queries, source="claims")
    assert "ac2 data model draft" in (caught.value.fix or "")


async def test_a_model_that_does_not_follow_the_standard_is_refused(
    data: Path, queries: Path
) -> None:
    rows = model_rows()
    rows[0]["description"] = "TODO: describe it"
    write_model(rows, data / "data_model.csv")
    with pytest.raises(CliError, match="does not follow the standard") as caught:
        await from_model(data, queries, source="claims")
    assert "ac2 data model check" in (caught.value.fix or "")


async def test_a_folder_without_query_files_is_refused(data: Path, tmp_path: Path) -> None:
    (tmp_path / "empty").mkdir()
    with pytest.raises(CliError, match="no query files"):
        await from_model(data, tmp_path / "empty", source="claims")


@pytest.mark.parametrize(
    ("body", "problem"),
    [
        ("SELECT a FROM ghosts WHERE a = :x", "reads the table ghosts"),
        ("SELECT claim_id FROM claims WHERE colour = :x", "uses the column colour"),
        ("SELECT claim_id FROM claims WHERE status = :x AND y = 1", "uses the column y"),
    ],
)
async def test_a_query_that_uses_what_the_model_does_not_describe_is_refused(
    data: Path, tmp_path: Path, body: str, problem: str
) -> None:
    folder = tmp_path / "own"
    folder.mkdir()
    (folder / "bad.sql").write_text(
        f"-- description: A query the model cannot account for.\n-- param x: string\n"
        f"-- max_rows: 5\n-- classification: internal\n{body}\n",
        encoding="utf-8",
    )
    with pytest.raises(CliError, match=problem) as caught:
        await from_model(data, folder, source="claims")
    assert "bad.sql" in str(caught.value)
    assert "data_model.csv" in (caught.value.fix or "")


async def test_a_name_defined_inside_the_query_is_not_a_column_the_model_lacks(
    data: Path, tmp_path: Path
) -> None:
    folder = tmp_path / "own"
    folder.mkdir()
    (folder / "totals.sql").write_text(
        "-- description: Total claimed and number of claims for one client.\n"
        "-- param uen: string\n-- max_rows: 5\n-- classification: internal\n"
        "WITH t AS (SELECT uen, SUM(amount) AS total, COUNT(*) AS n FROM claims GROUP BY uen)\n"
        "SELECT uen, total, n FROM t WHERE uen = :uen ORDER BY uen\n",
        encoding="utf-8",
    )
    proposal = await from_model(data, folder, source="claims")
    assert proposal.plan.queries[0].columns == ("uen", "total", "n")


async def test_a_query_that_describes_nothing_is_refused(data: Path, tmp_path: Path) -> None:
    folder = tmp_path / "own"
    folder.mkdir()
    (folder / "terse.sql").write_text(
        "-- description: Claims.\n-- max_rows: 5\n-- classification: internal\n"
        "SELECT claim_id FROM claims ORDER BY claim_id\n",
        encoding="utf-8",
    )
    with pytest.raises(CliError, match=r"terse.*description") as caught:
        await from_model(data, folder, source="claims")
    assert "model reads it to choose the tool" in str(caught.value)


async def test_a_query_file_the_library_refuses_is_refused_with_its_reason(
    data: Path, tmp_path: Path
) -> None:
    folder = tmp_path / "own"
    folder.mkdir()
    (folder / "writes.sql").write_text(
        "-- description: Deletes claims, which no tool may do.\n-- max_rows: 5\n"
        "DELETE FROM claims\n",
        encoding="utf-8",
    )
    with pytest.raises(CliError, match=r"writes\.sql"):
        await from_model(data, folder, source="claims")


# --- the plan keeps the descriptions of arguments ----------------------------------------------


def test_a_described_argument_is_declared_with_its_description() -> None:
    query = PlannedQuery(
        "claims_by_uen",
        "Claims of one client.",
        (
            PlannedParameter("uen", "string", "A-1", "Client key."),
            PlannedParameter("limit", "integer", 5),
        ),
        ("claim_id",),
        (),
        "confidential",
        50,
        "x\n",
    )
    assert query.signature == ('uen: Annotated[str, Field(description="Client key.")], limit: int')
    assert query.plain_signature == "uen: str, limit: int"


def test_a_query_with_no_described_argument_is_declared_as_before() -> None:
    query = PlannedQuery(
        "q", "d", (PlannedParameter("a", "string", "x"),), ("a",), (), "internal", 5, "x\n"
    )
    assert query.signature == "a: str" == query.plain_signature


def test_the_description_of_an_argument_survives_the_workspace_file() -> None:
    plan = DataPlan(
        origin="model",
        source="claims",
        kind="duckdb_csv",
        queries=(
            PlannedQuery(
                "q",
                "d",
                (PlannedParameter("a", "string", "x", 'Say "hi", \\ and über.'),),
                ("a",),
                (),
                "internal",
                5,
                "x\n",
            ),
        ),
    )
    raw = tomllib.loads("\n".join(plan.to_toml("mcp_servers.data")))["mcp_servers"]["data"]
    assert DataPlan.from_toml(raw) == plan


# --- the command ----------------------------------------------------------------------------------


def run(*arguments: str | Path, tools: Toolbox = TOOLS) -> int:
    return main([str(argument) for argument in arguments], toolbox=tools)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    assert run("init", "demo", "--dir", tmp_path, "--owner", "demo-team") == 0
    return tmp_path / "demo"


def test_new_mcp_from_model_writes_a_server_of_code_based_tools(
    workspace: Path, data: Path, queries: Path
) -> None:
    command = ("new", "mcp", "claims-mcp", "--from-model", data, "--queries", queries)
    assert run(*command, "--workspace", workspace) == 0
    service = workspace / "mcp-servers/claims-mcp"

    assert (service / "data/data_model.csv").read_bytes() == (data / "data_model.csv").read_bytes()
    assert (service / "data/claims.csv").read_text(encoding="utf-8") == CLAIMS
    assert (service / "queries/claims_by_uen.sql").read_text(encoding="utf-8") == CLAIMS_BY_UEN

    server = (service / "src/claims_mcp/server.py").read_text(encoding="utf-8")
    assert "from typing import Annotated, Any" in server
    assert "from pydantic import Field" in server
    assert (
        'async def claims_by_uen(uen: Annotated[str, Field(description="Client the claim' in server
    )
    assert "Claims opened by one client, newest first. One row per claim_id." in server
    assert 'await source.query("claims_by_uen", {"uen": uen})' in server

    plan = load_answers(workspace).mcp_servers[0].plan
    assert plan.origin == "model"
    assert plan.masked == ("email",)
    assert '"queries_dir": "queries"' in (service / ".env.example").read_text(encoding="utf-8")
    readme = (service / "README.md").read_text(encoding="utf-8")
    assert "data_model.csv" in readme


def test_propose_shows_the_tools_and_writes_nothing(
    workspace: Path, data: Path, queries: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run("new", "mcp", "claims-mcp", "--from-model", data, "--queries", queries,
               "--propose", "--workspace", workspace) == 0  # fmt: skip
    out = capsys.readouterr().out
    assert "claims_mcp.claims_by_uen(uen: str)" in out or "claims.claims_by_uen(uen: str)" in out
    assert "Claims opened by one client, newest first. One row per claim_id." in out
    assert not (workspace / "mcp-servers/claims-mcp").exists()


def test_masking_can_be_asked_for_by_column(workspace: Path, data: Path, queries: Path) -> None:
    assert run("new", "mcp", "claims-mcp", "--from-model", data, "--queries", queries,
               "--mask", "amount", "--workspace", workspace) == 0  # fmt: skip
    assert "amount" in load_answers(workspace).mcp_servers[0].plan.masked


def test_from_model_needs_queries_and_queries_need_from_model(
    workspace: Path, data: Path, queries: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run("new", "mcp", "a-mcp", "--from-model", data, "--workspace", workspace) == 2
    assert "--from-model needs --queries" in capsys.readouterr().err
    assert run("new", "mcp", "a-mcp", "--queries", queries, "--workspace", workspace) == 2
    assert "--queries" in capsys.readouterr().err
    assert run("new", "mcp", "a-mcp", "--mask", "email", "--workspace", workspace) == 2
    assert "--mask" in capsys.readouterr().err
    assert run("new", "mcp", "a-mcp", "--from-model", data, "--queries", queries,
               "--from-csv", data, "--workspace", workspace) == 2  # fmt: skip
    assert "give only one of" in capsys.readouterr().err


def test_a_model_with_problems_stops_the_command_and_creates_nothing(
    workspace: Path, data: Path, queries: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rows = model_rows()
    rows[0]["table_description"] = "TODO: describe it"
    write_model(rows, data / "data_model.csv")
    assert run("new", "mcp", "claims-mcp", "--from-model", data, "--queries", queries,
               "--workspace", workspace) == 1  # fmt: skip
    assert "does not follow the standard" in capsys.readouterr().err
    assert not (workspace / "mcp-servers/claims-mcp").exists()
