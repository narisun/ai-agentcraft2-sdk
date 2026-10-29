"""Put the workspace root on the path, so that the evaluation imports as evals.sql_schema."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
