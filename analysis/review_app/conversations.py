"""Build reviewable conversations from Cartwheel support traces.

Cartwheel writes one Langfuse trace per user turn. A reviewer needs the whole
conversation, so this module groups traces by the ``cartwheel.session_id``
attribute (the Langfuse ``sessionId`` field is empty on these traces) and
rebuilds each turn in order:

    user message -> agent text -> tool call + result -> ... -> final reply

The generation spans carry no output, so the shared helper in
``analysis/helpers/normalization.py`` cannot recover the agent's text between
tool calls. The input of the last generation in a turn holds that history, so
this module reads the turn from there and takes each tool result from the
matching tool span (clean JSON, plus the permission-denied attribute).
"""

from __future__ import annotations

import ast
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from analysis.helpers.normalization import _data

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_EXPORT = REPO_ROOT / "traces" / "support_traces.json"
DEFAULT_SCENARIOS = REPO_ROOT / "scenarios" / "support_scenarios.jsonl"
SCENARIO_PREFIX = "support-"


def _attributes(trace: dict[str, Any]) -> dict[str, Any]:
    metadata = trace.get("metadata") or {}
    attrs = metadata.get("attributes") if isinstance(metadata, dict) else None
    if isinstance(attrs, str):
        try:
            attrs = json.loads(attrs)
        except ValueError:
            attrs = None
    return attrs if isinstance(attrs, dict) else {}


def _parts_text(messages: Any) -> str:
    """Join the text parts of an OTel GenAI message list."""
    if isinstance(messages, str):
        return messages
    texts: list[str] = []
    for message in messages or []:
        if not isinstance(message, dict):
            continue
        for part in message.get("parts") or []:
            if isinstance(part, dict) and part.get("type") == "text" and part.get("content"):
                texts.append(str(part["content"]))
    return "\n".join(texts)


def _parse_result(value: Any) -> Any:
    """Tool responses inside generation input are Python reprs, not JSON."""
    if not isinstance(value, str):
        return value
    for parse in (json.loads, ast.literal_eval):
        try:
            return parse(value)
        except (ValueError, SyntaxError):
            continue
    return value


def summarize_result(result: Any) -> str:
    """One line a reviewer can scan: ok/error first, then the key fields."""
    if not isinstance(result, dict):
        return str(result)[:160]
    if result.get("ok") is False:
        return f"error · {result.get('error', '?')} · {result.get('reason', '')}"[:200]
    bits = ["ok"]
    for key in ("status", "eligible", "refund_id", "ticket_id", "amount_usd", "count", "policy_id"):
        if key in result:
            bits.append(f"{key}={result[key]}")
    order = result.get("order")
    if isinstance(order, dict):
        bits.append(
            f"order #{order.get('order_id')} {order.get('status')} ${order.get('total_usd')}"
        )
    for key in ("orders", "results", "products"):
        if isinstance(result.get(key), list):
            bits.append(f"{len(result[key])} {key}")
    return " · ".join(str(b) for b in bits)


def _tool_spans(trace: dict[str, Any]) -> list[dict[str, Any]]:
    spans = [o for o in trace.get("observations") or [] if o.get("type") == "TOOL"]
    return sorted(spans, key=lambda o: o.get("startTime") or "")


def _tool_step(name: str, arguments: Any, result: Any, span: dict[str, Any] | None) -> dict[str, Any]:
    denied = False
    if span is not None:
        attrs = (span.get("metadata") or {}).get("attributes") or {}
        denied = str(attrs.get("cartwheel.permission_denied", "")).lower() == "true"
        if span.get("output") is not None:
            result = _data(span["output"])
    if isinstance(result, dict) and result.get("error") == "permission_denied":
        denied = True
    return {
        "kind": "tool",
        "name": name,
        "arguments": arguments,
        "result": result,
        "summary": summarize_result(result),
        "permission_denied": denied,
    }


