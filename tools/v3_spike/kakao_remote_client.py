"""Kakao Remote Hub HTTP client (Controller GUI용)."""
from __future__ import annotations

import base64
import io
import os
from typing import Any

import requests
from PIL import Image


class RemoteKakaoClient:
    def __init__(
        self,
        hub_url: str,
        token: str,
        remote_pc_id: str,
        timeout: float = 15.0,
    ) -> None:
        self.hub_url = hub_url.rstrip("/")
        self.remote_pc_id = remote_pc_id
        self.timeout = timeout
        self._session = requests.Session()
        self._session.headers["Authorization"] = f"Bearer {token.strip()}"
        self._session.headers["Content-Type"] = "application/json"

    @classmethod
    def from_env(cls, remote_pc_id: str) -> "RemoteKakaoClient | None":
        hub = (os.getenv("KAKAO_REMOTE_HUB_URL") or "").strip()
        token = (os.getenv("KAKAO_REMOTE_TOKEN") or "").strip()
        if not hub or not token:
            return None
        return cls(hub, token, remote_pc_id)

    @classmethod
    def from_pc_record(cls, record: dict) -> "RemoteKakaoClient | None":
        hub = (record.get("hub_url") or os.getenv("KAKAO_REMOTE_HUB_URL") or "").strip()
        token = (os.getenv("KAKAO_REMOTE_TOKEN") or "").strip()
        pc_id = (record.get("remote_pc_id") or record.get("id") or "").strip()
        if not hub or not token or not pc_id:
            return None
        return cls(hub, token, pc_id)

    def list_pcs(self) -> list[dict]:
        resp = self._session.get(
            f"{self.hub_url}/api/controller/pcs",
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json().get("pcs", [])

    def get_pc_status(self) -> dict | None:
        for pc in self.list_pcs():
            if pc.get("pc_id") == self.remote_pc_id:
                return pc
        return None

    def get_frame_jpeg_b64(self) -> tuple[str | None, str | None]:
        resp = self._session.get(
            f"{self.hub_url}/api/controller/pcs/{self.remote_pc_id}/frame",
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("jpeg_b64"), data.get("updated_at")

    def get_frame_image(self) -> Image.Image | None:
        b64, _ = self.get_frame_jpeg_b64()
        if not b64:
            return None
        raw = base64.b64decode(b64)
        return Image.open(io.BytesIO(raw)).convert("RGB")

    def get_candidates(self) -> list[dict]:
        resp = self._session.get(
            f"{self.hub_url}/api/controller/pcs/{self.remote_pc_id}/candidates",
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json().get("candidates", [])

    def send_command(self, command_type: str, payload: dict | None = None) -> int:
        body = {
            "pc_id": self.remote_pc_id,
            "command_type": command_type,
            "payload": payload or {},
        }
        resp = self._session.post(
            f"{self.hub_url}/api/controller/commands",
            json=body,
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return int(resp.json().get("id", 0))

    def click(
        self,
        hwnd: int,
        image_x: int,
        image_y: int,
        image_w: int,
        image_h: int,
        *,
        double: bool = False,
    ) -> int:
        cmd = "double_click" if double else "click"
        return self.send_command(
            cmd,
            {
                "hwnd": hwnd,
                "image_x": image_x,
                "image_y": image_y,
                "image_w": image_w,
                "image_h": image_h,
            },
        )

    def wheel(
        self,
        hwnd: int,
        image_x: int,
        image_y: int,
        image_w: int,
        image_h: int,
        delta: int,
    ) -> int:
        return self.send_command(
            "wheel",
            {
                "hwnd": hwnd,
                "image_x": image_x,
                "image_y": image_y,
                "image_w": image_w,
                "image_h": image_h,
                "delta": delta,
            },
        )

    def type_text(self, hwnd: int, text: str) -> int:
        return self.send_command("type_text", {"hwnd": hwnd, "text": text})

    def press_enter(self, hwnd: int, *, allow_send: bool = False) -> int:
        return self.send_command(
            "press_enter",
            {"hwnd": hwnd, "allow_send": allow_send},
        )

    def send_text(self, hwnd: int, text: str, *, allow_send: bool = False) -> int:
        return self.send_command(
            "send_text",
            {"hwnd": hwnd, "text": text, "allow_send": allow_send},
        )

    def refresh_candidates(self) -> int:
        return self.send_command("list_candidates", {})

    def refresh_preview(self, hwnd: int | None = None) -> int:
        return self.send_command("capture_frame", {"hwnd": hwnd})

    def close_window(self, hwnd: int) -> int:
        return self.send_command("close_window", {"hwnd": hwnd})

    def bring_to_front(self, hwnd: int) -> int:
        return self.send_command("bring_to_front", {"hwnd": hwnd})
