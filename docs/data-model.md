# The data model

A `data_model.csv` describes the CSV files of a data folder: what each table holds, how its rows
are identified, how tables join and what each column means. A person reads it to learn the data,
and a model reads it to write queries, so a missing description is a missing fact.

Three commands work with it:

| Command | What it does |
| --- | --- |
| `ac2 data model draft DATA_DIR` | Writes a draft from the files. Never replaces a model unless `--force`. |
| `ac2 data model diff DATA_DIR` | Lists where the files differ from the model. Exit code 1 if they do. |
| `ac2 data model check DATA_DIR` | Checks the model against the standard and the files. Exit code 1 on an error. |

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

## Limits

Everything inferred comes from the rows present. A column that is unique today may not be
tomorrow, and a column that is never empty may be allowed to be. Inference reads only column
names, never meaning, so it cannot find a join between differently named columns, and it cannot
write a description. The standard has no place yet for sensitivity, allowed values as data, or
the column that scopes a row to one client.
