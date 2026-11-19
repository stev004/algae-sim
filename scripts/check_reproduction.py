"""Rerun the first N benchmark runs and compare them row for row with the committed data.

    uv run python scripts/check_reproduction.py --runs 1

The benchmark is deterministic, so a fresh run must reproduce the committed
``docs/benchmark/observations.jsonl`` exactly. Tests only compare two runs in the
same process; this compares against what the docs actually cite.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from algae_sim.autolab.benchmark import run_all

COMMITTED = Path(__file__).resolve().parent.parent / "docs/benchmark/observations.jsonl"


def rows(path: Path, runs: int) -> list[dict]:
    selected = []
    for line in path.read_text().splitlines():
        row = json.loads(line)
        if row["run"] < runs:
            selected.append(row)
    return sorted(selected, key=lambda r: (r["arm"], r["run"], r["index"]))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        fresh = run_all(Path(tmp), args.runs, args.workers)
        new, old = rows(Path(fresh), args.runs), rows(COMMITTED, args.runs)
    if new == old:
        print(f"reproduced: {len(new)} rows match the committed observations")
        return 0
    mismatched = sum(a != b for a, b in zip(new, old, strict=False)) + abs(len(new) - len(old))
    print(f"NOT reproduced: {mismatched} of {len(old)} rows differ", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
