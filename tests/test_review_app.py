"""Offline tests for the Homework 4 review interface (no network, no model)."""

from __future__ import annotations

import pytest

from analysis.review_app import conversations as conv
from analysis.review_app import server


def _trace(trace_id, session, ts, user, reply, scenario="support-0001", tool_out=None):
    tool_out = tool_out or {"ok": True, "order": {"order_id": 7, "status": "delivered", "total_usd": 12.5}}
    history = [
        {"role": "system", "parts": [{"type": "text", "content": "prompt"}]},
        {"role": "user", "parts": [{"type": "text", "content": user}]},
        {"role": "assistant", "parts": [
            {"type": "text", "content": "I'll look up the order."},
            {"type": "tool_call", "id": "c1", "name": "get_order", "arguments": {"order_id": 7}},
        ]},
        {"role": "tool", "parts": [{"type": "tool_call_response", "id": "c1", "response": repr(tool_out)}]},
    ]
    return {
        "id": trace_id,
        "timestamp": ts,
        "projectId": "p",
        "input": [{"role": "user", "parts": [{"type": "text", "content": user}]}],
        "output": [{"role": "assistant", "parts": [{"type": "text", "content": reply}]}],
        "metadata": {"attributes": {
            "cartwheel.scenario_id": scenario, "cartwheel.session_id": session,
            "cartwheel.user_role": "shopper", "cartwheel.user_id": "5",
        }},
        "observations": [
            {"type": "GENERATION", "startTime": ts + "1", "model": "m", "input": history[:2], "output": None},
            {"type": "TOOL", "name": "get_order", "startTime": ts + "2", "input": {"order_id": 7}, "output": tool_out,
             "metadata": {"attributes": {"cartwheel.permission_denied": "false"}}},
            {"type": "GENERATION", "startTime": ts + "3", "model": "m", "input": history, "output": None},
        ],
    }


def test_sessions_group_turns_in_order_and_skip_other_sets():
    traces = [
        _trace("t2", "s1", "2026-01-01T00:02", "second", "reply two"),
        _trace("t1", "s1", "2026-01-01T00:01", "first", "reply one"),
        _trace("p1", "s2", "2026-01-01T00:03", "pilot", "x", scenario="pilot-0001"),
    ]
    out = conv.build_conversations(traces)
    assert len(out) == 1
    c = out[0]
    assert c["id"] == "t1" and c["trace_ids"] == ["t1", "t2"]
    assert [t["user"] for t in c["turns"]] == ["first", "second"]
    steps = c["turns"][0]["steps"]
    assert [s["kind"] for s in steps] == ["agent_text", "tool"]
    assert steps[1]["result"]["order"]["order_id"] == 7
    assert "order #7 delivered" in steps[1]["summary"]
    assert not c["incomplete"]


def test_permission_denied_result_is_flagged():
    denied = {"ok": False, "error": "permission_denied", "reason": "not your order"}
    c = conv.build_conversations([_trace("t1", "s1", "2026-01-01", "u", "r", tool_out=denied)])[0]
    tool = c["turns"][0]["steps"][1]
    assert tool["permission_denied"] is True
    assert tool["summary"].startswith("error · permission_denied")


def test_run_without_generations_is_incomplete():
    trace = _trace("t1", "s1", "2026-01-01", "u", "")
    trace["observations"] = []
    trace["output"] = None
    assert conv.build_conversations([trace])[0]["incomplete"] is True


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("CARTWHEEL_ANALYSIS_STATE", str(tmp_path))
    for key in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_HOST"):
        monkeypatch.delenv(key, raising=False)
    return tmp_path


def test_label_flip_appends_and_supersedes(state):
    first = server.record_label({"mode": "unconfirmed_write", "trace_id": "t1", "label": 1})
    second = server.record_label({"mode": "unconfirmed_write", "trace_id": "t1", "label": 0})
    rows = [line for line in (state / "labels" / "unconfirmed_write.jsonl").read_text().splitlines() if line]
    assert len(rows) == 2
    assert first["langfuse_synced"] is False
    live = server.current_labels()
    assert [(r["trace_id"], r["label"], r["label_id"]) for r in live] == [("t1", 0, second["label_id"])]


@pytest.mark.parametrize("body", [
    {"mode": "Bad Name", "trace_id": "t1", "label": 1},
    {"mode": "ok_mode", "trace_id": "", "label": 1},
    {"mode": "ok_mode", "trace_id": "t1", "label": 2},
])
def test_label_rejects_bad_input(state, body):
    with pytest.raises(ValueError):
        server.record_label(body)


def test_spec_ids_parse():
    ids = {item["id"] for item in server.parse_spec(server.SPEC_PATH.read_text())}
    assert {"RESP-2", "ESC-1", "AUTH-1", "SCOPE-2", "TOOL-7"} <= ids


def test_hw5_label_uses_pass_convention_and_separate_file(state):
    server.record_label({"mode": "unrequested_detail", "trace_id": "t1", "label": 1, "hw5": True})
    server.record_label({"mode": "unrequested_detail", "trace_id": "t2", "label": 0, "hw5": True})
    stored = {r["trace_id"]: r["label"] for r in server._state.read_jsonl(state / "hw5_labels" / "unrequested_detail.jsonl")}
    assert stored == {"t1": 0, "t2": 1}  # 1 = Pass, 0 = Fail in the HW5 file
    assert not (state / "labels" / "unrequested_detail.jsonl").exists()
    ui = {r["trace_id"]: (r["label"], r["pass"]) for r in server.current_hw5_labels()}
    assert ui == {"t1": (1, 0), "t2": (0, 1)}
