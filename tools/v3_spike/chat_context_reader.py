"""Read chat context from KakaoTalk chat window.

KakaoTalk renders chat history in a custom DirectUI list (EVA_VH_ListControl)
that exposes NO text via win32/UIA. Two ways to recover history:
  1. CLIPBOARD (preferred): focus the message list, Ctrl+A -> Ctrl+C, read and
     parse the clipboard. Accurate sender/time/text. (read_chat_via_clipboard)
  2. OCR (fallback): easyocr on the captured message region. (read_chat_via_ocr)

SAFETY: only cross-process-MARSHALED window messages may be sent to KakaoTalk.
WM_GETTEXT / WM_GETTEXTLENGTH / WM_SETTEXT / EM_SETSEL / WM_CLEAR / WM_CHAR are
marshaled or integer-only and are safe. Do NOT send EM_GETTEXTEX/EM_SETTEXTEX or
any message whose wParam/lParam are pointers into OUR address space — KakaoTalk
would dereference them in its own address space and crash. (This previously
crashed KakaoTalk.) Reading history uses clipboard + global keybd_event only,
never injects Enter, and never touches the input RICHEDIT.
"""
from __future__ import annotations

import ctypes
import logging
import re
import time

try:
    import win32api
    import win32clipboard
    import win32con
    import win32gui
    import win32process
except ImportError:
    win32api = None  # type: ignore
    win32clipboard = None  # type: ignore
    win32con = None  # type: ignore
    win32gui = None  # type: ignore
    win32process = None  # type: ignore

log = logging.getLogger("kakao_workspace")

CHAT_EDIT_CLASS = "RICHEDIT50W"
LIST_CONTROL_CLASS_PREFIX = "EVA_VH_ListControl"
INPUT_PLACEHOLDER = "메시지 입력"  # shown when the input box is empty
WM_GETTEXTLENGTH = 0x000E
WM_GETTEXT = 0x000D

# Virtual-key codes / flags for global key injection (safe, foreground-scoped)
_VK_CONTROL = 0x11
_VK_A = 0x41
_VK_C = 0x43
_VK_MENU = 0x12  # Alt
_KEYEVENTF_KEYUP = 0x0002
_SW_RESTORE = 9


# ---------------------------------------------------------------------------
# Clipboard text -> structured messages (ported from kakaotalk-mcp parser.py)
# ---------------------------------------------------------------------------

_MESSAGE_PATTERN = re.compile(r"^\[(.+?)\]\s*\[(오전|오후)\s*(\d{1,2}:\d{2})\]\s*(.*)")
_DATE_SEPARATOR_PATTERN = re.compile(
    r"^-*\s*(\d{4}년\s*\d{1,2}월\s*\d{1,2}일\s*\S+요일)\s*-*$"
)
_HEADER_PATTERN = re.compile(r"^\[(.+?)\]\s*\[대화상대\s*(\d+)")
_URL_PATTERN = re.compile(r"https?://[^\s<>\"')\]]+", re.IGNORECASE)


def parse_chat_text(raw_text: str) -> dict:
    """Parse raw KakaoTalk clipboard text into structured chat data."""
    result: dict = {
        "room_name": None,
        "member_count": None,
        "messages": [],
        "dates": [],
    }
    if not raw_text or not raw_text.strip():
        return result

    header_match = _HEADER_PATTERN.search(raw_text)
    if header_match:
        result["room_name"] = header_match.group(1)
        try:
            result["member_count"] = int(header_match.group(2))
        except ValueError:
            pass

    for date_match in _DATE_SEPARATOR_PATTERN.finditer(raw_text):
        result["dates"].append(date_match.group(1))

    current_message: dict | None = None
    for line in raw_text.split("\n"):
        stripped = line.strip()
        if not stripped or _DATE_SEPARATOR_PATTERN.match(stripped):
            continue
        if _HEADER_PATTERN.match(stripped):
            continue
        msg_match = _MESSAGE_PATTERN.match(stripped)
        if msg_match:
            if current_message is not None:
                _finalize_message(current_message, result["messages"])
            current_message = {
                "sender": msg_match.group(1),
                "time": f"{msg_match.group(2)} {msg_match.group(3)}",
                "text": msg_match.group(4),
            }
        elif current_message is not None and stripped:
            current_message["text"] += "\n" + stripped
    if current_message is not None:
        _finalize_message(current_message, result["messages"])
    return result


