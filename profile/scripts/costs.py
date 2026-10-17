"""Homework 9 Part A: cost per call site, written to profile/results/costs.csv."""

from __future__ import annotations

import csv
import json
import statistics
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

import tiktoken

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "profile" / "results" / "costs.csv"
CONFIG = json.loads((ROOT / "optimize" / "config.json").read_text())

# USD per million tokens. The agent model comes from the HW8 config; the
# judge model is priced by DeepSeek's peak and off-peak periods (deepseek-chat
# billed at DeepSeek-V4.1-Flash rates, cache-miss input).
PRICES = {
    "claude-haiku-5-5": CONFIG["prices_per_million_tokens_usd"]["claude-haiku-5-5"],
    "deepseek/deepseek-chat": {
        "peak": {"input": 0.30, "output": 1.20},
        "off_peak": {"input": 0.15, "output": 0.60},
    },
}

# DeepSeek peak windows in UTC, Monday to Friday; every other hour is off-peak.
# (Chinese public holidays are off-peak too; none fall on our run dates.)
PEAK_WINDOWS_UTC = [(time(1, 0), time(4, 0)), (time(6, 0), time(10, 0))]

# OpenAI's tokenizer: an approximation for Claude and DeepSeek text.
ENCODER = tiktoken.get_encoding("o200k_base")


def count_tokens(text: str) -> int:
    return len(ENCODER.encode(text))


def deepseek_period(ts: str) -> str:
    """Return 'peak' or 'off_peak' for an ISO timestamp in UTC."""
    moment = datetime.fromisoformat(ts)
    if moment.weekday() >= 5:  # Saturday or Sunday
        return "off_peak"
    t = moment.time()
    if any(start <= t < end for start, end in PEAK_WINDOWS_UTC):
        return "peak"
    return "off_peak"


def cost_usd(model: str, input_tokens: int, output_tokens: int, period: str | None = None) -> float:
    price = PRICES[model][period] if period else PRICES[model]
    return (input_tokens * price["input"] + output_tokens * price["output"]) / 1_000_000


def runner_agent_rows() -> list[dict]:
    """Agent calls made by the HW8 runner: API-reported token totals."""
    totals: dict[str, dict] = {}
    for path in sorted((ROOT / "optimize" / "results").glob("*.json")):
        if path.name == "latest-development.json":
            continue  # a copy of the newest development run, not a separate run
        result = json.loads(path.read_text())
        if "evaluated_case_runs" not in result:
            continue  # target.json, frontier notes, and other non-run files
        row = totals.setdefault(
            result["model"], {"calls": 0, "input_tokens": 0, "output_tokens": 0}
        )
        row["calls"] += result["evaluated_case_runs"]
        row["input_tokens"] += result["input_tokens"]
        row["output_tokens"] += result["output_tokens"]
    rows = []
    for model, row in totals.items():
        prices = CONFIG["prices_per_million_tokens_usd"][model]
        cost = (row["input_tokens"] * prices["input"] + row["output_tokens"] * prices["output"]) / 1_000_000
        rows.append({
            "cost_category": "customer_conversation",
            "call_site": "replay/rollout.py:210",
            "purpose": "HW8 development and test runs; API-reported tokens; calls = conversations (one conversation makes several model requests)",
            "model": model,
            **row,
            "cost_usd": round(cost, 6),
        })
    return rows


def message_text(messages: list[dict] | None, roles: set[str] | None = None) -> str:
    """Flatten Langfuse message parts (text, tool calls, tool results) to text."""
    chunks = []
    for message in messages or []:
        if roles and message.get("role") not in roles:
            continue
        for part in message.get("parts", []):
            if part.get("type") == "text":
                chunks.append(part.get("content", ""))
            else:  # tool_call arguments or tool_call_response results
                chunks.append(json.dumps(part, sort_keys=True))
    return "\n".join(chunks)


