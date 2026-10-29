# The data model

A `data_model.csv` describes the CSV files of a data folder: what each table holds, how its rows
are identified, how tables join and what each column means. A person reads it to learn the data,
and a model reads it to write queries, so a missing description is a missing fact.

Five commands work with it:

| Command | What it does |
| --- | --- |
| `ac2 data model draft DATA_DIR` | Writes a draft from the files. Never replaces a model unless `--force`. |
| `ac2 data model diff DATA_DIR` | Lists where the files differ from the model. Exit code 1 if they do. |
| `ac2 data model check DATA_DIR` | Checks the model against the standard and the files. Exit code 1 on an error. |
| `ac2 data model validate DATA_DIR` | Checks every value in the files against the model, with DuckDB. Exit code 1 on an error. |
| `ac2 data model schema DATA_DIR` | Writes the tables, columns and joins for a model that writes SQL. |

`--model FILE` names a model that is not `DATA_DIR/data_model.csv`, and `--output FILE` where the
draft goes.

## The standard

One row describes one column of one table. The header is exactly this, in this order:

| Column | Required | What goes in it |
| --- | --- | --- |
| `domain` | yes | The business area of the table (`crm`, `lending`). One domain per table. |
| `table` | yes | The table name, which is the CSV file name (`covenants` is `covenants.csv`). |
| `table_description` | yes | What the table holds, in one or two sentences, repeated on every row of the table. |
| `column` | yes | The column name, in the order the file has it. |
| `datatype` | yes | `string`, `integer`, `decimal`, `boolean`, `date` or `timestamp`. |
| `nullable` | yes | `Y` if the value may be empty, `N` if it may not. |
| `pk` | no | `Y` if the column is part of the primary key. Every table has a primary key. |
| `uk` | no | `Y` if the column is unique across the table. |
| `fk` | no | `domain.table.column`, then an optional note in brackets. |
| `description` | yes | What the column means: unit, currency, allowed values, what a reader could get wrong. |

Names are lower case words joined by underscores. A table description is 20 to 300 characters, ends
with a full stop and says more than the table name repeated.

`check` reports an **error** for a missing or wrong fact (an empty required field, a table with no
primary key, a nullable key column, a foreign key to a column that does not exist, a column that
differs from its file's header) and a **warning** for something to look at (a very short column
description, a decimal column inside a key, a foreign key that names a table and no column).

## Drafting a model

`draft` reads every CSV file in the folder, except `data_model.csv`, and writes one row per column.
It infers what the files show:

- **Types**: the narrowest of boolean, integer, decimal, date, timestamp that every value fits,
  otherwise string. A number with a leading zero is an identifier, so a string.
- **Nullable**: `N` for a primary key column and `Y` for the rest. A column that was never empty
  in these rows says so in its description, because that is evidence and not a promise.
- **Primary key**: the smallest set of columns that is unique in every row, up to six. A unique
  column named like an identifier (`id`, `claim_id`) wins over other unique columns. Columns with
  empty cells, decimals and booleans are never part of a key. A table with no such set gets no key
  and the command says so.
- **Joins**: a column whose every value exists in another table's single-column key of the same name.

What only a person knows is a placeholder: the `domain` is `todo`, and every description starts
with `TODO`. A column with a few repeated values lists them (`Values seen: closed | open`).
`check` reports every placeholder as an error until it is replaced, so a draft cannot be mistaken
for a finished model.

### How good is the draft

On the supplied rmprep-v2 data, which has a model that a team reviewed (32 tables, 310 columns),
the draft agreed with that model on:

| | Agreement |
| --- | --- |
| Datatype | 295 of 310 columns |
| Primary key columns | 290 of 310 columns |
| Nullable | 223 of 310 columns |
| Joins found | 26 of the model's 42 joins that name a column, every one with the same target |

Most differences are what a draft cannot know. Eight columns the model types as decimal hold
whole numbers in this delivery. The model's keys for `balances` and `financial_facts` include a
measure, and no key without one is unique, so the draft gives them none. The joins it missed
connect columns with different names (`connected_uen` to `uen`) or are about meaning (a product
name to the catalog). Treat the draft as a first pass to be reviewed, never as the model.

