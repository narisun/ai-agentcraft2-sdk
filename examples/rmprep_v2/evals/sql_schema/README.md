# Does the schema help a model write SQL?

`ac2 data model schema` turns a `data_model.csv` into text for a prompt. This evaluation asks the
question the command exists for: does a model write better SQL with that text than without it? It
asks a model for SQL under three prompts, runs the SQL on the workspace's CSV files with DuckDB the
way the data service does, and scores the result.

It is a harness and a first set of measurements, not a verdict. Read [what it cannot say](#what-it-cannot-say)
before using a number.

## The three prompts

| Prompt | What the model is told about the data |
| --- | --- |
| `A_bare` | The tables and columns with their types, as a plain list (about 1,600 tokens) |
| `B_schema` | `ac2 data model schema --exclude rm_scenarios`: grain, keys, joins, descriptions (about 13,200 tokens) |
| `C_values` | The same with `--values 8`: also the spellings of short text columns, read from the data (about 14,900 tokens) |

The scenario table is left out of every prompt: the services never offer it.

## The questions

- **Set 1: 31 cases that name their rows.** The existing cases of the Q&A agent
  (`agents/rmprep-v2/.../evals/cases.jsonl`) that list `required_refs`, 55 rows in all. The model may
  write up to three statements that fetch the evidence for the question. A case is scored by whether
  the rows it names are in the result, which needs the result to include the key columns of the row
  (so a row is "checkable" only when it does). There is no gold SQL.
- **Set 2: 21 SQL questions with gold SQL** (`questions.py`). Each says which columns to return, and
  the result is compared with the gold result by value: row order is ignored, amounts are compared to
  the cent. They test spellings of values (`V`), joins (`J`), a relation that is not a key (`R`), a
  time window (`T`) and the latest row of a group (`L`). Every gold query is run by the tests and
  returns rows.

## Results

Two models, three runs each. Run 3 used the schema header with the boolean rule described below, so
`B_schema` and `C_values` changed between runs 2 and 3 (marked `*`); `A_bare` was not run again.
One run is one answer per question and prompt, and these models take no temperature, so a rerun can
differ.

**Set 2: questions answered correctly, of 21** (one number per run)

| Model | `A_bare` | `B_schema` | `C_values` |
| --- | --- | --- | --- |
| Haiku 5.5 | 20, 18 | 21, 21, 21* | 21, 21, 21* |
| Sonnet 5.5 | 20, 20 | 18, 18, 20* | 20, 21, 21* |

**Set 1: rows found, of the 55 the cases name** (one number per run)

| Model | `A_bare` | `B_schema` | `C_values` |
| --- | --- | --- | --- |
| Haiku 5.5 | 39, 38 | 39, 38, 37* | 37, 37, 42* |
| Sonnet 5.5 | 39, 39 | 46, 44, 44* | 45, 48, 44* |

What these support, and what they do not:

- **Sonnet retrieves more of the rows it is asked for with the schema** (44 to 48 against 39). Part of
  the reason is that the schema marks the primary key of every table, so the model returns the columns
  that identify a row, and a row can only be found if they are returned.
- **Haiku shows no consistent difference** on set 1, and on set 2 it scores 18 to 21 of 21 in every
  prompt. The easy questions do not separate the prompts.
- **On set 2 the schema is not clearly better than the bare list.** The bare list scored as well, and
  Sonnet scored lower with the plain schema (18 twice) than without it (20 twice). The failures say
  why, and they are the more useful result:

| Failure | Where | Cause |
| --- | --- | --- |
| `LOWER(in_compliance) = 'no'` fails to run | Sonnet, plain schema, S02 and S05 | The description of `covenants.in_compliance` says "(Yes\|No)", the file holds `true` and `false`, and DuckDB reads a boolean. The model followed the description. |
| `'%upper middle%'` returns nothing | Sonnet, plain schema, S17 and S18 | The spelling "Upper Mid-Market" is in no description. With `--values` the same questions pass. |
| `'outgoing'` and `fiscal_year = '2025'` return nothing | Haiku and Sonnet, bare list, S13 and S15 | The bare list says nothing about the spellings `outbound` and `FY2025`; the descriptions do. |

The first row led to a change in the command: a schema with a boolean column now says to compare it
with `TRUE` or `FALSE`. Run 3 is that change. S02 and S05 then passed in both schema prompts for both
models, and the one miss left in `B_schema` is S17, the spelling that only `--values` supplies. That
is one run, not a confirmation.

Cost: run 1 $1.35 (312 calls), run 2 $0.94 (312), run 3 $1.15 (208), at Anthropic list prices. A full
run of Haiku 5.5 costs about 8 cents and of Sonnet 5.5 about a dollar.

## What it cannot say

- **The questions are few and were written by the author of the tool.** 21 questions and 31 cases
  cannot show a small difference, and one question is one point. A difference of one or two between
  prompts is within what a rerun changed.
- **The gold SQL was written by one person**, checked only by running it on the data. A reviewer should
  read `questions.py`.
- **Set 1 rewards returning key columns**, which the schema tells the model about and the bare list
  does not. That is a real benefit of the schema, but it is not the same as better SQL.
- **Two models.** Neither is `claude-sonnet-4-5`, the model this project has been run with. A weaker
  model may gain more, a stronger one less.
- **Sample data, no policy.** The questions run on the supplied CSV files with no masking, row scope or
  authorization, which the data service adds. Nothing here says anything about those.
- **`C_values` copies values out of the data into the prompt.** On this sample data that is harmless; on
  real client data it is a decision, which is why `--values` is off by default.

## Running it

From the workspace root (`examples/rmprep_v2`), with the SDK installed with DuckDB
(`agentcraft2-core[duckdb]`, `agentcraft2-cli`) and `pip install anthropic`:

```bat
set ANTHROPIC_API_KEY=your key
python -m evals.sql_schema.runner --models claude-haiku-5-5 --ceiling 0.50
python -m evals.sql_schema.score answers.jsonl --detail
```

The key is read from the environment only, never from a file, and never printed. `--ceiling` stops
a run past that many dollars. Answers and scores go to `results/`, which git ignores: raw model
answers are not kept in the repository. Scoring calls no model, so it can be redone at no cost.
`--limit 3` runs the first three questions of each set, and `--conditions A_bare B_schema` chooses
prompts. The prices in `config.py` are the list prices of October 2026.

The checks of the evaluation itself need no key and no network:

```bat
python -m pytest evals/sql_schema
```

The command it measures, `ac2 data model schema`, is described in `docs/data-model.md` of the SDK.
