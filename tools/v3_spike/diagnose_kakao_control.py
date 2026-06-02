"""Diagnose KakaoTalk window control: rects, UIA, click methods A–E.

Non-send only: room-list clicks and scroll tests. Never triggers Enter or send.

Usage:
    python tools/v3_spike/diagnose_kakao_control.py
    python tools/v3_spike/diagnose_kakao_control.py --hwnd 12345
    python tools/v3_spike/diagnose_kakao_control.py --matrix-only
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

SCRIPT_DIR = Path(__file__).resolve().parent
LOG_DIR = SCRIPT_DIR / "logs"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

try:
    import win32gui
except ImportError as exc:
    raise SystemExit("pywin32 required") from exc

import capture_kakao_window as capture
from pyqt_live_preview import (
    capture_image_hash,
    capture_mapping_rect,
    capture_region_hash,
    classify_kakao_controls,
    click_method_named_control,
    click_method_physical,
    click_method_postmessage_child,
    click_method_postmessage_toplevel,
    click_method_uia_at_point,
    enumerate_kakao_children,
    find_child_at_screen_point,
    find_named_control,
    get_role_screen_rect,
    image_to_screen_coords,
    is_unsafe_send_region,
    main_content_region,
    screen_point_in_role,
    wheel_method_physical,
    wheel_method_postmessage_child,
)

try:
    from inspect_kakao_uia import UiaNode, connect_main_window, rect_tuple, safe_automation_id
    from inspect_kakao_uia import safe_class_name as uia_class_name
    from inspect_kakao_uia import safe_control_type, safe_text, walk_tree
except ImportError:
    connect_main_window = None  # type: ignore[assignment,misc]
    walk_tree = None  # type: ignore[assignment,misc]


@dataclass
class MethodResult:
    method: str
    ok: bool
    detail: str
    hash_before: str
    hash_after: str
    title_before: str
    title_after: str
    error: str = ""


def _log_path() -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return LOG_DIR / f"diagnose_{stamp}.log"


def _write_log(path: Path, lines: list[str]) -> None:
    text = "\n".join(lines) + "\n"
    path.write_text(text, encoding="utf-8")
    print(f"Log written: {path}")


def _dpi_for_window(hwnd: int) -> int | str:
    try:
        return int(ctypes.windll.user32.GetDpiForWindow(hwnd))
    except Exception:
        return "n/a"


def _dump_child_tree(hwnd: int, max_depth: int = 4, max_nodes: int = 80) -> list[str]:
    lines: list[str] = []
    count = 0

    def walk(child: int, depth: int) -> None:
        nonlocal count
        if count >= max_nodes or depth > max_depth:
            return
        if not win32gui.IsWindowVisible(child):
            return
        cls = win32gui.GetClassName(child)
        title = win32gui.GetWindowText(child)[:40]
        rect = win32gui.GetWindowRect(child)
        lines.append(f"{'  ' * depth}- hwnd={child} class={cls!r} title={title!r} rect={rect}")
        count += 1
        try:
            win32gui.EnumChildWindows(child, lambda h, _: walk(h, depth + 1) or True, None)
        except Exception:
            pass

    try:
        win32gui.EnumChildWindows(hwnd, lambda h, _: walk(h, 1) or True, None)
    except Exception:
        pass
    return lines


def _screen_rect_to_image_point(
    hwnd: int, rect: tuple[int, int, int, int], fx: float, fy: float
) -> tuple[int, int]:
    map_left, map_top, map_right, map_bottom = capture_mapping_rect(hwnd)
    sx = rect[0] + (rect[2] - rect[0]) * fx
    sy = rect[1] + (rect[3] - rect[1]) * fy
    ix = int((sx - map_left))
    iy = int((sy - map_top))
    return ix, iy


def _safe_test_points(hwnd: int, image_size: tuple[int, int]) -> dict[str, tuple[int, int]]:
    """Non-send targets derived from real control rects when available."""
    w, h = image_size
    points: dict[str, tuple[int, int]] = {}

    list_rect = get_role_screen_rect(hwnd, "room_list")
    if list_rect:
        # a row near the top of the room list (avoid the very first pinned area)
        points["room_list"] = _screen_rect_to_image_point(hwnd, list_rect, 0.45, 0.12)

    msg_rect = get_role_screen_rect(hwnd, "message_list")
    if msg_rect:
        # center of the chat message area (scroll/select, never the input box)
        points["message_list"] = _screen_rect_to_image_point(hwnd, msg_rect, 0.5, 0.4)

    if not points:
        chat_rect = get_role_screen_rect(hwnd, "main_view")
        if chat_rect:
            points["chat_center"] = _screen_rect_to_image_point(hwnd, chat_rect, 0.55, 0.4)
        else:
            points["chat_center"] = (int(w * 0.55), max(80, int(h * 0.4)))
    return points


def _baseline_region_stable(hwnd: int, region: tuple[int, int, int, int] | None, samples: int = 3) -> bool:
    """True if the detection region is stable without any action (no self-noise)."""
    hashes = []
    for _ in range(samples):
        hashes.append(capture_region_hash(hwnd, region))
        time.sleep(0.25)
    hashes = [h for h in hashes if h]
    return len(set(hashes)) <= 1 and bool(hashes)


def _run_click_method(
    label: str,
    hwnd: int,
    image_x: int,
    image_y: int,
    image_size: tuple[int, int],
    fn: Callable[[], str],
    *,
    region: tuple[int, int, int, int] | None,
    trials: int = 2,
) -> MethodResult:
    if not capture.is_window_alive(hwnd):
        return MethodResult(label, False, "", "", "", "", "", error="hwnd_not_alive")
    screen_x, screen_y, _ = image_to_screen_coords(hwnd, image_x, image_y, image_size)
    if is_unsafe_send_region(hwnd, screen_x, screen_y):
        return MethodResult(label, False, "", "", "", "", "", error="unsafe_send_region")

    ok_count = 0
    last_detail = ""
    last_error = ""
    h_before = capture_region_hash(hwnd, region)
    title_before = win32gui.GetWindowText(hwnd)
    h_after = h_before
    title_after = title_before
    for _ in range(max(1, trials)):
        b = capture_region_hash(hwnd, region)
        t_b = win32gui.GetWindowText(hwnd)
        try:
            last_detail = fn()
        except Exception as exc:
            last_error = str(exc)
            continue
        time.sleep(0.35)
        h_after = capture_region_hash(hwnd, region)
        title_after = win32gui.GetWindowText(hwnd)
        changed = bool(b and h_after and b != h_after) or t_b != title_after
        if changed:
            ok_count += 1
        time.sleep(0.25)

    return MethodResult(
        method=f"{label} ({ok_count}/{trials})",
        ok=ok_count > 0,
        detail=last_detail,
        hash_before=h_before,
        hash_after=h_after,
        title_before=title_before,
        title_after=title_after,
        error=last_error,
    )


def run_click_matrix(hwnd: int, image_size: tuple[int, int], point_name: str, image_xy: tuple[int, int]) -> list[MethodResult]:
    ix, iy = image_xy
    screen_x, screen_y, _ = image_to_screen_coords(hwnd, ix, iy, image_size)
    region = main_content_region(hwnd)
    results: list[MethodResult] = []

    runners: list[tuple[str, Callable[[], str]]] = [
        ("A_postmessage_child", lambda: click_method_postmessage_child(hwnd, screen_x, screen_y)),
        ("B_postmessage_toplevel", lambda: click_method_postmessage_toplevel(hwnd, screen_x, screen_y)),
        ("C_uia_point", lambda: click_method_uia_at_point(screen_x, screen_y)),
        ("D_physical", lambda: click_method_physical(screen_x, screen_y, focus_hwnd=hwnd)),
    ]
    # A+ named-control routing (room_list / message_list) when applicable
    if screen_point_in_role(hwnd, "room_list", screen_x, screen_y):
        role = "room_list"
    elif screen_point_in_role(hwnd, "message_list", screen_x, screen_y):
        role = "message_list"
    else:
        role = "main_view"
    if find_named_control(hwnd, role):
        runners.insert(
            1,
            (
                f"Aplus_named[{role}]",
                lambda r=role: click_method_named_control(hwnd, r, screen_x, screen_y),
            ),
        )

    for label, fn in runners:
        if not capture.is_window_alive(hwnd):
            results.append(
                MethodResult(f"{point_name}:{label}", False, "", "", "", "", "", error="hwnd_lost_mid_matrix")
            )
            break
        results.append(
            _run_click_method(f"{point_name}:{label}", hwnd, ix, iy, image_size, fn, region=region)
        )
        time.sleep(0.4)

    # E: wheel variants — test on scrollable areas
    if point_name in ("room_list", "message_list", "chat_center"):
        cx, cy, _ = image_to_screen_coords(hwnd, ix, iy, image_size)
        for tag, wheel_fn in (
            ("E_wheel_child", lambda: wheel_method_postmessage_child(hwnd, cx, cy, 120)),
            ("E_wheel_physical", lambda: wheel_method_physical(cx, cy, -120)),
        ):
            h_before = capture_region_hash(hwnd, region)
            try:
                detail = wheel_fn()
                time.sleep(0.3)
                h_after = capture_region_hash(hwnd, region)
                results.append(
                    MethodResult(
                        method=f"{point_name}:{tag}",
                        ok=bool(h_before and h_after and h_before != h_after),
                        detail=detail,
                        hash_before=h_before,
                        hash_after=h_after,
                        title_before="",
                        title_after="",
                    )
                )
            except Exception as exc:
                results.append(
                    MethodResult(f"{point_name}:{tag}", False, "", h_before, "", "", "", error=str(exc))
                )
            time.sleep(0.4)
    return results


def dump_window_info(hwnd: int, lines: list[str]) -> None:
    title = win32gui.GetWindowText(hwnd)
    cls = win32gui.GetClassName(hwnd)
    gwr = capture._window_rect(hwnd)
    dwm = capture._safe_rect(hwnd)
    mapping = capture_mapping_rect(hwnd)
    lines.append(f"hwnd={hwnd} title={title!r} class={cls!r}")
    lines.append(f"  GetWindowRect={gwr}")
    lines.append(f"  DWM/safe_rect={dwm}")
    lines.append(f"  mapping_rect(PrintWindow)={mapping}")
    lines.append(f"  DPI={_dpi_for_window(hwnd)}")
    try:
        img = capture.capture_with_printwindow(hwnd)
        lines.append(f"  PrintWindow image size={img.size}")
        lines.append(f"  image md5={hashlib.md5(img.tobytes()).hexdigest()[:16]}")
    except Exception as exc:
        lines.append(f"  PrintWindow failed: {exc}")
    lines.append("  Child tree (sample):")
    lines.extend(_dump_child_tree(hwnd))


def dump_named_controls(hwnd: int, lines: list[str]) -> None:
    groups = classify_kakao_controls(hwnd)
    lines.append("Named controls (win32 child titles):")
    if not groups:
        lines.append("  (none classified)")
        return
    for role, items in groups.items():
        for info in items:
            lines.append(
                f"  [{role}] hwnd={info['hwnd']} class={info['class']!r} "
                f"title={str(info['title'])[:40]!r} rect={info['rect']}"
            )
    region = main_content_region(hwnd)
    lines.append(f"Detection region (main content): {region}")


def dump_uia_room_items(hwnd: int, lines: list[str], limit: int = 40) -> None:
    if connect_main_window is None or walk_tree is None:
        lines.append("UIA: inspect_kakao_uia not available")
        return
    try:
        root = connect_main_window(hwnd)
        nodes = walk_tree(root, max_depth=8, limit=500)
    except Exception as exc:
        lines.append(f"UIA walk failed: {exc}")
        return
    lines.append(f"UIA nodes total={len(nodes)} (text items with rect, limit {limit}):")
    shown = 0
    for info, _node in nodes:
        if not isinstance(info, UiaNode):
            continue
        if not info.text or info.rect == (0, 0, 0, 0):
            continue
        if shown >= limit:
            break
        text = info.text.replace("\n", " ")[:80]
        lines.append(
            f"  - {info.control_type} text={text!r} rect={info.rect} class={info.class_name!r}"
        )
        shown += 1


def pick_target_hwnd(explicit: int | None) -> tuple[int, str, tuple[int, int]]:
    groups = capture.list_candidates_by_instance()
    if not groups:
        raise RuntimeError("No KakaoTalk windows found. Open KakaoTalk and retry.")

    if explicit:
        for _pid, items in groups.items():
            for hwnd, kind, title, _rect, _cls in items:
                if hwnd == explicit and kind in ("chat", "main"):
                    img = capture.capture_with_printwindow(hwnd)
                    return hwnd, title, img.size
        if win32gui.IsWindow(explicit):
            img = capture.capture_with_printwindow(explicit)
            return explicit, win32gui.GetWindowText(explicit), img.size
        raise RuntimeError(f"hwnd {explicit} not found")

    for _pid, items in groups.items():
        for hwnd, kind, title, _rect, _cls in items:
            if kind == "chat":
                img = capture.capture_with_printwindow(hwnd)
                return hwnd, title, img.size
        for hwnd, kind, title, _rect, _cls in items:
            if kind == "main":
                img = capture.capture_with_printwindow(hwnd)
                return hwnd, title, img.size
    first = next(iter(groups.values()))[0]
    hwnd = first[0]
    img = capture.capture_with_printwindow(hwnd)
    return hwnd, first[2], img.size


def summarize_best(results: list[MethodResult]) -> str:
    ok_methods = [r.method for r in results if r.ok]
    if not ok_methods:
        return "No method produced visible change — try foreground Kakao or different window."
    return f"Working methods: {', '.join(ok_methods)}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnose KakaoTalk click/scroll control.")
    parser.add_argument("--hwnd", type=int, help="Target chat/main hwnd")
    parser.add_argument("--matrix-only", action="store_true", help="Skip window/UIA dump")
    parser.add_argument("--json", action="store_true", help="Print JSON summary to stdout")
    args = parser.parse_args()

    if sys.platform != "win32":
        print("Windows only.", file=sys.stderr)
        return 2

    capture.set_process_dpi_awareness()
    log_path = _log_path()
    lines: list[str] = [f"diagnose_kakao_control {datetime.now().isoformat()}"]

    try:
        hwnd, title, image_size = pick_target_hwnd(args.hwnd)
    except Exception as exc:
        print(exc, file=sys.stderr)
        return 1

    lines.append(f"TARGET hwnd={hwnd} title={title!r} image_size={image_size}")

    if not args.matrix_only:
        dump_window_info(hwnd, lines)
        dump_named_controls(hwnd, lines)
        dump_uia_room_items(hwnd, lines)

    region = main_content_region(hwnd)
    stable = _baseline_region_stable(hwnd, region)
    lines.append(f"Baseline region stable (no-action): {stable}")
    if not stable:
        lines.append(
            "  WARNING: detection region self-changes (live messages/animation). "
            "Hash-based OK/FAIL may have false positives."
        )

    points = _safe_test_points(hwnd, image_size)
    lines.append(f"Test points (image coords): {points}")
    all_results: list[MethodResult] = []
    for name, xy in points.items():
        lines.append(f"--- matrix {name} @ {xy} ---")
        results = run_click_matrix(hwnd, image_size, name, xy)
        all_results.extend(results)
        for r in results:
            status = "OK" if r.ok else "FAIL"
            lines.append(
                f"  [{status}] {r.method} hash {r.hash_before}->{r.hash_after} "
                f"title {r.title_before!r}->{r.title_after!r}"
            )
            if r.detail:
                lines.append(f"       detail: {r.detail}")
            if r.error:
                lines.append(f"       error: {r.error}")

    summary = summarize_best(all_results)
    lines.append(f"SUMMARY: {summary}")
    lines.append(
        "RECOMMENDATION: auto routes room-list clicks to ChatRoomListCtrl (PostMessage), "
        "others to child PostMessage, with physical-click fallback when the "
        "main-content region does not change."
    )

    _write_log(log_path, lines)

    if args.json:
        print(json.dumps([asdict(r) for r in all_results], ensure_ascii=False, indent=2))
    else:
        print(summary)
        for r in all_results:
            mark = "+" if r.ok else "-"
            print(f"  {mark} {r.method}")

    return 0 if any(r.ok for r in all_results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
