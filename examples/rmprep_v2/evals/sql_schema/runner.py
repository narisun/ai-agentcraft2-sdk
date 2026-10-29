"""Ask the models for SQL and save every raw answer. Scoring is separate, so redoing it costs nothing.

    export ANTHROPIC_API_KEY=...        # set it in the environment; it is never read from a file
    python -m evals.sql_schema.runner --models claude-haiku-5-5 --ceiling 0.50 --limit 3

Answers go to ``results/`` (ignored by git). The run stops by itself past ``--ceiling`` dollars.
"""

from __future__ import annotations

import argparse
import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, cast

from .config import CASES, RESULTS, Effort, Meter, ask, client
from .prompts import question_prompt, system_prompts
from .questions import AS_OF, QUESTIONS

SET1_TASK = (
    "Write up to 3 read-only SELECT statements, each in its own ```sql block, that fetch the rows of "
    "evidence needed to answer the question. Select the rows themselves, with the columns that "
    "identify each row (its key), not a summary."
)
SET2_TASK = "Write exactly one SELECT statement in one ```sql block."


def load_items() -> list[dict[str, Any]]:
    """Set 1: the existing cases that name the rows they need. Set 2: the questions with gold SQL."""
    items: list[dict[str, Any]] = []
    with CASES.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            case = json.loads(line)
            if not case["required_refs"]:
                continue
            prompt = question_prompt(case["question"], case["uen"], case["as_of"])
            items.append({"set": 1, "id": case["id"], "prompt": f"{prompt}\n\n{SET1_TASK}"})
    for q in QUESTIONS:
        prompt = question_prompt(q["question"], None, AS_OF)
        items.append({"set": 2, "id": q["id"], "prompt": f"{prompt}\n\n{SET2_TASK}"})
    return items


def main() -> None:
    import anthropic

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", nargs="+", required=True)
    parser.add_argument("--conditions", nargs="+", default=["A_bare", "B_schema", "C_values"])
    parser.add_argument(
        "--limit", type=int, default=0, help="only the first N questions of each set"
    )
    parser.add_argument("--ceiling", type=float, required=True, help="stop past this many dollars")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--out", default="answers.jsonl")
    parser.add_argument("--effort", default="medium", choices=["low", "medium", "high"])
    args = parser.parse_args()

    RESULTS.mkdir(exist_ok=True)
    out = RESULTS / args.out
    done: set[tuple[str, str, int, str]] = set()
    if out.exists():
        for line in out.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            done.add((row["model"], row["condition"], row["set"], row["id"]))

    items = load_items()
    if args.limit:
        items = [i for s in (1, 2) for i in [x for x in items if x["set"] == s][: args.limit]]
    prompts = system_prompts()
    meter, c, write_lock = Meter(ceiling=args.ceiling), client(), threading.Lock()
    effort = cast(Effort, args.effort)

    def work(model: str, condition: str, item: dict[str, Any]) -> None:
        try:
            result = ask(c, meter, model, prompts[condition], item["prompt"], effort)
        except anthropic.APIStatusError as error:
            result = {
                "text": "",
                "error": f"{error.status_code} {str(error.message)[:200]}",
                "cost": 0.0,
            }
        row = {
            "model": model,
            "condition": condition,
            "set": item["set"],
            "id": item["id"],
            **result,
        }
        with write_lock, out.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    for model in args.models:
        for condition in args.conditions:
            todo = [i for i in items if (model, condition, i["set"], i["id"]) not in done]
            print(f"{model} {condition}: {len(todo)} to do", flush=True)
            with ThreadPoolExecutor(max_workers=args.workers) as pool:
                futures = [pool.submit(work, model, condition, i) for i in todo]
                for n, future in enumerate(as_completed(futures), 1):
                    future.result()
                    if n % 10 == 0:
                        print(f"   {n}/{len(todo)}  spent ${meter.spent:.3f}", flush=True)
    by_model = {k: round(v, 3) for k, v in meter.by_model.items()}
    print(f"done: {meter.calls} calls, ${meter.spent:.3f}", by_model)


if __name__ == "__main__":
    main()
