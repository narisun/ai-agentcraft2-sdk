"""The evidence sections the tools can return, and the shape of the query that reads each.

The enums name the sections a tool accepts. Each section's SQL is written next to its table, its
keys and its note: for ``get_client_records`` in ``client_sections.py``, for ``get_reference_data``
in ``reference_sections.py``, and for ``find_clients`` in ``server.py``, above the tool.
"""

from dataclasses import dataclass
from enum import StrEnum
from inspect import cleandoc

CLIENT_PARAMETERS = ("uen: string", "start_date: date", "end_date: date", "row_offset: integer")
"""A client's section: one client, a date window, one page."""
REFERENCE_PARAMETERS = (
    "search: string",
    "start_date: date",
    "end_date: date",
    "row_offset: integer",
)
"""A reference section: a search text, a date window, one page."""
MAX_ROWS = 50
"""The most rows one call returns; a call that has more says so and gives the next offset."""


class ClientSection(StrEnum):
    """Sections available through the corresponding MCP tool."""

    PROFILE = "profile"
    RELATIONSHIP = "relationship"
    COMPANY = "company"
    CONTACTS = "contacts"
    ACTIVITIES = "activities"
    OPPORTUNITIES = "opportunities"
    CREDIT = "credit"
    FACILITIES = "facilities"
    COVENANTS = "covenants"
    UTILIZATION = "utilization"
    BORROWING_BASE = "borrowing_base"
    CREDIT_EXCEPTIONS = "credit_exceptions"
    WORKING_CAPITAL = "working_capital"
    COMMITMENTS = "commitments"
    TREASURY_CONTROLS = "treasury_controls"
    SOURCE_READINESS = "source_readiness"
    SERVICE_REQUESTS = "service_requests"
    OPERATIONAL_SERVICE_CASES = "operational_service_cases"
    DEPOSITS = "deposits"
    DEPOSIT_HISTORY = "deposit_history"
    FINANCIALS = "financials"
    PROFITABILITY = "profitability"
    TRANSACTIONS = "transactions"
    PAYMENT_SUMMARIES = "payment_summaries"
    COUNTERPARTIES = "counterparties"
    BENCHMARKS = "benchmarks"
    PAYMENT_TOTALS = "payment_totals"


class ReferenceSection(StrEnum):
    """Sections available through the corresponding MCP tool."""

    CATALOG = "catalog"
    RATES = "rates"
    FX = "fx"
    COMMODITIES = "commodities"
    FORWARD_CURVE = "forward_curve"
    XREF = "xref"
    DATA_MODEL = "data_model"


@dataclass(frozen=True, kw_only=True)
class Query:
    """A named, read-only SQL query and its declared interface.

    Attributes:
        sql: The SELECT, with its parameters written as ``:name``.
        parameters: Each parameter as ``name: type``, in the order the tool passes them.
        classification: How sensitive the rows are.
        description: What the query returns; left out, it is derived from the query's name.
        max_rows: The most rows one call returns.
    """

    sql: str
    parameters: tuple[str, ...] = CLIENT_PARAMETERS
    classification: str = "confidential"
    description: str = ""
    max_rows: int = MAX_ROWS

    def text(self, name: str) -> str:
        """Return the query as the data source reads it: header lines, then the SQL."""
        description = self.description or (
            f"Reviewed {name.replace('_', ' ')} evidence; "
            "dated rows are bounded by the requested dates."
        )
        header = [
            f"-- description: {description}",
            *(f"-- param {parameter}" for parameter in self.parameters),
            f"-- max_rows: {self.max_rows}",
            f"-- classification: {self.classification}",
        ]
        return "\n".join(header) + "\n" + cleandoc(self.sql) + "\n"


@dataclass(frozen=True, kw_only=True)
class SectionSpec(Query):
    """A section: its query, and where to look up a row and how to cite it.

    Attributes:
        table: The table the rows come from, as cited in ``source_ref``.
        keys: The columns that identify a row; ``source_ref`` is built from them.
        note: What the model is told about the section, so it reads no more into it than it holds.
    """

    table: str
    keys: tuple[str, ...]
    note: str
