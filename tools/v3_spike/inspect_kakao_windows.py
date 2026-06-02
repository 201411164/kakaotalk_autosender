"""Read-only KakaoTalk window inspection for V3 spike.

This script does not send messages or change KakaoTalk state. It only lists
visible top-level windows and identifies likely KakaoTalk main/chat windows.

Usage:
    python tools/v3_spike/inspect_kakao_windows.py
    python tools/v3_spike/inspect_kakao_windows.py --json
    python tools/v3_spike/inspect_kakao_windows.py --children "카카오톡"
    python tools/v3_spike/inspect_kakao_windows.py --children-hwnd 123456
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from typing import Any

try:
    import win32gui
except ImportError as exc:  # pragma: no cover - Windows dependency
    raise SystemExit("pywin32 is required: pip install pywin32") from exc


KAKAO_MAIN_TITLES = {"카카오톡", "KakaoTalk"}
CHAT_EDIT_CLASS = "RICHEDIT50W"


@dataclass
class WindowInfo:
    hwnd: int
    title: str
    class_name: str
    rect: tuple[int, int, int, int]
    is_visible: bool
    is_kakao_main: bool
    is_likely_chat: bool


@dataclass
class ChildInfo:
    hwnd: int
    class_name: str
    text: str
    rect: tuple[int, int, int, int]


def _safe_window_text(hwnd: int) -> str:
    try:
        return win32gui.GetWindowText(hwnd)
    except Exception:
        return ""


def _safe_class_name(hwnd: int) -> str:
    try:
        return win32gui.GetClassName(hwnd)
    except Exception:
        return ""


def _safe_rect(hwnd: int) -> tuple[int, int, int, int]:
    try:
        return tuple(win32gui.GetWindowRect(hwnd))  # type: ignore[return-value]
    except Exception:
        return (0, 0, 0, 0)


def has_child_class(hwnd: int, class_name: str) -> bool:
    return win32gui.FindWindowEx(hwnd, None, class_name, None) != 0


def enum_top_windows() -> list[WindowInfo]:
    windows: list[WindowInfo] = []

    def callback(hwnd: int, _: Any) -> None:
        if not win32gui.IsWindowVisible(hwnd):
            return

        title = _safe_window_text(hwnd).strip()
        if not title:
            return

        is_main = title in KAKAO_MAIN_TITLES
        is_chat = (not is_main) and has_child_class(hwnd, CHAT_EDIT_CLASS)
        class_name = _safe_class_name(hwnd)

        windows.append(
            WindowInfo(
                hwnd=hwnd,
                title=title,
                class_name=class_name,
                rect=_safe_rect(hwnd),
                is_visible=True,
                is_kakao_main=is_main,
                is_likely_chat=is_chat,
            )
        )

    win32gui.EnumWindows(callback, None)
    windows.sort(key=lambda item: (not item.is_kakao_main, not item.is_likely_chat, item.title))
    return windows


def find_window_by_title(title: str) -> int | None:
    for window in enum_top_windows():
        if window.title == title:
            return window.hwnd
    return None


def find_window_by_hwnd(hwnd: int) -> int | None:
    if win32gui.IsWindow(hwnd):
        return hwnd
    return None


def enum_children(hwnd: int, limit: int = 200) -> list[ChildInfo]:
    children: list[ChildInfo] = []

    def callback(child_hwnd: int, _: Any) -> None:
        if len(children) >= limit:
            return
        children.append(
            ChildInfo(
                hwnd=child_hwnd,
                class_name=_safe_class_name(child_hwnd),
                text=_safe_window_text(child_hwnd),
                rect=_safe_rect(child_hwnd),
            )
        )

    win32gui.EnumChildWindows(hwnd, callback, None)
    return children


def print_text_report(
    windows: list[WindowInfo],
    child_title: str | None = None,
    child_hwnd: int | None = None,
) -> None:
    main_windows = [item for item in windows if item.is_kakao_main]
    chat_windows = [item for item in windows if item.is_likely_chat]

    print("KakaoTalk V3 Spike - Window Inspection")
    print("=" * 48)
    print(f"main_windows: {len(main_windows)}")
    print(f"likely_chat_windows: {len(chat_windows)}")
    print()

    if main_windows:
        print("[Main windows]")
        for item in main_windows:
            print(f"- hwnd={item.hwnd} title={item.title!r} class={item.class_name!r} rect={item.rect}")
        print()

    if chat_windows:
        print("[Likely chat windows: visible top-level windows containing RICHEDIT50W]")
        for item in chat_windows:
            print(f"- hwnd={item.hwnd} title={item.title!r} class={item.class_name!r} rect={item.rect}")
        print()

    if child_title or child_hwnd is not None:
        hwnd = find_window_by_hwnd(child_hwnd) if child_hwnd is not None else find_window_by_title(child_title or "")
        if not hwnd:
            print(f"[Children] window not found: {child_title or child_hwnd!r}")
            return

        print(f"[Children] hwnd={hwnd} title={_safe_window_text(hwnd)!r}")
        for child in enum_children(hwnd):
            text = child.text.replace("\r", " ").replace("\n", " ")
            if len(text) > 80:
                text = text[:77] + "..."
            print(f"- hwnd={child.hwnd} class={child.class_name!r} text={text!r} rect={child.rect}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect KakaoTalk windows without sending messages.")
    parser.add_argument("--json", action="store_true", help="Print JSON output.")
    parser.add_argument("--children", help="Print child controls for the exact window title.")
    parser.add_argument("--children-hwnd", type=int, help="Print child controls for the given window handle.")
    args = parser.parse_args()

    if sys.platform != "win32":
        print("This spike only runs on Windows.", file=sys.stderr)
        return 2

    windows = enum_top_windows()

    if args.json:
        payload: dict[str, Any] = {"windows": [asdict(item) for item in windows]}
        if args.children or args.children_hwnd is not None:
            hwnd = (
                find_window_by_hwnd(args.children_hwnd)
                if args.children_hwnd is not None
                else find_window_by_title(args.children)
            )
            payload["children"] = [asdict(item) for item in enum_children(hwnd)] if hwnd else []
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    print_text_report(windows, child_title=args.children, child_hwnd=args.children_hwnd)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
