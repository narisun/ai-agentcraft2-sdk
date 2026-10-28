"""The sections ``get_client_records`` returns: for each, its SQL, its table and its note.

A section is read for one client (``:uen``), inside a date window (``:start_date`` to
``:end_date``) and one page at a time (``:row_offset``); a query returns at most 50 rows. The SQL
is written in the dialect of the production database (Redshift) and checked when the service
starts. ``keys`` are the columns that identify a row: ``source_ref`` cites them. ``note`` is
what the model is told about the section, so it does not read more into the data than it holds.

``rmprep_v2_data.queries`` turns each entry into the query the data source loads.
"""

from rmprep_v2_data.sections import SectionSpec

CLIENT_SECTIONS: dict[str, SectionSpec] = {
    "profile": SectionSpec(
        table="accounts",
        keys=("uen",),
        note="Current CRM snapshot; no observation date supplied. Revenue is client sales in USD; currency is the primary operating currency.",
        sql="""
            SELECT t."uen" AS "uen",
                   t."connected_uen" AS "connected_uen",
                   t."name" AS "name",
                   t."industry" AS "industry",
                   LPAD(CAST(t.sic_code AS VARCHAR), 4, '0') AS sic_code,
                   CAST(t.naics_code AS VARCHAR) AS naics_code,
                   t."annual_revenue" AS "annual_revenue",
                   t."employees" AS "employees",
                   t."city" AS "city",
                   t."state" AS "state",
                   t."country" AS "country",
                   t."currency" AS "currency",
                   t."relationship_manager" AS "relationship_manager",
                   t."segment" AS "segment",
                   t."customer_since" AS "customer_since",
                   t."founded_year" AS "founded_year",
                   t."business_description" AS "business_description",
                   t."locations" AS "locations",
                   t."nps" AS "nps",
                   t."logo_url" AS "logo_url"
            FROM "accounts" t
            WHERE t.uen = :uen AND :start_date <= :end_date
            ORDER BY "uen"
            OFFSET :row_offset
        """,
    ),
    "relationship": SectionSpec(
        table="relationship",
        keys=("uen",),
        note="Current relationship snapshot without an as-of date. nii/nir are bank-attributed income in USD, not client sales or payment fees. Do not infer freshness from review dates.",
        sql="""
            SELECT t."uen" AS "uen",
                   t."nii" AS "nii",
                   t."nir" AS "nir",
                   t."total_deposits" AS "total_deposits",
                   t."roe" AS "roe",
                   t."coverage_team" AS "coverage_team",
                   t."strategic_priorities" AS "strategic_priorities",
                   t."ask" AS "ask",
                   t."commercial" AS "commercial",
                   t."tps" AS "tps",
                   t."capital_markets" AS "capital_markets",
                   t."wealth" AS "wealth",
                   t."retail" AS "retail",
                   t."products_held" AS "products_held",
                   t."total_exposure" AS "total_exposure",
                   t."primary_operating_account" AS "primary_operating_account",
                   t."last_review_date" AS "last_review_date",
                   t."next_review_date" AS "next_review_date",
                   t."wallet_share_pct" AS "wallet_share_pct",
                   t."raroc_pct" AS "raroc_pct"
            FROM "relationship" t
            WHERE t.uen = :uen AND :start_date <= :end_date
            ORDER BY "uen"
            OFFSET :row_offset
        """,
    ),
    "company": SectionSpec(
        table="company",
        keys=("uen",),
        note="Company lookup is supplied data, not independently verified filings. Blank CIK means no filing identifier is supplied.",
        sql="""
            SELECT t."uen" AS "uen",
                   t."company_name" AS "company_name",
                   t."cik" AS "cik"
            FROM "company" t
            WHERE t.uen = :uen AND :start_date <= :end_date
            ORDER BY "uen"
            OFFSET :row_offset
        """,
    ),
    "contacts": SectionSpec(
        table="contacts",
        keys=("uen", "first_name", "last_name", "title"),
        note="Contact snapshot has no observation date. Use only contact details relevant to the request.",
        sql="""
            SELECT t."uen" AS "uen",
                   t."first_name" AS "first_name",
                   t."last_name" AS "last_name",
                   t."title" AS "title",
                   t."email" AS "email",
                   t."phone" AS "phone",
                   t."is_primary" AS "is_primary",
                   t."role" AS "role",
                   t."department" AS "department"
            FROM "contacts" t
            WHERE t.uen = :uen AND :start_date <= :end_date
            ORDER BY "uen", "first_name", "last_name", "title"
            OFFSET :row_offset
        """,
    ),
    "activities": SectionSpec(
        table="activities",
        keys=("activity_id",),
        note="",
        sql="""
            SELECT t."activity_id" AS "activity_id",
                   t."uen" AS "uen",
                   t."type" AS "type",
                   t."subject" AS "subject",
                   t."activity_date" AS "activity_date",
                   t."owner" AS "owner",
                   t."notes" AS "notes"
            FROM "activities" t
            WHERE t.uen = :uen AND t.activity_date BETWEEN :start_date AND :end_date
            ORDER BY activity_date DESC, activity_id
            OFFSET :row_offset
        """,
    ),
    "opportunities": SectionSpec(
        table="opportunities",
        keys=("opportunity_id",),
        note="Status is the current supplied snapshot, not a historical status reconstruction. Amount is opportunity value, not booked bank revenue.",
        sql="""
            SELECT t."opportunity_id" AS "opportunity_id",
                   t."uen" AS "uen",
                   t."name" AS "name",
                   t."product" AS "product",
                   t."stage" AS "stage",
                   t."amount" AS "amount",
                   t."probability" AS "probability",
                   t."close_date" AS "close_date",
                   t."competitor" AS "competitor",
                   t."owner" AS "owner",
                   t."created_date" AS "created_date",
                   t."next_step" AS "next_step"
            FROM "opportunities" t
            WHERE t.uen = :uen AND t.created_date BETWEEN :start_date AND :end_date
            ORDER BY created_date DESC, opportunity_id
            OFFSET :row_offset
        """,
    ),
    "credit": SectionSpec(
        table="credit_metrics",
        keys=("uen",),
        note="Rating date is supplied; other ratios may have different effective dates. Keep covenant-specific tested values separate.",
        sql="""
            SELECT t."uen" AS "uen",
                   t."risk_rating" AS "risk_rating",
                   t."prior_risk_rating" AS "prior_risk_rating",
                   t."risk_rating_as_of" AS "risk_rating_as_of",
                   t."annual_review_date" AS "annual_review_date",
                   t."trigger_exceptions" AS "trigger_exceptions",
                   t."ebitda" AS "ebitda",
                   t."profit_margin_pct" AS "profit_margin_pct",
                   t."debt_to_net_worth" AS "debt_to_net_worth",
                   t."fixed_charge_coverage" AS "fixed_charge_coverage",
                   t."debt_service_coverage" AS "debt_service_coverage",
                   t."current_ratio" AS "current_ratio",
                   t."pd_pct" AS "pd_pct",
                   t."lgd_pct" AS "lgd_pct",
                   t."rating_outlook" AS "rating_outlook",
                   t."watch_status" AS "watch_status"
            FROM "credit_metrics" t
            WHERE t.uen = :uen AND t.risk_rating_as_of BETWEEN :start_date AND :end_date
            ORDER BY "uen"
            OFFSET :row_offset
        """,
    ),
    "facilities": SectionSpec(
        table="facilities",
        keys=("facility_id",),
        note="Facility amounts are USD per data_model.csv. No balance as-of date is supplied; maturity countdown uses the requested end date. our_hold differs from commitment on syndications.",
        sql="""
            SELECT t."facility_id" AS "facility_id",
                   t."uen" AS "uen",
                   t."lender" AS "lender",
                   t."facility_type" AS "facility_type",
                   t."commitment" AS "commitment",
                   t."outstanding" AS "outstanding",
                   t."interest_rate_basis" AS "interest_rate_basis",
                   t."origination_date" AS "origination_date",
                   t."maturity_date" AS "maturity_date",
                   t."purpose" AS "purpose",
                   t."collateral_type" AS "collateral_type",
                   t."collateral_value" AS "collateral_value",
                   t."ltv_pct" AS "ltv_pct",
                   t."our_hold" AS "our_hold",
                   t."facility_risk_rating" AS "facility_risk_rating",
                   t."spread_bps" AS "spread_bps",
                   t."all_in_rate_pct" AS "all_in_rate_pct",
                   t."unused_fee_bps" AS "unused_fee_bps",
                   DATEDIFF(day, :end_date, t.maturity_date) AS days_to_maturity,
                   'USD' AS amount_currency
            FROM "facilities" t
            WHERE t.uen = :uen AND t.origination_date BETWEEN :start_date AND :end_date
            ORDER BY maturity_date, facility_id
            OFFSET :row_offset
        """,
    ),
    "covenants": SectionSpec(
        table="covenants",
        keys=("facility_id", "name"),
        note="No test observation date is supplied; next_test_date is not the test date. Use in_compliance as the recorded verdict.",
        sql="""
            SELECT t."facility_id" AS "facility_id",
                   t."name" AS "name",
                   t."threshold" AS "threshold",
                   t."current_value" AS "current_value",
                   t."in_compliance" AS "in_compliance",
                   t."next_test_date" AS "next_test_date",
                   f.uen AS uen
            FROM "covenants" t JOIN facilities f ON f.facility_id = t.facility_id
            WHERE f.uen = :uen AND :start_date <= :end_date
            ORDER BY "facility_id", "name"
            OFFSET :row_offset
        """,
    ),
    "utilization": SectionSpec(
        table="utilization",
        keys=("facility_id", "as_of"),
        note="Dated monthly balances; not necessarily the same snapshot as facilities.outstanding. Do not infer financial health from utilization alone.",
        sql="""
            SELECT t."facility_id" AS "facility_id",
                   t."as_of" AS "as_of",
                   t."drawn" AS "drawn",
                   t."available" AS "available",
                   t."utilization_pct" AS "utilization_pct",
                   f.uen AS uen,
                   'USD' AS amount_currency
            FROM "utilization" t JOIN facilities f ON f.facility_id = t.facility_id
            WHERE f.uen = :uen AND t.as_of BETWEEN :start_date AND :end_date
            ORDER BY as_of DESC, facility_id
            OFFSET :row_offset
        """,
    ),
    "borrowing_base": SectionSpec(
        table="borrowing_base",
        keys=("facility_id", "as_of"),
        note="availability is supplied excess availability after reserves. Percentage is calculated only when certificate currency matches the USD facility commitment; missing conversion stays null.",
        sql="""
            SELECT t."facility_id" AS "facility_id",
                   t."uen" AS "uen",
                   t."as_of" AS "as_of",
                   t."eligible_ar" AS "eligible_ar",
                   t."eligible_inventory" AS "eligible_inventory",
                   t."advance_rate_ar_pct" AS "advance_rate_ar_pct",
                   t."advance_rate_inventory_pct" AS "advance_rate_inventory_pct",
                   t."availability" AS "availability",
                   t."currency" AS "currency",
                   f.commitment AS facility_commitment_usd,
                   CASE WHEN t.currency = 'USD' THEN ROUND(100.0 * t.availability / NULLIF(f.commitment, 0), 2) END AS availability_pct_of_commitment
            FROM "borrowing_base" t JOIN facilities f ON f.facility_id = t.facility_id AND f.uen = t.uen
            WHERE t.uen = :uen AND t.as_of BETWEEN :start_date AND :end_date
            ORDER BY as_of DESC, facility_id
            OFFSET :row_offset
        """,
    ),
    "credit_exceptions": SectionSpec(
        table="credit_exceptions",
        keys=("exception_id",),
        note="",
        sql="""
            SELECT t."exception_id" AS "exception_id",
                   t."uen" AS "uen",
                   t."facility_id" AS "facility_id",
                   t."exception_type" AS "exception_type",
                   t."requirement" AS "requirement",
                   t."due_date" AS "due_date",
                   t."received_date" AS "received_date",
                   t."status" AS "status",
                   t."severity" AS "severity",
                   t."owner" AS "owner",
                   t."waiver_expiry" AS "waiver_expiry",
                   t."cure_plan" AS "cure_plan",
                   t."last_updated" AS "last_updated"
            FROM "credit_exceptions" t
            WHERE t.uen = :uen AND t.last_updated BETWEEN :start_date AND :end_date
            ORDER BY due_date, exception_id
            OFFSET :row_offset
        """,
    ),
    "working_capital": SectionSpec(
        table="working_capital",
        keys=("uen", "period_end"),
        note="Compare consecutive source periods in the same currency; empty history is a gap, not zero.",
        sql="""
            SELECT t."uen" AS "uen",
                   t."period_end" AS "period_end",
                   t."dso_days" AS "dso_days",
                   t."dio_days" AS "dio_days",
                   t."dpo_days" AS "dpo_days",
                   t."cash_conversion_cycle_days" AS "cash_conversion_cycle_days",
                   t."ar_over_90_pct" AS "ar_over_90_pct",
                   t."inventory_aging_pct" AS "inventory_aging_pct",
                   t."net_working_capital" AS "net_working_capital",
                   t."currency" AS "currency",
                   t."data_quality" AS "data_quality"
            FROM "working_capital" t
            WHERE t.uen = :uen AND t.period_end BETWEEN :start_date AND :end_date
            ORDER BY period_end DESC
            OFFSET :row_offset
        """,
    ),
    "commitments": SectionSpec(
        table="commitments",
        keys=("commitment_id",),
        note="",
        sql="""
            SELECT t."commitment_id" AS "commitment_id",
                   t."uen" AS "uen",
                   t."meeting_date" AS "meeting_date",
                   t."owner" AS "owner",
                   t."commitment" AS "commitment",
                   t."due_date" AS "due_date",
                   t."status" AS "status",
                   t."client_or_bank" AS "client_or_bank",
                   t."priority" AS "priority",
                   t."last_updated" AS "last_updated"
            FROM "commitments" t
            WHERE t.uen = :uen AND t.last_updated BETWEEN :start_date AND :end_date
            ORDER BY due_date, commitment_id
            OFFSET :row_offset
        """,
    ),
    "treasury_controls": SectionSpec(
        table="treasury_controls",
        keys=("control_id",),
        note="",
        sql="""
            SELECT t."control_id" AS "control_id",
                   t."uen" AS "uen",
                   t."product" AS "product",
                   t."control_type" AS "control_type",
                   t."status" AS "status",
                   t."severity" AS "severity",
                   t."observation" AS "observation",
                   t."detected_date" AS "detected_date",
                   t."owner" AS "owner",
                   t."target_date" AS "target_date",
                   t."last_updated" AS "last_updated"
            FROM "treasury_controls" t
            WHERE t.uen = :uen AND t.last_updated BETWEEN :start_date AND :end_date
            ORDER BY target_date, control_id
            OFFSET :row_offset
        """,
    ),
    "source_readiness": SectionSpec(
        table="source_readiness",
        keys=("readiness_id",),
        note="Missing readiness rows mean readiness has not been assessed here; they do not establish that a domain is ready.",
        sql="""
            SELECT t."readiness_id" AS "readiness_id",
                   t."uen" AS "uen",
                   t."domain" AS "domain",
                   t."as_of_date" AS "as_of_date",
                   t."completeness_pct" AS "completeness_pct",
                   t."reconciliation_status" AS "reconciliation_status",
                   t."quality_status" AS "quality_status",
                   t."issue" AS "issue",
                   t."owner" AS "owner",
                   t."last_updated" AS "last_updated"
            FROM "source_readiness" t
            WHERE t.uen = :uen AND t.last_updated BETWEEN :start_date AND :end_date
            ORDER BY domain, readiness_id
            OFFSET :row_offset
        """,
    ),
    "service_requests": SectionSpec(
        table="service_requests",
        keys=("uen", "opened_date", "request", "product"),
        note="Current supplied status; an open row may be old. Do not infer a new incident from an old open case.",
        sql="""
            SELECT t."uen" AS "uen",
                   t."request" AS "request",
                   t."product" AS "product",
                   t."close_date" AS "close_date",
                   t."status" AS "status",
                   t."category" AS "category",
                   t."priority" AS "priority",
                   t."opened_date" AS "opened_date"
            FROM "service_requests" t
            WHERE t.uen = :uen AND t.opened_date BETWEEN :start_date AND :end_date
            ORDER BY opened_date DESC, request, product
            OFFSET :row_offset
        """,
    ),
    "operational_service_cases": SectionSpec(
        table="operational_service_cases",
        keys=("uen", "opened_date", "request", "product"),
        note="Additional operational cases. Preserve provenance separately from service_requests; do not double-count the same case.",
        sql="""
            SELECT t."uen" AS "uen",
                   t."request" AS "request",
                   t."product" AS "product",
                   t."close_date" AS "close_date",
                   t."status" AS "status",
                   t."category" AS "category",
                   t."priority" AS "priority",
                   t."opened_date" AS "opened_date"
            FROM "operational_service_cases" t
            WHERE t.uen = :uen AND t.opened_date BETWEEN :start_date AND :end_date
            ORDER BY opened_date DESC, request, product
            OFFSET :row_offset
        """,
    ),
    "deposits": SectionSpec(
        table="balances",
        keys=("uen", "as_of", "account_type", "currency"),
        note="Latest available deposit month. Keep native currencies separate. Average collected balances, end balances and idle estimates have different meanings.",
        sql="""
            SELECT t."uen" AS "uen",
                   t."as_of" AS "as_of",
                   t."account_type" AS "account_type",
                   t."avg_balance" AS "avg_balance",
                   t."idle_estimate" AS "idle_estimate",
                   t."apy_pct" AS "apy_pct",
                   t."currency" AS "currency",
                   t."period_end_balance" AS "period_end_balance",
                   t."earnings_credit_rate_pct" AS "earnings_credit_rate_pct",
                   t."sweep_enabled" AS "sweep_enabled"
            FROM "balances" t
            WHERE t.uen = :uen AND t.as_of = (SELECT MAX(b.as_of) FROM balances b WHERE b.uen = :uen AND b.as_of BETWEEN :start_date AND :end_date) AND t.as_of BETWEEN :start_date AND :end_date
            ORDER BY account_type, currency
            OFFSET :row_offset
        """,
    ),
    "deposit_history": SectionSpec(
        table="balances",
        keys=("uen", "as_of", "account_type", "currency"),
        note="Monthly average balances cannot be summed across months as a current balance. Preserve currency and account type.",
        sql="""
            SELECT t."uen" AS "uen",
                   t."as_of" AS "as_of",
                   t."account_type" AS "account_type",
                   t."avg_balance" AS "avg_balance",
                   t."idle_estimate" AS "idle_estimate",
                   t."apy_pct" AS "apy_pct",
                   t."currency" AS "currency",
                   t."period_end_balance" AS "period_end_balance",
                   t."earnings_credit_rate_pct" AS "earnings_credit_rate_pct",
                   t."sweep_enabled" AS "sweep_enabled"
            FROM "balances" t
            WHERE t.uen = :uen AND t.as_of BETWEEN :start_date AND :end_date
            ORDER BY as_of DESC, account_type, currency
            OFFSET :row_offset
        """,
    ),
    "financials": SectionSpec(
        table="financial_facts",
        keys=("uen", "concept", "period_end", "statement", "is_annual"),
        note="Future period ends are excluded. No filing/publication date is supplied, so a period cutoff does not prove information was available historically. Preserve unit, fiscal year and annual/interim flag.",
        sql="""
            SELECT t."uen" AS "uen",
                   t."concept" AS "concept",
                   t."label" AS "label",
                   t."statement" AS "statement",
                   t."value" AS "value",
                   t."unit" AS "unit",
                   t."fiscal_year" AS "fiscal_year",
                   t."period_end" AS "period_end",
                   t."is_annual" AS "is_annual"
            FROM "financial_facts" t
            WHERE t.uen = :uen AND t.period_end BETWEEN :start_date AND :end_date
            ORDER BY period_end DESC, concept, statement, is_annual
            OFFSET :row_offset
        """,
    ),
    "profitability": SectionSpec(
        table="product_profitability",
        keys=("uen", "period", "line_of_business", "product"),
        note="All supplied period labels are returned; no exact observation date is supplied. USD attributed income, expense, risk cost and capital; do not combine FY and overlapping TTM periods.",
        sql="""
            SELECT t."uen" AS "uen",
                   t."period" AS "period",
                   t."line_of_business" AS "line_of_business",
                   t."product" AS "product",
                   t."revenue_nii" AS "revenue_nii",
                   t."revenue_fee" AS "revenue_fee",
                   t."expense_direct" AS "expense_direct",
                   t."risk_cost" AS "risk_cost",
                   t."allocated_capital" AS "allocated_capital"
            FROM "product_profitability" t
            WHERE t.uen = :uen AND :start_date <= :end_date
            ORDER BY period DESC, line_of_business, product
            OFFSET :row_offset
        """,
    ),
    "transactions": SectionSpec(
        table="transactions",
        keys=("transaction_id",),
        note="Each amount and fee is in its own currency. For totals use payment_totals instead of summing a truncated page.",
        sql="""
            SELECT t."transaction_id" AS "transaction_id",
                   t."uen" AS "uen",
                   t."rail" AS "rail",
                   t."direction" AS "direction",
                   t."value_date" AS "value_date",
                   t."amount" AS "amount",
                   t."currency" AS "currency",
                   t."fee" AS "fee",
                   t."counterparty" AS "counterparty",
                   t."memo" AS "memo"
            FROM "transactions" t
            WHERE t.uen = :uen AND t.value_date BETWEEN :start_date AND :end_date
            ORDER BY value_date DESC, transaction_id
            OFFSET :row_offset
        """,
    ),
    "payment_summaries": SectionSpec(
        table="payment_summaries",
        keys=("uen", "rail", "direction", "period_end", "currency"),
        note="Source summary totals can mix native currencies; currency is only the modal bucket label. Do not present these totals as a single-currency amount. Use transaction-derived payment_totals.",
        sql="""
            SELECT t."uen" AS "uen",
                   t."rail" AS "rail",
                   t."direction" AS "direction",
                   t."period_start" AS "period_start",
                   t."period_end" AS "period_end",
                   t."transaction_count" AS "transaction_count",
                   t."total_value" AS "total_value",
                   t."currency" AS "currency",
                   t."fee_revenue" AS "fee_revenue"
            FROM "payment_summaries" t
            WHERE t.uen = :uen AND t.period_end BETWEEN :start_date AND :end_date
            ORDER BY period_end DESC, rail, direction, currency
            OFFSET :row_offset
        """,
    ),
    "counterparties": SectionSpec(
        table="counterparties",
        keys=("uen", "counterparty", "direction", "rail", "currency"),
        note="No measurement period is supplied. Dictionary says annual_value is USD while currency describes the corridor; verify this mapping before currency-based calculations.",
        sql="""
            SELECT t."uen" AS "uen",
                   t."counterparty" AS "counterparty",
                   t."role" AS "role",
                   t."direction" AS "direction",
                   t."rail" AS "rail",
                   t."country" AS "country",
                   t."currency" AS "currency",
                   t."annual_value" AS "annual_value",
                   t."counterparty_bank" AS "counterparty_bank",
                   t."note" AS "note"
            FROM "counterparties" t
            WHERE t.uen = :uen AND :start_date <= :end_date
            ORDER BY annual_value DESC, counterparty, direction, rail, currency
            OFFSET :row_offset
        """,
    ),
    "benchmarks": SectionSpec(
        table="benchmarks",
        keys=("naics_industry", "indicator"),
        note="Matched by first five NAICS digits. No observation date or cohort methodology is supplied; preserve units. Missing comparison stays missing.",
        sql="""
            SELECT t."naics_industry" AS "naics_industry",
                   t."sector" AS "sector",
                   t."indicator" AS "indicator",
                   t."external_macro" AS "external_macro",
                   t."cohort_avg" AS "cohort_avg",
                   t."unit" AS "unit",
                   t."higher_is_better" AS "higher_is_better"
            FROM "benchmarks" t JOIN accounts a ON a.uen = :uen
            WHERE t.naics_industry = CAST(SUBSTRING(CAST(a.naics_code AS VARCHAR), 1, 5) AS BIGINT) AND :start_date <= :end_date
            ORDER BY "naics_industry", "indicator"
            OFFSET :row_offset
        """,
    ),
    "payment_totals": SectionSpec(
        table="transactions",
        keys=("uen", "rail", "direction", "currency", "first_value_date", "last_value_date"),
        note="Transaction-derived totals for the requested date window, separated by native currency. Gross fees are not the bank-attributed NIR. No currency conversion is applied.",
        sql="""
            SELECT t.uen AS uen,
                   COALESCE(r.xref_name, t.rail) AS rail,
                   COALESCE(d.xref_name, t.direction) AS direction,
                   t.currency AS currency, MIN(t.value_date) AS first_value_date,
                   MAX(t.value_date) AS last_value_date, COUNT(*) AS transaction_count,
                   SUM(CAST(t.amount AS DECIMAL(24, 2))) AS total_value,
                   SUM(CAST(t.fee AS DECIMAL(24, 2))) AS gross_fees
            FROM transactions t
            LEFT JOIN (SELECT LOWER(xref_value) AS value_key, MIN(xref_name) AS xref_name
                       FROM xref WHERE xref_type = 'payment_rail' GROUP BY LOWER(xref_value)) r
            ON LOWER(t.rail) = r.value_key
            LEFT JOIN (SELECT LOWER(xref_value) AS value_key, MIN(xref_name) AS xref_name
                       FROM xref WHERE xref_type = 'payment_direction' GROUP BY LOWER(xref_value)) d
            ON LOWER(t.direction) = d.value_key
            WHERE t.uen = :uen AND t.value_date BETWEEN :start_date AND :end_date
            GROUP BY t.uen, COALESCE(r.xref_name, t.rail), COALESCE(d.xref_name, t.direction), t.currency
            ORDER BY currency, rail, direction
            OFFSET :row_offset
        """,
    ),
}
