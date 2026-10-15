"""Homework 5: judge inputs, label split, development runs, and the final test.

Run from the repository root:

    uv run python -m analysis.run_judges prepare
    uv run python -m analysis.run_judges split unrequested_detail
    uv run --env-file .env python -m analysis.run_judges dev unrequested_detail analysis/prompts/unrequested_detail-v0.txt
    uv run --env-file .env python -m analysis.run_judges test unrequested_detail-v<N>

Every judge input comes from the committed trace exports, so each prompt
version sees exactly the same records. Labels, review notes, and scenario
metadata never enter an input record.
"""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from pathlib import Path
from typing import Any

from analysis.helpers import _state, freeze_judge, judge_alignment, register_judge, run_judge, split_labels
from analysis.review_app import conversations as conv

REPO_ROOT = Path(__file__).resolve().parents[1]
INPUTS_PATH = REPO_ROOT / "analysis" / "state" / "hw5_trace_inputs.json"
REPORT_DIR = REPO_ROOT / "analysis" / "report"
# The handout specifies gpt-4o-mini; no OpenAI access, so DeepSeek-V3 (a different family from the Claude agent) is used for dev and test.
JUDGE_MODEL = "deepseek/deepseek-chat"
MODES = ("unrequested_detail",)
# Judge runs read the saved inputs, never live Langfuse, so every version sees the same records.
os.environ.setdefault("CARTWHEEL_JUDGE_TRACE_SOURCE", str(INPUTS_PATH))


def _hw5_labels(mode: str) -> dict[str, int]:
    """Live HW5 label per conversation, 1 = Pass and 0 = Fail."""
    live: dict[str, int] = {}
    for row in _state.read_jsonl(_state.state_path("hw5_labels", f"{mode}.jsonl")):
        if not row.get("superseded_by"):
            live[row["trace_id"]] = row["label"]
    return live


def _messages(conversation: dict[str, Any]) -> list[dict[str, Any]]:
    """The conversation as the judge sees it, turn by turn.

    ``assistant_step`` is the agent's text between tool calls; ``assistant``
    is the final reply of each turn, the text the judge evaluates. The tool
    name travels inside the tool data because the helpers flatten each
    message to ``role: content``.
    """
    out: list[dict[str, Any]] = []
    for turn in conversation["turns"]:
        out.append({"role": "user", "text": turn["user"]})
        for step in turn["steps"]:
            if step["kind"] == "agent_text":
                out.append({"role": "assistant_step", "text": step["text"]})
            else:
                out.append({"role": "tool_call", "name": step["name"],
                            "arguments": {"tool": step["name"], "arguments": step["arguments"]}})
                out.append({"role": "tool_result", "name": step["name"],
                            "content": {"tool": step["name"], "result": step["result"]}})
        out.append({"role": "assistant", "text": turn["reply"]})
    return out


def _variant_key(scenario: dict[str, Any]) -> Any:
    """The record a scenario is built on; scenarios sharing one are close variants."""
    t = scenario.get("tuple", {})
    if t.get("order_id"):
        return ("order", t["order_id"])
    if t.get("product_id"):
        return ("product", t["product_id"])
    return None


def prepare_inputs(mode: str = MODES[0]) -> list[dict[str, Any]]:
    """Write one judge input per eligible labelled conversation.

    Eligible: the conversation completed, and it is the kept member of its
    group of close variants (scenarios built on the same order or product).
    The kept member is a Fail when the group has one, else the lowest
    scenario id, so deduplication never removes a failure example.
    """
    labels = _hw5_labels(mode)
    scenarios = conv.load_scenarios()
    by_id = {c["id"]: c for c in conv.build_conversations(conv.load_export(), scenarios)}
    labelled = [by_id[t] for t in labels if t in by_id and not by_id[t]["incomplete"]]

    groups: dict[Any, list[dict[str, Any]]] = {}
    for c in labelled:
        key = _variant_key(scenarios.get(c["scenario_id"], {})) or ("conversation", c["id"])
        groups.setdefault(key, []).append(c)
    kept, dropped = [], []
    for members in groups.values():
        members.sort(key=lambda c: (labels[c["id"]], c["scenario_id"]))  # Fail (0) first
        kept.append(members[0])
        dropped.extend(members[1:])

    records = [{"trace_id": c["id"], "trace": _messages(c)} for c in sorted(kept, key=lambda c: c["scenario_id"])]
    INPUTS_PATH.write_text(json.dumps(records, indent=2, ensure_ascii=False) + "\n")
    missing = [t for t in labels if t not in by_id]
    print(f"{len(records)} inputs -> {INPUTS_PATH.relative_to(REPO_ROOT)}")
    print(f"dropped as close variants: {len(dropped)} ({', '.join(sorted(c['scenario_id'] for c in dropped))})")
    if missing:
        print(f"labels with no exported conversation: {missing}")
    return records


