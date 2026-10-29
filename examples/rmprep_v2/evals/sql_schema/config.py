"""Paths, prices and the metered client shared by the SQL-from-schema evaluation."""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    import anthropic

HERE = Path(__file__).resolve().parent
WORKSPACE = HERE.parents[1]
DATA = WORKSPACE / "data"
CASES = WORKSPACE / "agents" / "rmprep-v2" / "src" / "rmprep_v2" / "evals" / "cases.jsonl"
RESULTS = HERE / "results"
"""Where answers go. It is ignored by git: raw answers are not kept in the repository."""

SKIP_TABLES = ("rm_scenarios",)
"""The scenario answer keys, which the services never offer, so the model is not told about them."""

# USD per million tokens: input, output, cache read, cache write. Anthropic list prices when this
# was written (October 2026); check them before relying on the cost line.
PRICES: dict[str, tuple[float, float, float, float]] = {
    "claude-haiku-5-5": (0.10, 0.50, 0.01, 0.125),
    "claude-sonnet-5-5": (2.00, 10.00, 0.20, 2.50),
}

Effort = Literal["low", "medium", "high"]


def api_key() -> str:
    """The key, from the environment only. It is never read from a file and never printed."""
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        raise SystemExit("set ANTHROPIC_API_KEY in the environment to call the models")
    return key


@dataclass
class Meter:
    """What the calls have cost so far, with a ceiling that stops the run."""

    ceiling: float
    spent: float = 0.0
    calls: int = 0
    by_model: dict[str, float] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def add(self, model: str, usage: Any) -> float:
        """Add the cost of one response, given its ``usage``, and stop the run past the ceiling."""
        rate_in, rate_out, rate_read, rate_write = PRICES[model]
        read = getattr(usage, "cache_read_input_tokens", 0) or 0
        write = getattr(usage, "cache_creation_input_tokens", 0) or 0
        cost = (
            float(
                usage.input_tokens * rate_in
                + usage.output_tokens * rate_out
                + read * rate_read
                + write * rate_write
            )
            / 1_000_000
        )
        with self.lock:
            self.spent += cost
            self.calls += 1
            self.by_model[model] = self.by_model.get(model, 0.0) + cost
            over = self.spent > self.ceiling
        if over:
            raise SystemExit(
                f"stopped: spent ${self.spent:.2f}, over the ceiling of ${self.ceiling:.2f}"
            )
        return cost


def client() -> anthropic.Anthropic:
    """A client for the Anthropic API, keyed from the environment."""
    import anthropic

    return anthropic.Anthropic(api_key=api_key(), max_retries=3, timeout=300.0)


def ask(
    c: anthropic.Anthropic,
    meter: Meter,
    model: str,
    system: str,
    user: str,
    effort: Effort = "medium",
) -> dict[str, Any]:
    """One call: what the model is told about the data in a cached system block, the question after."""
    started = time.time()
    response = c.messages.create(
        model=model,
        max_tokens=8000,
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        output_config={"effort": effort},
        messages=[{"role": "user", "content": user}],
    )
    cost = meter.add(model, response.usage)
    return {
        "text": "".join(block.text for block in response.content if block.type == "text"),
        "stop_reason": response.stop_reason,
        "input_tokens": response.usage.input_tokens,
        "cache_read": getattr(response.usage, "cache_read_input_tokens", 0) or 0,
        "cache_write": getattr(response.usage, "cache_creation_input_tokens", 0) or 0,
        "output_tokens": response.usage.output_tokens,
        "cost": round(cost, 6),
        "seconds": round(time.time() - started, 1),
    }
