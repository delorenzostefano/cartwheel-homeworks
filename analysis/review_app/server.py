"""Review server for Homework 4: conversations, notes, taxonomy, and labels.

Adapted from the reference ``analysis/server.py``: the same standard-library
HTTP server and plain-file JSON API over ``analysis/state/``, so the
error-discovery workflow (watch annotations, push patterns and suggestions)
works unchanged. Changes for the Cartwheel traces:

  - ``/api/conversations`` serves whole sessions, grouped by
    ``cartwheel.session_id`` and rebuilt turn by turn (see conversations.py).
  - ``/api/labels`` records one present/absent judgment per conversation and
    mode: an append-only line in ``state/labels/<mode>.jsonl`` and a Langfuse
    score on the conversation's first trace.
  - ``/api/spec`` serves the requirement identifiers from ``SPEC.md``.

API:

    GET  /                      the review app
    GET  /api/conversations     support conversations (built at startup)
    GET  /api/spec              requirement ids and text from SPEC.md
    GET  /api/labels            current label per (mode, conversation)
    POST /api/labels            record one judgment
    GET|POST /api/annotations   free-text notes
    GET|POST /api/samples       review batches and the reason for each pick
    GET|POST /api/patterns      the taxonomy
    GET|POST /api/suggestions   AI suggestions and accept/reject decisions

Run it:

    uv run --env-file .env python -m analysis.review_app.server            # live Langfuse
    uv run python -m analysis.review_app.server --export traces/support_traces.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from analysis.helpers import _state, langfuse_io
from analysis.review_app import conversations as conv

HERE = Path(__file__).resolve().parent
SPEC_PATH = conv.REPO_ROOT / "SPEC.md"

# API path -> state file name and its empty default, as in the reference server.
API_FILES: dict[str, tuple[str, Any]] = {
    "/api/annotations": ("annotations.json", {"annotations": []}),
    "/api/samples": ("samples.json", []),
    "/api/patterns": ("patterns.json", {"modes": []}),
    "/api/suggestions": ("suggestions.json", []),
}

MODE_NAME = re.compile(r"^[a-z][a-z0-9_]*$")

CONVERSATIONS: list[dict[str, Any]] = []


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_spec(text: str) -> list[dict[str, str]]:
    """Return ``{id, text}`` for every bold requirement id such as **RESP-2.**"""
    items = []
    for match in re.finditer(r"\*\*([A-Z]+-\d+)\.\*\*\s*(.+?)(?=\n\s*\n|\n- \*\*|\Z)", text, re.S):
        items.append({"id": match.group(1), "text": " ".join(match.group(2).split())})
    for match in re.finditer(r"^\| (TOOL-\d+) \| `(\w+)` \|(.+)$", text, re.M):
        items.append({"id": match.group(1), "text": f"{match.group(2)}: {match.group(3).strip(' |')}"})
    return items


def current_labels() -> list[dict[str, Any]]:
    """Collapse every label file to the live judgment per conversation."""
    out = []
    labels_dir = _state.state_path("labels")
    if not labels_dir.exists():
        return out
    for path in sorted(labels_dir.glob("*.jsonl")):
        live: dict[str, dict[str, Any]] = {}
        for row in _state.read_jsonl(path):
            if not row.get("superseded_by"):
                live[row["trace_id"]] = row
        out.extend({**row, "mode": path.stem} for row in live.values())
    return out


def record_label(body: dict[str, Any]) -> dict[str, Any]:
    """Append one judgment; mark the previous live one superseded.

    ``label`` follows the stored convention: 1 = failure present, 0 = absent.
    The Langfuse score is written first so a failed canonical write leaves
    the local file unchanged and the reviewer can retry.
    """
    mode = str(body.get("mode", ""))
    trace_id = str(body.get("trace_id", ""))
    label = body.get("label")
    if not MODE_NAME.match(mode) or not trace_id or label not in (0, 1):
        raise ValueError("expected snake_case mode, trace_id, and label 0 or 1")

    synced = False
    if langfuse_io.is_configured():
        langfuse_io.write_label_score(trace_id, mode, int(label), comment=body.get("evidence"))
        synced = True

    path = _state.state_path("labels", f"{mode}.jsonl")
    rows = _state.read_jsonl(path)
    count = sum(1 for row in rows if row["trace_id"] == trace_id)
    record = {
        "trace_id": trace_id,
        "label": int(label),
        "source": body.get("source", "human"),
        "ts": _now(),
        "label_id": f"{trace_id}#{count}",
        "scenario_id": body.get("scenario_id"),
        "session_trace_ids": body.get("session_trace_ids", []),
        "evidence": body.get("evidence", ""),
        "langfuse_synced": synced,
    }
    for row in rows:
        if row["trace_id"] == trace_id and not row.get("superseded_by"):
            row["superseded_by"] = record["label_id"]
    _state.write_jsonl(path, [*rows, record])
    return record


class ReviewHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A002
        return

    def _send(self, body: bytes, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, data: Any, status: int = 200) -> None:
        self._send(json.dumps(data).encode(), "application/json", status)

    def _read_body(self) -> Any:
        length = int(self.headers.get("Content-Length", 0))
        if not length:
            return None
        try:
            return json.loads(self.rfile.read(length))
        except json.JSONDecodeError:
            return None

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            self._send((HERE / "index.html").read_bytes(), "text/html; charset=utf-8")
        elif path == "/api/conversations":
            self._send_json(CONVERSATIONS)
        elif path == "/api/spec":
            self._send_json(parse_spec(SPEC_PATH.read_text()))
        elif path == "/api/labels":
            self._send_json(current_labels())
        elif path in API_FILES:
            name, default = API_FILES[path]
            try:
                data = _state.read_json(_state.state_path(name), default)
            except json.JSONDecodeError:
                data = default
            self._send_json(data)
        else:
            self._send_json({"error": f"unknown path: {path}"}, status=404)

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        data = self._read_body()
        if data is None:
            self._send_json({"error": "expected a JSON body"}, status=400)
            return
        if path == "/api/labels":
            try:
                self._send_json({"ok": True, "label": record_label(data)})
            except ValueError as exc:
                self._send_json({"error": str(exc)}, status=400)
            except Exception as exc:  # network path: the local file is untouched
                self._send_json({"error": f"Langfuse score write failed: {exc}"}, status=502)
            return
        if path not in API_FILES:
            self._send_json({"error": f"cannot POST to {path}"}, status=404)
            return
        _state.write_json(_state.state_path(API_FILES[path][0]), data)
        if path == "/api/samples":
            _write_manifest(data)
        self._send_json({"ok": True})


def _write_manifest(samples: Any) -> None:
    """Keep sample_manifest.json in step with the batches in samples.json."""
    if not isinstance(samples, list):
        return
    batches: dict[str, list[dict[str, Any]]] = {}
    for item in samples:
        if isinstance(item, dict):
            batches.setdefault(item.get("batch", "unassigned"), []).append(
                {k: item.get(k) for k in ("trace_id", "scenario_id", "reason")}
            )
    _state.write_json(
        _state.state_path("sample_manifest.json"),
        {
            "updated_at": _now(),
            "total": sum(len(v) for v in batches.values()),
            "batches": batches,
        },
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Homework 4 review interface")
    parser.add_argument("--port", type=int, default=8030)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument(
        "--export",
        metavar="PATH",
        help="read traces from a Module 1 export instead of live Langfuse "
        "(record the reason in interface_comparison.md)",
    )
    args = parser.parse_args()

    if args.export:
        traces = conv.load_export(Path(args.export))
        source = args.export
    else:
        print("loading support traces from Langfuse...")
        traces = conv.load_langfuse()
        source = "langfuse"
    CONVERSATIONS.extend(conv.build_conversations(traces, conv.load_scenarios()))
    host = os.environ.get("LANGFUSE_HOST", "http://localhost:3000").rstrip("/")
    for c in CONVERSATIONS:
        c["langfuse_urls"] = [
            f"{host}/project/{c['project_id']}/traces/{tid}" for tid in c["trace_ids"]
        ] if c.get("project_id") else []
    incomplete = [c["scenario_id"] for c in CONVERSATIONS if c["incomplete"]]
    print(f"{len(CONVERSATIONS)} conversations from {len(traces)} traces ({source})")
    if incomplete:
        print(f"incomplete (no agent turn recorded): {', '.join(incomplete)}")
    if not langfuse_io.is_configured():
        print("Langfuse not configured: labels are saved locally only")

    server = ThreadingHTTPServer((args.host, args.port), ReviewHandler)
    print(f"review interface on http://{args.host}:{args.port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
