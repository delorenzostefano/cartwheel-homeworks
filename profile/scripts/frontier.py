"""Homework 9 Part E: score and cost frontier of the HW8 final run and the HW9 runs."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from optimize.workflow import mark_dominated

ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT / "optimize" / "results"
OUT = ROOT / "profile" / "results" / "frontier.csv"
CANDIDATES = {
    "hw8_final": "development-final-*.json",
    "fewer-tokens": "development-fewer-tokens-*.json",
    "cache-before": "development-cache-before-*.json",
    "cache-after": "development-cache-after-*.json",
}
FIELDS = [
    "candidate",
    "model",
    "development_score",
    "write_pass_5",
    "cost_per_100_conversations_usd",
    "median_latency_seconds",
    "frontier_status",
]


def main() -> None:
    rows = []
    for name, pattern in CANDIDATES.items():
        (path,) = RESULTS.glob(pattern)  # exactly one saved run per candidate
        result = json.loads(path.read_text())
        rows.append({
            "configuration": name,
            "model": result["model"],
            "score": result["score"],
            "write_pass_5": result["write_pass_5"],
            "cost_per_100_conversations_usd": result["cost_per_100_conversations_usd"],
            "median_latency_seconds": result["median_latency_seconds"],
        })
    with OUT.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        for row in mark_dominated(rows):
            writer.writerow({
                "candidate": row["configuration"],
                "model": row["model"],
                "development_score": row["score"],
                "write_pass_5": row["write_pass_5"],
                "cost_per_100_conversations_usd": row["cost_per_100_conversations_usd"],
                "median_latency_seconds": row["median_latency_seconds"],
                "frontier_status": "dominated" if row["dominated"] else "frontier",
            })
    print(OUT.read_text())


if __name__ == "__main__":
    main()
