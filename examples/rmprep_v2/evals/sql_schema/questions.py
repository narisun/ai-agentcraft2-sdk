"""Set 2: SQL questions with gold SQL, written against the rmprep-v2 data. As of 2026-08-03.

Each question says which columns to return and in what order, so a result can be compared with the
gold result by value. ``tests`` names what the question needs beyond the table names: a spelling of
a value (V), a join (J), a relation that is not a key (R), a time window (T), a latest-per-group (L).
"""

AS_OF = "2026-08-03"

QUESTIONS: list[dict[str, str]] = [
    {
        "id": "S01",
        "tests": "T",
        "question": "Which facilities of client ALLO-0001 mature after 2026-08-03? Return facility_id, facility_type, maturity_date.",
        "gold": "SELECT facility_id, facility_type, maturity_date FROM facilities WHERE uen = 'ALLO-0001' AND maturity_date > '2026-08-03'",
    },
    {
        "id": "S02",
        "tests": "J",
        "question": "List every covenant that is not in compliance, with the name of the client it belongs to. Return client name, facility_id, covenant name.",
        "gold": "SELECT a.name, c.facility_id, c.name FROM covenants c JOIN facilities f ON f.facility_id = c.facility_id JOIN accounts a ON a.uen = f.uen WHERE c.in_compliance = false",
    },
    {
        "id": "S03",
        "tests": "V",
        "question": "How many clients are in the upper mid-market segment? Return the count.",
        "gold": "SELECT COUNT(*) FROM accounts WHERE segment = 'Upper Mid-Market'",
    },
    {
        "id": "S04",
        "tests": "J",
        "question": "What is the total outstanding amount of facilities by lender, largest first? Return lender, total outstanding.",
        "gold": "SELECT lender, SUM(outstanding) FROM facilities GROUP BY lender ORDER BY SUM(outstanding) DESC",
    },
    {
        "id": "S05",
        "tests": "J",
        "question": "For every client with a covenant that is not in compliance, give the client name and its current risk rating. Return client name, risk rating. One row per client.",
        "gold": "SELECT DISTINCT a.name, m.risk_rating FROM covenants c JOIN facilities f ON f.facility_id = c.facility_id JOIN accounts a ON a.uen = f.uen JOIN credit_metrics m ON m.uen = a.uen WHERE c.in_compliance = false",
    },
    {
        "id": "S06",
        "tests": "VT",
        "question": "How much did client CANA-0010 receive in payments from 2025-08-01 to 2026-07-31, in each currency? Return currency, total amount.",
        "gold": "SELECT currency, SUM(amount) FROM transactions WHERE uen = 'CANA-0010' AND direction = 'inbound' AND value_date BETWEEN '2025-08-01' AND '2026-07-31' GROUP BY currency",
    },
    {
        "id": "S07",
        "tests": "L",
        "question": "For client ALLO-0001, what is the most recent month-end average balance of each account type? Return account type, month-end date, average balance.",
        "gold": "SELECT account_type, as_of, avg_balance FROM balances WHERE uen = 'ALLO-0001' AND as_of = (SELECT MAX(b2.as_of) FROM balances b2 WHERE b2.uen = balances.uen AND b2.account_type = balances.account_type)",
    },
    {
        "id": "S08",
        "tests": "V",
        "question": "Which opportunities of client BENC-0003 are still open, meaning not closed won or closed lost? Return opportunity_id, product, stage, amount, close_date.",
        "gold": "SELECT opportunity_id, product, stage, amount, close_date FROM opportunities WHERE uen = 'BENC-0003' AND stage NOT IN ('Closed Won', 'Closed Lost')",
    },
    {
        "id": "S09",
        "tests": "R",
        "question": "Which catalog products does client JONE-0022 meet the minimum annual revenue for? Return product_id, product name.",
        "gold": "SELECT p.product_id, p.name FROM catalog p JOIN accounts a ON a.annual_revenue >= p.min_annual_revenue WHERE a.uen = 'JONE-0022'",
    },
    {
        "id": "S10",
        "tests": "J",
        "question": "Which clients have had no logged activity since 2026-02-03? Return uen, client name.",
        "gold": "SELECT a.uen, a.name FROM accounts a WHERE NOT EXISTS (SELECT 1 FROM activities x WHERE x.uen = a.uen AND x.activity_date >= '2026-02-03')",
    },
    {
        "id": "S11",
        "tests": "L",
        "question": "Which facilities were more than 80 percent utilized in their most recent month? Return facility_id, month-end date, utilization percentage.",
        "gold": "SELECT u.facility_id, u.as_of, u.utilization_pct FROM utilization u WHERE u.as_of = (SELECT MAX(x.as_of) FROM utilization x WHERE x.facility_id = u.facility_id) AND u.utilization_pct > 80",
    },
    {
        "id": "S12",
        "tests": "VJ",
        "question": "Which five US clients have the largest total facility commitment? Return client name, total commitment, largest first.",
        "gold": "SELECT a.name, SUM(f.commitment) FROM facilities f JOIN accounts a ON a.uen = f.uen WHERE a.country = 'USA' GROUP BY a.name ORDER BY SUM(f.commitment) DESC LIMIT 5",
    },
    {
        "id": "S13",
        "tests": "V",
        "question": "What are the three largest outgoing payment counterparties of client JONE-0022 by annual value? Return counterparty, annual value, largest first.",
        "gold": "SELECT counterparty, annual_value FROM counterparties WHERE uen = 'JONE-0022' AND direction = 'outbound' ORDER BY annual_value DESC LIMIT 3",
    },
    {
        "id": "S14",
        "tests": "VT",
        "question": "Which credit exceptions are overdue as of 2026-08-03: not yet received, due before that date, and still open? Return exception_id, uen, due_date, severity.",
        "gold": "SELECT exception_id, uen, due_date, severity FROM credit_exceptions WHERE received_date IS NULL AND due_date < '2026-08-03' AND status = 'Open'",
    },
    {
        "id": "S15",
        "tests": "V",
        "question": "What was the annual revenue of client JONE-0022 in fiscal year 2025 according to its financial statements? Return the value and its unit.",
        "gold": "SELECT value, unit FROM financial_facts WHERE uen = 'JONE-0022' AND concept = 'Revenues' AND fiscal_year = 'FY2025' AND is_annual = true",
    },
    {
        "id": "S16",
        "tests": "L",
        "question": "For each client with working capital data, how many days did days-sales-outstanding change between its two most recent periods? Return uen, latest period_end, change in dso_days (latest minus previous). Leave out clients with only one period.",
        "gold": "WITH t AS (SELECT uen, period_end, dso_days - LAG(dso_days) OVER (PARTITION BY uen ORDER BY period_end) AS change, ROW_NUMBER() OVER (PARTITION BY uen ORDER BY period_end DESC) AS rn FROM working_capital) SELECT uen, period_end, change FROM t WHERE rn = 1 AND change IS NOT NULL",
    },
    {
        "id": "S17",
        "tests": "VJ",
        "question": "For upper mid-market clients, how many service requests that are not closed are there for each product? Return product, number of requests.",
        "gold": "SELECT s.product, COUNT(*) FROM service_requests s JOIN accounts a ON a.uen = s.uen WHERE a.segment = 'Upper Mid-Market' AND s.status <> 'Closed' GROUP BY s.product",
    },
    {
        "id": "S18",
        "tests": "VJT",
        "question": "Which lower mid-market clients have a facility maturing before 2027-01-01? Return client name, facility_id, maturity_date.",
        "gold": "SELECT a.name, f.facility_id, f.maturity_date FROM facilities f JOIN accounts a ON a.uen = f.uen WHERE a.segment = 'Lower Mid-Market' AND f.maturity_date < '2027-01-01'",
    },
    {
        "id": "S19",
        "tests": "R",
        "question": "Compare each client's net interest income in the relationship table with the sum of revenue_nii in product profitability for the period FY2025. List the clients where the two differ by more than 25 percent of the relationship figure. Return uen, relationship nii, profitability nii.",
        "gold": "SELECT r.uen, r.nii, p.total FROM relationship r JOIN (SELECT uen, SUM(revenue_nii) AS total FROM product_profitability WHERE period = 'FY2025' GROUP BY uen) p ON p.uen = r.uen WHERE ABS(r.nii - p.total) > 0.25 * ABS(r.nii)",
    },
    {
        "id": "S20",
        "tests": "VJ",
        "question": "Which primary contacts hold the title CFO or Treasurer? Return client name, first name, last name, title.",
        "gold": "SELECT a.name, c.first_name, c.last_name, c.title FROM contacts c JOIN accounts a ON a.uen = c.uen WHERE c.is_primary = true AND c.title IN ('CFO', 'Treasurer')",
    },
    {
        "id": "S21",
        "tests": "L",
        "question": "For each client, which is its facility with the largest commitment? Return uen, facility_id, commitment. One row per client.",
        "gold": "SELECT uen, facility_id, commitment FROM (SELECT uen, facility_id, commitment, ROW_NUMBER() OVER (PARTITION BY uen ORDER BY commitment DESC) AS rn FROM facilities) WHERE rn = 1",
    },
]
