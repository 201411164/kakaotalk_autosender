"""Benchmark 1-second KakaoTalk window capture for V3 live preview.

This script does not click, type, or send messages. It repeatedly captures one
selected KakaoTalk window to validate the V3 "current screen live preview" idea.

Usage:
    python tools/v3_spike/stream_capture_benchmark.py --list
    python tools/v3_spike/stream_capture_benchmark.py --hwnd 123456
    python tools/v3_spike/stream_capture_benchmark.py --hwnd 123456 --duration 60 --interval 1
"""
from __future__ import annotations

import argparse
import io
import json
import statistics
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from PIL import Image

import capture_kakao_window as capture


OUTPUT_DIR = Path(__file__).resolve().parent / "output" / "stream_benchmark"


@dataclass
class FrameResult:
    index: int
    ok: bool
    elapsed_ms: float
    png_bytes: int = 0
    jpeg_bytes: int = 0
    webp_bytes: int = 0
    saved_path: str | None = None
    error: str | None = None


def encoded_size(image: Image.Image, fmt: str, quality: int) -> int:
    buf = io.BytesIO()
    kwargs = {}
    if fmt.upper() in {"JPEG", "WEBP"}:
        kwargs["quality"] = quality
    image.save(buf, format=fmt, **kwargs)
    return len(buf.getvalue())


def save_frame(image: Image.Image, output_dir: Path, index: int, fmt: str, quality: int) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    ext = "jpg" if fmt.lower() == "jpeg" else fmt.lower()
    path = output_dir / f"frame-{index:04d}.{ext}"
    kwargs = {}
    if fmt.upper() in {"JPEG", "WEBP"}:
        kwargs["quality"] = quality
    image.save(path, format=fmt, **kwargs)
    return path


def capture_once(hwnd: int, method: str) -> Image.Image:
    if method == "printwindow":
        return capture.capture_with_printwindow(hwnd)
    if method == "imagegrab":
        return capture.capture_with_imagegrab(hwnd)
    if method == "auto":
        try:
            return capture.capture_with_printwindow(hwnd)
        except Exception:
            return capture.capture_with_imagegrab(hwnd)
    raise ValueError(f"Unknown method: {method}")


def print_candidates() -> None:
    capture.print_candidates()


def benchmark(
    hwnd: int,
    duration: float,
    interval: float,
    method: str,
    save_format: str,
    quality: int,
    save_frames: bool,
) -> dict:
    capture.set_process_dpi_awareness()
    target_title = capture._safe_title(hwnd)  # Spike script; private helper is acceptable here.
    started_at = datetime.now()
    run_id = started_at.strftime("%Y%m%d-%H%M%S")
    output_dir = OUTPUT_DIR / run_id

    results: list[FrameResult] = []
    start_monotonic = time.perf_counter()
    next_tick = start_monotonic
    index = 0

    while True:
        now = time.perf_counter()
        if now - start_monotonic >= duration:
            break
        if now < next_tick:
            time.sleep(next_tick - now)

        frame_start = time.perf_counter()
        try:
            image = capture_once(hwnd, method)
            elapsed_ms = (time.perf_counter() - frame_start) * 1000
            png_bytes = encoded_size(image, "PNG", quality)
            jpeg_bytes = encoded_size(image, "JPEG", quality)
            webp_bytes = 0
            try:
                webp_bytes = encoded_size(image, "WEBP", quality)
            except Exception:
                pass

            saved_path = None
            if save_frames:
                saved_path = str(save_frame(image, output_dir, index, save_format, quality))

            results.append(
                FrameResult(
                    index=index,
                    ok=True,
                    elapsed_ms=elapsed_ms,
                    png_bytes=png_bytes,
                    jpeg_bytes=jpeg_bytes,
                    webp_bytes=webp_bytes,
                    saved_path=saved_path,
                )
            )
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - frame_start) * 1000
            results.append(FrameResult(index=index, ok=False, elapsed_ms=elapsed_ms, error=str(exc)))

        index += 1
        next_tick = start_monotonic + index * interval

    ok_results = [item for item in results if item.ok]
    fail_results = [item for item in results if not item.ok]
    elapsed_values = [item.elapsed_ms for item in ok_results]

    summary = {
        "started_at": started_at.isoformat(),
        "target": {"hwnd": hwnd, "title": target_title, "rect": capture._safe_rect(hwnd)},
        "settings": {
            "duration": duration,
            "interval": interval,
            "method": method,
            "save_format": save_format,
            "quality": quality,
            "save_frames": save_frames,
        },
        "frames": {
            "attempted": len(results),
            "success": len(ok_results),
            "failed": len(fail_results),
        },
        "latency_ms": {
            "avg": round(statistics.mean(elapsed_values), 2) if elapsed_values else None,
            "min": round(min(elapsed_values), 2) if elapsed_values else None,
            "max": round(max(elapsed_values), 2) if elapsed_values else None,
        },
        "bytes_avg": {
            "png": round(statistics.mean([item.png_bytes for item in ok_results]), 2) if ok_results else None,
            "jpeg": round(statistics.mean([item.jpeg_bytes for item in ok_results]), 2) if ok_results else None,
            "webp": round(statistics.mean([item.webp_bytes for item in ok_results if item.webp_bytes]), 2)
            if any(item.webp_bytes for item in ok_results)
            else None,
        },
        "output_dir": str(output_dir) if save_frames else None,
        "results": [asdict(item) for item in results],
    }

    if save_frames:
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark 1-second KakaoTalk capture stream.")
    parser.add_argument("--list", action="store_true", help="List current KakaoTalk capture candidates and exit.")
    parser.add_argument("--hwnd", type=int, help="Target window handle.")
    parser.add_argument("--title", help="Exact window title. Used only when --hwnd is omitted.")
    parser.add_argument("--duration", type=float, default=60.0, help="Benchmark duration in seconds.")
    parser.add_argument("--interval", type=float, default=1.0, help="Capture interval in seconds.")
    parser.add_argument("--method", choices=("auto", "printwindow", "imagegrab"), default="printwindow")
    parser.add_argument("--save-format", choices=("JPEG", "PNG", "WEBP"), default="JPEG")
    parser.add_argument("--quality", type=int, default=80, help="JPEG/WEBP quality.")
    parser.add_argument("--no-save-frames", action="store_true", help="Only print summary; do not save frame files.")
    args = parser.parse_args()

    if sys.platform != "win32":
        print("This spike only runs on Windows.", file=sys.stderr)
        return 2

    if args.list:
        print_candidates()
        return 0

    target = capture.find_target(args.title, hwnd=args.hwnd)
    if not target:
        print("No target window found. Use --list to see candidates.", file=sys.stderr)
        return 1

    hwnd, kind, title, rect, _class_name = target
    print("Privacy warning: captured frames may contain chat contents.")
    print(f"Benchmark target kind={kind} title={title!r} hwnd={hwnd} rect={rect}")
    print(f"duration={args.duration}s interval={args.interval}s method={args.method}")

    summary = benchmark(
        hwnd=hwnd,
        duration=args.duration,
        interval=args.interval,
        method=args.method,
        save_format=args.save_format,
        quality=args.quality,
        save_frames=not args.no_save_frames,
    )

    print(json.dumps({k: v for k, v in summary.items() if k != "results"}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
