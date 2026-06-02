"""Background new-message monitor for a KakaoTalk chat window.

Foundation for AI auto-reply: polls one chat room via the clipboard reader,
detects NEW messages by hash diff, and emits them. It NEVER sends anything —
auto-reply must be an explicit, separately gated action.

NOTE: each poll briefly brings the chat window to the foreground (clipboard
Ctrl+A/Ctrl+C) and restores focus afterwards. Use a modest interval.
"""
from __future__ import annotations

import hashlib
import logging
import time

from PyQt6.QtCore import QThread, pyqtSignal

import chat_context_reader as reader

log = logging.getLogger("kakao_workspace")

MIN_INTERVAL_SEC = 5.0


def message_hash(msg: dict) -> str:
    key = f"{msg.get('sender', '')}|{msg.get('time', '')}|{msg.get('text', '')[:80]}"
    return hashlib.md5(key.encode("utf-8", "ignore")).hexdigest()


class ChatMonitor(QThread):
    """Polls a chat window and emits newly arrived messages.

    Signals:
        new_messages(list): list of new message dicts (sender/time/text/...)
        status(str): human-readable status updates
        failed(str): fatal error; the thread stops
    """

    new_messages = pyqtSignal(list)
    status = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(
        self,
        hwnd: int,
        title: str,
        *,
        interval_sec: float = 10.0,
        keywords: list[str] | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.hwnd = hwnd
        self.title = title
        self.interval_sec = max(MIN_INTERVAL_SEC, float(interval_sec))
        self.keywords = [k.strip().lower() for k in (keywords or []) if k.strip()]
        self._stop = False
        self._seen: set[str] = set()

    def stop(self) -> None:
        self._stop = True

    def _interruptible_sleep(self, seconds: float) -> None:
        steps = max(1, int(seconds * 10))
        for _ in range(steps):
            if self._stop:
                return
            time.sleep(0.1)

    def run(self) -> None:  # noqa: D401 - QThread entry point
        try:
            parsed = reader.read_chat_via_clipboard(self.hwnd)
        except Exception as exc:  # pragma: no cover - surfaced to UI
            log.error("monitor baseline failed: %s", exc)
            self.failed.emit(str(exc))
            return
        for msg in parsed.get("messages", []):
            self._seen.add(message_hash(msg))
        self.status.emit(
            f"모니터링 시작 · '{self.title}' 기존 {len(self._seen)}건 "
            f"(간격 {self.interval_sec:.0f}초)"
        )

        while not self._stop:
            self._interruptible_sleep(self.interval_sec)
            if self._stop:
                break
            try:
                parsed = reader.read_chat_via_clipboard(self.hwnd)
            except Exception as exc:
                log.debug("monitor poll error: %s", exc)
                self.status.emit(f"폴링 오류(계속 시도): {exc}")
                continue

            fresh: list[dict] = []
            for msg in parsed.get("messages", []):
                h = message_hash(msg)
                if h in self._seen:
                    continue
                self._seen.add(h)
                if self.keywords:
                    text = msg.get("text", "").lower()
                    if not any(k in text for k in self.keywords):
                        continue
                fresh.append(msg)
            if fresh:
                self.new_messages.emit(fresh)

        self.status.emit("모니터링 중지")
