"""Export CLI chat sessions into draft hw1-session.jsonl records.

Reads .sessions.db (written by agent/cli.py) and prints one JSON line per
user turn with role, user_id, store_id, request, tool_calls, and response
filled in. The four judgment fields (expected, requirement, met_requirement,
problem_source) are left for the student to fill by hand.

Usage:
    uv run python scripts/export_hw1_sessions.py            # all sessions
    uv run python scripts/export_hw1_sessions.py --last 3   # newest 3 sessions
    uv run python scripts/export_hw1_sessions.py >> hw1-session.jsonl
"""

from __future__ import annotations

import argparse
import ast
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from agent import db  # noqa: E402
from agent.cli import SESSIONS_DB  # noqa: E402


def _parse_result(raw: str) -> Any:
    """Tool outputs are stored as Python repr strings; turn them back into data."""
    try:
        return ast.literal_eval(raw)
    except (ValueError, SyntaxError):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw


def _text_of(item: dict[str, Any]) -> str:
    parts = item.get("content", [])
    if isinstance(parts, str):
        return parts
    return "".join(p.get("text", "") for p in parts if isinstance(p, dict))


def _store_for(user_id: int, role: str) -> int | None:
    if role != "merchant":
        return None
    conn = db.connect()
    try:
        user = db.get_user(conn, user_id)
    finally:
        conn.close()
    return user.store_id if user else None


def export_session(conn: sqlite3.Connection, session_id: str) -> list[dict[str, Any]]:
    _, role, user_id, _ = session_id.split("-", 3)
    user_id = int(user_id)
    store_id = _store_for(user_id, role)

    rows = conn.execute(
        "SELECT message_data FROM agent_messages WHERE session_id = ? ORDER BY id",
        (session_id,),
    ).fetchall()
    items = [json.loads(r[0]) for r in rows]

    records: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    pending: dict[str, dict[str, Any]] = {}

    for item in items:
        if item.get("role") == "user":
            if current is not None:
                records.append(current)
            current = {
                "role": role,
                "user_id": user_id,
                "store_id": store_id,
                "request": item["content"],
                "tool_calls": [],
                "response": "",
                "expected": "",
                "requirement": None,
                "met_requirement": None,
                "problem_source": None,
            }
            pending = {}
        elif item.get("type") == "function_call" and current is not None:
            call = {
                "name": item["name"],
                "arguments": json.loads(item.get("arguments") or "{}"),
                "result": None,
            }
            pending[item["call_id"]] = call
            current["tool_calls"].append(call)
        elif item.get("type") == "function_call_output" and current is not None:
            call = pending.get(item["call_id"])
            if call is not None:
                call["result"] = _parse_result(item.get("output", ""))
        elif item.get("role") == "assistant" and current is not None:
            text = _text_of(item)
            if text:
                current["response"] = text

    if current is not None:
        records.append(current)
    return records


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--last", type=int, default=None, help="only the newest N sessions")
    args = parser.parse_args()

    conn = sqlite3.connect(SESSIONS_DB)
    session_ids = [
        r[0]
        for r in conn.execute(
            "SELECT session_id FROM agent_messages "
            "GROUP BY session_id ORDER BY MIN(id)"
        ).fetchall()
    ]
    if args.last:
        session_ids = session_ids[-args.last :]

    for sid in session_ids:
        for record in export_session(conn, sid):
            print(json.dumps(record, ensure_ascii=False))


if __name__ == "__main__":
    main()
