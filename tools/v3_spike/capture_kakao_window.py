"""Read-only KakaoTalk window capture for V3 spike.

This script captures a KakaoTalk main/chat window. It does not click, type, or
send messages. By default it tries PrintWindow so overlapped windows can still
be captured. Use --bring-to-front only when you explicitly allow focus changes.

Usage:
    python tools/v3_spike/capture_kakao_window.py --list
    python tools/v3_spike/capture_kakao_window.py
    python tools/v3_spike/capture_kakao_window.py --title "카카오톡"
    python tools/v3_spike/capture_kakao_window.py --hwnd 123456
    python tools/v3_spike/capture_kakao_window.py --hwnd 123456 --method printwindow
    python tools/v3_spike/capture_kakao_window.py --hwnd 123456 --bring-to-front
    python tools/v3_spike/capture_kakao_window.py --title "고객상담-A" --output sample.png
"""
from __future__ import annotations

import argparse
import ctypes
import sys
import time
from datetime import datetime
from pathlib import Path
from ctypes import wintypes

try:
    import win32con
    import win32gui
    import win32ui
except ImportError as exc:  # pragma: no cover - Windows dependency
    raise SystemExit("pywin32 is required: pip install pywin32") from exc

try:
    from PIL import Image, ImageGrab
except ImportError as exc:  # pragma: no cover - dependency
    raise SystemExit("Pillow is required: pip install pillow") from exc


KAKAO_MAIN_TITLES = {"카카오톡", "KakaoTalk"}
KAKAO_LOGIN_CLASS = "EVA_Window"
CHAT_EDIT_CLASS = "RICHEDIT50W"
OUTPUT_DIR = Path(__file__).resolve().parent / "output"
DWMWA_EXTENDED_FRAME_BOUNDS = 9
GW_OWNER = 4
DIALOG_CLASS = "#32770"


def set_process_dpi_awareness() -> None:
    """Avoid DPI-virtualized window coordinates on scaled Windows displays."""
    try:
        # PROCESS_PER_MONITOR_DPI_AWARE = 2
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def _safe_title(hwnd: int) -> str:
    try:
        return win32gui.GetWindowText(hwnd).strip()
    except Exception:
        return ""


def _window_rect(hwnd: int) -> tuple[int, int, int, int]:
    try:
        return tuple(win32gui.GetWindowRect(hwnd))  # type: ignore[return-value]
    except Exception:
        return (0, 0, 0, 0)


def _dwm_rect(hwnd: int) -> tuple[int, int, int, int]:
    rect = wintypes.RECT()
    try:
        result = ctypes.windll.dwmapi.DwmGetWindowAttribute(
            wintypes.HWND(hwnd),
            wintypes.DWORD(DWMWA_EXTENDED_FRAME_BOUNDS),
            ctypes.byref(rect),
            ctypes.sizeof(rect),
        )
        if result == 0:
            return (rect.left, rect.top, rect.right, rect.bottom)
    except Exception:
        pass
    return (0, 0, 0, 0)


def _safe_rect(hwnd: int) -> tuple[int, int, int, int]:
    """Best display rect for logging and ImageGrab.

    DWM bounds match the visible extended frame better on Windows 10/11,
    especially with DPI scaling and shadows.
    """
    rect = _dwm_rect(hwnd)
    if is_reasonable_rect(rect):
        return rect
    return _window_rect(hwnd)


def _safe_class_name(hwnd: int) -> str:
    try:
        return win32gui.GetClassName(hwnd)
    except Exception:
        return ""


def _has_chat_editor(hwnd: int) -> bool:
    return win32gui.FindWindowEx(hwnd, None, CHAT_EDIT_CLASS, None) != 0


def _is_kakao_class(class_name: str) -> bool:
    return class_name.startswith("EVA_Window")


def _owner_is_kakao(hwnd: int) -> bool:
    """True if this window is owned by a KakaoTalk main/chat window."""
    try:
        owner = ctypes.windll.user32.GetWindow(hwnd, GW_OWNER)
        visited: set[int] = set()
        while owner and owner not in visited:
            visited.add(owner)
            if _is_kakao_class(_safe_class_name(owner)):
                return True
            if _has_chat_editor(owner):
                return True
            if _safe_title(owner) in KAKAO_MAIN_TITLES:
                return True
            owner = ctypes.windll.user32.GetWindow(owner, GW_OWNER)
        return False
    except Exception:
        return False


