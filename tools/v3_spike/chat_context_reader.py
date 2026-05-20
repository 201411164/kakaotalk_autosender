"""Read chat context from KakaoTalk chat window (input + optional UIA)."""
from __future__ import annotations

import ctypes
import logging

try:
    import win32con
    import win32gui
except ImportError:
    win32con = None  # type: ignore
    win32gui = None  # type: ignore

log = logging.getLogger("kakao_workspace")

CHAT_EDIT_CLASS = "RICHEDIT50W"
EM_GETTEXTLENGTH = 0x00BA
EM_GETTEXT = 0x000D
WM_GETTEXTLENGTH = 0x000E
WM_GETTEXT = 0x000D


def read_input_box_text(parent_hwnd: int) -> str:
    """Text currently in the chat input RICHEDIT (draft, not history)."""
    if not win32gui or parent_hwnd <= 0:
        return ""
    try:
        edit_hwnd = win32gui.FindWindowEx(parent_hwnd, None, CHAT_EDIT_CLASS, None)
        if not edit_hwnd:
            return ""
        length = win32gui.SendMessage(edit_hwnd, EM_GETTEXTLENGTH, 0, 0)
        if length <= 0:
            length = win32gui.SendMessage(edit_hwnd, WM_GETTEXTLENGTH, 0, 0)
        if length <= 0:
            return ""
        buf = ctypes.create_unicode_buffer(length + 2)
        win32gui.SendMessage(edit_hwnd, EM_GETTEXT, length + 1, buf)
        text = buf.value.strip()
        if text:
            return text
        win32gui.SendMessage(edit_hwnd, WM_GETTEXT, length + 1, buf)
        return buf.value.strip()
    except Exception:
        log.debug("read_input_box_text failed", exc_info=True)
        return ""


def _collect_child_texts(hwnd: int) -> list[str]:
    if not win32gui:
        return []
    texts: list[str] = []

    def _enum(child: int, _param) -> None:
        try:
            if win32gui.GetClassName(child) == CHAT_EDIT_CLASS:
                return
            t = win32gui.GetWindowText(child).strip()
            if t and len(t) > 1 and t not in texts:
                texts.append(t)
        except Exception:
            pass

    try:
        win32gui.EnumChildWindows(hwnd, _enum, None)
    except Exception:
        pass
    return texts


def read_uia_text_nodes(hwnd: int, max_nodes: int = 40) -> str:
    try:
        from pywinauto import Application
    except ImportError:
        return ""

    try:
        app = Application(backend="uia").connect(handle=hwnd)
        window = app.window(handle=hwnd)
        chunks: list[str] = []
        for elem in window.descendants():
            try:
                t = (elem.window_text() or "").strip()
            except Exception:
                continue
            if not t or len(t) < 2:
                continue
            if t in chunks:
                continue
            chunks.append(t)
            if len(chunks) >= max_nodes:
                break
        if not chunks:
            return ""
        return "\n".join(chunks[-25:])
    except Exception:
        log.debug("read_uia_text_nodes failed", exc_info=True)
        return ""


def read_chat_context(hwnd: int) -> tuple[str, str]:
    """Returns (combined_text, hint_message)."""
    if hwnd <= 0:
        return "", "톡방 창이 선택되지 않았습니다."

    parts: list[str] = []
    input_text = read_input_box_text(hwnd)
    if input_text:
        parts.append(f"[입력창 초안]\n{input_text}")

    child_texts = _collect_child_texts(hwnd)
    body_lines = [t for t in child_texts if t != input_text and len(t) < 500]
    if body_lines:
        tail = body_lines[-15:]
        parts.append("[창 텍스트]\n" + "\n".join(tail))

    uia = read_uia_text_nodes(hwnd)
    if uia and uia not in "\n".join(parts):
        parts.append("[UIA]\n" + uia)

    if not parts:
        return (
            "",
            "채팅 본문을 읽지 못했습니다. 수동으로 붙여넣거나 AI는 캡처 이미지를 사용합니다.",
        )

    hint = "입력창·UIA에서 일부만 읽었습니다. 불완전하면 수동 보완하세요."
    return "\n\n".join(parts), hint
