"""Kakao Manager V3 원격 제어 Hub (stdlib HTTP, FastAPI 불필요).

실행:
    set KAKAO_REMOTE_TOKEN=your-secret
    python tools/v3_spike/kakao_remote_hub.py --port 8765
"""
from __future__ import annotations

import argparse
import json
import os
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

_STALE_SEC = 90


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class HubStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.pcs: dict[str, dict[str, Any]] = {}
        self.frames: dict[str, dict[str, Any]] = {}
        self.candidates: dict[str, list[dict]] = {}
        self.commands: dict[int, dict[str, Any]] = {}
        self._next_cmd_id = 1

    def upsert_pc(self, pc_id: str, payload: dict) -> None:
        with self._lock:
            row = self.pcs.setdefault(pc_id, {"pc_id": pc_id})
            row.update(payload)
            row["last_heartbeat"] = _utc_now()

    def set_frame(self, pc_id: str, jpeg_b64: str, meta: dict | None = None) -> None:
        with self._lock:
            self.frames[pc_id] = {
                "jpeg_b64": jpeg_b64,
                "updated_at": _utc_now(),
                "meta": meta or {},
            }

    def set_candidates(self, pc_id: str, items: list[dict]) -> None:
        with self._lock:
            self.candidates[pc_id] = items

    def enqueue_command(self, pc_id: str, command_type: str, payload: dict) -> int:
        with self._lock:
            cmd_id = self._next_cmd_id
            self._next_cmd_id += 1
            self.commands[cmd_id] = {
                "id": cmd_id,
                "pc_id": pc_id,
                "command_type": command_type,
                "payload": payload,
                "status": "pending",
                "result_message": "",
                "created_at": _utc_now(),
            }
            return cmd_id

    def pending_for(self, pc_id: str) -> list[dict]:
        with self._lock:
            return [
                dict(c)
                for c in self.commands.values()
                if c["pc_id"] == pc_id and c["status"] == "pending"
            ]

    def update_command(self, cmd_id: int, status: str, message: str) -> None:
        with self._lock:
            cmd = self.commands.get(cmd_id)
            if cmd:
                cmd["status"] = status
                cmd["result_message"] = message
                cmd["finished_at"] = _utc_now()

    def list_pcs(self) -> list[dict]:
        with self._lock:
            out = []
            for pc_id, row in self.pcs.items():
                hb = row.get("last_heartbeat", "")
                online = False
                try:
                    ts = datetime.fromisoformat(hb.replace("Z", "+00:00"))
                    online = (datetime.now(timezone.utc) - ts).total_seconds() < _STALE_SEC
                except Exception:
                    pass
                out.append(
                    {
                        "pc_id": pc_id,
                        "pc_name": row.get("pc_name", pc_id),
                        "ip_address": row.get("ip_address"),
                        "app_version": row.get("app_version", ""),
                        "status": "online" if online else "offline",
                        "last_heartbeat": hb,
                        "selected_hwnd": row.get("selected_hwnd"),
                        "selected_title": row.get("selected_title", ""),
                        "main_hwnd": row.get("main_hwnd"),
                    }
                )
            return out


store = HubStore()


def _expected_token() -> str:
    return (os.getenv("KAKAO_REMOTE_TOKEN") or "").strip()


class HubHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8"))

    def _json_response(self, code: int, data: dict) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _check_auth(self) -> bool:
        token = _expected_token()
        if not token:
            self._json_response(503, {"error": "KAKAO_REMOTE_TOKEN not set on hub"})
            return False
        auth = self.headers.get("Authorization", "")
        if not auth.startswith("Bearer ") or auth[7:].strip() != token:
            self._json_response(403, {"error": "Invalid token"})
            return False
        return True

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        if path == "/health":
            self._json_response(200, {"ok": True, "time": _utc_now()})
            return

        if not self._check_auth():
            return

        if path == "/api/controller/pcs":
            self._json_response(200, {"pcs": store.list_pcs()})
            return

        if path.startswith("/api/controller/pcs/") and path.endswith("/frame"):
            pc_id = path.split("/")[4]
            frame = store.frames.get(pc_id, {"jpeg_b64": None, "updated_at": None})
            self._json_response(200, frame)
            return

        if path.startswith("/api/controller/pcs/") and path.endswith("/candidates"):
            pc_id = path.split("/")[4]
            self._json_response(200, {"candidates": store.candidates.get(pc_id, [])})
            return

        if path == "/api/agent/commands/pending":
            pc_id = (qs.get("pc_id") or [""])[0]
            self._json_response(200, {"commands": store.pending_for(pc_id)})
            return

        self._json_response(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path == "/health":
            self._json_response(200, {"ok": True})
            return
        if not self._check_auth():
            return
        body = self._read_json()

        if self.path == "/api/agent/heartbeat":
            pc_id = str(body.get("pc_id", "")).strip()
            if not pc_id:
                self._json_response(400, {"error": "pc_id required"})
                return
            store.upsert_pc(
                pc_id,
                {
                    "pc_name": body.get("pc_name", pc_id),
                    "ip_address": body.get("ip_address"),
                    "app_version": body.get("app_version", ""),
                    "selected_hwnd": body.get("selected_hwnd"),
                    "selected_title": body.get("selected_title", ""),
                    "main_hwnd": body.get("main_hwnd"),
                },
            )
            self._json_response(200, {"ok": True})
            return

        if self.path == "/api/agent/frame":
            pc_id = str(body.get("pc_id", "")).strip()
            if not pc_id or not body.get("jpeg_b64"):
                self._json_response(400, {"error": "pc_id and jpeg_b64 required"})
                return
            store.set_frame(pc_id, body["jpeg_b64"], body.get("meta"))
            self._json_response(200, {"ok": True})
            return

        if self.path == "/api/agent/candidates":
            pc_id = str(body.get("pc_id", "")).strip()
            store.set_candidates(pc_id, body.get("candidates") or [])
            self._json_response(200, {"ok": True})
            return

        if self.path == "/api/controller/commands":
            pc_id = str(body.get("pc_id", "")).strip()
            cmd_type = str(body.get("command_type", "")).strip()
            if not pc_id or not cmd_type:
                self._json_response(400, {"error": "pc_id and command_type required"})
                return
            cmd_id = store.enqueue_command(pc_id, cmd_type, body.get("payload") or {})
            self._json_response(200, {"id": cmd_id, "status": "queued"})
            return

        self._json_response(404, {"error": "not found"})

    def do_PATCH(self) -> None:  # noqa: N802
        if not self._check_auth():
            return
        parts = self.path.strip("/").split("/")
        if len(parts) == 4 and parts[0] == "api" and parts[1] == "agent" and parts[2] == "commands":
            cmd_id = int(parts[3])
            body = self._read_json()
            store.update_command(
                cmd_id,
                str(body.get("status", "done")),
                str(body.get("result_message", "")),
            )
            self._json_response(200, {"ok": True})
            return
        self._json_response(404, {"error": "not found"})


def main() -> None:
    parser = argparse.ArgumentParser(description="Kakao Remote Hub")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if not _expected_token():
        print("WARNING: KAKAO_REMOTE_TOKEN is empty.")
    server = ThreadingHTTPServer((args.host, args.port), HubHandler)
    print(f"Kakao Remote Hub http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Hub stopped.")


if __name__ == "__main__":
    main()
