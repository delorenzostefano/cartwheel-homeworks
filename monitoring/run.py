"""Homework 7 monitor: sample one period's conversations, judge them, correct, score.

    uv run --env-file .env python -m monitoring.run --period before
    uv run --env-file .env python -m monitoring.run --period after
    uv run --env-file .env python -m monitoring.run --last-hours 24
    uv run python -m monitoring.run --chart

A period is a fixed UTC window from ``monitoring/config.json`` that must
contain every one of the 50 monitoring scenarios, all on the configured
Cartwheel model. ``--last-hours`` is the scheduled form: it takes whatever
conversations the window holds, grouped by ``cartwheel.session_id``.

The judge sees the same normalized text as in Homework 5 (ordered turns,
the agent's working notes, tool calls with names, tool results, and final
replies). Labels, annotations, and scenario metadata never enter the text.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from analysis.helpers.normalization import _data, _flatten
from analysis.review_app import conversations as conv
from analysis.run_judges import _messages
from monitoring.chart import prevalence_chart
from monitoring.correct import corrected_mode_prevalence
from monitoring.run_judges import judge_sample, judge_test_data
from monitoring.sample import DEFAULT_RISK_GROUPS, select_traces
from monitoring.write_scores import build_score_records, post_scores

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = REPO_ROOT / "monitoring" / "config.json"
HISTORY_PATH = REPO_ROOT / "monitoring" / "history.jsonl"
CHART_PATH = REPO_ROOT / "monitoring" / "prevalence.svg"
OUTPUT_DIR = REPO_ROOT / "monitoring" / "output"
SCENARIOS_PATH = REPO_ROOT / "scenarios" / "monitoring_scenarios.jsonl"


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def fetch_traces(start: datetime, end: datetime) -> list[dict[str, Any]]:
    """Every Langfuse trace whose timestamp falls in [start, end), with observations."""
    from analysis.helpers import langfuse_io

    lf = langfuse_io._client()
    traces: list[dict[str, Any]] = []
    page = 1
    while True:
        resp = lf.api.trace.list(page=page, limit=100, from_timestamp=start, to_timestamp=end)
        batch = list(resp.data or [])
        for summary in batch:
            full = _data(lf.api.trace.get(summary.id))
            traces.append(json.loads(json.dumps(full, default=str)))
        if len(batch) < 100:
            break
        page += 1
    return traces


def _trace_models(trace: dict[str, Any]) -> set[str]:
    return {o["model"] for o in trace.get("observations") or [] if o.get("type") == "GENERATION" and o.get("model")}


def build_records(conversations: list[dict[str, Any]], traces_by_id: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """One record per conversation, keyed by its final trace so the score lands there."""
    records = []
    for c in conversations:
        tools = sorted({s["name"] for t in c["turns"] for s in t["steps"] if s["kind"] == "tool"})
        models = set().union(*(_trace_models(traces_by_id[t]) for t in c["trace_ids"]))
        records.append({
            "id": c["trace_ids"][-1],
            "session_id": c["session_id"],
            "scenario_id": c["scenario_id"],
            "trace_ids": c["trace_ids"],
            "tools": tools,
            "turn_count": len(c["turns"]),
            "models": sorted(models),
            "text": _flatten(_messages(c)),
        })
    return records


def _session_conversations(traces: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group traces by cartwheel.session_id without relying on scenario ids."""
    sessions: dict[str, list[dict[str, Any]]] = {}
    for trace in traces:
        attrs = conv._attributes(trace)
        sid = attrs.get("cartwheel.session_id")
        if sid:
            sessions.setdefault(sid, []).append({**trace, "_attrs": attrs})
    out = []
    for sid, group in sessions.items():
        group.sort(key=lambda t: t.get("timestamp") or "")
        turns = [conv.build_turn(t) for t in group]
        out.append({"id": group[0]["id"], "session_id": sid,
                    "scenario_id": group[0]["_attrs"].get("cartwheel.scenario_id"),
                    "trace_ids": [t["id"] for t in group], "turns": turns,
                    "incomplete": not all(t["complete"] for t in turns)})
    return sorted(out, key=lambda c: c["turns"][0]["timestamp"] or "")


