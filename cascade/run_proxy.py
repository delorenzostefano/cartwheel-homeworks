"""Homework 9 Part D: run the proxy judge (local Ollama qwen3:8b, thinking off) with a confidence score."""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

import litellm

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("CARTWHEEL_JUDGE_TRACE_SOURCE", str(ROOT / "analysis/state/hw5_trace_inputs.json"))

from analysis.helpers.scale import _trace_text, load_store_traces  # noqa: E402
from observability.instrument import load_env  # noqa: E402

PROXY_MODEL = "ollama_chat/qwen3:8b"  # local Ollama, thinking off
ORACLE_FILE = ROOT / "analysis/state/judges/unrequested_detail-v0.json"
CONFIDENCE_INSTRUCTION = (
    "Also state your confidence that your result is correct, as a number from 0 to 1. "
    'Reply with JSON only: {"critique": "...", "result": "Pass" or "Fail", "confidence": 0.0}'
)


def proxy_prompt(judge_prompt: str, trace_text: str) -> str:
    """The oracle's DocETL wrapper (analysis/helpers/scale.py), plus one confidence instruction."""
    return (
        f"{judge_prompt}\n\n--- Trace to evaluate ---\n{trace_text}\n\n"
        "First write a critique of the trace against the criterion. "
        "Use specific evidence from the provided trace. Then return result "
        "as exactly Pass when the named failure is absent, or Fail when present.\n\n"
        + CONFIDENCE_INSTRUCTION
    )


def complete_with_retry(**kwargs):
    """Free tiers limit requests and tokens per minute, so wait out rate limits."""
    for _ in range(10):
        try:
            return litellm.completion(**kwargs)
        except litellm.RateLimitError:
            time.sleep(15)
    raise RuntimeError("Rate limit did not clear after 10 waits")


def judge_one(judge_prompt: str, trace_id: str, store: dict) -> dict:
    response = complete_with_retry(
        model=PROXY_MODEL,
        messages=[{"role": "user", "content": proxy_prompt(judge_prompt, _trace_text(trace_id, store))}],
        temperature=0,
        response_format={"type": "json_object"},
        think=False,
    )
    usage = response.usage
    row = {"trace_id": trace_id, "input_tokens": usage.prompt_tokens, "output_tokens": usage.completion_tokens}
    try:
        answer = json.loads(response.choices[0].message.content)
        row["verdict"] = {"Pass": 1, "Fail": 0}[answer["result"]]
        row["confidence"] = min(1.0, max(0.0, float(answer["confidence"])))
    except (KeyError, TypeError, ValueError) as exc:
        # An unusable answer is never kept: confidence 0 sends it to the oracle.
        row.update(verdict=None, confidence=0.0, error=str(exc)[:200])
    return row


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--split", choices=("dev", "test"), required=True)
    parser.add_argument("--limit", type=int, help="judge only the first N traces (for the one-request check)")
    args = parser.parse_args()
    load_env()
    judge_prompt = json.loads(ORACLE_FILE.read_text())["prompt_text"]
    trace_ids = json.loads((ROOT / "analysis/state/splits.json").read_text())["unrequested_detail"][args.split]
    trace_ids = trace_ids[: args.limit] if args.limit else trace_ids
    store = {trace["trace_id"]: trace for trace in load_store_traces()}
    out = ROOT / "cascade/results" / f"proxy-{args.split}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    # Resume: keep verdicts already saved and judge only the remaining traces.
    done = {json.loads(line)["trace_id"] for line in out.open()} if out.exists() else set()
    with out.open("a") as handle:
        for trace_id in trace_ids:
            if trace_id in done:
                continue
            handle.write(json.dumps(judge_one(judge_prompt, trace_id, store)) + "\n")
            handle.flush()
    print(f"Saved {len(trace_ids)} proxy verdicts to {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