def _finalize_message(msg: dict, messages_list: list) -> None:
    text = msg["text"].strip()
    msg["is_photo"] = text == "사진"
    msg["is_video"] = text == "동영상"
    msg["is_file"] = text.startswith("파일:")
    msg["urls"] = _URL_PATTERN.findall(msg["text"])
    messages_list.append(msg)


def format_parsed_messages(parsed: dict, max_messages: int = 40) -> str:
    """Human-readable transcript from parsed clipboard data."""
    msgs = parsed.get("messages") or []
    if not msgs:
        return ""
    lines: list[str] = []
    header = parsed.get("room_name")
    if header:
        member = parsed.get("member_count")
        lines.append(f"# {header}" + (f" (대화상대 {member})" if member else ""))
    for msg in msgs[-max_messages:]:
        text = msg.get("text", "").replace("\n", " ").strip()
        lines.append(f"[{msg.get('time','')}] {msg.get('sender','')}: {text}")
    return "\n".join(lines)


def _find_edit(parent_hwnd: int) -> int:
    try:
        return win32gui.FindWindowEx(parent_hwnd, None, CHAT_EDIT_CLASS, None)
    except Exception:
        return 0


def _find_message_list_control(parent_hwnd: int) -> int:
    """Recursively find the chat message list control (EVA_VH_ListControl*)."""
    if not win32gui or parent_hwnd <= 0:
        return 0
    found: list[int] = []

    def _cb(child: int, _param) -> bool:
        try:
            cls = win32gui.GetClassName(child)
        except Exception:
            return True
        if cls.startswith(LIST_CONTROL_CLASS_PREFIX):
            found.append(child)
            return False
        return True

    try:
        win32gui.EnumChildWindows(parent_hwnd, _cb, None)
    except Exception:
        pass
    return found[0] if found else 0


def read_input_box_text(parent_hwnd: int) -> str:
    """Text currently in the chat input RICHEDIT (draft, not history).

    Uses only WM_GETTEXT*, which the OS marshals across processes (safe).
    """
    if not win32gui or parent_hwnd <= 0:
        return ""
    try:
        edit_hwnd = _find_edit(parent_hwnd)
        if not edit_hwnd:
            return ""
        length = win32gui.SendMessage(edit_hwnd, WM_GETTEXTLENGTH, 0, 0)
        if length <= 0:
            return ""
        buf = ctypes.create_unicode_buffer(length + 2)
        win32gui.SendMessage(edit_hwnd, WM_GETTEXT, length + 1, buf)
        text = buf.value.strip()
        if text == INPUT_PLACEHOLDER:
            return ""
        return text
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


_OCR_READER = None  # cached easyocr.Reader (heavy to construct)
_OCR_UNAVAILABLE = False


def _get_ocr_reader():
    """Lazily build a cached easyocr Reader, patching Pillow 10+ compat."""
    global _OCR_READER, _OCR_UNAVAILABLE
    if _OCR_READER is not None:
        return _OCR_READER
    if _OCR_UNAVAILABLE:
        return None
    try:
        from PIL import Image as _PILImage

        # easyocr <=1.7 references the removed PIL.Image.ANTIALIAS constant.
        if not hasattr(_PILImage, "ANTIALIAS"):
            _PILImage.ANTIALIAS = _PILImage.LANCZOS  # type: ignore[attr-defined]
        import easyocr  # type: ignore

        _OCR_READER = easyocr.Reader(["ko", "en"], gpu=False, verbose=False)
        return _OCR_READER
    except Exception:
        log.debug("easyocr unavailable", exc_info=True)
        _OCR_UNAVAILABLE = True
        return None


def _message_region_crop(hwnd: int):
    """Crop the chat message-list area from a PrintWindow capture, or None."""
    try:
        import capture_kakao_window as capture
        from pyqt_live_preview import capture_mapping_rect, get_role_screen_rect

        img = capture.capture_with_printwindow(hwnd)
        rect = get_role_screen_rect(hwnd, "message_list") or get_role_screen_rect(
            hwnd, "room_list"
        )
        if rect is None:
            return img
        map_left, map_top, _r, _b = capture_mapping_rect(hwnd)
        sx = max(0, int(rect[0] - map_left))
        sy = max(0, int(rect[1] - map_top))
        ex = min(img.width, int(rect[2] - map_left))
        ey = min(img.height, int(rect[3] - map_top))
        if ex - sx > 8 and ey - sy > 8:
            return img.crop((sx, sy, ex, ey))
        return img
    except Exception:
        log.debug("message region crop failed", exc_info=True)
        return None


