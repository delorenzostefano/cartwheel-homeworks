"""Homework 5: judge inputs, label split, development runs, and the final test.

Run from the repository root:

    uv run python -m analysis.run_judges prepare
    uv run python -m analysis.run_judges split unrequested_detail

Every judge input comes from the committed trace exports, so each prompt
version sees exactly the same records. Labels, review notes, and scenario
metadata never enter an input record.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from analysis.helpers import _state, split_labels
from analysis.review_app import conversations as conv

REPO_ROOT = Path(__file__).resolve().parents[1]
INPUTS_PATH = REPO_ROOT / "analysis" / "state" / "hw5_trace_inputs.json"
MODES = ("unrequested_detail",)


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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prepare"); p.add_argument("mode", nargs="?", default=MODES[0])
    s = sub.add_parser("split"); s.add_argument("mode")
    args = parser.parse_args()
    if args.command == "prepare":
        prepare_inputs(args.mode)
    else:
        split_data(args.mode)


if __name__ == "__main__":
    main()
