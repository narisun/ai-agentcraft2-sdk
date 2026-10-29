# Tools from a data model and your queries

`ac2 new mcp NAME --from-model DATA_DIR --queries QUERIES_DIR` makes an MCP server whose tools are
your queries. You write each query as a `.sql` file; the command turns each file into a tool and
uses the [data model](data-model.md) to describe it. The server is what `new mcp` always writes:
ordinary code-based tools, one function for each query, with the registry entries, pins, rules,
policy samples and tests. The query files are only a convenient way to say what the tools are.

## What you write

A query file is the existing named-query format: a header of comment lines, then one SELECT.
The file name is the tool's name.

```sql
-- description: Credit facilities of one client, with commitment, amount outstanding and maturity date.
-- param uen: string
-- max_rows: 50
-- classification: confidential
SELECT facility_id, uen, lender, facility_type, commitment, outstanding, maturity_date
FROM facilities
WHERE uen = :uen
ORDER BY maturity_date
```

`DATA_DIR` holds the CSV files and their `data_model.csv`, which must follow the standard
(`ac2 data model check DATA_DIR`).

```bat
ac2 new mcp rm-data --from-model data --queries queries --propose
ac2 new mcp rm-data --from-model data --queries queries
```

`--propose` shows the tools and writes nothing.

## What the model adds

- **A tool's description** is the query's description, and, for a query over one table, says what one
  row is: `... One row per facility_id.` A query over several tables gets no such sentence.
- **Each argument is described by the column it is compared with.** For `WHERE uen = :uen` the
  argument `uen` is described by the model's description of `facilities.uen`, and a model reading the
  tool's input schema sees it. An argument that is not compared with a column is declared plainly.
- **The test call of each tool uses a value from the data**, taken from the same column, so the
  generated tests return rows.

```python
@server.tool(name="rm-data.facilities_by_uen")
async def facilities_by_uen(
    uen: Annotated[str, Field(description="Borrowing client.")],
) -> dict[str, Any]:
    "Credit facilities of one client, with commitment, amount outstanding and maturity date. One row per facility_id."
    result = await source.query("facilities_by_uen", {"uen": uen})
    return result.to_payload()
```

## What it refuses

Each is reported with the file and what to do:

- a model that does not follow the standard, or a folder with no `data_model.csv`;
- a query the library does not accept (it writes, has an undeclared parameter, does not parse);
- a query that reads a table or uses a column the model does not describe, so that nothing a tool
  says about its data is missing from the model. A name the query defines itself (an alias, a column
  of a `WITH` subquery) is not checked;
- a description under 20 characters: a model reads it to choose the tool;
- a query that does not run over the files. Someone wrote it, so it is an error, not left out.

A declared type that differs from the column's type in the model (`claim_id` declared `string` where
the model says `integer`) is a note, not an error.

## Masking and classification

- A column whose name looks personal (`email`, `phone`, ...) is masked for analysts. `--mask COLUMN`
  masks more columns, and may be repeated. The proposal lists what is masked, to check.
- A query's classification is the header's. A query without one is **confidential**, and a note says so.

## Changing the tools

The server holds its own copy of the queries in `queries/`, written from yours. To add or change a
query, change the file and run the same command again with `--force`. It rewrites `server.py`, the
tests and the README, so keep changes of your own elsewhere. Run without `--force` the command only
reports that files differ.

## What it does not do

It does not write queries. `--from-model` without `--queries` is refused; proposing queries from the
model is a separate step. Masking comes from column names and `--mask`, because the data model has
no field for sensitivity. And what a caller may see is still decided by policy at run time, as for
every server.
