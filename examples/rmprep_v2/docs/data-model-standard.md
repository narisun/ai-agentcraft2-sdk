# The data model standard

`data/data_model.csv` is the one place that describes the data: what each table holds, how its rows
are identified, how tables join and what each column means. People read it to learn the data. A
model will read it to write queries. A missing description is a missing fact, so the file follows a
standard that a check enforces.

## The file

One row describes one column of one table. The header is exactly this, in this order:

| Column | Required | What goes in it |
| --- | --- | --- |
| `domain` | yes | The business area the table belongs to (`crm`, `lending`, `payments`). One domain per table. |
| `table` | yes | The table name, which is also the CSV file name (`covenants` is `covenants.csv`). |
| `table_description` | yes | What the table holds, in one or two sentences. **Repeat the same text on every row of the table.** |
| `column` | yes | The column name, in the order the CSV file has it. |
| `datatype` | yes | One of `string`, `integer`, `decimal`, `boolean`, `date`, `timestamp`. |
| `nullable` | yes | `Y` if the value may be empty, `N` if it may not. Never blank. |
| `pk` | no | `Y` if the column is part of the primary key. Every table has one. |
| `uk` | no | `Y` if the column is unique across the table. |
| `fk` | no | Where the value points: `domain.table.column`, then an optional note in brackets. |
| `description` | yes | What the column means: its unit, currency, allowed values and anything a reader could get wrong. |

Names (`domain`, `table`, `column`) are lower case words joined by underscores.

## Writing a good table description

- Say what one row is and what the table is for. A reader should know whether to use it.
- 20 to 300 characters, and end with a full stop.
- Say more than the table name repeated: "Accounts of clients." is not a description.
- Name the table it is easily confused with, if there is one (`service_requests` and
  `operational_service_cases`).
- Do not put values or thresholds in it. They belong in the column descriptions.

## The check

The check reads the model and the CSV files beside it. It is `check_data_model` in
`mcp-servers/rmprep-v2-data/src/rmprep_v2_data/data_model.py`, and the test
`test_the_supplied_data_model_follows_the_standard` runs it on every test run. A broken model
stops the build.

An **error** is a missing or wrong fact and fails the test:

- a required field is empty, or a name, datatype or flag is outside the standard;
- a table has no description, or its rows carry different descriptions, or it is too short, too
  long, has no full stop or only repeats the table name;
- a table has no primary key, or a primary key column is nullable;
- a column appears twice in a table, or a table appears in two domains;
- an `fk` names a table or column the model does not describe;
- a table has no CSV file, a CSV file has no table, or the columns differ from the file's header in
  name or order.

A **warning** is for a reviewer to look at. It does not fail the test:

- a column description under 15 characters;
- a primary key that includes a `decimal` column, which is usually a measure and not a key;
- an `fk` that names a table and no column.

## Review list for the team

The 32 table descriptions were drafted from each table's name, primary key and columns, and the
column descriptions. They need a reviewer who knows the business. Edit them in the CSV, on every
row of the table, then run the tests.

The check found these warnings in the supplied model. None is a defect in the data, and none was
changed:

- **Two primary keys include a measure.** `balances` includes `avg_balance` and `financial_facts`
  includes `value`. If the real key is smaller, say so; a model that reads the key to write a join
  or a deduplication would be misled.
- **Sixteen `fk` entries name a table and no column**, for example `reference.xref (payment_rail)`
  and `payments.transactions (reconciliation)`. For the vocabulary ones the join is
  `xref_type = 'payment_rail'` and `xref_value = <the column>`, which a plain `fk` cannot say.
  Decide whether to extend the standard (for example an `fk_filter`) or keep them as notes.
- **Twenty-two column descriptions are very short**, mostly the `uen` columns that point at
  `accounts`, and `contacts.title`, `email` and `phone`. Add the unit, format or meaning.
- **`service_requests` and `operational_service_cases` have identical columns.** Their table
  descriptions say how they differ; check that the difference is right.
- **`rm_scenarios` is an answer key**, excluded from the client-facing tools. Its description says
  so.

## What the standard does not yet cover

These are things a model needs to write correct SQL that the file has no place for yet:

- the sensitivity of a table or column (for masking and policy);
- the allowed values of a column, other than in its description text;
- which column scopes a row to one client, and the path to it (`covenants` reach a client only
  through `facilities`);
- sample values.

Add them as new columns when the team decides how to record them, so that the check can require
them.
