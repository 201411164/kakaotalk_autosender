"""V3 workspace settings and reservation persistence (local JSON)."""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

log = logging.getLogger("kakao_workspace")


def _settings_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


SETTINGS_PATH = _settings_root() / "v3_workspace_settings.json"


def default_settings() -> dict[str, Any]:
    return {
        "version": 1,
        "ui": {
            "auto_refresh": True,
            "forward_click": True,
            "forward_double_click": False,
            "background_input": True,
            "hide_native_input_px": 120,
            "interval_ms": 500,
        },
        "ai": {
            "mode_index": 1,
            "level": 3,
            "role": "",
            "prompt": "",
            "examples": "",
            "sources": "",
            "chat_context": "",
            "guardrails": "",
        },
        "known_room_titles": [],
        "reservations": [],
    }


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _serialize_reservation(item: dict) -> dict:
    out = {
        "room": item.get("room", ""),
        "message": item.get("message", ""),
        "send_at": item["send_at"].isoformat(timespec="seconds")
        if isinstance(item.get("send_at"), datetime)
        else str(item.get("send_at", "")),
    }
    if item.get("hwnd"):
        out["hwnd"] = int(item["hwnd"])
    return out


def load_workspace_settings() -> dict[str, Any]:
    if not SETTINGS_PATH.exists():
        return default_settings()
    try:
        raw = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return default_settings()
        base = default_settings()
        base["ui"].update(raw.get("ui") or {})
        base["ai"].update(raw.get("ai") or {})
        base["known_room_titles"] = list(raw.get("known_room_titles") or [])
        reservations = []
        for row in raw.get("reservations") or []:
            if not isinstance(row, dict) or not row.get("send_at"):
                continue
            try:
                send_at = _parse_dt(row["send_at"])
            except ValueError:
                continue
            if send_at <= datetime.now():
                continue
            reservations.append(
                {
                    "room": row.get("room", ""),
                    "message": row.get("message", ""),
                    "send_at": send_at,
                    "hwnd": row.get("hwnd"),
                    "created_at": datetime.now(),
                }
            )
        base["reservations"] = reservations
        return base
    except Exception:
        log.error("Failed to load workspace settings", exc_info=True)
        return default_settings()


def save_workspace_settings(
    *,
    ui: dict[str, Any],
    ai: dict[str, Any],
    known_room_titles: list[str],
    reservations: list[dict],
) -> None:
    payload = {
        "version": 1,
        "last_updated": datetime.now().isoformat(timespec="seconds"),
        "ui": ui,
        "ai": ai,
        "known_room_titles": known_room_titles,
        "reservations": [_serialize_reservation(r) for r in reservations],
    }
    try:
        SETTINGS_PATH.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        log.error("Failed to save workspace settings", exc_info=True)
