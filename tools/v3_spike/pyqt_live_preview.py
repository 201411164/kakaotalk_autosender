"""PyQt live preview prototype for V3 Kakao workspace.

This prototype shows a selected KakaoTalk chat window as a 1-second live preview
and replaces the native captured input area with our own UI input area.

By default, the Send button is simulation-only. To allow real KakaoTalk sending,
run with --allow-send and confirm the dialog in the UI.

Usage:
    python tools/v3_spike/pyqt_live_preview.py --list
    python tools/v3_spike/pyqt_live_preview.py --hwnd 123456
    python tools/v3_spike/pyqt_live_preview.py --hwnd 123456 --allow-send
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import io
import logging
import os
import random
import sys
import time
from datetime import datetime

try:
    import pyperclip
    import win32api
    import win32con
    import win32gui
except ImportError as exc:  # pragma: no cover - Windows dependency
    raise SystemExit("pywin32 and pyperclip are required. Run: pip install -r requirements.txt") from exc

from PIL import Image
from PyQt6.QtCore import QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

import capture_kakao_window as capture

log = logging.getLogger("kakao.preview")

CHAT_EDIT_CLASS = "RICHEDIT50W"
CHAT_EDIT_CLASSES = ("RICHEDIT50W", "RichEdit20W", "RICHEDIT")
DEFAULT_CHAR_DELAY_MS = (0.03, 0.09)

APP_STYLESHEET = """
QWidget {
    background-color: #121212;
    color: #FFFFFF;
    font-family: 'Pretendard', 'Segoe UI', sans-serif;
    font-size: 14px;
}
QLabel {
    color: #FFFFFF;
}
QListWidget, QTextEdit, QSpinBox {
    background-color: #1A1A1A;
    color: #FFFFFF;
    border: 1px solid #2A2A2A;
    border-radius: 8px;
    padding: 8px;
}
QListWidget::item {
    padding: 10px;
    border-bottom: 1px solid #2A2A2A;
}
QListWidget::item:selected {
    background: #FEE500;
    color: #191919;
}
QPushButton {
    background-color: #FEE500;
    color: #191919;
    padding: 10px 14px;
    border-radius: 8px;
    font-weight: 800;
    border: none;
}
QPushButton:hover {
    background-color: #F4D300;
}
QCheckBox {
    color: #FFFFFF;
}
"""


def pil_to_pixmap(image: Image.Image) -> QPixmap:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    pixmap = QPixmap()
    pixmap.loadFromData(buffer.getvalue(), "PNG")
    return pixmap


def crop_native_input_area(image: Image.Image, bottom_px: int) -> Image.Image:
    if bottom_px <= 0:
        return image
    width, height = image.size
    crop_bottom = max(1, height - min(bottom_px, height - 1))
    return image.crop((0, 0, width, crop_bottom))


def _is_edit_like_class(class_name: str) -> bool:
    upper = class_name.upper()
    return upper in CHAT_EDIT_CLASSES or "RICHEDIT" in upper or class_name == "Edit"


def find_chat_edit_hwnd(
    parent_hwnd: int,
    *,
    retries: int = 3,
    retry_delay: float = 0.15,
) -> int:
    """Find chat/login input control; searches child tree recursively."""
    if not parent_hwnd or not capture.is_window_alive(parent_hwnd):
        return 0
    for attempt in range(max(1, retries)):
        try:
            direct = win32gui.FindWindowEx(parent_hwnd, None, CHAT_EDIT_CLASS, None)
        except Exception:
            direct = 0
        if direct:
            return direct
        found: list[int] = []

        def callback(child_hwnd: int, _: object) -> bool:
            if not win32gui.IsWindowVisible(child_hwnd):
                return True
            try:
                cls = win32gui.GetClassName(child_hwnd)
            except Exception:
                return True
            if _is_edit_like_class(cls):
                found.append(child_hwnd)
            return True

        try:
            win32gui.EnumChildWindows(parent_hwnd, callback, None)
        except Exception:
            pass
        if found:
            for hwnd in found:
                if "RICHEDIT" in win32gui.GetClassName(hwnd).upper():
                    return hwnd
            return found[0]
        if attempt < retries - 1:
            time.sleep(retry_delay)
    return 0


def _focus_parent_window(hwnd: int) -> None:
    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    time.sleep(0.15)
    try:
        capture.bring_window_to_front(hwnd)
    except Exception:
        try:
            win32gui.SetForegroundWindow(hwnd)
        except Exception:
            pass
    time.sleep(0.15)


def _prepare_chat_edit(hwnd: int) -> int:
    edit_hwnd = find_chat_edit_hwnd(hwnd)
    if edit_hwnd:
        return edit_hwnd
    _focus_parent_window(hwnd)
    return find_chat_edit_hwnd(hwnd, retries=2)


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_ushort),
        ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class _INPUT(ctypes.Structure):
    class _I(ctypes.Union):
        _fields_ = [("ki", _KEYBDINPUT)]
    _fields_ = [("type", ctypes.c_ulong), ("ii", _I)]


_INPUT_KEYBOARD = 1
_KEYEVENTF_KEYUP = 0x0002


def _send_input_key(vk: int) -> None:
    inputs = (_INPUT * 2)()
    inputs[0].type = _INPUT_KEYBOARD
    inputs[0].ii.ki.wVk = vk
    inputs[1].type = _INPUT_KEYBOARD
    inputs[1].ii.ki.wVk = vk
    inputs[1].ii.ki.dwFlags = _KEYEVENTF_KEYUP
    ctypes.windll.user32.SendInput(2, ctypes.byref(inputs), ctypes.sizeof(_INPUT))


def _send_input_text(text: str, char_delay: tuple[float, float] = DEFAULT_CHAR_DELAY_MS) -> None:
    for ch in text:
        vk = ord(ch.upper())
        _send_input_key(vk)
        time.sleep(random.uniform(*char_delay))


def type_text_human_like(
    hwnd: int,
    text: str,
    *,
    edit_hwnd: int | None = None,
    char_delay_ms: tuple[float, float] = DEFAULT_CHAR_DELAY_MS,
) -> None:
    """Type text with small per-character delays (login/lock screen fallback).

    Tries WM_CHAR first; if that fails, falls back to SendInput (works on
    PIN lock screens and other custom controls).
    """
    if not text:
        return
    target_edit = edit_hwnd or _prepare_chat_edit(hwnd)
    _focus_parent_window(hwnd)
    if target_edit:
        try:
            win32gui.SetFocus(target_edit)
        except Exception:
            pass
        time.sleep(0.1)
        for ch in text:
            win32api.PostMessage(target_edit, win32con.WM_CHAR, ord(ch), 0)
            time.sleep(random.uniform(*char_delay_ms))
        return

    _send_input_text(text, char_delay_ms)


def _post_enter(edit_hwnd: int | None, hwnd: int) -> None:
    if edit_hwnd:
        win32api.PostMessage(edit_hwnd, win32con.WM_KEYDOWN, win32con.VK_RETURN, 0)
        time.sleep(0.05)
        win32api.PostMessage(edit_hwnd, win32con.WM_KEYUP, win32con.VK_RETURN, 0)
        return
    _focus_parent_window(hwnd)
    _send_input_key(win32con.VK_RETURN)


def send_text_to_chat(hwnd: int, text: str) -> None:
    edit_hwnd = _prepare_chat_edit(hwnd)
    if edit_hwnd:
        _focus_parent_window(hwnd)
        pyperclip.copy(text)
        win32api.keybd_event(win32con.VK_CONTROL, 0, 0, 0)
        win32api.keybd_event(ord("V"), 0, 0, 0)
        win32api.keybd_event(ord("V"), 0, win32con.KEYEVENTF_KEYUP, 0)
        win32api.keybd_event(win32con.VK_CONTROL, 0, win32con.KEYEVENTF_KEYUP, 0)
        time.sleep(0.2)
        _post_enter(edit_hwnd, hwnd)
        return
    type_text_human_like(hwnd, text)
    time.sleep(0.1)
    _post_enter(None, hwnd)


def send_text_to_chat_background(hwnd: int, text: str) -> None:
    edit_hwnd = _prepare_chat_edit(hwnd)
    if edit_hwnd:
        try:
            win32gui.SendMessage(edit_hwnd, win32con.WM_SETTEXT, 0, text)
            time.sleep(0.1)
            _post_enter(edit_hwnd, hwnd)
            return
        except Exception:
            pass
    type_text_human_like(hwnd, text, edit_hwnd=edit_hwnd or None)
    time.sleep(0.1)
    _post_enter(edit_hwnd or None, hwnd)


def type_text_to_chat_background(hwnd: int, text: str) -> None:
    """Write text into the chat input WITHOUT pressing Enter."""
    edit_hwnd = _prepare_chat_edit(hwnd)
    if edit_hwnd:
        try:
            win32gui.SendMessage(edit_hwnd, win32con.WM_SETTEXT, 0, text)
            return
        except Exception:
            pass
    type_text_human_like(hwnd, text, edit_hwnd=edit_hwnd or None)


def press_enter_in_chat(hwnd: int) -> None:
    """Press Enter in the chat input to send the current text."""
    edit_hwnd = _prepare_chat_edit(hwnd)
    _post_enter(edit_hwnd or None, hwnd)


def _point_in_rect(point: tuple[int, int], rect: tuple[int, int, int, int]) -> bool:
    x, y = point
    left, top, right, bottom = rect
    return left <= x < right and top <= y < bottom


def capture_mapping_rect(hwnd: int) -> tuple[int, int, int, int]:
    """PrintWindow capture rect — preview image coords map to this box."""
    left, top, right, bottom = capture._window_rect(hwnd)
    if not capture.is_reasonable_rect((left, top, right, bottom)):
        left, top, right, bottom = capture._safe_rect(hwnd)
    return left, top, right, bottom


def image_to_screen_coords(
    hwnd: int,
    image_x: int,
    image_y: int,
    image_size: tuple[int, int],
) -> tuple[int, int, dict[str, object]]:
    """Map preview image coordinates to screen pixels."""
    window_left, window_top, window_right, window_bottom = capture_mapping_rect(hwnd)
    window_width = max(1, window_right - window_left)
    window_height = max(1, window_bottom - window_top)
    image_width, image_height = max(1, image_size[0]), max(1, image_size[1])
    screen_x = int(window_left + image_x * window_width / image_width)
    screen_y = int(window_top + image_y * window_height / image_height)
    meta: dict[str, object] = {
        "mapping_rect": (window_left, window_top, window_right, window_bottom),
        "gwr_rect": capture._window_rect(hwnd),
        "dwm_rect": capture._safe_rect(hwnd),
        "image_size": image_size,
    }
    return screen_x, screen_y, meta


def _chat_edit_screen_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    edit_hwnd = find_chat_edit_hwnd(hwnd, retries=1)
    if not edit_hwnd:
        return None
    try:
        return tuple(win32gui.GetWindowRect(edit_hwnd))  # type: ignore[return-value]
    except Exception:
        return None


def is_unsafe_send_region(hwnd: int, screen_x: int, screen_y: int, *, margin: int = 8) -> bool:
    """True if the point overlaps the chat input (avoid accidental send focus)."""
    rect = _chat_edit_screen_rect(hwnd)
    if rect is None:
        return False
    left, top, right, bottom = rect
    return _point_in_rect(
        (screen_x, screen_y),
        (left - margin, top - margin, right + margin, bottom + margin),
    )


def find_child_at_screen_point(hwnd: int, screen_point: tuple[int, int]) -> int:
    matches: list[tuple[int, int]] = []

    def callback(child_hwnd: int, _: object) -> None:
        if not win32gui.IsWindowVisible(child_hwnd):
            return
        rect = win32gui.GetWindowRect(child_hwnd)
        if _point_in_rect(screen_point, rect):
            left, top, right, bottom = rect
            area = max(1, right - left) * max(1, bottom - top)
            matches.append((area, child_hwnd))

    win32gui.EnumChildWindows(hwnd, callback, None)
    if not matches:
        return hwnd
    matches.sort(key=lambda item: item[0])
    return matches[0][1]


# ---------------------------------------------------------------------------
# Semantic control location via win32 child titles
#
# KakaoTalk does not expose chat rooms through UI Automation (the list is a
# custom DirectUI EVA_VH_ListControl). However, EnumChildWindows + GetWindowText
# reveal stable semantic titles like "ChatRoomListCtrl_0x...", letting us route
# clicks/scroll to the correct control instead of guessing by coordinate.
# ---------------------------------------------------------------------------

# title-substring -> logical role. Matched case-insensitively.
KAKAO_CONTROL_TITLE_HINTS: dict[str, str] = {
    "chatroomlistctrl": "room_list",
    "chatroomlistview": "room_list_view",
    "onlinemainview": "main_view",
    "chattingview": "chat_view",
    "chatroomview": "chat_view",
    "chatlist": "chat_messages",
    "richedit": "chat_input",
}

# class-name fragments for scroll / list controls
KAKAO_SCROLL_CLASS = "_EVA_CustomScrollCtrl"
KAKAO_LIST_CLASS = "EVA_VH_ListControl"


def enumerate_kakao_children(hwnd: int) -> list[dict[str, object]]:
    """Recursively enumerate visible child windows with class/title/rect."""
    out: list[dict[str, object]] = []
    if not hwnd or not capture.is_window_alive(hwnd):
        return out

    def visit(child: int) -> None:
        try:
            if not win32gui.IsWindowVisible(child):
                return
            cls = win32gui.GetClassName(child)
            title = win32gui.GetWindowText(child)
            rect = tuple(win32gui.GetWindowRect(child))
        except Exception:
            return
        out.append({"hwnd": child, "class": cls, "title": title, "rect": rect})

    def callback(child: int, _: object) -> bool:
        visit(child)
        return True

    try:
        win32gui.EnumChildWindows(hwnd, callback, None)
    except Exception:
        pass
    return out


def classify_kakao_controls(hwnd: int) -> dict[str, list[dict[str, object]]]:
    """Group child windows by logical role using title/class hints."""
    groups: dict[str, list[dict[str, object]]] = {}
    for info in enumerate_kakao_children(hwnd):
        title = str(info.get("title", "")).lower()
        cls = str(info.get("class", ""))
        role: str | None = None
        for hint, logical in KAKAO_CONTROL_TITLE_HINTS.items():
            if hint in title:
                role = logical
                break
        if role is None:
            if KAKAO_SCROLL_CLASS in cls:
                role = "scroll"
            elif KAKAO_LIST_CLASS in cls:
                # EVA list with no semantic title: chat-window message list
                role = "message_list"
        if role:
            groups.setdefault(role, []).append(info)
    return groups


def is_chat_window(hwnd: int) -> bool:
    """Heuristic: a single-room chat window (has message_list + RICHEDIT, no room_list)."""
    groups = classify_kakao_controls(hwnd)
    return bool(groups.get("message_list")) and not groups.get("room_list")


def find_named_control(hwnd: int, role: str) -> int | None:
    """Return the largest child HWND matching a logical role, or None."""
    candidates = classify_kakao_controls(hwnd).get(role, [])
    if not candidates:
        return None

    def area(info: dict[str, object]) -> int:
        left, top, right, bottom = info["rect"]  # type: ignore[misc]
        return max(1, right - left) * max(1, bottom - top)

    candidates.sort(key=area, reverse=True)
    return int(candidates[0]["hwnd"])  # type: ignore[index]


def get_role_screen_rect(hwnd: int, role: str) -> tuple[int, int, int, int] | None:
    ctrl = find_named_control(hwnd, role)
    if not ctrl:
        return None
    try:
        return tuple(win32gui.GetWindowRect(ctrl))  # type: ignore[return-value]
    except Exception:
        return None


def screen_point_in_role(hwnd: int, role: str, screen_x: int, screen_y: int) -> bool:
    rect = get_role_screen_rect(hwnd, role)
    if rect is None:
        return False
    return _point_in_rect((screen_x, screen_y), rect)


def capture_region_hash(
    hwnd: int,
    region: tuple[int, int, int, int] | None = None,
) -> str:
    """md5 of a screen-rect region of the PrintWindow capture (noise-limited).

    region is in SCREEN coordinates; converted to image-local crop using the
    capture mapping rect so we can ignore the clock / ad banner that change
    on their own.
    """
    try:
        img = capture.capture_with_printwindow(hwnd)
    except Exception:
        return ""
    if region is not None:
        map_left, map_top, map_right, map_bottom = capture_mapping_rect(hwnd)
        map_w = max(1, map_right - map_left)
        map_h = max(1, map_bottom - map_top)
        sx = max(0, int((region[0] - map_left) * img.width / map_w))
        sy = max(0, int((region[1] - map_top) * img.height / map_h))
        ex = min(img.width, int((region[2] - map_left) * img.width / map_w))
        ey = min(img.height, int((region[3] - map_top) * img.height / map_h))
        if ex - sx > 4 and ey - sy > 4:
            img = img.crop((sx, sy, ex, ey))
    return hashlib.md5(img.tobytes()).hexdigest()[:16]


def main_content_region(hwnd: int) -> tuple[int, int, int, int] | None:
    """Stable region for change detection: list/message area, excluding ad/clock."""
    for role in ("room_list", "message_list", "room_list_view", "main_view"):
        rect = get_role_screen_rect(hwnd, role)
        if rect:
            return rect
    return None


def make_lparam(x: int, y: int) -> int:
    return (y << 16) | (x & 0xFFFF)


def make_wparam_from_wheel_delta(delta: int) -> int:
    return (delta & 0xFFFF) << 16


def _target_class_needs_physical_click(target_hwnd: int, root_hwnd: int) -> bool:
    try:
        cls = win32gui.GetClassName(target_hwnd)
    except Exception:
        cls = ""
    if "EVA" in cls:
        return True
    return target_hwnd == root_hwnd


def _post_click_to_hwnd(target_hwnd: int, screen_x: int, screen_y: int, *, double: bool) -> str:
    client_x, client_y = win32gui.ScreenToClient(target_hwnd, (screen_x, screen_y))
    lparam = make_lparam(client_x, client_y)
    cls = win32gui.GetClassName(target_hwnd)
    win32api.PostMessage(target_hwnd, win32con.WM_MOUSEMOVE, 0, lparam)
    time.sleep(0.02)
    if double:
        win32api.PostMessage(target_hwnd, win32con.WM_LBUTTONDBLCLK, win32con.MK_LBUTTON, lparam)
    else:
        win32api.PostMessage(target_hwnd, win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, lparam)
        time.sleep(0.05)
        win32api.PostMessage(target_hwnd, win32con.WM_LBUTTONUP, 0, lparam)
    return f"class={cls!r} target={target_hwnd} client=({client_x},{client_y})"


def click_method_postmessage_child(
    hwnd: int, screen_x: int, screen_y: int, *, double: bool = False
) -> str:
    """A: PostMessage to smallest child HWND at screen point."""
    target_hwnd = find_child_at_screen_point(hwnd, (screen_x, screen_y))
    detail = _post_click_to_hwnd(target_hwnd, screen_x, screen_y, double=double)
    return f"A child {detail}"


def click_method_named_control(
    hwnd: int, role: str, screen_x: int, screen_y: int, *, double: bool = False
) -> str:
    """A+: PostMessage to a semantic control (e.g. ChatRoomListCtrl)."""
    ctrl = find_named_control(hwnd, role)
    if not ctrl:
        raise RuntimeError(f"named control {role!r} not found")
    detail = _post_click_to_hwnd(ctrl, screen_x, screen_y, double=double)
    return f"A+ role={role} {detail}"


def click_method_postmessage_toplevel(
    hwnd: int, screen_x: int, screen_y: int, *, double: bool = False
) -> str:
    """B: PostMessage on root window client coordinates."""
    client_x, client_y = win32gui.ScreenToClient(hwnd, (screen_x, screen_y))
    lparam = make_lparam(client_x, client_y)
    win32api.PostMessage(hwnd, win32con.WM_MOUSEMOVE, 0, lparam)
    time.sleep(0.02)
    if double:
        win32api.PostMessage(hwnd, win32con.WM_LBUTTONDBLCLK, win32con.MK_LBUTTON, lparam)
    else:
        win32api.PostMessage(hwnd, win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, lparam)
        time.sleep(0.05)
        win32api.PostMessage(hwnd, win32con.WM_LBUTTONUP, 0, lparam)
    return f"B toplevel client=({client_x},{client_y}) hwnd={hwnd}"


def click_method_uia_at_point(screen_x: int, screen_y: int, *, double: bool = False) -> str:
    """C: UIA element at screen point — invoke/click_input."""
    try:
        from pywinauto import Desktop
    except ImportError as exc:
        raise RuntimeError("pywinauto required for UIA click") from exc
    element = Desktop(backend="uia").from_point(screen_x, screen_y)
    cls = ""
    try:
        cls = str(element.element_info.class_name or "")
    except Exception:
        pass
    if double:
        try:
            element.double_click_input()
        except Exception:
            element.click_input(double=True)
    else:
        try:
            element.invoke()
        except Exception:
            try:
                element.select()
            except Exception:
                element.click_input()
    time.sleep(0.15)
    return f"C uia class={cls!r} at=({screen_x},{screen_y})"


def click_method_physical(
    screen_x: int,
    screen_y: int,
    *,
    double: bool = False,
    focus_hwnd: int | None = None,
) -> str:
    """D: Real cursor click (works on DirectUI / EVA_* surfaces)."""
    if focus_hwnd:
        try:
            capture.bring_window_to_front(focus_hwnd)
        except Exception:
            pass
    win32api.SetCursorPos((screen_x, screen_y))
    time.sleep(0.08)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0)
    time.sleep(0.05)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0)
    if double:
        time.sleep(0.08)
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0)
        time.sleep(0.05)
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0)
    return f"D physical screen=({screen_x},{screen_y}) double={double}"


def wheel_method_postmessage_child(
    hwnd: int,
    screen_x: int,
    screen_y: int,
    delta: int,
    *,
    toplevel: bool = False,
    target_override: int | None = None,
) -> str:
    """E (post): WM_MOUSEWHEEL to a list control / child / root."""
    if target_override:
        target_hwnd = target_override
        tag = "E-named"
    elif toplevel:
        target_hwnd = hwnd
        tag = "E-top"
    else:
        target_hwnd = find_child_at_screen_point(hwnd, (screen_x, screen_y))
        tag = "E-child"
    lparam = make_lparam(screen_x, screen_y)
    wparam = make_wparam_from_wheel_delta(delta)
    win32api.PostMessage(target_hwnd, win32con.WM_MOUSEWHEEL, wparam, lparam)
    return f"{tag} wheel delta={delta} target={target_hwnd} screen=({screen_x},{screen_y})"


def scroll_target_for_point(hwnd: int, screen_x: int, screen_y: int) -> int | None:
    """The scrollable list control (room/message list) under the point, if any."""
    for role in ("room_list", "message_list"):
        if screen_point_in_role(hwnd, role, screen_x, screen_y):
            return find_named_control(hwnd, role)
    return None


def wheel_method_physical(screen_x: int, screen_y: int, delta: int) -> str:
    """E (physical): cursor wheel at screen position."""
    win32api.SetCursorPos((screen_x, screen_y))
    time.sleep(0.05)
    win32api.mouse_event(win32con.MOUSEEVENTF_WHEEL, 0, 0, int(delta), 0)
    return f"E-physical wheel delta={delta} screen=({screen_x},{screen_y})"


def capture_image_hash(hwnd: int, image_size: tuple[int, int] | None = None) -> str:
    try:
        img = capture.capture_with_printwindow(hwnd)
        if image_size:
            img = img.crop((0, 0, min(img.width, image_size[0]), min(img.height, image_size[1])))
        return hashlib.md5(img.tobytes()).hexdigest()[:16]
    except Exception:
        return ""


def _click_strategy() -> str:
    return os.environ.get("KAKAO_CLICK_STRATEGY", "auto").strip().lower()


def post_background_click(hwnd: int, image_x: int, image_y: int, image_size: tuple[int, int]) -> str:
    screen_x, screen_y, meta = image_to_screen_coords(hwnd, image_x, image_y, image_size)
    if is_unsafe_send_region(hwnd, screen_x, screen_y):
        raise RuntimeError(
            "입력창 영역 클릭은 차단됩니다. 방 목록·채팅 영역을 클릭하세요."
        )
    target_hwnd = find_child_at_screen_point(hwnd, (screen_x, screen_y))
    target_cls = win32gui.GetClassName(target_hwnd)
    strategy = _click_strategy()
    log.info(
        "click img=(%s,%s) screen=(%s,%s) target=%s class=%r strategy=%s meta=%s",
        image_x, image_y, screen_x, screen_y, target_hwnd, target_cls, strategy, meta,
    )

    if strategy == "postmessage":
        return click_method_postmessage_child(hwnd, screen_x, screen_y)

    if strategy == "physical":
        return click_method_physical(screen_x, screen_y, focus_hwnd=hwnd)

    if strategy == "uia":
        return click_method_uia_at_point(screen_x, screen_y)

    if strategy == "toplevel":
        return click_method_postmessage_toplevel(hwnd, screen_x, screen_y)

    if strategy == "named":
        if screen_point_in_role(hwnd, "room_list", screen_x, screen_y):
            role = "room_list"
        elif screen_point_in_role(hwnd, "message_list", screen_x, screen_y):
            role = "message_list"
        else:
            role = "main_view"
        return click_method_named_control(hwnd, role, screen_x, screen_y)

    # auto: route by semantic control, verify on a noise-limited region,
    # physical-click fallback only if nothing changed.
    region = main_content_region(hwnd)
    before = capture_region_hash(hwnd, region)

    detail: str
    try:
        if screen_point_in_role(hwnd, "room_list", screen_x, screen_y):
            detail = click_method_named_control(hwnd, "room_list", screen_x, screen_y)
        elif screen_point_in_role(hwnd, "message_list", screen_x, screen_y):
            detail = click_method_named_control(hwnd, "message_list", screen_x, screen_y)
        else:
            detail = click_method_postmessage_child(hwnd, screen_x, screen_y)
    except Exception as exc:
        log.info("primary click path failed (%s); using physical", exc)
        return f"{click_method_physical(screen_x, screen_y, focus_hwnd=hwnd)} | primary-failed"

    time.sleep(0.3)
    after = capture_region_hash(hwnd, region)
    if before and after and before == after:
        log.info("postmessage click no region change; physical fallback")
        detail = f"{click_method_physical(screen_x, screen_y, focus_hwnd=hwnd)} | fallback"
    return detail


def post_background_double_click(
    hwnd: int, image_x: int, image_y: int, image_size: tuple[int, int]
) -> str:
    screen_x, screen_y, meta = image_to_screen_coords(hwnd, image_x, image_y, image_size)
    if is_unsafe_send_region(hwnd, screen_x, screen_y):
        raise RuntimeError("입력창 영역 더블클릭은 차단됩니다.")
    target_hwnd = find_child_at_screen_point(hwnd, (screen_x, screen_y))
    strategy = _click_strategy()
    log.info(
        "dblclick screen=(%s,%s) target=%s strategy=%s meta=%s",
        screen_x, screen_y, target_hwnd, strategy, meta,
    )
    if strategy in ("physical", "auto") and (
        strategy == "physical" or _target_class_needs_physical_click(target_hwnd, hwnd)
    ):
        return click_method_physical(screen_x, screen_y, double=True, focus_hwnd=hwnd)
    if strategy == "uia":
        return click_method_uia_at_point(screen_x, screen_y, double=True)
    if strategy == "toplevel":
        return click_method_postmessage_toplevel(hwnd, screen_x, screen_y, double=True)
    return click_method_postmessage_child(hwnd, screen_x, screen_y, double=True)


def post_background_wheel(hwnd: int, image_x: int, image_y: int, image_size: tuple[int, int], delta: int) -> str:
    screen_x, screen_y, meta = image_to_screen_coords(hwnd, image_x, image_y, image_size)
    if is_unsafe_send_region(hwnd, screen_x, screen_y):
        raise RuntimeError("입력창 영역 스크롤은 차단됩니다.")
    target_hwnd = find_child_at_screen_point(hwnd, (screen_x, screen_y))
    strategy = _click_strategy()
    log.info(
        "wheel delta=%s screen=(%s,%s) target=%s strategy=%s",
        delta, screen_x, screen_y, target_hwnd, strategy,
    )
    if strategy == "physical":
        return wheel_method_physical(screen_x, screen_y, delta)
    scroll_target = scroll_target_for_point(hwnd, screen_x, screen_y)
    if strategy == "postmessage":
        return wheel_method_postmessage_child(
            hwnd, screen_x, screen_y, delta, target_override=scroll_target
        )
    region = main_content_region(hwnd)
    before = capture_region_hash(hwnd, region)
    detail = wheel_method_postmessage_child(
        hwnd, screen_x, screen_y, delta, target_override=scroll_target
    )
    time.sleep(0.2)
    after = capture_region_hash(hwnd, region)
    if before and after and before == after:
        # PostMessage wheel to the list control is the reliable path; physical
        # is only a last resort and often fails on non-foreground chat windows.
        detail = f"{wheel_method_physical(screen_x, screen_y, delta)} | fallback"
    return detail


def post_background_drag(
    hwnd: int,
    start_x: int, start_y: int,
    end_x: int, end_y: int,
    image_size: tuple[int, int],
    steps: int = 10,
) -> str:
    sx, sy, _ = image_to_screen_coords(hwnd, start_x, start_y, image_size)
    ex, ey, meta = image_to_screen_coords(hwnd, end_x, end_y, image_size)
    if is_unsafe_send_region(hwnd, sx, sy) or is_unsafe_send_region(hwnd, ex, ey):
        raise RuntimeError("입력창 영역 드래그는 차단됩니다.")

    target_hwnd = find_child_at_screen_point(hwnd, (sx, sy))
    if _target_class_needs_physical_click(target_hwnd, hwnd) or _click_strategy() == "physical":
        win32api.SetCursorPos((sx, sy))
        time.sleep(0.05)
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0)
        for i in range(1, steps + 1):
            t = i / steps
            mx = int(sx + (ex - sx) * t)
            my = int(sy + (ey - sy) * t)
            win32api.SetCursorPos((mx, my))
            time.sleep(0.02)
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0)
        return f"drag physical ({sx},{sy})->({ex},{ey}) meta={meta}"

    cx, cy = win32gui.ScreenToClient(target_hwnd, (sx, sy))
    lp_start = make_lparam(cx, cy)
    win32api.PostMessage(target_hwnd, win32con.WM_MOUSEMOVE, 0, lp_start)
    time.sleep(0.02)
    win32api.PostMessage(target_hwnd, win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, lp_start)
    time.sleep(0.02)

    for i in range(1, steps + 1):
        t = i / steps
        mx = int(sx + (ex - sx) * t)
        my = int(sy + (ey - sy) * t)
        mcx, mcy = win32gui.ScreenToClient(target_hwnd, (mx, my))
        lp = make_lparam(mcx, mcy)
        win32api.PostMessage(target_hwnd, win32con.WM_MOUSEMOVE, win32con.MK_LBUTTON, lp)
        time.sleep(0.01)

    ecx, ecy = win32gui.ScreenToClient(target_hwnd, (ex, ey))
    lp_end = make_lparam(ecx, ecy)
    win32api.PostMessage(target_hwnd, win32con.WM_LBUTTONUP, 0, lp_end)
    return (
        f"drag postmessage screen=({sx},{sy})->({ex},{ey}) "
        f"target={target_hwnd}"
    )


class ClickablePreviewLabel(QLabel):
    imageClicked = pyqtSignal(int, int)
    imageDoubleClicked = pyqtSignal(int, int)
    imageWheeled = pyqtSignal(int, int, int)
    imageMoved = pyqtSignal(int, int)
    imageDragged = pyqtSignal(int, int, int, int)

    def __init__(self) -> None:
        super().__init__()
        self.source_size: tuple[int, int] | None = None
        self.setMouseTracking(True)
        self._drag_start: tuple[int, int] | None = None
        self._is_dragging = False
        self._drag_threshold = 6

    def set_source_size(self, size: tuple[int, int]) -> None:
        self.source_size = size

    def _image_coords_from_event(self, event) -> tuple[int, int] | None:
        if self.pixmap() is None or self.source_size is None:
            return None
        pixmap_size = self.pixmap().size()
        label_width = self.width()
        label_height = self.height()
        offset_x = (label_width - pixmap_size.width()) / 2
        offset_y = (label_height - pixmap_size.height()) / 2
        x = event.position().x() - offset_x
        y = event.position().y() - offset_y
        if x < 0 or y < 0 or x > pixmap_size.width() or y > pixmap_size.height():
            return None
        source_width, source_height = self.source_size
        image_x = int(x * source_width / max(1, pixmap_size.width()))
        image_y = int(y * source_height / max(1, pixmap_size.height()))
        return image_x, image_y

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        coords = self._image_coords_from_event(event)
        if coords is not None:
            self.imageMoved.emit(coords[0], coords[1])
        if self._drag_start is not None and event.buttons() & Qt.MouseButton.LeftButton:
            if coords is not None:
                dx = abs(coords[0] - self._drag_start[0])
                dy = abs(coords[1] - self._drag_start[1])
                if dx + dy >= self._drag_threshold:
                    self._is_dragging = True
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        coords = self._image_coords_from_event(event)
        if coords is None:
            return super().mousePressEvent(event)
        self._drag_start = coords
        self._is_dragging = False

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mouseReleaseEvent(event)
        coords = self._image_coords_from_event(event)
        if self._is_dragging and self._drag_start is not None and coords is not None:
            self.imageDragged.emit(
                self._drag_start[0], self._drag_start[1],
                coords[0], coords[1],
            )
        elif not self._is_dragging and self._drag_start is not None:
            if coords is not None:
                self.imageClicked.emit(coords[0], coords[1])
        self._drag_start = None
        self._is_dragging = False

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mouseDoubleClickEvent(event)
        coords = self._image_coords_from_event(event)
        if coords is None:
            return super().mouseDoubleClickEvent(event)
        self.imageDoubleClicked.emit(coords[0], coords[1])
        event.accept()

    def wheelEvent(self, event) -> None:  # noqa: N802
        coords = self._image_coords_from_event(event)
        if coords is None:
            return super().wheelEvent(event)
        delta = event.angleDelta().y()
        if delta:
            self.imageWheeled.emit(coords[0], coords[1], delta)
            event.accept()


class LivePreviewWindow(QMainWindow):
    def __init__(
        self,
        hwnd: int,
        title: str,
        interval_ms: int,
        hide_native_input_px: int,
        allow_send: bool,
    ) -> None:
        super().__init__()
        self.hwnd = hwnd
        self.title = title
        self.interval_ms = interval_ms
        self.allow_send = allow_send
        self.last_image: Image.Image | None = None

        self.setWindowTitle(f"카카오 매니저 미리보기 - {title}")
        self.resize(760, 980)
        self.setStyleSheet(APP_STYLESHEET)

        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        self.status_label = QLabel("자동 갱신 준비 중")
        self.status_label.setStyleSheet("color: #FEE500; font-weight: 700;")
        layout.addWidget(self.status_label)

        controls = QHBoxLayout()
        self.auto_refresh_checkbox = QCheckBox("1초 자동 갱신")
        self.auto_refresh_checkbox.setChecked(True)
        controls.addWidget(self.auto_refresh_checkbox)

        controls.addWidget(QLabel("하단 입력 영역 숨김(px):"))
        self.crop_spin = QSpinBox()
        self.crop_spin.setRange(0, 400)
        self.crop_spin.setValue(hide_native_input_px)
        controls.addWidget(self.crop_spin)

        self.refresh_button = QPushButton("지금 새로고침")
        self.refresh_button.clicked.connect(self.refresh_preview)
        controls.addWidget(self.refresh_button)
        self.forward_click_checkbox = QCheckBox("미리보기 클릭 전달")
        self.forward_click_checkbox.setChecked(True)
        controls.addWidget(self.forward_click_checkbox)
        self.background_input_checkbox = QCheckBox("백그라운드 입력")
        self.background_input_checkbox.setChecked(True)
        controls.addWidget(self.background_input_checkbox)
        controls.addStretch()
        layout.addLayout(controls)

        self.preview_label = ClickablePreviewLabel()
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setMinimumHeight(520)
        self.preview_label.setStyleSheet(
            "background: #111; border: 1px solid #2A2A2A; border-radius: 8px;"
        )
        self.preview_label.imageClicked.connect(self.handle_preview_click)
        self.preview_label.imageWheeled.connect(self.handle_preview_wheel)
        layout.addWidget(self.preview_label, stretch=1)

        input_label = QLabel("우리 UI 입력 영역")
        input_label.setStyleSheet("font-weight: 700;")
        layout.addWidget(input_label)

        self.message_edit = QTextEdit()
        self.message_edit.setPlaceholderText("메시지를 입력하세요")
        self.message_edit.setFixedHeight(84)
        layout.addWidget(self.message_edit)

        send_row = QHBoxLayout()
        self.send_mode_label = QLabel(
            "실제 전송 비활성화: 보내기 버튼은 UI 느낌만 확인합니다."
            if not allow_send
            else "실제 전송 활성화: 확인 후 카카오톡으로 전송합니다."
        )
        self.send_mode_label.setStyleSheet("color: #B5B5B5;")
        send_row.addWidget(self.send_mode_label, stretch=1)

        self.send_button = QPushButton("보내기")
        self.send_button.setStyleSheet(
            "background-color: #FEE500; color: #191919; padding: 10px 18px; "
            "border-radius: 8px; font-weight: 800;"
        )
        self.send_button.clicked.connect(self.handle_send)
        send_row.addWidget(self.send_button)
        layout.addLayout(send_row)

        self.setCentralWidget(root)

        self.timer = QTimer(self)
        self.timer.setInterval(interval_ms)
        self.timer.timeout.connect(self.refresh_if_enabled)
        self.timer.start()
        self.refresh_preview()

    def refresh_if_enabled(self) -> None:
        if self.auto_refresh_checkbox.isChecked():
            self.refresh_preview()

    def refresh_preview(self) -> None:
        try:
            image = capture.capture_with_printwindow(self.hwnd)
            self.last_image = image
            cropped = crop_native_input_area(image, self.crop_spin.value())
            pixmap = pil_to_pixmap(cropped)
            scaled = pixmap.scaled(
                self.preview_label.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.preview_label.set_source_size(cropped.size)
            self.preview_label.setPixmap(scaled)
            self.status_label.setText(
                f"{self.title} · 마지막 갱신 {datetime.now().strftime('%H:%M:%S')} · "
                f"{image.size[0]}x{image.size[1]}"
            )
        except Exception as exc:
            self.status_label.setText(f"갱신 실패: {exc}")

    def handle_preview_click(self, image_x: int, image_y: int) -> None:
        if not self.forward_click_checkbox.isChecked():
            self.status_label.setText("미리보기 클릭 전달이 꺼져 있습니다.")
            return
        try:
            cropped_height = self.last_image.height - self.crop_spin.value() if self.last_image else 0
            image_size = (self.last_image.width, max(1, cropped_height)) if self.last_image else (1, 1)
            detail = post_background_click(self.hwnd, image_x, image_y, image_size)
            self.status_label.setText(f"클릭 전달 완료 · {detail}")
            QTimer.singleShot(300, self.refresh_preview)
        except Exception as exc:
            QMessageBox.warning(self, "클릭 전달 실패", str(exc))

    def handle_preview_wheel(self, image_x: int, image_y: int, delta: int) -> None:
        if not self.forward_click_checkbox.isChecked():
            self.status_label.setText("미리보기 클릭/스크롤 전달이 꺼져 있습니다.")
            return
        try:
            cropped_height = self.last_image.height - self.crop_spin.value() if self.last_image else 0
            image_size = (self.last_image.width, max(1, cropped_height)) if self.last_image else (1, 1)
            detail = post_background_wheel(self.hwnd, image_x, image_y, image_size, delta)
            self.status_label.setText(f"스크롤 전달 완료 · {detail}")
            QTimer.singleShot(300, self.refresh_preview)
        except Exception as exc:
            QMessageBox.warning(self, "스크롤 전달 실패", str(exc))

    def handle_send(self) -> None:
        text = self.message_edit.toPlainText().strip()
        if not text:
            QMessageBox.warning(self, "입력 필요", "보낼 메시지를 입력하세요.")
            return

        if not self.allow_send:
            self.status_label.setText("모의 보내기 완료 · 1초 뒤 화면을 다시 갱신합니다.")
            QTimer.singleShot(1000, self.refresh_preview)
            return

        reply = QMessageBox.question(
            self,
            "실제 전송 확인",
            f"'{self.title}' 대화창에 실제 메시지를 보낼까요?\n\n{text}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            if self.background_input_checkbox.isChecked():
                send_text_to_chat_background(self.hwnd, text)
            else:
                send_text_to_chat(self.hwnd, text)
            self.message_edit.clear()
            self.status_label.setText("실제 전송 완료 · 1초 뒤 화면을 다시 갱신합니다.")
            QTimer.singleShot(1000, self.refresh_preview)
        except Exception as exc:
            QMessageBox.critical(self, "전송 실패", str(exc))

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        if self.last_image is not None:
            cropped = crop_native_input_area(self.last_image, self.crop_spin.value())
            pixmap = pil_to_pixmap(cropped)
            self.preview_label.setPixmap(
                pixmap.scaled(
                    self.preview_label.size(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )


class CandidatePickerWindow(QMainWindow):
    def __init__(self, interval_ms: int, hide_native_input_px: int, allow_send: bool) -> None:
        super().__init__()
        self.interval_ms = interval_ms
        self.hide_native_input_px = hide_native_input_px
        self.allow_send = allow_send
        self.preview_window: LivePreviewWindow | None = None

        self.setWindowTitle("카카오 매니저 - 대화창 선택")
        self.resize(620, 420)
        self.setStyleSheet(APP_STYLESHEET)

        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        info = QLabel("카카오톡 대화창을 열어둔 뒤 새로고침하고, 대화창 후보를 선택하세요.")
        info.setStyleSheet("color: #FEE500; font-weight: 700;")
        layout.addWidget(info)

        self.list_widget = QListWidget()
        self.list_widget.itemDoubleClicked.connect(self.open_selected)
        layout.addWidget(self.list_widget, stretch=1)

        buttons = QHBoxLayout()
        self.refresh_button = QPushButton("후보 새로고침")
        self.refresh_button.clicked.connect(self.refresh_candidates)
        buttons.addWidget(self.refresh_button)

        self.open_button = QPushButton("선택한 대화창 열기")
        self.open_button.clicked.connect(self.open_selected)
        buttons.addWidget(self.open_button)
        buttons.addStretch()
        layout.addLayout(buttons)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #B5B5B5;")
        layout.addWidget(self.status_label)

        self.setCentralWidget(root)
        self.refresh_candidates()

    def refresh_candidates(self) -> None:
        self.list_widget.clear()
        candidates = capture.list_candidates()
        chat_count = 0
        for hwnd, kind, title, rect, class_name in candidates:
            label = f"[{kind}] {title} · hwnd={hwnd} · rect={rect}"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, (hwnd, kind, title))
            if kind != "chat":
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            else:
                chat_count += 1
            self.list_widget.addItem(item)
        self.status_label.setText(f"대화창 후보 {chat_count}개 / 전체 후보 {len(candidates)}개")

    def open_selected(self) -> None:
        item = self.list_widget.currentItem()
        if item is None:
            QMessageBox.information(self, "선택 필요", "대화창 후보를 선택하세요.")
            return

        hwnd, kind, title = item.data(Qt.ItemDataRole.UserRole)
        if kind != "chat":
            QMessageBox.warning(self, "대화창 필요", "메인 창이 아니라 카카오톡 대화창을 선택하세요.")
            return

        self.preview_window = LivePreviewWindow(
            hwnd=hwnd,
            title=title,
            interval_ms=self.interval_ms,
            hide_native_input_px=self.hide_native_input_px,
            allow_send=self.allow_send,
        )
        self.preview_window.show()


def print_candidates() -> None:
    capture.print_candidates()


def main() -> int:
    parser = argparse.ArgumentParser(description="PyQt live preview prototype for V3 workspace.")
    parser.add_argument("--list", action="store_true", help="List current KakaoTalk capture candidates and exit.")
    parser.add_argument("--hwnd", type=int, help="Target KakaoTalk chat window handle.")
    parser.add_argument("--title", help="Exact target window title. Used only when --hwnd is omitted.")
    parser.add_argument("--interval-ms", type=int, default=1000, help="Auto refresh interval in milliseconds.")
    parser.add_argument(
        "--hide-native-input-px",
        type=int,
        default=120,
        help="Crop this many pixels from the bottom of the captured chat image.",
    )
    parser.add_argument(
        "--allow-send",
        action="store_true",
        help="Enable real KakaoTalk sending. Without this, Send is simulation-only.",
    )
    args = parser.parse_args()

    if sys.platform != "win32":
        print("This prototype only runs on Windows.", file=sys.stderr)
        return 2

    capture.set_process_dpi_awareness()

    if args.list:
        print_candidates()
        return 0

    app = QApplication(sys.argv)

    target = capture.find_target(args.title, hwnd=args.hwnd)
    if not target:
        picker = CandidatePickerWindow(
            interval_ms=args.interval_ms,
            hide_native_input_px=args.hide_native_input_px,
            allow_send=args.allow_send,
        )
        picker.show()
        return app.exec()

    hwnd, kind, title, _rect, _class_name = target
    if kind not in {"chat", "unknown"} and args.hwnd is None and args.title is None:
        picker = CandidatePickerWindow(
            interval_ms=args.interval_ms,
            hide_native_input_px=args.hide_native_input_px,
            allow_send=args.allow_send,
        )
        picker.show()
        return app.exec()

    if kind not in {"chat", "unknown"}:
        print("Select a chat window for this prototype. Use --list to see candidates.", file=sys.stderr)
        return 1

    window = LivePreviewWindow(
        hwnd=hwnd,
        title=title,
        interval_ms=args.interval_ms,
        hide_native_input_px=args.hide_native_input_px,
        allow_send=args.allow_send,
    )
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
