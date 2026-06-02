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
        "ai_presets": {},
        "monitor_rooms": [],
        "known_room_titles": [],
        "reservations": [],
        "instance_names": {},
        "kakao_exe_path": "",
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
    if item.get("instance_pid") is not None:
        out["instance_pid"] = int(item["instance_pid"])
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
        raw_presets = raw.get("ai_presets")
        if isinstance(raw_presets, dict):
            base["ai_presets"] = {
                str(name): dict(cfg)
                for name, cfg in raw_presets.items()
                if isinstance(cfg, dict)
            }
        raw_monitor = raw.get("monitor_rooms")
        if isinstance(raw_monitor, list):
            base["monitor_rooms"] = [
                {
                    "title": str(m.get("title", "")),
                    "keywords": [str(k) for k in (m.get("keywords") or [])],
                }
                for m in raw_monitor
                if isinstance(m, dict) and m.get("title")
            ]
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
            inst_pid = row.get("instance_pid")
            reservations.append(
                {
                    "room": row.get("room", ""),
                    "message": row.get("message", ""),
                    "send_at": send_at,
                    "hwnd": row.get("hwnd"),
                    "instance_pid": int(inst_pid) if inst_pid is not None else None,
                    "created_at": datetime.now(),
                }
            )
        base["reservations"] = reservations
        raw_names = raw.get("instance_names") or {}
        if isinstance(raw_names, dict):
            base["instance_names"] = {str(k): str(v) for k, v in raw_names.items()}
        base["kakao_exe_path"] = str(raw.get("kakao_exe_path") or "")
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
    instance_names: dict[str, str] | None = None,
    kakao_exe_path: str = "",
    ai_presets: dict[str, Any] | None = None,
    monitor_rooms: list[dict] | None = None,
) -> None:
    payload = {
        "version": 1,
        "last_updated": datetime.now().isoformat(timespec="seconds"),
        "ui": ui,
        "ai": ai,
        "ai_presets": ai_presets or {},
        "monitor_rooms": monitor_rooms or [],
        "known_room_titles": known_room_titles,
        "reservations": [_serialize_reservation(r) for r in reservations],
        "instance_names": instance_names or {},
        "kakao_exe_path": kakao_exe_path or "",
    }
    try:
        SETTINGS_PATH.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        log.error("Failed to save workspace settings", exc_info=True)