def period_records(config: dict[str, Any], period: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
    """Validated records for a configured period; raise if the period is not comparable."""
    expected = {json.loads(line)["id"] for line in SCENARIOS_PATH.read_text().splitlines() if line.strip()}
    traces = fetch_traces(_parse_time(period["from"]), _parse_time(period["to"]))
    traces = [t for t in traces if conv._attributes(t).get("cartwheel.scenario_id") in expected]
    by_id = {t["id"]: t for t in traces}
    conversations = _session_conversations(traces)
    found = {c["scenario_id"] for c in conversations}
    if found != expected:
        raise SystemExit(f"period {period['label']}: missing scenario ids {sorted(expected - found)[:10]}")
    per_scenario: dict[str, int] = {}
    for c in conversations:
        per_scenario[c["scenario_id"]] = per_scenario.get(c["scenario_id"], 0) + 1
    repeated = sorted(s for s, n in per_scenario.items() if n > 1)
    if repeated:
        raise SystemExit(f"period {period['label']}: scenarios run more than once (separate retries) {repeated[:10]}")
    incomplete = [c["scenario_id"] for c in conversations if c["incomplete"]]
    if incomplete:
        raise SystemExit(f"period {period['label']}: incomplete conversations {incomplete[:10]}")
    records = build_records(conversations, by_id)
    wrong = [r["scenario_id"] for r in records if r["models"] != [config["model"]]]
    if wrong:
        raise SystemExit(f"period {period['label']}: traces not on {config['model']}: {wrong[:10]}")
    if len(records) != 50:
        raise SystemExit(f"period {period['label']}: expected 50 conversations, built {len(records)}")
    return records, len(traces)


def window_records(config: dict[str, Any], hours: float) -> tuple[list[dict[str, Any]], int, str]:
    """Records for the scheduled form: complete conversations on the configured model."""
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=hours)
    traces = fetch_traces(start, end)
    by_id = {t["id"]: t for t in traces}
    conversations = [c for c in _session_conversations(traces) if not c["incomplete"]]
    records = [r for r in build_records(conversations, by_id) if r["models"] == [config["model"]]]
    return records, len(traces), f"last-{hours:g}h-{end:%Y-%m-%dT%H%MZ}"  # no colon: artifact names reject it


def _save_history(entry: dict[str, Any]) -> None:
    lines = [json.loads(l) for l in HISTORY_PATH.read_text().splitlines() if l.strip()] if HISTORY_PATH.exists() else []
    lines = [l for l in lines if l["label"] != entry["label"]] + [entry]
    order = {p["label"]: i for i, p in enumerate(json.loads(CONFIG_PATH.read_text())["periods"])}
    lines.sort(key=lambda l: (order.get(l["label"], len(order)), l.get("run_at", "")))
    HISTORY_PATH.write_text("".join(json.dumps(l) + "\n" for l in lines))


def write_chart() -> Path:
    config = json.loads(CONFIG_PATH.read_text())
    labels = [p["label"] for p in config["periods"]]
    history = {l["label"]: l for l in (json.loads(x) for x in HISTORY_PATH.read_text().splitlines() if x.strip())}
    points = [{"label": lab, "corrected": history[lab]["corrected"], "ci_low": history[lab]["ci_low"],
               "ci_high": history[lab]["ci_high"]} for lab in labels if lab in history]
    CHART_PATH.write_text(prevalence_chart(points, threshold=config["threshold"], mode=config["judge_mode"]))
    return CHART_PATH