def read_chat_via_ocr(hwnd: int, *, min_confidence: float = 0.3) -> str:
    """Read existing chat history from the captured image via OCR.

    KakaoTalk's message list exposes no text to win32/UIA, so OCR on the
    captured region is the only way to recover history. Returns "" if OCR is
    unavailable or finds nothing.
    """
    if hwnd <= 0:
        return ""
    reader = _get_ocr_reader()
    if reader is None:
        return ""
    crop = _message_region_crop(hwnd)
    if crop is None:
        return ""
    try:
        import numpy as np

        results = reader.readtext(np.array(crop))
    except Exception:
        log.debug("OCR readtext failed", exc_info=True)
        return ""
    lines = [t.strip() for (_box, t, conf) in results if conf >= min_confidence and t.strip()]
    return "\n".join(lines[-40:])


# ---------------------------------------------------------------------------
# Clipboard-based chat reading (preferred; accurate, no OCR)
# ---------------------------------------------------------------------------

def _backup_clipboard_text() -> str | None:
    """Return current clipboard unicode text, or None if not text/unavailable."""
    if not win32clipboard:
        return None
    for _ in range(5):
        try:
            win32clipboard.OpenClipboard()
            try:
                if win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_UNICODETEXT):
                    return win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
                return None
            finally:
                win32clipboard.CloseClipboard()
        except Exception:
            time.sleep(0.05)
    return None


def _restore_clipboard_text(text: str | None) -> None:
    if not win32clipboard:
        return
    for _ in range(5):
        try:
            win32clipboard.OpenClipboard()
            try:
                win32clipboard.EmptyClipboard()
                if text:
                    win32clipboard.SetClipboardText(text, win32clipboard.CF_UNICODETEXT)
            finally:
                win32clipboard.CloseClipboard()
            return
        except Exception:
            time.sleep(0.05)


def _read_clipboard_text(max_retries: int = 12, interval_sec: float = 0.05) -> str:
    if not win32clipboard:
        return ""
    for _ in range(max_retries):
        try:
            win32clipboard.OpenClipboard()
            try:
                if win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_UNICODETEXT):
                    data = win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
                    if data:
                        return data
            finally:
                win32clipboard.CloseClipboard()
        except Exception:
            pass
        time.sleep(interval_sec)
    return ""


_HWND_TOPMOST = -1
_HWND_NOTOPMOST = -2
_SWP_NOMOVE = 0x0002
_SWP_NOSIZE = 0x0001
_SWP_SHOWWINDOW = 0x0040


def _bring_to_foreground(hwnd: int) -> None:
    """Restore + foreground a window (Alt trick + topmost toggle). Safe.

    The topmost toggle is what reliably activates KakaoTalk chat windows so
    that Ctrl+A/Ctrl+C reach the message list (verified live).
    """
    u = ctypes.windll.user32
    try:
        u.ShowWindow(hwnd, _SW_RESTORE)
        u.keybd_event(_VK_MENU, 0, 0, 0)
        u.keybd_event(_VK_MENU, 0, _KEYEVENTF_KEYUP, 0)
        u.SetForegroundWindow(hwnd)
        flags = _SWP_NOMOVE | _SWP_NOSIZE | _SWP_SHOWWINDOW
        u.SetWindowPos(hwnd, _HWND_TOPMOST, 0, 0, 0, 0, flags)
        u.SetWindowPos(hwnd, _HWND_NOTOPMOST, 0, 0, 0, 0, flags)
    except Exception:
        log.debug("_bring_to_foreground failed", exc_info=True)


def _click_control_center(control_hwnd: int) -> None:
    """Left-click the center of a control to give it keyboard focus.

    Used only on the message LIST control (history area). It never targets the
    input box, so it cannot trigger a send. Selection highlight is harmless.
    """
    u = ctypes.windll.user32
    try:
        left, top, right, bottom = win32gui.GetWindowRect(control_hwnd)
        cx = (left + right) // 2
        cy = (top + bottom) // 2
        u.SetCursorPos(cx, cy)
        time.sleep(0.05)
        u.mouse_event(0x0002, 0, 0, 0, 0)  # LEFTDOWN
        u.mouse_event(0x0004, 0, 0, 0, 0)  # LEFTUP
        time.sleep(0.05)
    except Exception:
        log.debug("_click_control_center failed", exc_info=True)


def _send_ctrl_combo(vk_key: int) -> None:
    u = ctypes.windll.user32
    u.keybd_event(_VK_CONTROL, 0, 0, 0)
    time.sleep(0.02)
    u.keybd_event(vk_key, 0, 0, 0)
    time.sleep(0.02)
    u.keybd_event(vk_key, 0, _KEYEVENTF_KEYUP, 0)
    time.sleep(0.02)
    u.keybd_event(_VK_CONTROL, 0, _KEYEVENTF_KEYUP, 0)


