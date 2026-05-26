"""Unlock KakaoTalk multi-instance by closing the Named Semaphore handle.

KakaoTalk prevents duplicate execution via Named Semaphore
`{97C4DDD9-D36D-48b5-BB47-2C8299BA7D1E}`.  Closing that handle allows
launching additional instances.

Reference: https://github.com/Blue-B/KakaoTalk-Multi-Instance

This module does NOT modify KakaoTalk binaries, intercept network traffic,
or bypass authentication.  It only closes a kernel handle so Windows
allows a second process to start normally.

Requires: pywin32, psutil (optional but recommended for PID enumeration).
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import logging
import os
import subprocess
import sys
from pathlib import Path

log = logging.getLogger("kakao_workspace")

SEMAPHORE_GUID = "{97C4DDD9-D36D-48b5-BB47-2C8299BA7D1E}"
KAKAO_EXE_NAME = "KakaoTalk.exe"

# --- NT internals for handle enumeration (no external tool needed) ----------

NTSTATUS = ctypes.c_long
STATUS_INFO_LENGTH_MISMATCH = 0xC0000004
SystemHandleInformation = 16
PROCESS_DUP_HANDLE = 0x0040
PROCESS_QUERY_INFORMATION = 0x0400
DUPLICATE_CLOSE_SOURCE = 0x0001
OBJ_SEMAPHORE = 5  # ObjectTypeIndex varies; we match by name instead

ntdll = ctypes.windll.ntdll
kernel32 = ctypes.windll.kernel32


class SYSTEM_HANDLE_TABLE_ENTRY(ctypes.Structure):
    _fields_ = [
        ("OwnerPid", wt.USHORT),
        ("CreatorBackTraceIndex", wt.USHORT),
        ("ObjectTypeIndex", ctypes.c_ubyte),
        ("HandleAttributes", ctypes.c_ubyte),
        ("HandleValue", wt.USHORT),
        ("Object", ctypes.c_void_p),
        ("GrantedAccess", wt.DWORD),
    ]


class OBJECT_NAME_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("Length", wt.USHORT),
        ("MaximumLength", wt.USHORT),
        ("Buffer", ctypes.c_wchar_p),
    ]


def _get_kakao_pids() -> list[int]:
    """Return PIDs of all running KakaoTalk.exe processes."""
    try:
        import psutil
        return [
            p.pid for p in psutil.process_iter(["name"])
            if (p.info.get("name") or "").lower() == KAKAO_EXE_NAME.lower()
        ]
    except ImportError:
        pass
    # fallback: tasklist
    try:
        out = subprocess.check_output(
            ["tasklist", "/FI", f"IMAGENAME eq {KAKAO_EXE_NAME}", "/FO", "CSV", "/NH"],
            text=True, creationflags=0x08000000,
        )
        pids: list[int] = []
        for line in out.strip().splitlines():
            parts = line.replace('"', "").split(",")
            if len(parts) >= 2 and parts[1].strip().isdigit():
                pids.append(int(parts[1].strip()))
        return pids
    except Exception:
        return []


def _close_semaphore_for_pid(pid: int) -> bool:
    """Close the KakaoTalk duplicate-check semaphore in the given process.

    Returns True if a matching handle was found and closed.
    Uses NtQuerySystemInformation to enumerate handles without external tools.
    """
    buf_size = 0x100000
    buf = ctypes.create_string_buffer(buf_size)
    ret_len = wt.DWORD(0)

    while True:
        status = ntdll.NtQuerySystemInformation(
            SystemHandleInformation, buf, buf_size, ctypes.byref(ret_len)
        )
        if status == STATUS_INFO_LENGTH_MISMATCH:
            buf_size *= 2
            buf = ctypes.create_string_buffer(buf_size)
            continue
        if status < 0:
            log.error("NtQuerySystemInformation failed: 0x%08X", status & 0xFFFFFFFF)
            return False
        break

    count = ctypes.c_ulong.from_buffer_copy(buf, 0).value
    entry_offset = ctypes.sizeof(ctypes.c_ulong)
    entry_size = ctypes.sizeof(SYSTEM_HANDLE_TABLE_ENTRY)

    proc_handle = kernel32.OpenProcess(
        PROCESS_DUP_HANDLE | PROCESS_QUERY_INFORMATION, False, pid
    )
    if not proc_handle:
        log.debug("Cannot open process %d", pid)
        return False

    closed = False
    try:
        for i in range(count):
            offset = entry_offset + i * entry_size
            if offset + entry_size > len(buf):
                break
            entry = SYSTEM_HANDLE_TABLE_ENTRY.from_buffer_copy(buf, offset)
            if entry.OwnerPid != pid:
                continue

            dup = wt.HANDLE()
            status = ntdll.NtDuplicateObject(
                proc_handle,
                entry.HandleValue,
                kernel32.GetCurrentProcess(),
                ctypes.byref(dup),
                0, 0, 0,
            )
            if status < 0:
                continue

            name_buf = ctypes.create_string_buffer(0x1000)
            ret = wt.DWORD(0)
            status = ntdll.NtQueryObject(
                dup, 1,  # ObjectNameInformation
                name_buf, len(name_buf), ctypes.byref(ret),
            )
            kernel32.CloseHandle(dup)

            if status < 0:
                continue

            info = OBJECT_NAME_INFORMATION.from_buffer_copy(name_buf)
            name = info.Buffer or ""
            if SEMAPHORE_GUID not in name:
                continue

            log.info(
                "Found semaphore handle %d in PID %d: %s",
                entry.HandleValue, pid, name,
            )
            # Close the handle in the target process
            dup2 = wt.HANDLE()
            ntdll.NtDuplicateObject(
                proc_handle,
                entry.HandleValue,
                kernel32.GetCurrentProcess(),
                ctypes.byref(dup2),
                0, 0, DUPLICATE_CLOSE_SOURCE,
            )
            kernel32.CloseHandle(dup2)
            closed = True
            log.info("Closed semaphore handle in PID %d", pid)
    finally:
        kernel32.CloseHandle(proc_handle)

    return closed


def unlock_multi_instance() -> tuple[bool, str]:
    """Close the KakaoTalk semaphore so a new instance can start.

    Returns (success: bool, message: str).
    """
    pids = _get_kakao_pids()
    if not pids:
        return False, "실행 중인 카카오톡 프로세스가 없습니다."

    any_closed = False
    for pid in pids:
        try:
            if _close_semaphore_for_pid(pid):
                any_closed = True
        except Exception as exc:
            log.debug("close_semaphore_for_pid(%d) error: %s", pid, exc)

    if any_closed:
        return True, f"세마포어 해제 완료 (PID: {pids}). 카카오톡을 새로 실행하세요."
    return False, (
        "세마포어를 찾지 못했습니다. "
        "관리자 권한으로 실행하거나, 이미 해제됐을 수 있습니다."
    )


def get_kakao_instance_count() -> int:
    return len(_get_kakao_pids())


def get_pid_for_hwnd(hwnd: int) -> int:
    """Return the process ID that owns the given window handle."""
    pid = wt.DWORD(0)
    ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def group_by_instance(
    candidates: list[tuple[int, str, str, tuple[int, int, int, int], str]],
) -> dict[int, list[tuple[int, str, str, tuple[int, int, int, int], str]]]:
    """Group window candidates by their owning KakaoTalk PID."""
    groups: dict[int, list] = {}
    for item in candidates:
        hwnd = item[0]
        pid = get_pid_for_hwnd(hwnd)
        groups.setdefault(pid, []).append(item)
    return groups


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    ok, msg = unlock_multi_instance()
    print(msg)
    print(f"현재 카카오톡 인스턴스 수: {get_kakao_instance_count()}")
