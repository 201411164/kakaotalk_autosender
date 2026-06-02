#!/usr/bin/env python3
"""
EXE 전용: 1시간(또는 설정 간격)마다 서버(GitHub Releases)에서 새 버전을 확인하고,
있으면 다운로드·적용 후 재시작합니다. manual/case/safe_* 런처에서 호출합니다.
"""

import os
import sys
import time
from pathlib import Path


def _is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _app_dir() -> Path:
    if _is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _last_check_file() -> Path:
    d = _app_dir() / "updates"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return d / "last_update_check.txt"


def maybe_check_and_apply_auto_update(interval_sec: int = 3600) -> None:
    """
    EXE일 때만 동작. interval_sec마다 최대 1회 서버에 업데이트 확인 후,
    새 버전이 있으면 다운로드·적용하고 프로세스를 종료(배치가 새 EXE 재시작).
    적용 시 이 함수는 return하지 않고 프로세스가 종료됩니다.
    """
    if not _is_frozen():
        return
    disable = os.getenv("KAKAO_SENDER_DISABLE_AUTO_UPDATE") or os.getenv("ROBOTRAFFIC_DISABLE_AUTO_UPDATE") or ""
    if disable.strip().lower() in ("1", "true", "yes", "y", "on"):
        return
    try:
        interval = int(
            (os.getenv("KAKAO_SENDER_AUTO_UPDATE_INTERVAL_SEC")
             or os.getenv("ROBOTRAFFIC_AUTO_UPDATE_INTERVAL_SEC")
             or str(interval_sec)).strip() or interval_sec
        )
    except Exception:
        interval = interval_sec
    if interval <= 0:
        return

    now = time.time()
    last_file = _last_check_file()
    last_ts = 0.0
    try:
        if last_file.exists():
            t = (last_file.read_text(encoding="utf-8", errors="ignore")).strip()
            if t:
                last_ts = float(t)
    except Exception:
        pass
    if last_ts > 0 and (now - last_ts) < interval:
        return

    try:
        last_file.write_text(str(now), encoding="utf-8")
    except Exception:
        pass

    try:
        from updater import AutoUpdater
    except ImportError:
        try:
            from version import __version__
            from updater import AutoUpdater
        except ImportError:
            return

    try:
        current = getattr(sys.modules.get("version"), "__version__", "0.0.0")
    except Exception:
        current = "0.0.0"
    updater = AutoUpdater(current_version=current)
    try:
        info = updater.check_for_updates()
    except Exception:
        return
    if not info.get("update_available") or not info.get("download_url"):
        return

    try:
        update_file = updater.download_update(info["download_url"])
        updater.apply_update(update_file)
    except Exception:
        pass
    sys.exit(0)
