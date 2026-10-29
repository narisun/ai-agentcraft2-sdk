"""The three conditions: what the model is told about the data before it writes SQL."""

from __future__ import annotations

import csv
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from .config import DATA, SKIP_TABLES

INSTRUCTIONS = """You write SQL for a relationship-manager data service over a snapshot of CSV tables.

Rules:
- Write exactly one read-only SELECT statement in the {dialect} dialect.
- Use only the tables and columns you were told about.
- Return the columns the question asks for, in the order it asks for them.
- Answer with the SQL only, in one ```sql code block. No explanation."""

SQL_BLOCK = re.compile(r"```(?:sql)?\s*(.*?)```", re.S | re.IGNORECASE)


def schema_text(*options: str) -> str:
    """The output of ``ac2 data model schema`` for the workspace data, without the scenario table.

    It goes through a file because a file is UTF-8 on every system.
    """
    exclude = [a for name in SKIP_TABLES for a in ("--exclude", name)]
    with tempfile.TemporaryDirectory() as folder:
        target = Path(folder) / "schema.md"
        command = [
            sys.executable, "-m", "agentcraft2_cli", "data", "model", "schema", str(DATA),
            *exclude, *options, "-o", str(target),
        ]  # fmt: skip
        subprocess.run(command, capture_output=True, text=True, check=True)
        return target.read_text(encoding="utf-8")


def bare_text() -> str:
    """What a plain listing of the tables gives: names and types, and nothing else."""
    tables: dict[str, list[str]] = {}
    with (DATA / "data_model.csv").open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row["table"] not in SKIP_TABLES:
                tables.setdefault(row["table"], []).append(f"{row['column']} {row['datatype']}")
    listing = "\n".join(f"{name}({', '.join(cols)})" for name, cols in tables.items())
    return f"Tables and columns:\n{listing}\n"


def system_prompts(dialect: str = "redshift") -> dict[str, str]:
    """The system prompt of each condition, by name."""
    head = INSTRUCTIONS.format(dialect=dialect)
    return {
        "A_bare": f"{head}\n\n{bare_text()}",
        "B_schema": f"{head}\n\n{schema_text('--dialect', dialect)}",
        "C_values": f"{head}\n\n{schema_text('--dialect', dialect, '--values', '8')}",
    }


def question_prompt(question: str, uen: str | None = None, as_of: str | None = None) -> str:
    """The user turn: the date and client, if any, then the question."""
    lines = []
    if as_of:
        lines.append(f"Today's date for this question is {as_of}.")
    if uen:
        lines.append(f"The client in question is {uen}.")
    lines.append(f"Question: {question}")
    return "\n".join(lines)