def _get_pid_for_hwnd(hwnd: int) -> int:
    """Return the process ID that owns the given window handle."""
    import ctypes
    import ctypes.wintypes as wt
    pid = wt.DWORD(0)
    ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def list_candidates() -> list[tuple[int, str, str, tuple[int, int, int, int], str]]:
    candidates: list[tuple[int, str, str, tuple[int, int, int, int], str]] = []

    def callback(hwnd: int, _: object) -> None:
        if not win32gui.IsWindowVisible(hwnd):
            return
        class_name = _safe_class_name(hwnd)
        title = _safe_title(hwnd)
        kind = ""
        if title in KAKAO_MAIN_TITLES:
            kind = "main"
        elif _has_chat_editor(hwnd):
            kind = "chat"
        elif _is_kakao_class(class_name):
            kind = "kakao_other"
        elif class_name == DIALOG_CLASS and _owner_is_kakao(hwnd):
            kind = "dialog"
            if not title:
                title = "파일 선택"
        elif not title:
            return
        if kind:
            candidates.append((hwnd, kind, title, _safe_rect(hwnd), class_name))

    win32gui.EnumWindows(callback, None)
    candidates.sort(
        key=lambda item: (
            item[1] != "dialog",
            item[1] != "main",
            item[1] != "kakao_other",
            item[2],
        )
    )
    return candidates


def list_candidates_by_instance() -> dict[int, list[tuple[int, str, str, tuple[int, int, int, int], str]]]:
    """list_candidates() grouped by KakaoTalk PID (instance).

    Returns {pid: [candidate, ...], ...}.  Each group has its own main/chat
    windows so the UI can display per-session room lists.
    """
    all_items = list_candidates()
    groups: dict[int, list[tuple[int, str, str, tuple[int, int, int, int], str]]] = {}
    for item in all_items:
        pid = _get_pid_for_hwnd(item[0])
        groups.setdefault(pid, []).append(item)
    return groups


def find_target(
    title: str | None,
    hwnd: int | None = None,
) -> tuple[int, str, str, tuple[int, int, int, int], str] | None:
    candidates = list_candidates()
    if hwnd is not None:
        for item in candidates:
            if item[0] == hwnd:
                return item
        if win32gui.IsWindow(hwnd) and win32gui.IsWindowVisible(hwnd):
            title_for_hwnd = _safe_title(hwnd)
            if title_for_hwnd:
                kind = "main" if title_for_hwnd in KAKAO_MAIN_TITLES else "unknown"
                return (hwnd, kind, title_for_hwnd, _safe_rect(hwnd), _safe_class_name(hwnd))
        return None
    if title:
        for item in candidates:
            if item[2] == title:
                return item
        return None
    return candidates[0] if candidates else None


def default_output_path(title: str) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    safe_title = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in title).strip("_")
    if not safe_title:
        safe_title = "kakao"
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return OUTPUT_DIR / f"{stamp}-{safe_title}.png"


def is_reasonable_rect(rect: tuple[int, int, int, int]) -> bool:
    left, top, right, bottom = rect
    return right > left and bottom > top and (right - left) > 10 and (bottom - top) > 10


def is_window_minimized(hwnd: int) -> bool:
    try:
        return bool(win32gui.IsIconic(hwnd))
    except Exception:
        return False


def is_window_valid(hwnd: int) -> bool:
    try:
        return (
            bool(win32gui.IsWindow(hwnd))
            and bool(win32gui.IsWindowVisible(hwnd))
            and not bool(win32gui.IsIconic(hwnd))
        )
    except Exception:
        return False


def is_window_alive(hwnd: int) -> bool:
    """Window exists (may be minimized). Use for ownership checks, not capture."""
    try:
        return bool(win32gui.IsWindow(hwnd)) and bool(win32gui.IsWindowVisible(hwnd))
    except Exception:
        return False


def close_window(hwnd: int) -> None:
    try:
        win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
    except Exception:
        pass


def bring_window_to_front(hwnd: int) -> None:
    """Restore and foreground a window without clicking or typing."""
    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    time.sleep(0.2)
    try:
        win32gui.SetForegroundWindow(hwnd)
    except Exception:
        # Windows foreground-lock rules may reject this; the capture can still proceed.
        pass
    time.sleep(0.3)


def capture_with_imagegrab(hwnd: int) -> Image.Image:
    if win32gui.IsIconic(hwnd):
        raise RuntimeError("Target window is minimized. Restore it before capture.")

    rect = _safe_rect(hwnd)
    if not is_reasonable_rect(rect):
        raise RuntimeError(f"Invalid window rect: {rect}")

    return ImageGrab.grab(bbox=rect)