_LAST_RAW_CLIPBOARD = ""  # debug aid: raw text from the last clipboard read


def read_chat_via_clipboard(hwnd: int) -> dict:
    """Read chat history via the message list: Ctrl+A -> Ctrl+C -> parse.

    Safe: never injects Enter, never touches the input box, backs up and
    restores the clipboard, and restores the previously focused window.
    Returns the parsed dict (see parse_chat_text); empty messages on failure.
    """
    empty = {"room_name": None, "member_count": None, "messages": [], "dates": []}
    if not win32gui or not win32clipboard or hwnd <= 0:
        return empty
    list_hwnd = _find_message_list_control(hwnd)
    if not list_hwnd:
        return empty

    u = ctypes.windll.user32
    prev_foreground = 0
    try:
        prev_foreground = u.GetForegroundWindow()
    except Exception:
        pass

    backup = _backup_clipboard_text()
    try:
        if win32clipboard:
            try:
                win32clipboard.OpenClipboard()
                win32clipboard.EmptyClipboard()
                win32clipboard.CloseClipboard()
            except Exception:
                pass

        _bring_to_foreground(hwnd)
        time.sleep(0.4)
        # Mouse click on the message-list center grabs keyboard focus for the
        # DirectUI list. Do NOT use AttachThreadInput+SetFocus here — it stole
        # foreground to another window and broke the copy (verified live).
        _click_control_center(list_hwnd)
        time.sleep(0.3)

        _send_ctrl_combo(_VK_A)  # select all messages
        time.sleep(0.35)
        _send_ctrl_combo(_VK_C)  # copy
        time.sleep(0.35)

        raw = _read_clipboard_text()
        global _LAST_RAW_CLIPBOARD
        _LAST_RAW_CLIPBOARD = raw
        return parse_chat_text(raw)
    except Exception:
        log.debug("read_chat_via_clipboard failed", exc_info=True)
        return empty
    finally:
        _restore_clipboard_text(backup)
        # Return focus to whatever was in front before (e.g. our app window).
        if prev_foreground and prev_foreground != hwnd:
            try:
                u.keybd_event(_VK_MENU, 0, 0, 0)
                u.keybd_event(_VK_MENU, 0, _KEYEVENTF_KEYUP, 0)
                u.SetForegroundWindow(prev_foreground)
            except Exception:
                pass


def read_chat_context(
    hwnd: int, *, use_clipboard: bool = True, use_ocr: bool = True
) -> tuple[str, str]:
    """Returns (combined_text, hint_message).

    Strategy: clipboard (accurate, structured) first; OCR fallback; then the
    input-box draft / window texts.
    """
    if hwnd <= 0:
        return "", "톡방 창이 선택되지 않았습니다."

    parts: list[str] = []
    input_text = read_input_box_text(hwnd)
    if input_text:
        parts.append(f"[입력창 초안]\n{input_text}")

    clip_body = ""
    if use_clipboard:
        try:
            parsed = read_chat_via_clipboard(hwnd)
            clip_body = format_parsed_messages(parsed)
        except Exception:
            log.debug("clipboard read failed", exc_info=True)
    if clip_body:
        parts.append("[대화 내용]\n" + clip_body)

    # OCR only if clipboard yielded nothing (it is slower / less accurate).
    ocr_text = ""
    if not clip_body and use_ocr:
        ocr_text = read_chat_via_ocr(hwnd)
        if ocr_text:
            parts.append("[OCR 본문]\n" + ocr_text)

    if not clip_body and not ocr_text:
        child_texts = _collect_child_texts(hwnd)
        body_lines = [t for t in child_texts if t != input_text and len(t) < 500]
        if body_lines:
            parts.append("[창 텍스트]\n" + "\n".join(body_lines[-15:]))

    if not parts:
        return (
            "",
            "채팅 본문을 읽지 못했습니다. 수동으로 붙여넣거나 AI는 캡처 이미지를 사용합니다.",
        )

    if clip_body:
        hint = "클립보드로 대화 내용을 읽었습니다(발신자·시간 포함)."
    elif ocr_text:
        hint = "클립보드 실패 → OCR로 읽었습니다. 오탈자가 있을 수 있어 검토하세요."
    else:
        hint = "입력창·창 텍스트만 읽었습니다. 불완전하면 수동 보완하세요."
    return "\n\n".join(parts), hint
