"""The sections ``get_reference_data`` returns: for each, its SQL and its table.

A reference section is read by a search text (``:search``, empty for all), inside a date window
(``:start_date`` to ``:end_date``) and one page at a time (``:row_offset``); a query returns at
most 50 rows. The tables are not about one client, so they are internal, not confidential. The SQL
is written in the dialect of the production database (Redshift) and checked when the service
starts. ``keys`` are the columns that identify a row: ``source_ref`` cites them.

``rmprep_v2_data.queries`` turns each entry into the query the data source loads.
"""

from functools import partial

from rmprep_v2_data.sections import REFERENCE_PARAMETERS, SectionSpec

NOTE = "Reference snapshot; dates, units and source labels are supplied values, not live retrieval. Catalog thresholds and pricing are indicative, not product approval."

reference = partial(
    SectionSpec, parameters=REFERENCE_PARAMETERS, classification="internal", note=NOTE
)

REFERENCE_SECTIONS: dict[str, SectionSpec] = {
    "catalog": reference(
        table="catalog",
        keys=("product_id",),
        sql="""
            SELECT t."product_id" AS "product_id",
                   t."name" AS "name",
                   t."family" AS "family",
                   t."description" AS "description",
                   t."client_benefit" AS "client_benefit",
                   t."min_annual_revenue" AS "min_annual_revenue",
                   t."min_dscr" AS "min_dscr",
                   t."max_risk_rating" AS "max_risk_rating",
                   t."eligibility_criteria" AS "eligibility_criteria",
                   t."revenue_to_bank" AS "revenue_to_bank",
                   t."typical_pricing" AS "typical_pricing"
            FROM "catalog" t
            WHERE STRPOS(LOWER(CAST(t.name AS VARCHAR)), LOWER(:search)) > 0 AND :start_date <= :end_date
            ORDER BY "product_id"
            OFFSET :row_offset
        """,
    ),
    "rates": reference(
        table="rates",
        keys=("name", "as_of"),
        sql="""
            SELECT t."name" AS "name",
                   t."value" AS "value",
                   t."unit" AS "unit",
                   t."as_of" AS "as_of",
                   t."source" AS "source"
            FROM "rates" t
            WHERE STRPOS(LOWER(CAST(t.name AS VARCHAR)), LOWER(:search)) > 0 AND t.as_of BETWEEN :start_date AND :end_date
            ORDER BY "name", "as_of"
            OFFSET :row_offset
        """,
    ),
    "fx": reference(
        table="fx",
        keys=("pair", "as_of"),
        sql="""
            SELECT t."pair" AS "pair",
                   t."rate" AS "rate",
                   t."as_of" AS "as_of",
                   t."source" AS "source",
                   t."ytd_change_pct" AS "ytd_change_pct",
                   t."fwd_3m_pct" AS "fwd_3m_pct",
                   t."fwd_12m_pct" AS "fwd_12m_pct",
                   t."implied_vol_3m_pct" AS "implied_vol_3m_pct"
            FROM "fx" t
            WHERE STRPOS(LOWER(CAST(t.pair AS VARCHAR)), LOWER(:search)) > 0 AND t.as_of BETWEEN :start_date AND :end_date
            ORDER BY "pair", "as_of"
            OFFSET :row_offset
        """,
    ),
    "commodities": reference(
        table="commodities",
        keys=("name", "as_of"),
        sql="""
            SELECT t."name" AS "name",
                   t."value" AS "value",
                   t."unit" AS "unit",
                   t."as_of" AS "as_of",
                   t."source" AS "source",
                   t."qoq_change_pct" AS "qoq_change_pct",
                   t."note" AS "note"
            FROM "commodities" t
            WHERE STRPOS(LOWER(CAST(t.name AS VARCHAR)), LOWER(:search)) > 0 AND t.as_of BETWEEN :start_date AND :end_date
            ORDER BY "name", "as_of"
            OFFSET :row_offset
        """,
    ),
    "forward_curve": reference(
        table="forward_curve",
        keys=("curve", "tenor", "as_of"),
        sql="""
            SELECT t."curve" AS "curve",
                   t."tenor" AS "tenor",
                   t."rate" AS "rate",
                   t."as_of" AS "as_of",
                   t."source" AS "source"
            FROM "forward_curve" t
            WHERE STRPOS(LOWER(CAST(t.curve AS VARCHAR)), LOWER(:search)) > 0 AND t.as_of BETWEEN :start_date AND :end_date
            ORDER BY "curve", "tenor", "as_of"
            OFFSET :row_offset
        """,
    ),
    "xref": reference(
        table="xref",
        keys=("xref_id",),
        sql="""
            SELECT t."xref_id" AS "xref_id",
                   t."xref_type" AS "xref_type",
                   t."xref_name" AS "xref_name",
                   t."xref_value" AS "xref_value",
                   t."xref_description" AS "xref_description"
            FROM "xref" t
            WHERE STRPOS(LOWER(CAST(t.xref_type AS VARCHAR)), LOWER(:search)) > 0 AND :start_date <= :end_date
            ORDER BY "xref_id"
            OFFSET :row_offset
        """,
    ),
    "data_model": reference(
        table="data_model",
        keys=("table", "column"),
        sql="""
            SELECT t."domain" AS "domain",
                   t."table" AS "table",
                   t."column" AS "column",
                   t."datatype" AS "datatype",
                   t."nullable" AS "nullable",
                   t."pk" AS "pk",
                   t."uk" AS "uk",
                   t."fk" AS "fk",
                   t."description" AS "description"
            FROM "data_model" t
            WHERE STRPOS(LOWER(CAST(t."table" AS VARCHAR)), LOWER(:search)) > 0 AND t."table" <> 'rm_scenarios' AND :start_date <= :end_date
            ORDER BY "table", "column"
            OFFSET :row_offset
        """,
    ),
}