def capture_with_printwindow(hwnd: int) -> Image.Image:
    if win32gui.IsIconic(hwnd):
        raise RuntimeError("Target window is minimized. Restore it before capture.")

    left, top, right, bottom = _window_rect(hwnd)
    if not is_reasonable_rect((left, top, right, bottom)):
        left, top, right, bottom = _safe_rect(hwnd)
    width = right - left
    height = bottom - top
    if width <= 10 or height <= 10:
        raise RuntimeError(f"Invalid window rect: {(left, top, right, bottom)}")

    hwnd_dc = win32gui.GetWindowDC(hwnd)
    src_dc = win32ui.CreateDCFromHandle(hwnd_dc)
    mem_dc = src_dc.CreateCompatibleDC()
    bitmap = win32ui.CreateBitmap()
    bitmap.CreateCompatibleBitmap(src_dc, width, height)
    mem_dc.SelectObject(bitmap)

    try:
        # 0x00000002 == PW_RENDERFULLCONTENT. It helps on newer Windows apps.
        result = ctypes.windll.user32.PrintWindow(hwnd, mem_dc.GetSafeHdc(), 0x00000002)
        if result != 1:
            result = ctypes.windll.user32.PrintWindow(hwnd, mem_dc.GetSafeHdc(), 0)
        if result != 1:
            raise RuntimeError("PrintWindow failed.")

        bmp_info = bitmap.GetInfo()
        bmp_bytes = bitmap.GetBitmapBits(True)
        image = Image.frombuffer(
            "RGB",
            (bmp_info["bmWidth"], bmp_info["bmHeight"]),
            bmp_bytes,
            "raw",
            "BGRX",
            0,
            1,
        )
        return image.copy()
    finally:
        win32gui.DeleteObject(bitmap.GetHandle())
        mem_dc.DeleteDC()
        src_dc.DeleteDC()
        win32gui.ReleaseDC(hwnd, hwnd_dc)


def capture_window(hwnd: int, output: Path, method: str, bring_to_front: bool = False) -> Path:
    if bring_to_front:
        bring_window_to_front(hwnd)

    if method == "printwindow":
        image = capture_with_printwindow(hwnd)
    elif method == "imagegrab":
        image = capture_with_imagegrab(hwnd)
    elif method == "auto":
        try:
            image = capture_with_printwindow(hwnd)
        except Exception as exc:
            print(f"PrintWindow failed, falling back to ImageGrab: {exc}", file=sys.stderr)
            image = capture_with_imagegrab(hwnd)
    else:
        raise ValueError(f"Unknown capture method: {method}")

    output.parent.mkdir(parents=True, exist_ok=True)
    image.save(output)
    return output


def print_candidates() -> None:
    candidates = list_candidates()
    if not candidates:
        print("No KakaoTalk main/chat windows found.")
        return

    print("KakaoTalk capture candidates")
    print("=" * 40)
    for hwnd, kind, title, rect, class_name in candidates:
        print(f"- hwnd={hwnd} kind={kind} title={title!r} class={class_name!r} rect={rect}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture a visible KakaoTalk window without interaction.")
    parser.add_argument("--list", action="store_true", help="List capture candidates and exit.")
    parser.add_argument("--title", help="Exact window title to capture. Defaults to KakaoTalk main, then chat.")
    parser.add_argument("--hwnd", type=int, help="Window handle to capture. Useful when title matching fails.")
    parser.add_argument(
        "--method",
        choices=("auto", "printwindow", "imagegrab"),
        default="auto",
        help="Capture method. Default: auto (PrintWindow then ImageGrab fallback).",
    )
    parser.add_argument(
        "--bring-to-front",
        action="store_true",
        help="Restore and foreground the target before capture. This changes focus but does not click/type.",
    )
    parser.add_argument("--output", help="Output PNG path. Defaults to tools/v3_spike/output/*.png.")
    args = parser.parse_args()

    if sys.platform != "win32":
        print("This spike only runs on Windows.", file=sys.stderr)
        return 2

    set_process_dpi_awareness()

    if args.list:
        print_candidates()
        return 0

    target = find_target(args.title, hwnd=args.hwnd)
    if not target:
        print("No target window found. Use --list to see candidates.", file=sys.stderr)
        return 1

    hwnd, kind, title, rect, _class_name = target
    output = Path(args.output) if args.output else default_output_path(title)

    print("Privacy warning: screenshots may contain chat contents.")
    print(f"Capturing kind={kind} title={title!r} rect={rect} method={args.method}")
    if args.bring_to_front:
        print("bring_to_front: enabled (window focus may change)")
    saved = capture_window(hwnd, output, method=args.method, bring_to_front=args.bring_to_front)
    print(f"Saved: {saved}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
