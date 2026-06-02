"""Kakao Remote Agent — 원격 PC에서 Hub에 연결해 캡처·명령 실행.

실행 (원격 PC, 카카오톡 실행 중):
    set KAKAO_REMOTE_HUB_URL=http://hub-host:8765
    set KAKAO_REMOTE_TOKEN=your-secret
    set KAKAO_REMOTE_PC_ID=office-pc-1
    set KAKAO_REMOTE_ENABLE=1
    python tools/v3_spike/kakao_remote_agent.py

기본 allow_send=False — Enter/실제 전송 명령은 거부합니다.
"""
from __future__ import annotations

import argparse
import base64
import io
import logging
import os
import platform
import socket
import sys
import time
import traceback
from pathlib import Path

import requests

SPIKE_DIR = Path(__file__).resolve().parent
ROOT_DIR = SPIKE_DIR.parents[1]
if str(SPIKE_DIR) not in sys.path:
    sys.path.insert(0, str(SPIKE_DIR))

import capture_kakao_window as capture  # noqa: E402
from pyqt_live_preview import (  # noqa: E402
    crop_native_input_area,
    post_background_click,
    post_background_double_click,
    post_background_wheel,
    press_enter_in_chat,
    send_text_to_chat_background,
    type_text_to_chat_background,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("kakao_remote_agent")

POLL_INTERVAL = 2.0
HEARTBEAT_INTERVAL = 30.0
CAPTURE_INTERVAL = 0.5


def _local_ip() -> str | None:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return None


def _load_app_version() -> str:
    try:
        vp = ROOT_DIR / "version.py"
        if vp.is_file():
            ns: dict = {}
            exec(compile(vp.read_text(encoding="utf-8"), str(vp), "exec"), ns)
            return str(ns.get("__version__", "0.0.0"))
    except Exception:
        pass
    return "0.0.0"


class KakaoRemoteAgent:
    def __init__(
        self,
        hub_url: str,
        token: str,
        pc_id: str,
        *,
        allow_send: bool = False,
        hide_native_input_px: int = 120,
    ) -> None:
        self.hub_url = hub_url.rstrip("/")
        self.pc_id = pc_id
        self.allow_send = allow_send
        self.hide_native_input_px = hide_native_input_px
        self._session = requests.Session()
        self._session.headers["Authorization"] = f"Bearer {token.strip()}"
        self._session.headers["Content-Type"] = "application/json"
        self.selected_hwnd: int | None = None
        self.selected_title = ""
        self.main_hwnd: int | None = None
        self._last_hb = 0.0
        self._last_capture = 0.0

    def _post(self, path: str, body: dict, timeout: float = 15.0) -> requests.Response:
        return self._session.post(f"{self.hub_url}{path}", json=body, timeout=timeout)

    def _get(self, path: str, params: dict | None = None, timeout: float = 15.0) -> requests.Response:
        return self._session.get(f"{self.hub_url}{path}", params=params or {}, timeout=timeout)

    def _patch(self, path: str, body: dict, timeout: float = 15.0) -> requests.Response:
        return self._session.patch(f"{self.hub_url}{path}", json=body, timeout=timeout)

    def heartbeat(self) -> None:
        body = {
            "pc_id": self.pc_id,
            "pc_name": socket.gethostname(),
            "ip_address": _local_ip(),
            "app_version": _load_app_version(),
            "selected_hwnd": self.selected_hwnd,
            "selected_title": self.selected_title,
            "main_hwnd": self.main_hwnd,
        }
        self._post("/api/agent/heartbeat", body, timeout=8)

    def upload_candidates(self) -> None:
        try:
            candidates = capture.list_candidates()
            items = []
            for hwnd, kind, title, _rect, _cls in candidates:
                items.append(
                    {
                        "hwnd": hwnd,
                        "kind": kind,
                        "title": title,
                    }
                )
                if kind == "main" and self.main_hwnd is None:
                    self.main_hwnd = hwnd
                if kind == "chat" and self.selected_hwnd is None:
                    self.selected_hwnd = hwnd
                    self.selected_title = title
            self._post(
                "/api/agent/candidates",
                {"pc_id": self.pc_id, "candidates": items},
                timeout=12,
            )
        except Exception:
            log.error("upload_candidates failed:\n%s", traceback.format_exc())

    def upload_frame(self, hwnd: int | None = None) -> None:
        target = hwnd or self.selected_hwnd or self.main_hwnd
        if target is None:
            return
        try:
            if not capture.is_window_valid(target):
                return
            image = capture.capture_with_printwindow(target)
            cropped = crop_native_input_area(image, self.hide_native_input_px)
            buf = io.BytesIO()
            cropped.save(buf, format="JPEG", quality=82)
            b64 = base64.b64encode(buf.getvalue()).decode("ascii")
            self._post(
                "/api/agent/frame",
                {
                    "pc_id": self.pc_id,
                    "jpeg_b64": b64,
                    "meta": {
                        "hwnd": target,
                        "title": self.selected_title,
                        "w": cropped.width,
                        "h": cropped.height,
                    },
                },
                timeout=20,
            )
        except Exception:
            log.error("upload_frame failed:\n%s", traceback.format_exc())

    def _report(self, cmd_id: int, status: str, message: str) -> None:
        try:
            self._patch(
                f"/api/agent/commands/{cmd_id}",
                {"status": status, "result_message": message},
                timeout=10,
            )
        except Exception as exc:
            log.error("report cmd %s failed: %s", cmd_id, exc)

    def _execute(self, cmd: dict) -> None:
        cmd_id = int(cmd["id"])
        cmd_type = cmd["command_type"]
        payload = cmd.get("payload") or {}
        try:
            result = self._dispatch(cmd_type, payload)
            self._report(cmd_id, "done", result)
        except Exception as exc:
            log.error("command %s failed: %s", cmd_type, exc)
            self._report(cmd_id, "failed", str(exc))

    def _dispatch(self, cmd_type: str, payload: dict) -> str:
        if cmd_type == "list_candidates":
            self.upload_candidates()
            return "candidates refreshed"

        if cmd_type == "capture_frame":
            hwnd = payload.get("hwnd") or self.selected_hwnd or self.main_hwnd
            if hwnd:
                self.upload_frame(int(hwnd))
            return "frame captured"

        if cmd_type == "click":
            hwnd = int(payload["hwnd"])
            detail = post_background_click(
                hwnd,
                int(payload["image_x"]),
                int(payload["image_y"]),
                (int(payload["image_w"]), int(payload["image_h"])),
            )
            QTimer_refresh = 0.3
            time.sleep(QTimer_refresh)
            self.upload_frame(hwnd)
            return detail

        if cmd_type == "double_click":
            hwnd = int(payload["hwnd"])
            detail = post_background_double_click(
                hwnd,
                int(payload["image_x"]),
                int(payload["image_y"]),
                (int(payload["image_w"]), int(payload["image_h"])),
            )
            time.sleep(0.5)
            self.upload_candidates()
            self.upload_frame(hwnd)
            return detail

        if cmd_type == "wheel":
            hwnd = int(payload["hwnd"])
            detail = post_background_wheel(
                hwnd,
                int(payload["image_x"]),
                int(payload["image_y"]),
                (int(payload["image_w"]), int(payload["image_h"])),
                int(payload["delta"]),
            )
            time.sleep(0.3)
            self.upload_frame(hwnd)
            return detail

        if cmd_type == "type_text":
            hwnd = int(payload["hwnd"])
            type_text_to_chat_background(hwnd, str(payload.get("text", "")))
            time.sleep(0.3)
            self.upload_frame(hwnd)
            return "typed"

        if cmd_type == "press_enter":
            if not self.allow_send and not payload.get("allow_send"):
                return "press_enter blocked (allow_send=False)"
            hwnd = int(payload["hwnd"])
            press_enter_in_chat(hwnd)
            time.sleep(0.5)
            self.upload_frame(hwnd)
            return "enter sent"

        if cmd_type == "send_text":
            if not self.allow_send and not payload.get("allow_send"):
                return "send_text blocked (allow_send=False)"
            hwnd = int(payload["hwnd"])
            send_text_to_chat_background(hwnd, str(payload.get("text", "")))
            time.sleep(0.5)
            self.upload_frame(hwnd)
            return "sent"

        if cmd_type == "close_window":
            hwnd = int(payload["hwnd"])
            capture.close_window(hwnd)
            if self.selected_hwnd == hwnd:
                self.selected_hwnd = None
                self.selected_title = ""
            time.sleep(0.5)
            self.upload_candidates()
            return "closed"

        if cmd_type == "bring_to_front":
            hwnd = int(payload["hwnd"])
            capture.bring_window_to_front(hwnd)
            time.sleep(0.3)
            self.upload_frame(hwnd)
            return "brought to front"

        if cmd_type == "set_selected":
            self.selected_hwnd = int(payload["hwnd"])
            self.selected_title = str(payload.get("title", ""))
            self.upload_frame(self.selected_hwnd)
            return f"selected {self.selected_title}"

        return f"unknown command: {cmd_type}"

    def poll_commands(self) -> None:
        resp = self._get(
            "/api/agent/commands/pending",
            {"pc_id": self.pc_id},
            timeout=10,
        )
        resp.raise_for_status()
        for cmd in resp.json().get("commands", []):
            self._execute(cmd)

    def tick(self) -> None:
        now = time.time()
        if now - self._last_hb >= HEARTBEAT_INTERVAL:
            self.heartbeat()
            self._last_hb = now
        if now - self._last_capture >= CAPTURE_INTERVAL:
            if self.selected_hwnd or self.main_hwnd:
                self.upload_frame()
            self._last_capture = now
        self.poll_commands()

    def run_forever(self) -> None:
        capture.set_process_dpi_awareness()
        log.info(
            "Agent started pc_id=%s hub=%s allow_send=%s",
            self.pc_id,
            self.hub_url,
            self.allow_send,
        )
        self.upload_candidates()
        self.upload_frame()
        while True:
            try:
                self.tick()
            except Exception:
                log.error("tick error:\n%s", traceback.format_exc())
            time.sleep(POLL_INTERVAL)


def main() -> int:
    parser = argparse.ArgumentParser(description="Kakao Remote Agent")
    parser.add_argument("--hub-url", default=os.getenv("KAKAO_REMOTE_HUB_URL", ""))
    parser.add_argument("--token", default=os.getenv("KAKAO_REMOTE_TOKEN", ""))
    parser.add_argument("--pc-id", default=os.getenv("KAKAO_REMOTE_PC_ID", ""))
    parser.add_argument("--allow-send", action="store_true")
    parser.add_argument("--hide-native-input-px", type=int, default=120)
    args = parser.parse_args()

    if sys.platform != "win32":
        print("Windows only.", file=sys.stderr)
        return 2

    enable = (os.getenv("KAKAO_REMOTE_ENABLE") or "1").strip().lower()
    if enable in ("0", "false", "no", "off"):
        print("KAKAO_REMOTE_ENABLE is off — exit.")
        return 0

    hub = args.hub_url.strip()
    token = args.token.strip()
    pc_id = args.pc_id.strip() or socket.gethostname()
    if not hub or not token:
        print("KAKAO_REMOTE_HUB_URL and KAKAO_REMOTE_TOKEN required.", file=sys.stderr)
        return 2

    agent = KakaoRemoteAgent(
        hub,
        token,
        pc_id,
        allow_send=args.allow_send,
        hide_native_input_px=args.hide_native_input_px,
    )
    agent.run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