## Comparing a model with the files

`diff` reads the files the way `draft` does and compares them with the model. A **difference** is a
contradiction:

- a table or column on one side only, or the columns in another order;
- a datatype the values do not fit;
- a column the model says is never empty that is empty in some rows;
- a primary key with empty or repeated values; a `uk` column with repeats;
- a foreign key with values that match nothing in the table it points at.

A **note** is something to know that does not contradict the model: a primary key larger than it
needs to be, a text column whose values all look like decimals or dates, a join the data shows that
the model does not state, and unmatched values of a join the model qualifies in brackets (such as
`soft reference`). Findings count values and never print them.

The rmprep-v2 model compared with its own data has no differences and 22 notes.

## Validating the values

`diff` asks whether the model describes the files. `validate` asks whether the *values* are what
the model says they are, and answers with the exact place of each problem:

```text
claims.csv
  error   type    row 2 (line 3), column amount: '1,234.50' is not a plain number: it has a thousands separator; remove the commas
  error   type    row 3 (line 4), column opened: '03/04/2026' is not a date: the day and month order is ambiguous; write it as YYYY-MM-DD
1 file, 2 errors, 0 warnings.
```

A row is numbered from 1 after the header and also shows its **line** in the file, which differs
when a quoted value contains a line break. `--report problems.csv` writes every finding with its
file, row, line, column and value; the screen shows five of each rule in each column and counts
the rest (`--limit`). `--table NAME` limits the run and `--hide-values` leaves values out of the
output and the report, for when it may be shared.

DuckDB is the engine the services read these files with, so it is the engine used here. Each file
is loaded twice. A **dry run** loads it as a service does, with type detection, and compares the
result with the model. The checks proper load it again as text, so that every value is seen as
written. DuckDB's own casting is lenient (it accepts `' 12 '`), so each type has an exact format:

| Type | A value is valid when |
| --- | --- |
| `integer` | optional sign, then digits only. |
| `decimal` | optional sign, digits and at most one decimal point. No thousands separators, currency symbols, percent signs, brackets or exponents. |
| `boolean` | `Yes`/`No`, `Y`/`N`, `true`/`false` or `1`/`0`, in any case. |
| `date` | `YYYY-MM-DD` and a real date (`2026-02-30` is not). |
| `timestamp` | `YYYY-MM-DD hh:mm[:ss[.ffffff]]`, with a space or a `T` and an optional zone (`Z`, `+02:00`), or a date alone. |
| `string` | anything. |

Where a value is wrong the message says why: a currency symbol, a thousands separator, a day and
month that could be swapped, a time of day in a date column, a missing-value marker such as `N/A`.

| Rule | Level | Meaning |
| --- | --- | --- |
| `type` | error | a value that is not its column's type. |
| `empty` | error | an empty value in a column the model says is never empty, or in a primary key. |
| `duplicate` | error | a primary key or a unique column that repeats an earlier row. |
| `orphan` | error | a foreign key value with no match in the table it points at. A key the model qualifies in brackets (`soft reference`, `reconciliation`) is not checked row by row; `diff` counts its misses. |
| `structure` | error | a row with more or fewer fields than the header. |
| `load` | error | DuckDB cannot read the file, or does not take the column names from the header. A single ragged row can make DuckDB read a whole file as one column, which is what a service would then see. |
| `columns` | error, warning | a column the model has and the file lacks (error); a file or column the model does not describe (warning). |
| `whitespace` | warning | spaces at the start or end of a value, which a join will not ignore. |
| `mixed` | warning | yes and no spelled several ways in one column, or timestamps with and without a time zone. |
| `runtime_type` | warning | DuckDB would load the column as a different type from the model's (the model says text and DuckDB loads a number or a date, or the reverse). |

The rmprep-v2 data validated against its reviewed model has **no errors**: every date is ISO, every
number plain, every key unique and every join matched. It has four `runtime_type` warnings:
`accounts.naics_code`, `benchmarks.naics_industry` and `catalog.max_risk_rating` are declared text
and DuckDB loads them as integers, and `covenants.current_value` is declared text and loads as a
double. A query that compares those columns with text, or relies on leading zeros, is at risk.

