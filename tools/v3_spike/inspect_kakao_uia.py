"""UI Automation inspection/click spike for KakaoTalk V3.

This script is for checking whether KakaoTalk exposes chat room list items,
last messages, unread badges, or buttons through Windows UI Automation.

Default mode is read-only. Use --click-text only when you intentionally want to
click a matching UIA element. This script never types or sends messages.

Usage:
    python tools/v3_spike/inspect_kakao_uia.py
    python tools/v3_spike/inspect_kakao_uia.py --text-only
    python tools/v3_spike/inspect_kakao_uia.py --contains "고동욱"
    python tools/v3_spike/inspect_kakao_uia.py --click-text "고동욱"
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass
from typing import Any

try:
    from pywinauto import Application
except ImportError as exc:  # pragma: no cover - optional spike dependency
    raise SystemExit("pywinauto is required: pip install pywinauto") from exc

try:
    import win32gui
except ImportError as exc:  # pragma: no cover - Windows dependency
    raise SystemExit("pywin32 is required: pip install pywin32") from exc


KAKAO_MAIN_TITLES = {"카카오톡", "KakaoTalk"}


@dataclass
class UiaNode:
    depth: int
    text: str
    control_type: str
    class_name: str
    automation_id: str
    rect: tuple[int, int, int, int]


def find_kakao_main_hwnd() -> int | None:
    for title in KAKAO_MAIN_TITLES:
        hwnd = win32gui.FindWindow(None, title)
        if hwnd:
            return hwnd
    return None


def rect_tuple(wrapper: Any) -> tuple[int, int, int, int]:
    try:
        rect = wrapper.rectangle()
        return (rect.left, rect.top, rect.right, rect.bottom)
    except Exception:
        return (0, 0, 0, 0)


def safe_text(wrapper: Any) -> str:
    try:
        text = wrapper.window_text()
        if text:
            return text.strip()
    except Exception:
        pass
    try:
        text = wrapper.element_info.name
        if text:
            return text.strip()
    except Exception:
        pass
    return ""


def safe_control_type(wrapper: Any) -> str:
    try:
        return str(wrapper.element_info.control_type or "")
    except Exception:
        return ""


def safe_class_name(wrapper: Any) -> str:
    try:
        return str(wrapper.element_info.class_name or "")
    except Exception:
        return ""


def safe_automation_id(wrapper: Any) -> str:
    try:
        return str(wrapper.element_info.automation_id or "")
    except Exception:
        return ""


def connect_main_window(hwnd: int):
    app = Application(backend="uia").connect(handle=hwnd)
    return app.window(handle=hwnd)


def walk_tree(root: Any, max_depth: int, limit: int) -> list[tuple[UiaNode, Any]]:
    results: list[tuple[UiaNode, Any]] = []

    def walk(node: Any, depth: int) -> None:
        if len(results) >= limit or depth > max_depth:
            return
        info = UiaNode(
            depth=depth,
            text=safe_text(node),
            control_type=safe_control_type(node),
            class_name=safe_class_name(node),
            automation_id=safe_automation_id(node),
            rect=rect_tuple(node),
        )
        results.append((info, node))
        try:
            children = node.children()
        except Exception:
            children = []
        for child in children:
            walk(child, depth + 1)

    walk(root, 0)
    return results


def filter_nodes(nodes: list[tuple[UiaNode, Any]], contains: str | None, text_only: bool) -> list[tuple[UiaNode, Any]]:
    filtered = nodes
    if text_only:
        filtered = [(info, node) for info, node in filtered if info.text]
    if contains:
        lowered = contains.lower()
        filtered = [(info, node) for info, node in filtered if lowered in info.text.lower()]
    return filtered


def print_nodes(nodes: list[tuple[UiaNode, Any]]) -> None:
    for info, _node in nodes:
        indent = "  " * info.depth
        text = info.text.replace("\r", " ").replace("\n", " ")
        if len(text) > 120:
            text = text[:117] + "..."
        print(
            f"{indent}- {info.control_type or '?'} "
            f"text={text!r} class={info.class_name!r} auto_id={info.automation_id!r} rect={info.rect}"
        )


def click_matching_node(nodes: list[tuple[UiaNode, Any]], query: str) -> bool:
    lowered = query.lower()
    for info, node in nodes:
        if lowered not in info.text.lower():
            continue
        print(f"Clicking match: text={info.text!r} type={info.control_type} rect={info.rect}")
        try:
            node.click_input()
        except Exception:
            node.click()
        time.sleep(0.5)
        return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect KakaoTalk UI Automation tree.")
    parser.add_argument("--hwnd", type=int, help="KakaoTalk main window handle. Defaults to title lookup.")
    parser.add_argument("--max-depth", type=int, default=8)
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--text-only", action="store_true", help="Only print nodes with visible text/name.")
    parser.add_argument("--contains", help="Only print nodes whose text contains this value.")
    parser.add_argument("--json", action="store_true", help="Print JSON instead of text.")
    parser.add_argument("--click-text", help="Click the first UIA node whose text contains this value.")
    args = parser.parse_args()

    if sys.platform != "win32":
        print("This spike only runs on Windows.", file=sys.stderr)
        return 2

    hwnd = args.hwnd or find_kakao_main_hwnd()
    if not hwnd:
        print("KakaoTalk main window not found.", file=sys.stderr)
        return 1

    root = connect_main_window(hwnd)
    nodes = walk_tree(root, max_depth=args.max_depth, limit=args.limit)

    if args.click_text:
        if click_matching_node(nodes, args.click_text):
            return 0
        print(f"No matching UIA node found: {args.click_text!r}", file=sys.stderr)
        return 1

    filtered = filter_nodes(nodes, contains=args.contains, text_only=args.text_only)
    if args.json:
        print(json.dumps([asdict(info) for info, _node in filtered], ensure_ascii=False, indent=2))
    else:
        print(f"KakaoTalk UIA nodes: hwnd={hwnd} total={len(nodes)} shown={len(filtered)}")
        print_nodes(filtered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