def hw3_agent_row() -> dict:
    """Agent calls in the HW3 Langfuse export: tokens estimated from saved text."""
    export = json.loads((ROOT / "traces" / "support_traces.json").read_text())
    calls = input_tokens = output_tokens = 0
    for trace in export["traces"]:
        generations = sorted(
            (o for o in trace["observations"] if o.get("type") == "GENERATION"),
            key=lambda o: o["startTime"],
        )
        if not generations:
            continue
        calls += len(generations)
        input_tokens += sum(count_tokens(message_text(g["input"])) for g in generations)
        written_before = count_tokens(message_text(generations[0]["input"], {"assistant"}))
        written_by_end = count_tokens(message_text(generations[-1]["input"], {"assistant"}))
        output_tokens += written_by_end - written_before + count_tokens(message_text(trace["output"]))
    model = "claude-haiku-5-5"
    return {
        "cost_category": "customer_conversation",
        "call_site": "server/app.py:212",
        "purpose": "HW3 support scenarios via the server; tokens ESTIMATED with tiktoken from saved text (Langfuse recorded 0); excludes tool definitions",
        "model": model,
        "calls": calls,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": round(cost_usd(model, input_tokens, output_tokens), 6),
    }


LOCAL_TZ = timezone(timedelta(hours=2))  # Harbor writes local time without a zone


def to_utc(ts: str) -> str:
    moment = datetime.fromisoformat(ts)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=LOCAL_TZ)
    return moment.astimezone(timezone.utc).isoformat()


def judge_call_size(judge_file: str) -> tuple[int, int]:
    """Estimated (input, output) tokens for one call of a saved judge version."""
    judges = ROOT / "analysis" / "state" / "judges"
    judge = json.loads((judges / judge_file).read_text())
    traces = json.loads((ROOT / "analysis" / "state" / "hw5_trace_inputs.json").read_text())
    trace_tokens = statistics.mean(count_tokens(json.dumps(t["trace"])) for t in traces)
    critiques = json.loads((judges / "unrequested_detail-v0.json").read_text())["critiques"]
    # Critiques are grouped by prompt hash: {prompt_hash: {trace_id: text}}.
    output_tokens = statistics.mean(
        count_tokens(text) for by_trace in critiques.values() for text in by_trace.values()
    )
    return round(count_tokens(judge["prompt_text"]) + trace_tokens), round(output_tokens)


JUDGE_MODEL = "deepseek/deepseek-chat"