def build_turn(trace: dict[str, Any]) -> dict[str, Any]:
    """Rebuild one user turn as an ordered list of steps."""
    user_text = _parts_text(trace.get("input"))
    reply = _parts_text(trace.get("output"))
    generations = sorted(
        (o for o in trace.get("observations") or [] if o.get("type") == "GENERATION"),
        key=lambda o: o.get("startTime") or "",
    )
    spans = _tool_spans(trace)
    steps: list[dict[str, Any]] = []

    history = generations[-1].get("input") if generations else None
    if isinstance(history, list) and history:
        # The history may include earlier turns; this turn starts at the last user message.
        start = max(
            (i for i, m in enumerate(history) if isinstance(m, dict) and m.get("role") == "user"),
            default=0,
        )
        span_iter = iter(spans)
        for message in history[start + 1 :]:
            if not isinstance(message, dict) or message.get("role") != "assistant":
                continue
            for part in message.get("parts") or []:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "text" and str(part.get("content", "")).strip():
                    steps.append({"kind": "agent_text", "text": part["content"]})
                elif part.get("type") == "tool_call":
                    span = next(span_iter, None)
                    if span is not None and span.get("name") != part.get("name"):
                        span = None
                    response = _find_response(history, part.get("id"))
                    steps.append(
                        _tool_step(part.get("name", "tool"), part.get("arguments"), response, span)
                    )
    else:
        # No generation history (a failed run): fall back to the tool spans alone.
        for span in spans:
            steps.append(_tool_step(span.get("name", "tool"), _data(span.get("input")), None, span))

    return {
        "trace_id": trace["id"],
        "timestamp": trace.get("timestamp"),
        "user": user_text,
        "steps": steps,
        "reply": reply,
        "models": sorted({o["model"] for o in generations if o.get("model")}),
        "complete": bool(reply) and bool(generations),
    }


def _find_response(history: list[Any], call_id: Any) -> Any:
    for message in history:
        if not isinstance(message, dict) or message.get("role") != "tool":
            continue
        for part in message.get("parts") or []:
            if isinstance(part, dict) and part.get("id") == call_id:
                return _parse_result(part.get("response"))
    return None


def load_scenarios(path: Path = DEFAULT_SCENARIOS) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return {row["id"]: row for row in rows}


def build_conversations(
    traces: list[dict[str, Any]],
    scenarios: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Group support traces by session and return one record per conversation.

    The conversation id is the first turn's trace id, the same join key the
    course helpers use for Langfuse scores. Sessions with no complete turn
    (infrastructure failures) are marked ``incomplete`` so the reviewer can
    skip them without losing track of them.
    """
    scenarios = scenarios or {}
    sessions: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for trace in traces:
        trace = _data(trace)
        attrs = _attributes(trace)
        scenario_id = trace.get("cartwheel_scenario_id") or attrs.get("cartwheel.scenario_id")
        if not str(scenario_id or "").startswith(SCENARIO_PREFIX):
            continue
        session_id = attrs.get("cartwheel.session_id") or trace["id"]
        sessions[session_id].append({**trace, "_attrs": attrs, "_scenario": scenario_id})

    conversations = []
    for session_id, group in sessions.items():
        group.sort(key=lambda t: t.get("timestamp") or "")
        first = group[0]
        attrs = first["_attrs"]
        scenario = scenarios.get(first["_scenario"], {})
        tuple_ = scenario.get("tuple", {})
        turns = [build_turn(t) for t in group]
        conversations.append(
            {
                "id": first["id"],
                "session_id": session_id,
                "scenario_id": first["_scenario"],
                "trace_ids": [t["id"] for t in group],
                "project_id": first.get("projectId"),
                "role": attrs.get("cartwheel.user_role") or tuple_.get("role"),
                "user_id": attrs.get("cartwheel.user_id") or tuple_.get("user_id"),
                "store_id": attrs.get("cartwheel.store_id") or tuple_.get("store_id"),
                "prompt_version": attrs.get("cartwheel.prompt_version"),
                "intent": tuple_.get("intent"),
                "scenario_group": scenario.get("scenario_group"),
                "data_quality_case_id": scenario.get("data_quality_case_id"),
                "expected": scenario.get("expected"),
                "turns": turns,
                "incomplete": not any(turn["complete"] for turn in turns),
            }
        )
    conversations.sort(key=lambda c: (c["scenario_id"], c["turns"][0]["timestamp"] or ""))
    return conversations


def load_export(path: Path = DEFAULT_EXPORT) -> list[dict[str, Any]]:
    data = json.loads(path.read_text())
    return data["traces"] if isinstance(data, dict) else data


def load_langfuse() -> list[dict[str, Any]]:
    """Fetch every support trace from the configured Langfuse project."""
    from analysis.helpers import langfuse_io

    lf = langfuse_io._client()
    traces: list[dict[str, Any]] = []
    page = 1
    while True:
        resp = lf.api.trace.list(page=page, limit=100)
        batch = list(resp.data or [])
        for summary in batch:
            # Round-trip through JSON so SDK datetimes become ISO strings, as in the export.
            full = json.loads(json.dumps(_data(lf.api.trace.get(summary.id)), default=str))
            scenario_id = _attributes(full).get("cartwheel.scenario_id", "")
            if str(scenario_id).startswith(SCENARIO_PREFIX):
                traces.append(full)
        if len(batch) < 100:
            break
        page += 1
    return traces