def monitor(label: str, records: list[dict[str, Any]], trace_count: int, config: dict[str, Any],
            *, write: bool, assume_yes: bool) -> dict[str, Any] | None:
    mode, judge_id = config["judge_mode"], config["judge_id"]
    groups = {name: DEFAULT_RISK_GROUPS[name] for name in config["risk_groups"]}
    plan = select_traces(records, config["random_rate"], groups, seed=7)
    random_ids = [r["id"] for r in plan["random"]]
    risk_ids = list(dict.fromkeys(r["id"] for members in plan["risk_groups"].values() for r in members))
    print(f"[{label}] {trace_count} Langfuse traces, {len(records)} conversations")
    print(f"[{label}] random sample {len(random_ids)}, risk groups "
          + ", ".join(f"{k} {len(v)}" for k, v in plan["risk_groups"].items())
          + f"; judge calls {len(plan['to_judge'])} on {judge_id}")
    if not assume_yes and input("Run the judge? [y/N] ").strip().lower() != "y":
        print("stopped before the judge")
        return None

    verdicts = judge_sample(judge_id, plan["to_judge"])
    random_verdicts = {t: verdicts[t] for t in random_ids}
    risk_verdicts = {t: verdicts[t] for t in risk_ids}
    labels, preds = judge_test_data(judge_id)
    estimate = corrected_mode_prevalence(list(random_verdicts.values()), labels, preds)
    records_out = build_score_records(mode, random_verdicts, risk_verdicts, estimate, label)
    written = post_scores(records_out) if write else 0

    by_id = {r["id"]: r for r in records}
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / f"{label}.json").write_text(json.dumps({
        "label": label, "judge_id": judge_id, "model": config["model"], "estimate": estimate,
        "random": [{"trace_id": t, "scenario_id": by_id[t]["scenario_id"], "verdict": v} for t, v in random_verdicts.items()],
        "risk": {name: [{"trace_id": r["id"], "scenario_id": r["scenario_id"], "verdict": verdicts[r["id"]]} for r in members]
                 for name, members in plan["risk_groups"].items()},
        "scores_written": written,
    }, indent=2) + "\n")
    entry = {
        "label": label, "judge_id": judge_id, "model": config["model"],
        "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "langfuse_traces": trace_count, "conversations": len(records),
        "random_sample": len(random_ids), "risk_sample": len(risk_ids),
        "risk_groups": {k: len(v) for k, v in plan["risk_groups"].items()},
        "risk_flag_rate": round(sum(risk_verdicts.values()) / len(risk_verdicts), 4) if risk_verdicts else None,
        "raw": estimate["raw"], "corrected": estimate["corrected"],
        "ci_low": estimate["ci_low"], "ci_high": estimate["ci_high"],
        "failure_sensitivity": estimate["failure_sensitivity"],
        "pass_specificity": estimate["pass_specificity"],
        "threshold": config["threshold"], "scores_written": written,
    }
    print(f"[{label}] raw {estimate['raw']}, corrected {estimate['corrected']} "
          f"(95% CI {estimate['ci_low']}-{estimate['ci_high']}), threshold {config['threshold']}; "
          f"{written} scores written")
    return entry


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--period", help="a period label from monitoring/config.json")
    target.add_argument("--last-hours", type=float, help="monitor the conversations of the last N hours")
    target.add_argument("--chart", action="store_true", help="redraw monitoring/prevalence.svg from history")
    parser.add_argument("--yes", action="store_true", help="skip the confirmation before the judge runs")
    parser.add_argument("--no-scores", action="store_true", help="do not write scores to Langfuse")
    args = parser.parse_args()
    config = json.loads(CONFIG_PATH.read_text())

    if args.chart:
        print(f"wrote {write_chart().relative_to(REPO_ROOT)}")
        return
    if args.period:
        period = next((p for p in config["periods"] if p["label"] == args.period), None)
        if period is None:
            raise SystemExit(f"no period {args.period!r} in {CONFIG_PATH}")
        records, trace_count = period_records(config, period)
        label = period["label"]
    else:
        records, trace_count, label = window_records(config, args.last_hours)
        if not records:
            OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            (OUTPUT_DIR / "last-run.json").write_text(json.dumps(
                {"label": label, "langfuse_traces": trace_count, "conversations": 0, "judge_calls": 0}) + "\n")
            print(f"[{label}] {trace_count} Langfuse traces, 0 eligible conversations; judge not called")
            return
    entry = monitor(label, records, trace_count, config, write=not args.no_scores, assume_yes=args.yes)
    if entry is None:
        sys.exit(0)
    if args.period:
        _save_history(entry)
        write_chart()
    else:
        (OUTPUT_DIR / "last-run.json").write_text(json.dumps(entry, indent=2) + "\n")


if __name__ == "__main__":
    main()