To test the tool itself, eleven faults were put into a copy of that data (a day-first date, a
currency amount, a `maybe` for a yes/no, an impossible date, `N/A` in a number column, an empty required
value, an unmatched foreign key, a repeated key, a padded value, a date with a single-digit month,
and a row missing a field in a 30,000-row file). All eleven were reported at their exact file, row,
line and column.

`validate` does not know what a value means. It cannot tell a date that is wrong but valid, an
amount in the wrong currency, or a value outside the range the business allows; those need rules
the model has no place for yet.

## Writing the schema for a model that writes SQL

`schema` turns the model into what a model needs to write SQL that runs: which tables exist and
what one row of each is, what each column holds, which columns identify a row, and how tables
join. It prints Markdown to put in a prompt, or JSON with `--format json`, and says on the other
stream how many tables and about how many tokens it wrote, so the schema can be piped.

```text
# Data schema

SQL dialect: redshift. Use only the tables, columns and joins listed here. Join only as the join
list says; do not guess a join. Dates are written YYYY-MM-DD and timestamps in ISO 8601.

## Tables
| table | domain | what it holds | one row per |
| accounts | crm | One row per client legal entity: identity, industry, size, ... | uen |

## Joins
- covenants.facility_id = facilities.facility_id (many-to-one)
- accounts.connected_uen = accounts.uen (many-to-one, soft): may match nothing (each entry; ...)

## Other relations
- relationship.nii relates to product_profitability.revenue_nii (reconciliation)

## covenants
Domain: lending. Primary key: facility_id, name.
| column | type | null | key | description |
| facility_id | string | no | pk, fk facilities.facility_id | Facility key. |
```

A join says its **cardinality**: `many-to-one` when the column points at a key of the other table,
`one-to-one` when it is also the whole key of its own table, and `unknown` when it points at a part
of a composite key, which can repeat rows (the warning names the whole key). A **soft** join is one
the model qualifies in brackets, where some rows may match nothing. A reference to a column that is
not a key (a reconciliation of two amounts, an eligibility threshold) or between tables with no
column is listed under **Other relations**, never as a condition, because a model that sees
`relationship.nii = product_profitability.revenue_nii` will join on amounts.

A prompt should hold what a question needs, not the whole model. `--table NAME` (repeat it) keeps
those tables, `--related` adds the tables one join away in either direction, and `--exclude NAME`
leaves a table out and names it nowhere, such as a table of answer keys the services do not offer.
`--dialect` says which SQL to write; it is told to the model, not checked. On the rmprep-v2 data
(31 tables after leaving out the scenario table) the whole schema is about 13,100 tokens, two
tables with their neighbours (`--table facilities --table covenants --related`, 8 tables) about
4,300, and the two alone about 1,000.

`--values N` also lists the different values of a text column that has at most N of them (a segment,
a status, a currency), most common first, because a model that has seen `Retail` does not write
`'retail'`. Those values are **read from the data files** and copied into the prompt, so it is off
unless asked for, the output says so, and a primary key, a unique column and a foreign key never list
any. A long value is cut at 40 characters and one with a line break is left out. Check them before
the prompt leaves the machine: a short column can still hold something that is not meant to be
shared. On the rmprep-v2 data `--values 8` finds 61 such columns and adds about 1,700 tokens.

A model that does not follow the standard (a `TODO` left in a description) is refused, since
whatever is wrong would reach the prompt. `ac2 data model check` says what, and `--allow-problems`
writes the schema anyway.

## Limits

The schema repeats the model; it adds nothing the model lacks, and cannot say what a join means
beyond the note in brackets. It does not know the real database: the dialect is only a sentence,
and a query it leads to still has to be checked and run by something that enforces masking and row
scope. Everything inferred comes from the rows present. A column that is unique today may not be
tomorrow, and a column that is never empty may be allowed to be. Inference reads only column
names, never meaning, so it cannot find a join between differently named columns, and it cannot
write a description. The standard has no place yet for sensitivity, allowed values as data, or
the column that scopes a row to one client.