def judge_runs() -> list[dict]:
    """Every judge run we can date: call site, time, number of calls, judge version."""
    runs = [
        # HW5 development and test runs (analysis/report/, n = 79 each).
        {"site": "analysis/helpers/scale.py:201", "ts": "2026-10-15T16:51:47+00:00", "calls": 79, "judge": "unrequested_detail-v0.json"},
        {"site": "analysis/helpers/scale.py:201", "ts": "2026-10-15T17:40:47+00:00", "calls": 79, "judge": "unrequested_detail-v0.json"},
        {"site": "analysis/helpers/scale.py:201", "ts": "2026-10-15T17:45:35+00:00", "calls": 79, "judge": "unrequested_detail-v1.json"},
        {"site": "analysis/helpers/scale.py:201", "ts": "2026-10-15T18:05:18+00:00", "calls": 79, "judge": "unrequested_detail-v2.json"},
        {"site": "analysis/helpers/scale.py:201", "ts": "2026-10-15T18:26:44+00:00", "calls": 79, "judge": "unrequested_detail-v0.json"},
        # HW6 Harbor jobs (local time) and the two GitHub CI runs (commit time).
        {"site": "harbor_adapter/export.py:305", "ts": "2026-10-16T09:32:12", "calls": 1, "judge": "unrequested_detail-v0.json"},
        {"site": "harbor_adapter/export.py:305", "ts": "2026-10-16T10:37:37", "calls": 5, "judge": "unrequested_detail-v0.json"},
        {"site": "harbor_adapter/export.py:305", "ts": "2026-10-16T10:44:32", "calls": 5, "judge": "unrequested_detail-v0.json"},
        {"site": "harbor_adapter/export.py:305", "ts": "2026-10-16T10:51:44", "calls": 5, "judge": "unrequested_detail-v0.json"},
        {"site": "harbor_adapter/export.py:305", "ts": "2026-10-16T11:39:31", "calls": 15, "judge": "unrequested_detail-v0.json"},
        {"site": "harbor_adapter/export.py:305", "ts": "2026-10-16T12:01:19", "calls": 15, "judge": "unrequested_detail-v0.json"},
        {"site": "harbor_adapter/export.py:305", "ts": "2026-10-16T12:21:53", "calls": 15, "judge": "unrequested_detail-v0.json"},
        {"site": "harbor_adapter/export.py:305", "ts": "2026-10-16T12:55:43", "calls": 15, "judge": "unrequested_detail-v0.json"},
    ]
    # HW7 monitoring: random plus risk sample per run.
    for line in (ROOT / "monitoring" / "history.jsonl").read_text().splitlines():
        run = json.loads(line)
        runs.append({"site": "analysis/helpers/scale.py:201", "ts": run["run_at"],
                     "calls": run["random_sample"] + run["risk_sample"], "judge": run["judge_id"] + ".json"})
    # HW8 runner: one judge call per run of a judged case.
    cases = [json.loads(line) for line in (ROOT / "eval_cases" / "cases.jsonl").read_text().splitlines()]
    judged = {case["id"] for case in cases if case["expected"].get("judges")}
    for path in sorted((ROOT / "optimize" / "results").glob("*.json")):
        if path.name == "latest-development.json":
            continue
        result = json.loads(path.read_text())
        if "cases" not in result:
            continue
        calls = sum(c["runs"] for c in result["cases"] if c["case_id"] in judged)
        runs.append({"site": "replay/rollout.py:426", "ts": result["created_at"], "calls": calls, "judge": "unrequested_detail-v0.json"})
    return runs


def judge_rows() -> list[dict]:
    """One row per judge call site; each run priced in its own DeepSeek period."""
    purposes = {
        "analysis/helpers/scale.py:201": "HW5 judge development and test runs, HW7 monitoring",
        "harbor_adapter/export.py:305": "HW6 Harbor baselines, 15-run analysis, smoke and two CI runs (CI times from commits; infra-error job counted in full)",
        "replay/rollout.py:426": "HW8 runner, one call per judged case run",
    }
    rows: dict[str, dict] = {}
    for run in judge_runs():
        input_each, output_each = judge_call_size(run["judge"])
        period = deepseek_period(to_utc(run["ts"]))
        row = rows.setdefault(run["site"], {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "periods": set()})
        row["calls"] += run["calls"]
        row["input_tokens"] += run["calls"] * input_each
        row["output_tokens"] += run["calls"] * output_each
        row["cost_usd"] += cost_usd(JUDGE_MODEL, run["calls"] * input_each, run["calls"] * output_each, period)
        row["periods"].add(period)
    return [
        {
            "cost_category": "judge",
            "call_site": site,
            "purpose": f"{purposes[site]}; tokens ESTIMATED (prompt + mean trace text in, mean saved critique out); priced {'/'.join(sorted(row['periods']))}",
            "model": JUDGE_MODEL,
            "calls": row["calls"],
            "input_tokens": row["input_tokens"],
            "output_tokens": row["output_tokens"],
            "cost_usd": round(row["cost_usd"], 6),
        }
        for site, row in rows.items()
    ]


FIELDS = ["cost_category", "call_site", "purpose", "model", "calls", "input_tokens", "output_tokens", "cost_usd"]


def main() -> None:
    rows = runner_agent_rows() + [hw3_agent_row()] + judge_rows()
    rows.sort(key=lambda row: (row["cost_category"], -row["cost_usd"]))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    for row in rows:
        print(f"{row['cost_category']:22} {row['call_site']:32} {row['model']:24} "
              f"calls={row['calls']:5} in={row['input_tokens']:>9} out={row['output_tokens']:>8} ${row['cost_usd']:.4f}")
    print(f"Saved {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