def split_data(mode: str) -> dict[str, list[str]]:
    """Split the eligible HW5 labels 20/40/40 once, and print the class counts."""
    records = json.loads(INPUTS_PATH.read_text())
    splits = split_labels(
        mode,
        fractions=(0.20, 0.40, 0.40),
        seed=7,
        min_per_class=10,
        eligible_trace_ids=[record["trace_id"] for record in records],
    )
    labels = _hw5_labels(mode)
    for name, ids in splits.items():
        counts = Counter("Pass" if labels[t] == 1 else "Fail" for t in ids)
        print(f"{name:5} {len(ids):3}  Pass {counts['Pass']:3}  Fail {counts['Fail']:3}")
    return splits


def _class_counts(mode: str, split: str) -> dict[str, int]:
    ids = _state.read_json(_state.state_path("splits.json"))[mode][split]
    labels = _hw5_labels(mode)
    return {"pass": sum(labels[t] == 1 for t in ids), "fail": sum(labels[t] == 0 for t in ids)}


def _save_metrics(name: str, metrics: dict[str, Any], **extra: Any) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / f"{name}.json"
    path.write_text(json.dumps({**metrics, **extra}, indent=2) + "\n")
    return path


def _print_metrics(m: dict[str, Any]) -> None:
    print(f"TP {m['tp']}  FN {m['fn']}  TN {m['tn']}  FP {m['fp']}  (n={m['n']})")
    print(f"TPR {m['tpr']:.3f}  95% Wilson {m['tpr_interval']}")
    print(f"TNR {m['tnr']:.3f}  95% Wilson {m['tnr_interval']}")


def run_development(mode: str, prompt_path: str) -> dict[str, Any]:
    """Register a prompt version, run it on dev, and save its metrics."""
    record = register_judge(mode=mode, prompt_text=Path(prompt_path).read_text(), judge_model=JUDGE_MODEL)
    judge_id = record["judge_id"]
    run_judge(judge_id, split="dev", batch_size=10)
    metrics = judge_alignment(judge_id, split="dev")
    path = _save_metrics(f"dev-{judge_id}", metrics, prompt_path=prompt_path, model=JUDGE_MODEL,
                         prompt_hash=record["prompt_hash"], class_counts=_class_counts(mode, "dev"))
    print(f"judge {judge_id} -> {path.relative_to(REPO_ROOT)}")
    _print_metrics(metrics)
    return metrics


def run_test(judge_id: str) -> dict[str, Any]:
    """Freeze the chosen version, run it once on test, and save its metrics.

    Call once. To resume an interrupted test run, call run_judge and
    judge_alignment directly; never freeze again.
    """
    freeze_judge(judge_id)
    run_judge(judge_id, split="test", batch_size=10)
    metrics = judge_alignment(judge_id, split="test")
    mode = judge_id.rsplit("-v", 1)[0]
    path = _save_metrics(f"test-{judge_id}", metrics, model=JUDGE_MODEL, class_counts=_class_counts(mode, "test"))
    print(f"judge {judge_id} -> {path.relative_to(REPO_ROOT)}")
    _print_metrics(metrics)
    return metrics


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prepare"); p.add_argument("mode", nargs="?", default=MODES[0])
    s = sub.add_parser("split"); s.add_argument("mode")
    d = sub.add_parser("dev"); d.add_argument("mode"); d.add_argument("prompt_path")
    t = sub.add_parser("test"); t.add_argument("judge_id")
    args = parser.parse_args()
    if args.command == "prepare":
        prepare_inputs(args.mode)
    elif args.command == "split":
        split_data(args.mode)
    elif args.command == "dev":
        run_development(args.mode, args.prompt_path)
    else:
        run_test(args.judge_id)


if __name__ == "__main__":
    main()
