"""Homework 9 Part D: agreement and cost of the cascade at several thresholds."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
THRESHOLDS = [0.5, 0.6, 0.7, 0.8, 0.85, 0.9, 0.95, 0.99, 1.01]  # 1.01 = oracle only
PROXY_PRICE = {"input": 0.0, "output": 0.0}  # local Ollama qwen3:8b: no API fee; hardware and electricity not counted
# Oracle cost per verdict from Part A's estimate, DeepSeek off-peak
# (about 3,030 input and 370 output tokens per call).
ORACLE_COST_PER_VERDICT = (3030 * 0.15 + 370 * 0.60) / 1_000_000
FIELDS = [
    "threshold",
    "agreement_with_oracle",
    "agreement_with_human_labels",
    "proxy_share",
    "cost_per_1000_verdicts_usd",
]


def latest_human_labels() -> dict[str, int]:
    labels = {}
    for line in (ROOT / "analysis/state/hw5_labels/unrequested_detail.jsonl").read_text().splitlines():
        row = json.loads(line)
        labels[row["trace_id"]] = row["label"]  # later lines replace earlier fixes
    return labels


def oracle_verdicts() -> dict[str, int]:
    judge = json.loads((ROOT / "analysis/state/judges/unrequested_detail-v0.json").read_text())
    return judge["predictions"][judge["prompt_hash"]]


def read_proxy(split: str) -> list[dict]:
    path = ROOT / "cascade/results" / f"proxy-{split}.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()]


def cascade_row(threshold: float, proxy: list[dict], oracle: dict, human: dict) -> dict:
    kept = [r for r in proxy if r["verdict"] is not None and r["confidence"] >= threshold]
    kept_ids = {r["trace_id"] for r in kept}
    final = {r["trace_id"]: r["verdict"] for r in kept}
    final.update({r["trace_id"]: oracle[r["trace_id"]] for r in proxy if r["trace_id"] not in kept_ids})
    ids = [r["trace_id"] for r in proxy]
    proxy_cost = sum(r["input_tokens"] * PROXY_PRICE["input"] + r["output_tokens"] * PROXY_PRICE["output"]
                     for r in proxy) / 1_000_000
    oracle_cost = (len(ids) - len(kept)) * ORACLE_COST_PER_VERDICT
    return {
        "threshold": threshold,
        "agreement_with_oracle": round(sum(final[i] == oracle[i] for i in ids) / len(ids), 4),
        "agreement_with_human_labels": round(sum(final[i] == human[i] for i in ids) / len(ids), 4),
        "proxy_share": round(len(kept) / len(ids), 4),
        "cost_per_1000_verdicts_usd": round((proxy_cost + oracle_cost) / len(ids) * 1000, 4),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--split", choices=("dev",), default="dev")
    args = parser.parse_args()
    proxy = read_proxy(args.split)
    oracle = oracle_verdicts()
    human = latest_human_labels()
    rows = [cascade_row(t, proxy, oracle, human) for t in THRESHOLDS]
    out = ROOT / "cascade/results/thresholds.csv"
    with out.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    errors = sum(1 for r in proxy if r["verdict"] is None)
    print(f"{len(proxy)} proxy verdicts, {errors} unusable (sent to the oracle)")
    for row in rows:
        print(row)
    print(f"Saved {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
