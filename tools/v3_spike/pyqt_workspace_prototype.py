"""V3 Kakao workspace GUI prototype — 카카오 매니저 작업대.

- 좌측: PC 목록 + 열린 톡방 (유일한 방 선택기)
- 중앙: 작업(미리보기·입력) / 톡방 관리 / 발송·고급
- 하단: 선택 방 대상 표시 + 메시지·예약·보내기

기본은 모의 보내기. --allow-send 로 실제 전송(확인 팝업).
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import logging
import os
import socket
import sys
import threading
import traceback
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QFontDatabase, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QFileDialog,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None

import capture_kakao_window as capture
from chat_context_reader import read_chat_context
from kakao_multi_instance import (
    find_kakao_exe,
    get_kakao_instance_count,
    get_pid_for_hwnd,
    launch_new_instance,
    terminate_kakao_instance,
)
from kakao_remote_client import RemoteKakaoClient
from v3_workspace_store import load_workspace_settings, save_workspace_settings
from pyqt_live_preview import (
    ClickablePreviewLabel,
    crop_native_input_area,
    pil_to_pixmap,
    post_background_click,
    post_background_double_click,
    post_background_drag,
    post_background_wheel,
    press_enter_in_chat,
    send_text_to_chat,
    send_text_to_chat_background,
    type_text_to_chat_background,
)

ROOT_DIR = Path(__file__).resolve().parents[2]


def _load_app_version() -> str:
    candidates = [
        ROOT_DIR / "version.py",
        Path(sys.executable).resolve().parent / "version.py",
        Path(sys.executable).resolve().parent / "_internal" / "version.py",
    ]
    for vp in candidates:
        if vp.is_file():
            ns: dict = {}
            try:
                exec(compile(vp.read_text(encoding="utf-8"), str(vp), "exec"), ns)
                ver = str(ns.get("__version__", ""))
                if ver:
                    return f"v{ver}" if not ver.startswith("v") else ver
            except Exception:
                pass
    return "v1.4.3"


APP_VERSION = _load_app_version()
PC_REGISTRY_PATH = ROOT_DIR / "v3_registered_pcs.json"
LOCAL_PC_ID = "__local__"
LOG_DIR = Path(__file__).resolve().parent / "output"
LOG_FILE = LOG_DIR / "workspace.log"

COLORS = {
    "bg": "#121212",
    "card": "#1A1A1A",
    "border": "#2A2A2A",
    "hover": "#2A2A2A",
    "text": "#FFFFFF",
    "muted": "#B5B5B5",
    "accent": "#FEE500",
    "accent_text": "#191919",
    "danger": "#FF6B6B",
    "online": "#4CD964",
}

ROOM_ICON_COLORS = [
    "#FF6B6B", "#FFA94D", "#FFD43B", "#69DB7C",
    "#38D9A9", "#4DABF7", "#748FFC", "#DA77F2",
]

WORKSPACE_STYLESHEET = f"""
QWidget {{
    background-color: {COLORS["bg"]};
    color: {COLORS["text"]};
    font-family: 'Pretendard', 'Segoe UI', sans-serif;
    font-size: 16px;
}}
QLabel {{
    color: {COLORS["text"]};
}}
QFrame#card {{
    background-color: {COLORS["card"]};
    border: 1px solid {COLORS["border"]};
    border-radius: 8px;
}}
QTabWidget::pane {{
    border: 1px solid {COLORS["border"]};
    border-radius: 8px;
    background: {COLORS["card"]};
    top: -1px;
}}
QTabBar::tab {{
    background: {COLORS["card"]};
    color: {COLORS["muted"]};
    padding: 10px 18px;
    margin-right: 4px;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
    font-weight: 700;
}}
QTabBar::tab:selected {{
    background: {COLORS["accent"]};
    color: {COLORS["accent_text"]};
}}
QTextEdit, QSpinBox, QComboBox {{
    background-color: {COLORS["card"]};
    color: {COLORS["text"]};
    border: 1px solid {COLORS["border"]};
    border-radius: 8px;
    padding: 8px;
}}
QGroupBox {{
    border: 1px solid {COLORS["border"]};
    border-radius: 8px;
    margin-top: 12px;
    padding-top: 16px;
    font-weight: 800;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
}}
QScrollArea {{
    border: none;
    background: transparent;
}}
"""


def setup_logger() -> logging.Logger:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("kakao_workspace")
    logger.setLevel(logging.DEBUG)
    if not logger.handlers:
        fh = logging.FileHandler(str(LOG_FILE), encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        logger.addHandler(fh)
        ch = logging.StreamHandler()
        ch.setLevel(logging.INFO)
        ch.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
        logger.addHandler(ch)
    return logger


log = setup_logger()


def load_pretendard_font() -> str:
    font_path = ROOT_DIR / "Pretendard-Regular.ttf"
    if font_path.exists():
        font_id = QFontDatabase.addApplicationFont(str(font_path))
        families = QFontDatabase.applicationFontFamilies(font_id)
        if families:
            return families[0]
    return "Segoe UI"


def room_icon_color(title: str) -> str:
    return ROOM_ICON_COLORS[hash(title) % len(ROOM_ICON_COLORS)]


def load_registered_pcs() -> list[dict]:
    if not PC_REGISTRY_PATH.exists():
        return []
    try:
        data = json.loads(PC_REGISTRY_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        log.error("Failed to load PC registry:\n%s", traceback.format_exc())
        return []


def save_registered_pcs(pcs: list[dict]) -> None:
    PC_REGISTRY_PATH.write_text(
        json.dumps(pcs, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


class PcRegisterDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("PC 등록")
        self.setMinimumWidth(400)
        layout = QVBoxLayout(self)
        hint = QLabel(
            "원격 PC를 Hub에 연결합니다. 원격 PC에서는 Agent를 실행해야 합니다.\n"
            "Hub URL·원격 PC ID·토큰(.env KAKAO_REMOTE_TOKEN)이 필요합니다."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {COLORS['muted']};")
        layout.addWidget(hint)

        form = QFormLayout()
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("예: 사무실 PC, 매장 PC")
        form.addRow("PC 이름", self.name_edit)
        self.host_edit = QLineEdit()
        self.host_edit.setPlaceholderText("예: office-pc.local 또는 IP")
        form.addRow("호스트/IP", self.host_edit)
        self.hub_edit = QLineEdit()
        self.hub_edit.setPlaceholderText("예: http://192.168.0.10:8765")
        hub_default = os.getenv("KAKAO_REMOTE_HUB_URL", "")
        if hub_default:
            self.hub_edit.setText(hub_default)
        form.addRow("Hub URL", self.hub_edit)
        self.remote_pc_edit = QLineEdit()
        self.remote_pc_edit.setPlaceholderText("Agent의 KAKAO_REMOTE_PC_ID와 동일")
        form.addRow("원격 PC ID", self.remote_pc_edit)
        self.note_edit = QLineEdit()
        self.note_edit.setPlaceholderText("메모 (선택)")
        form.addRow("메모", self.note_edit)
        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def pc_data(self) -> dict | None:
        name = self.name_edit.text().strip()
        if not name:
            return None
        remote_pc_id = self.remote_pc_edit.text().strip() or name
        return {
            "id": str(uuid.uuid4()),
            "name": name,
            "host": self.host_edit.text().strip(),
            "hub_url": self.hub_edit.text().strip(),
            "remote_pc_id": remote_pc_id,
            "note": self.note_edit.text().strip(),
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }


# ---------------------------------------------------------------------------
# Button widgets
# ---------------------------------------------------------------------------

class GhostButton(QPushButton):
    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet(
            f"""
            QPushButton {{
                background: transparent;
                color: {COLORS["muted"]};
                border: 1px solid {COLORS["border"]};
                border-radius: 8px;
                padding: 6px 12px;
                font-weight: 700;
            }}
            QPushButton:hover {{
                background: {COLORS["hover"]};
                color: {COLORS["text"]};
            }}
            """
        )


class PrimaryButton(QPushButton):
    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet(
            f"""
            QPushButton {{
                background: {COLORS["accent"]};
                color: {COLORS["accent_text"]};
                border: none;
                border-radius: 8px;
                padding: 10px 16px;
                font-weight: 800;
            }}
            QPushButton:hover {{
                background: #F4D300;
            }}
            """
        )


class SmallButton(QPushButton):
    def __init__(self, text: str, accent: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        if accent:
            bg, fg = COLORS["accent"], COLORS["accent_text"]
        else:
            bg, fg = COLORS["hover"], COLORS["text"]
        self.setStyleSheet(
            f"""
            QPushButton {{
                background: {bg};
                color: {fg};
                border: none;
                border-radius: 6px;
                padding: 4px 10px;
                font-size: 13px;
                font-weight: 700;
            }}
            QPushButton:hover {{
                background: {COLORS["accent"] if not accent else "#F4D300"};
            }}
            """
        )


class CloseXButton(QPushButton):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("✕", parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(22, 22)
        self.setStyleSheet(
            f"""
            QPushButton {{
                background: transparent;
                color: {COLORS["muted"]};
                border: none;
                border-radius: 11px;
                font-size: 13px;
                font-weight: 700;
            }}
            QPushButton:hover {{
                background: {COLORS["danger"]};
                color: white;
            }}
            """
        )


# ---------------------------------------------------------------------------
# PC row widget
# ---------------------------------------------------------------------------

class PcRowWidget(QFrame):
    clicked = pyqtSignal()

    def __init__(self, name: str, status: str, badge: int, selected: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("pc_row")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._selected = selected
        self._apply_style(selected)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(10)

        self.dot_label = QLabel("●")
        self.dot_label.setStyleSheet(f"color: {COLORS['online']}; font-size: 12px;")
        layout.addWidget(self.dot_label)

        text_col = QVBoxLayout()
        text_col.setSpacing(2)
        self.name_label = QLabel(name)
        self.name_label.setStyleSheet("font-weight: 800; font-size: 15px;")
        self.status_label = QLabel(status)
        self.status_label.setStyleSheet(f"color: {COLORS['muted']}; font-size: 13px;")
        text_col.addWidget(self.name_label)
        text_col.addWidget(self.status_label)
        layout.addLayout(text_col, stretch=1)

        self.badge_label: QLabel | None = None
        self._badge_layout = layout
        self.update_data(name, status, badge, selected)

    def update_data(self, name: str, status: str, badge: int, selected: bool) -> None:
        self.name_label.setText(name)
        self.status_label.setText(status)
        self.set_selected(selected)
        if self.badge_label is not None:
            self.badge_label.deleteLater()
            self.badge_label = None
        if badge > 0:
            self.badge_label = QLabel(str(badge))
            self.badge_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.badge_label.setFixedSize(24, 24)
            self.badge_label.setStyleSheet(
                f"background: {COLORS['accent']}; color: {COLORS['accent_text']}; "
                "border-radius: 12px; font-weight: 900; font-size: 12px;"
            )
            self._badge_layout.addWidget(self.badge_label)

    def set_selected(self, selected: bool) -> None:
        self._selected = selected
        self._apply_style(selected)

    def _apply_style(self, selected: bool) -> None:
        if selected:
            self.setStyleSheet(
                f"QFrame#pc_row {{ background: {COLORS['accent']}; border-radius: 8px; border: none; }}"
                f"QLabel {{ color: {COLORS['accent_text']}; }}"
            )
        else:
            self.setStyleSheet(
                f"QFrame#pc_row {{ background: {COLORS['card']}; border: 1px solid {COLORS['border']}; border-radius: 8px; }}"
            )

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self.clicked.emit()
        super().mousePressEvent(event)


# ---------------------------------------------------------------------------
# KakaoTalk session card (multi-instance)
# ---------------------------------------------------------------------------

class SessionCardWidget(QFrame):
    clicked = pyqtSignal()
    close_clicked = pyqtSignal()
    rename_requested = pyqtSignal()

    def __init__(
        self,
        display_name: str,
        chat_count: int,
        selected: bool = False,
        online: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("session_card")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        border = f"2px solid {COLORS['accent']}" if selected else f"1px solid {COLORS['border']}"
        self.setStyleSheet(
            f"QFrame#session_card {{ background: {COLORS['card']}; border: {border}; "
            f"border-radius: 8px; }}"
            f"QFrame#session_card:hover {{ background: {COLORS['hover']}; }}"
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 8, 8, 8)
        layout.setSpacing(8)

        dot_color = COLORS["online"] if online else COLORS["muted"]
        dot = QLabel("●")
        dot.setStyleSheet(f"color: {dot_color}; font-size: 14px; font-weight: 900;")
        dot.setFixedWidth(16)
        layout.addWidget(dot)

        text_col = QVBoxLayout()
        text_col.setSpacing(2)
        name_label = QLabel(display_name)
        name_label.setStyleSheet("font-size: 13px; font-weight: 800;")
        text_col.addWidget(name_label)
        sub = QLabel(f"열린 톡방 {chat_count}개")
        sub.setStyleSheet(f"color: {COLORS['muted']}; font-size: 11px;")
        text_col.addWidget(sub)
        layout.addLayout(text_col, stretch=1)

        close_btn = CloseXButton()
        close_btn.clicked.connect(self.close_clicked.emit)
        layout.addWidget(close_btn)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self.clicked.emit()
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        self.rename_requested.emit()
        super().mouseDoubleClickEvent(event)


# ---------------------------------------------------------------------------
# Left panel compact room row (for quick switching)
# ---------------------------------------------------------------------------

class LeftRoomRow(QFrame):
    select_clicked = pyqtSignal()
    close_clicked = pyqtSignal()

    def __init__(self, title: str, selected: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("left_room")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._selected = selected
        self._apply_style()

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 6, 6)
        layout.setSpacing(8)

        icon_color = room_icon_color(title)
        icon = QLabel(title[0] if title else "?")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setFixedSize(28, 28)
        icon.setStyleSheet(
            f"background: {icon_color}; color: white; border-radius: 14px; "
            "font-weight: 900; font-size: 13px;"
        )
        layout.addWidget(icon)

        name = QLabel(title)
        name.setStyleSheet("font-size: 13px; font-weight: 700;")
        name.setWordWrap(False)
        layout.addWidget(name, stretch=1)

        close_btn = CloseXButton()
        close_btn.clicked.connect(self.close_clicked.emit)
        layout.addWidget(close_btn)

    def _apply_style(self) -> None:
        if self._selected:
            self.setStyleSheet(
                f"QFrame#left_room {{ background: {COLORS['accent']}; border-radius: 6px; }}"
                f"QLabel {{ color: {COLORS['accent_text']}; }}"
            )
        else:
            self.setStyleSheet(
                f"QFrame#left_room {{ background: transparent; border-radius: 6px; }}"
                f"QFrame#left_room:hover {{ background: {COLORS['hover']}; }}"
            )

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self.select_clicked.emit()
        super().mousePressEvent(event)


# ---------------------------------------------------------------------------
# Room row widget (톡방 보기 tab, KakaoTalk-style)
# ---------------------------------------------------------------------------

class RoomRowWidget(QFrame):
    open_clicked = pyqtSignal()
    close_clicked = pyqtSignal()
    register_clicked = pyqtSignal()

    def __init__(
        self,
        title: str,
        preview: str,
        time_text: str,
        tags: list[str] | None = None,
        show_register: bool = False,
        show_close: bool = True,
        selected: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("room_row")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        border = f"2px solid {COLORS['accent']}" if selected else f"1px solid {COLORS['border']}"
        self.setStyleSheet(
            f"QFrame#room_row {{ background: {COLORS['card']}; border: {border}; border-radius: 8px; }}"
            f"QFrame#room_row:hover {{ background: {COLORS['hover']}; }}"
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        icon_color = room_icon_color(title)
        icon = QLabel(title[0] if title else "?")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setFixedSize(42, 42)
        icon.setStyleSheet(
            f"background: {icon_color}; color: white; border-radius: 21px; "
            "font-weight: 900; font-size: 18px;"
        )
        layout.addWidget(icon)

        text_col = QVBoxLayout()
        text_col.setSpacing(3)

        title_row = QHBoxLayout()
        title_label = QLabel(title)
        title_label.setStyleSheet("font-weight: 800; font-size: 15px;")
        title_row.addWidget(title_label)
        for tag in tags or []:
            tag_label = QLabel(tag)
            tag_label.setStyleSheet(
                f"background: {COLORS['hover']}; color: {COLORS['muted']}; "
                "padding: 2px 8px; border-radius: 4px; font-size: 11px;"
            )
            title_row.addWidget(tag_label)
        title_row.addStretch()
        text_col.addLayout(title_row)

        preview_label = QLabel(preview)
        preview_label.setStyleSheet(f"color: {COLORS['muted']}; font-size: 13px;")
        preview_label.setWordWrap(False)
        text_col.addWidget(preview_label)
        layout.addLayout(text_col, stretch=1)

        right_col = QVBoxLayout()
        right_col.setSpacing(4)
        time_label = QLabel(time_text)
        time_label.setStyleSheet(f"color: {COLORS['muted']}; font-size: 11px;")
        time_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        right_col.addWidget(time_label)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(4)
        open_btn = SmallButton("열기", accent=True)
        open_btn.clicked.connect(self.open_clicked.emit)
        btn_row.addWidget(open_btn)
        if show_register:
            reg_btn = SmallButton("등록")
            reg_btn.clicked.connect(self.register_clicked.emit)
            btn_row.addWidget(reg_btn)
        if show_close:
            close_btn = CloseXButton()
            close_btn.clicked.connect(self.close_clicked.emit)
            btn_row.addWidget(close_btn)
        right_col.addLayout(btn_row)
        layout.addLayout(right_col)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self.open_clicked.emit()
        super().mousePressEvent(event)


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class WorkspaceWindow(QMainWindow):
    def __init__(self, allow_send: bool, interval_ms: int, hide_native_input_px: int) -> None:
        super().__init__()
        self._loaded_settings = load_workspace_settings()
        _ui = self._loaded_settings.get("ui") or {}
        self.allow_send = allow_send
        self.interval_ms = int(_ui.get("interval_ms") or interval_ms)
        self.hide_native_input_px = int(
            _ui.get("hide_native_input_px")
            if _ui.get("hide_native_input_px") is not None
            else hide_native_input_px
        )
        self.selected_hwnd: int | None = None
        self.selected_title = ""
        self.main_hwnd: int | None = None
        self.kakao_any_hwnd: int | None = None
        self.kakao_instances: dict[int, dict] = {}
        self.active_instance_pid: int | None = None
        self.instance_names: dict[str, str] = dict(
            self._loaded_settings.get("instance_names") or {}
        )
        self.kakao_exe_path: str = str(
            self._loaded_settings.get("kakao_exe_path") or ""
        )
        self._session_card_widgets: dict[int, SessionCardWidget] = {}
        self._pending_launch_pid: int | None = None
        self.last_image = None
        self.last_main_image = None
        self.reservations: list[dict] = list(
            self._loaded_settings.get("reservations") or []
        )
        self.chat_rooms: list[tuple[int, str]] = []
        self.unregistered_rooms: list[tuple[int, str]] = []
        self.known_rooms: dict[str, int | None] = {
            str(t): True for t in (self._loaded_settings.get("known_room_titles") or [])
        }
        self.pc_name = socket.gethostname()
        self._refresh_error_count = 0
        self._viewing_dialog = False
        self._restore_hwnd: int | None = None
        self._restore_title = ""
        self.active_dialog_hwnd: int | None = None
        self.registered_pcs: list[dict] = load_registered_pcs()
        self.active_pc_id = LOCAL_PC_ID
        self.remote_client: RemoteKakaoClient | None = None
        self._hub_pc_status: dict[str, dict] = {}
        self._pc_row_widgets: dict[str, PcRowWidget] = {}
        self._ignore_next_click = False
        self._update_in_progress = False
        self._suppress_text_changed = False
        self._embedded_hub = None
        self._embedded_agent = None
        self._has_log_error = False
        self._send_warning_acknowledged = allow_send
        self._status_action_timer = QTimer(self)
        self._status_action_timer.setSingleShot(True)
        self._status_action_timer.timeout.connect(self._fade_status_action)

        log.info("WorkspaceWindow init: allow_send=%s interval=%dms", allow_send, interval_ms)

        self.setWindowTitle(f"카카오 매니저 {APP_VERSION}")
        self.resize(1280, 900)
        self.setStyleSheet(WORKSPACE_STYLESHEET)

        self._setup_ui()
        self._wire_timers()
        self._save_settings_timer = QTimer(self)
        self._save_settings_timer.setSingleShot(True)
        self._save_settings_timer.setInterval(800)
        self._save_settings_timer.timeout.connect(self._save_workspace_settings_now)
        self._apply_loaded_settings()
        self._local_status = "온라인"
        self._local_badge = 0
        self.refresh_candidates()

        self._start_embedded_p2p()

    def openai_model(self) -> str:
        return os.getenv("OPENAI_MODEL", "gpt-5.4-mini")

    def openai_key_available(self) -> bool:
        return bool(os.getenv("OPENAI_API_KEY"))

    # -------------------------------------------------------------------
    # UI setup
    # -------------------------------------------------------------------

    def _setup_ui(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_title_bar())
        root.addWidget(self._build_status_strip())
        root.addWidget(self._build_send_warning_banner())

        body_wrap = QWidget()
        body_wrap.setStyleSheet(f"background: {COLORS['bg']};")
        body = QHBoxLayout(body_wrap)
        body.setContentsMargins(12, 12, 12, 8)
        body.setSpacing(12)

        body.addWidget(self._build_left_panel(), stretch=0)
        body.addLayout(self._build_center_panel(), stretch=1)
        root.addWidget(body_wrap, stretch=1)

        root.addWidget(self._build_bottom_bar())
        self.setCentralWidget(central)
        self._sync_send_mode_ui()

    def _build_title_bar(self) -> QFrame:
        bar = QFrame()
        bar.setFixedHeight(52)
        bar.setStyleSheet(f"background: {COLORS['card']}; border-bottom: 1px solid {COLORS['border']};")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(16, 0, 12, 0)

        icon = QLabel()
        icon.setFixedSize(28, 28)
        icon.setStyleSheet(f"background: {COLORS['accent']}; border-radius: 14px;")
        layout.addWidget(icon)

        title = QLabel(f"카카오 매니저  {APP_VERSION}")
        title.setStyleSheet("font-size: 18px; font-weight: 900;")
        layout.addWidget(title)
        layout.addStretch()

        settings_btn = GhostButton("설정")
        settings_btn.clicked.connect(self._show_settings_menu)
        layout.addWidget(settings_btn)
        update_btn = GhostButton("업데이트")
        update_btn.clicked.connect(self._check_updates_manual)
        layout.addWidget(update_btn)
        self.log_btn = GhostButton("로그")
        self.log_btn.clicked.connect(self._show_log_viewer)
        layout.addWidget(self.log_btn)
        help_btn = GhostButton("도움말")
        help_btn.clicked.connect(self._show_help_dialog)
        layout.addWidget(help_btn)

        for sym, action in (("—", self.showMinimized), ("□", lambda: self.showMaximized()), ("✕", self.close)):
            win_btn = GhostButton(sym)
            win_btn.setFixedWidth(36)
            win_btn.clicked.connect(action)
            layout.addWidget(win_btn)
        return bar

    def _build_status_strip(self) -> QFrame:
        strip = QFrame()
        strip.setFixedHeight(40)
        strip.setStyleSheet(
            f"background: {COLORS['bg']}; border-bottom: 1px solid {COLORS['border']};"
        )
        layout = QHBoxLayout(strip)
        layout.setContentsMargins(14, 0, 14, 0)
        layout.setSpacing(16)

        self.status_pc_label = QLabel(f"PC: {self.pc_name}")
        self.status_pc_label.setStyleSheet(
            f"color: {COLORS['text']}; font-size: 13px; font-weight: 700;"
        )
        layout.addWidget(self.status_pc_label)

        sep1 = QLabel("|")
        sep1.setStyleSheet(f"color: {COLORS['border']};")
        layout.addWidget(sep1)

        self.status_session_label = QLabel("세션: —")
        self.status_session_label.setStyleSheet(
            f"color: {COLORS['muted']}; font-size: 13px; font-weight: 600;"
        )
        self.status_session_label.setMinimumWidth(100)
        layout.addWidget(self.status_session_label)

        sep_sess = QLabel("|")
        sep_sess.setStyleSheet(f"color: {COLORS['border']};")
        layout.addWidget(sep_sess)

        self.status_room_label = QLabel("방: (없음)")
        self.status_room_label.setStyleSheet(
            f"color: {COLORS['muted']}; font-size: 13px; font-weight: 600;"
        )
        self.status_room_label.setMinimumWidth(120)
        layout.addWidget(self.status_room_label)

        sep2 = QLabel("|")
        sep2.setStyleSheet(f"color: {COLORS['border']};")
        layout.addWidget(sep2)

        self.allow_send_checkbox = QCheckBox("실제 보내기")
        self.allow_send_checkbox.setChecked(self.allow_send)
        self.allow_send_checkbox.setStyleSheet(
            f"font-size: 13px; font-weight: 800; color: {COLORS['text']};"
        )
        self.allow_send_checkbox.toggled.connect(self._on_allow_send_toggled)
        layout.addWidget(self.allow_send_checkbox)

        layout.addStretch()

        self.status_action_label = QLabel("준비됨")
        self.status_action_label.setStyleSheet(
            f"color: {COLORS['muted']}; font-size: 12px; font-weight: 600;"
        )
        self.status_action_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        layout.addWidget(self.status_action_label, stretch=1)

        self._update_status_strip_pc()
        self._update_status_strip_session()
        self._update_status_strip_room()
        return strip

    def _build_send_warning_banner(self) -> QFrame:
        self.send_warning_banner = QFrame()
        self.send_warning_banner.setFixedHeight(32)
        self.send_warning_banner.setStyleSheet(
            f"background: #3d3500; border-bottom: 1px solid {COLORS['accent']};"
        )
        warn_layout = QHBoxLayout(self.send_warning_banner)
        warn_layout.setContentsMargins(14, 0, 14, 0)
        warn_label = QLabel(
            "⚠ 실제 보내기 켜짐 — 메시지·Enter·보내기가 카카오톡에 실제 전송됩니다."
        )
        warn_label.setStyleSheet(
            f"color: {COLORS['accent']}; font-size: 13px; font-weight: 800;"
        )
        warn_layout.addWidget(warn_label)
        warn_layout.addStretch()
        dismiss_btn = GhostButton("확인")
        dismiss_btn.setFixedWidth(56)
        dismiss_btn.clicked.connect(lambda: self.send_warning_banner.hide())
        warn_layout.addWidget(dismiss_btn)
        self.send_warning_banner.hide()
        return self.send_warning_banner

    def _sync_send_mode_ui(self) -> None:
        if self.allow_send:
            self.mode_label.setText("실제 보내기")
            self.mode_label.setStyleSheet(
                f"color: {COLORS['accent']}; font-size: 12px; font-weight: 800;"
            )
            self.allow_send_checkbox.setStyleSheet(
                f"font-size: 13px; font-weight: 800; color: {COLORS['accent']};"
            )
            if not self._send_warning_acknowledged:
                self.send_warning_banner.show()
                self._send_warning_acknowledged = True
        else:
            self.mode_label.setText("모의 보내기")
            self.mode_label.setStyleSheet(f"color: {COLORS['muted']}; font-size: 12px;")
            self.allow_send_checkbox.setStyleSheet(
                f"font-size: 13px; font-weight: 800; color: {COLORS['text']};"
            )
            self.send_warning_banner.hide()
        self.allow_send_checkbox.blockSignals(True)
        self.allow_send_checkbox.setChecked(self.allow_send)
        self.allow_send_checkbox.blockSignals(False)

    def _on_allow_send_toggled(self, checked: bool) -> None:
        if checked and not self.allow_send:
            reply = QMessageBox.warning(
                self,
                "실제 보내기",
                "켜면 보내기·Enter·원격 전송이 카카오톡에 실제로 전달됩니다.\n"
                "테스트 방 1개로만 확인하는 것을 권장합니다.\n\n"
                "실제 보내기를 켤까요?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                self.allow_send_checkbox.blockSignals(True)
                self.allow_send_checkbox.setChecked(False)
                self.allow_send_checkbox.blockSignals(False)
                return
        self.allow_send = checked
        if self._embedded_agent is not None:
            self._embedded_agent.allow_send = checked
        self._sync_send_mode_ui()
        self.set_status(
            "실제 보내기 ON" if checked else "모의 보내기 (전송 시뮬레이션)"
        )

    def _update_status_strip_pc(self) -> None:
        if self.active_pc_id == LOCAL_PC_ID:
            pc_text = f"PC: {self.pc_name} (이 PC)"
        else:
            pc = next(
                (p for p in self.registered_pcs if p["id"] == self.active_pc_id), None
            )
            name = pc["name"] if pc else "원격"
            remote_id = pc.get("remote_pc_id", "") if pc else ""
            hub_row = self._hub_pc_status.get(remote_id, {}) if remote_id else {}
            online = hub_row.get("status") == "online"
            dot = "●" if online else "○"
            pc_text = f"PC: {name} {dot}"
        self.status_pc_label.setText(pc_text)

    def _update_status_strip_session(self) -> None:
        pid = self.active_instance_pid
        if pid and pid in self.kakao_instances:
            name = self._instance_display_name(pid)
            short = name if len(name) <= 20 else name[:18] + "…"
            self.status_session_label.setText(f"세션: {short} ●")
            self.status_session_label.setStyleSheet(
                f"color: {COLORS['accent']}; font-size: 13px; font-weight: 700;"
            )
        elif self.kakao_instances:
            first_pid = next(iter(self.kakao_instances))
            name = self._instance_display_name(first_pid)
            self.status_session_label.setText(f"세션: {name}")
            self.status_session_label.setStyleSheet(
                f"color: {COLORS['muted']}; font-size: 13px; font-weight: 600;"
            )
        else:
            self.status_session_label.setText("세션: —")
            self.status_session_label.setStyleSheet(
                f"color: {COLORS['muted']}; font-size: 13px; font-weight: 600;"
            )

    def _update_status_strip_room(self) -> None:
        if self.selected_title:
            short = (
                self.selected_title
                if len(self.selected_title) <= 28
                else self.selected_title[:26] + "…"
            )
            self.status_room_label.setText(f"방: {short}")
            self.status_room_label.setStyleSheet(
                f"color: {COLORS['accent']}; font-size: 13px; font-weight: 700;"
            )
        else:
            self.status_room_label.setText("방: (없음)")
            self.status_room_label.setStyleSheet(
                f"color: {COLORS['muted']}; font-size: 13px; font-weight: 600;"
            )

    def _set_log_error_indicator(self, has_error: bool) -> None:
        self._has_log_error = has_error
        if has_error:
            self.log_btn.setText("로그 ●")
            self.log_btn.setStyleSheet(
                self.log_btn.styleSheet()
                + f"QPushButton {{ color: {COLORS['danger']}; border-color: {COLORS['danger']}; }}"
            )
        else:
            self.log_btn.setText("로그")
            self.log_btn.setStyleSheet("")

    def _fade_status_action(self) -> None:
        self.status_action_label.setStyleSheet(
            f"color: {COLORS['border']}; font-size: 12px; font-weight: 600;"
        )

    def _build_left_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("card")
        panel.setFixedWidth(280)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        header = QHBoxLayout()
        header.addWidget(QLabel("PC 목록"))
        header.itemAt(0).widget().setStyleSheet("font-weight: 900; font-size: 16px;")
        header.addStretch()
        refresh_btn = GhostButton("↻")
        refresh_btn.setFixedWidth(36)
        refresh_btn.clicked.connect(self.refresh_candidates)
        header.addWidget(refresh_btn)
        quick_add_btn = PrimaryButton("+ 내 PC")
        quick_add_btn.setFixedWidth(80)
        quick_add_btn.clicked.connect(self._quick_register_this_pc)
        header.addWidget(quick_add_btn)
        add_btn = GhostButton("수동 등록")
        add_btn.clicked.connect(self._show_pc_register_dialog)
        header.addWidget(add_btn)
        layout.addLayout(header)

        self.pc_list_container = QVBoxLayout()
        self.pc_list_container.setSpacing(6)
        layout.addLayout(self.pc_list_container)
        self._rebuild_pc_list()

        sess_title = QLabel("카카오톡 세션")
        sess_title.setStyleSheet("font-weight: 900; font-size: 14px; margin-top: 4px;")
        layout.addWidget(sess_title)

        self.sessions_scroll = QScrollArea()
        self.sessions_scroll.setWidgetResizable(True)
        self.sessions_scroll.setMaximumHeight(150)
        self.sessions_scroll.setStyleSheet("background: transparent; border: none;")
        sessions_wrap = QWidget()
        self.sessions_container = QVBoxLayout(sessions_wrap)
        self.sessions_container.setContentsMargins(0, 0, 0, 0)
        self.sessions_container.setSpacing(4)
        self.sessions_container.addStretch()
        self.sessions_scroll.setWidget(sessions_wrap)
        layout.addWidget(self.sessions_scroll)

        add_session_btn = PrimaryButton("+ 카카오톡 추가")
        add_session_btn.setToolTip(
            "세마포어 해제 후 카카오톡을 자동 실행합니다.\n"
            "로그인만 하면 새 세션이 목록에 나타납니다."
        )
        add_session_btn.clicked.connect(self._add_kakao_instance_one_click)
        layout.addWidget(add_session_btn)

        rooms_header = QHBoxLayout()
        rooms_label = QLabel("열린 톡방")
        rooms_label.setStyleSheet("font-weight: 900; font-size: 14px; margin-top: 6px;")
        rooms_header.addWidget(rooms_label)
        rooms_header.addStretch()
        layout.addLayout(rooms_header)

        self.left_rooms_scroll = QScrollArea()
        self.left_rooms_scroll.setWidgetResizable(True)
        self.left_rooms_scroll.setStyleSheet("background: transparent;")
        left_rooms_wrap = QWidget()
        self.left_rooms_container = QVBoxLayout(left_rooms_wrap)
        self.left_rooms_container.setContentsMargins(0, 0, 0, 0)
        self.left_rooms_container.setSpacing(3)
        self.left_rooms_container.addStretch()
        self.left_rooms_scroll.setWidget(left_rooms_wrap)
        layout.addWidget(self.left_rooms_scroll, stretch=1)

        return panel

    def _build_center_panel(self) -> QVBoxLayout:
        center = QVBoxLayout()
        center.setSpacing(8)

        header_row = QHBoxLayout()
        self.header_label = QLabel(self.pc_name)
        self.header_label.setStyleSheet("font-size: 26px; font-weight: 900;")
        header_row.addWidget(self.header_label)
        header_row.addStretch()
        self.header_refresh_btn = GhostButton("새로고침")
        self.header_refresh_btn.clicked.connect(self.refresh_candidates)
        header_row.addWidget(self.header_refresh_btn)
        center.addLayout(header_row)

        self.summary_label = QLabel("온라인 · 카카오 실행 상태 확인 중")
        self.summary_label.setStyleSheet(f"color: {COLORS['muted']}; font-weight: 600; font-size: 14px;")
        center.addWidget(self.summary_label)

        self.tabs = QTabWidget()
        self._setup_screen_tab()
        self._setup_rooms_tab()
        self._setup_send_tab()
        self.tabs.setCurrentIndex(0)
        center.addWidget(self.tabs, stretch=1)
        return center

    def _setup_rooms_tab(self) -> None:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        hint = QLabel(
            "톡방 전환·미리보기·입력은 「작업」 탭과 좌측 「열린 톡방」에서 하세요.\n"
            "여기서는 열린 방·닫힌 방 목록과 예약 상태만 관리합니다."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {COLORS['muted']}; font-size: 13px; padding: 4px 0;")
        layout.addWidget(hint)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        wrap = QWidget()
        inner = QVBoxLayout(wrap)
        inner.setSpacing(12)

        reg_title = QLabel("등록 톡방 (열림)")
        reg_title.setStyleSheet("font-weight: 900; font-size: 15px;")
        inner.addWidget(reg_title)
        self.registered_container = QVBoxLayout()
        self.registered_container.setSpacing(6)
        inner.addLayout(self.registered_container)

        unreg_title = QLabel("미등록 톡방 / 개인톡 (닫힘)")
        unreg_title.setStyleSheet("font-weight: 900; font-size: 15px; margin-top: 8px;")
        inner.addWidget(unreg_title)
        self.unregistered_container = QVBoxLayout()
        self.unregistered_container.setSpacing(6)
        inner.addLayout(self.unregistered_container)
        inner.addStretch()

        scroll.setWidget(wrap)
        layout.addWidget(scroll, stretch=1)
        self.tabs.addTab(tab, "톡방 관리")

    def _setup_send_tab(self) -> None:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(14)

        guide = QLabel(
            "일상 작업은 「작업」 탭 + 하단 입력창을 사용하세요. "
            "닫힌 방을 열 때만 아래 「카카오 목록」을 펼칩니다."
        )
        guide.setWordWrap(True)
        guide.setStyleSheet(f"color: {COLORS['muted']};")
        layout.addWidget(guide)

        actions = QHBoxLayout()
        open_btn = GhostButton("선택 톡방 앞으로")
        open_btn.clicked.connect(self.bring_selected_to_front)
        actions.addWidget(open_btn)
        work_btn = GhostButton("작업 탭으로")
        work_btn.clicked.connect(lambda: self.tabs.setCurrentIndex(0))
        actions.addWidget(work_btn)
        stop_btn = GhostButton("발송 중지")
        stop_btn.clicked.connect(lambda: self.set_status("발송 중지 — 프로토타입 모의 동작"))
        actions.addWidget(stop_btn)
        actions.addStretch()
        layout.addLayout(actions)

        self.kakao_advanced_group = QGroupBox("카카오 목록 (고급 — 닫힌 방 열기)")
        self.kakao_advanced_group.setCheckable(True)
        self.kakao_advanced_group.setChecked(False)
        kakao_layout = QVBoxLayout(self.kakao_advanced_group)
        kakao_hint = QLabel("메인 목록 캡처 후 클릭·스크롤로 방을 엽니다.")
        kakao_hint.setWordWrap(True)
        kakao_hint.setStyleSheet(f"color: {COLORS['muted']}; font-size: 13px;")
        kakao_layout.addWidget(kakao_hint)
        kakao_row = QHBoxLayout()
        refresh_main = GhostButton("목록 갱신")
        refresh_main.clicked.connect(self.refresh_main_preview)
        kakao_row.addWidget(refresh_main)
        kakao_row.addStretch()
        kakao_layout.addLayout(kakao_row)
        self.main_preview_label = ClickablePreviewLabel()
        self.main_preview_label.setMinimumHeight(280)
        self.main_preview_label.setStyleSheet(
            f"background:#090909; border:1px solid {COLORS['border']}; border-radius:8px;"
        )
        self.main_preview_label.imageClicked.connect(self.handle_main_preview_click)
        self.main_preview_label.imageDoubleClicked.connect(self.handle_main_preview_double_click)
        self.main_preview_label.imageWheeled.connect(self.handle_main_preview_wheel)
        kakao_layout.addWidget(self.main_preview_label)
        layout.addWidget(self.kakao_advanced_group)

        schedule_group = QGroupBox("예약 발송")
        schedule_layout = QVBoxLayout(schedule_group)
        self.reservation_container = QVBoxLayout()
        self.reservation_container.setSpacing(6)
        schedule_layout.addLayout(self.reservation_container)
        reserve_hint = QLabel("예약은 톡방 row 옆 타이머로도 표시됩니다.")
        reserve_hint.setStyleSheet(f"color: {COLORS['muted']}; font-size: 13px;")
        schedule_layout.addWidget(reserve_hint)
        layout.addWidget(schedule_group)

        ai_group = QGroupBox("AI 자동 응답")
        ai_layout = QVBoxLayout(ai_group)
        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("모드"))
        self.ai_mode_combo = QComboBox()
        self.ai_mode_combo.addItems(
            ["off - 사용 안함", "semi - 명확한 질문만", "on - 모든 질문 후보"]
        )
        self.ai_mode_combo.setCurrentIndex(1)
        mode_row.addWidget(self.ai_mode_combo)
        key_status = "키 설정됨" if self.openai_key_available() else "키 없음"
        mode_row.addWidget(QLabel(f"{self.openai_model()} · {key_status}"))
        mode_row.addStretch()
        ai_layout.addLayout(mode_row)

        for label, attr, height, placeholder in [
            ("AI 역할", "ai_role_edit", 70, "친절한 분양 상담원…"),
            ("답변 방식 / 프롬프트", "ai_prompt_edit", 70, "3문장 이내, 공감 후 핵심 안내…"),
            ("예시 응답", "ai_examples_edit", 60, "문의 감사합니다…"),
            ("참고 자료", "ai_sources_edit", 60, "FAQ, 가격표…"),
            ("채팅 텍스트 (있으면 우선)", "ai_chat_context_edit", 60, "텍스트 추출 시 여기"),
            ("금지/주의", "ai_guardrails_edit", 56, "확정 가격 약속 금지…"),
        ]:
            ai_layout.addWidget(QLabel(label))
            edit = QTextEdit()
            edit.setPlaceholderText(placeholder)
            edit.setFixedHeight(height)
            setattr(self, attr, edit)
            ai_layout.addWidget(edit)

        level_row = QHBoxLayout()
        level_row.addWidget(QLabel("답변 적극성"))
        self.ai_level_spin = QSpinBox()
        self.ai_level_spin.setRange(1, 5)
        self.ai_level_spin.setValue(3)
        level_row.addWidget(self.ai_level_spin)
        level_row.addStretch()
        ai_layout.addLayout(level_row)

        ai_actions = QHBoxLayout()
        read_chat_btn = GhostButton("채팅 읽기")
        read_chat_btn.clicked.connect(self.read_chat_into_ai_context)
        ai_actions.addWidget(read_chat_btn)
        ai_btn = PrimaryButton("AI 답변 후보 생성")
        ai_btn.clicked.connect(self.show_ai_reply_dialog)
        ai_actions.addWidget(ai_btn)
        ai_actions.addStretch()
        ai_layout.addLayout(ai_actions)
        layout.addWidget(ai_group)
        for edit in (
            self.ai_role_edit,
            self.ai_prompt_edit,
            self.ai_examples_edit,
            self.ai_sources_edit,
            self.ai_chat_context_edit,
            self.ai_guardrails_edit,
        ):
            edit.textChanged.connect(self._queue_save_workspace_settings)
        self.ai_mode_combo.currentIndexChanged.connect(self._queue_save_workspace_settings)
        self.ai_level_spin.valueChanged.connect(self._queue_save_workspace_settings)
        layout.addStretch()

        scroll.setWidget(tab)
        self.tabs.addTab(scroll, "발송·고급")

    def _setup_screen_tab(self) -> None:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(8, 8, 8, 8)

        work_hint = QLabel(
            "좌측 「열린 톡방」으로 방을 바꾼 뒤, 아래 미리보기에서 클릭·입력하세요."
        )
        work_hint.setStyleSheet(f"color: {COLORS['muted']}; font-size: 13px;")
        layout.addWidget(work_hint)

        self.work_target_label = QLabel("선택: (없음)")
        self.work_target_label.setStyleSheet(
            f"color: {COLORS['accent']}; font-size: 15px; font-weight: 800;"
        )
        layout.addWidget(self.work_target_label)

        controls = QHBoxLayout()
        self.auto_refresh_checkbox = QCheckBox("0.5초 자동 갱신")
        self.auto_refresh_checkbox.setChecked(True)
        controls.addWidget(self.auto_refresh_checkbox)
        self.forward_click_checkbox = QCheckBox("클릭/스크롤 전달")
        self.forward_click_checkbox.setChecked(True)
        controls.addWidget(self.forward_click_checkbox)
        self.forward_double_click_checkbox = QCheckBox("더블클릭 전달")
        self.forward_double_click_checkbox.setChecked(False)
        self.forward_double_click_checkbox.setToolTip(
            "켜면 미리보기 더블클릭이 카카오톡에 전달됩니다. "
            "끄면 채팅창 안에서만 클릭·스크롤을 쓰세요 (잘못된 방 열림 방지)."
        )
        controls.addWidget(self.forward_double_click_checkbox)
        self.background_input_checkbox = QCheckBox("백그라운드 입력")
        self.background_input_checkbox.setChecked(True)
        controls.addWidget(self.background_input_checkbox)
        controls.addWidget(QLabel("하단 숨김(px):"))
        self.crop_spin = QSpinBox()
        self.crop_spin.setRange(0, 500)
        self.crop_spin.setValue(self.hide_native_input_px)
        self.crop_spin.valueChanged.connect(self._queue_save_workspace_settings)
        controls.addWidget(self.crop_spin)
        self.auto_refresh_checkbox.toggled.connect(self._queue_save_workspace_settings)
        self.forward_click_checkbox.toggled.connect(self._queue_save_workspace_settings)
        self.forward_double_click_checkbox.toggled.connect(
            self._queue_save_workspace_settings
        )
        self.background_input_checkbox.toggled.connect(self._queue_save_workspace_settings)
        refresh = GhostButton("지금 갱신")
        refresh.clicked.connect(self.refresh_preview)
        controls.addWidget(refresh)
        bring_btn = GhostButton("창 앞으로")
        bring_btn.clicked.connect(self.bring_selected_to_front)
        controls.addWidget(bring_btn)
        controls.addStretch()
        layout.addLayout(controls)

        preview_header = QHBoxLayout()
        preview_header.addWidget(QLabel("미리보기"))
        preview_header.itemAt(0).widget().setStyleSheet("font-weight: 800;")
        preview_header.addStretch()
        self.room_preview_status = QLabel("0.5초 자동 갱신")
        self.room_preview_status.setStyleSheet(f"color: {COLORS['muted']}; font-size: 13px;")
        preview_header.addWidget(self.room_preview_status)
        layout.addLayout(preview_header)

        self.preview_label = ClickablePreviewLabel()
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setMinimumHeight(480)
        self.preview_label.setStyleSheet(
            f"background:#090909; border:1px solid {COLORS['border']}; border-radius:8px;"
        )
        self.preview_label.imageClicked.connect(self.handle_preview_click)
        self.preview_label.imageDoubleClicked.connect(self.handle_preview_double_click)
        self.preview_label.imageWheeled.connect(self.handle_preview_wheel)
        self.preview_label.imageMoved.connect(self._on_preview_mouse_move)
        self.preview_label.imageDragged.connect(self.handle_preview_drag)
        layout.addWidget(self.preview_label, stretch=1)

        self.cursor_pos_label = QLabel("마우스: (—, —)")
        self.cursor_pos_label.setStyleSheet(
            f"color: {COLORS['accent']}; font-size: 13px; font-weight: 700;"
        )
        layout.addWidget(self.cursor_pos_label)

        input_frame = QFrame()
        input_frame.setStyleSheet(
            f"background: {COLORS['card']}; border: 1px solid {COLORS['border']}; border-radius: 8px;"
        )
        input_layout = QHBoxLayout(input_frame)
        input_layout.setContentsMargins(10, 8, 10, 8)
        input_layout.setSpacing(8)

        self.screen_input = QLineEdit()
        self.screen_input.setPlaceholderText("메시지 입력 (입력하면 실시간으로 카카오톡에 반영, Enter로 전송)")
        self.screen_input.setStyleSheet(
            f"background: {COLORS['bg']}; border: 1px solid {COLORS['border']}; "
            f"border-radius: 8px; padding: 10px; font-size: 15px; color: {COLORS['text']};"
        )
        self.screen_input.textChanged.connect(self._on_screen_input_changed)
        self.screen_input.returnPressed.connect(self._on_screen_input_enter)
        input_layout.addWidget(self.screen_input, stretch=1)

        self.screen_typing_label = QLabel("")
        self.screen_typing_label.setStyleSheet(f"color: {COLORS['muted']}; font-size: 12px;")
        self.screen_typing_label.setFixedWidth(100)
        input_layout.addWidget(self.screen_typing_label)

        layout.addWidget(input_frame)
        self.tabs.addTab(tab, "작업")

    def _build_bottom_bar(self) -> QFrame:
        bar = QFrame()
        bar.setStyleSheet(f"background: {COLORS['card']}; border-top: 1px solid {COLORS['border']};")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(10)

        full_refresh = GhostButton("전체 새로고침")
        full_refresh.clicked.connect(self.refresh_candidates)
        layout.addWidget(full_refresh)

        target_col = QVBoxLayout()
        target_col.setSpacing(2)
        self.send_target_label = QLabel("톡방을 선택하세요")
        self.send_target_label.setStyleSheet(
            f"color: {COLORS['accent']}; font-size: 12px; font-weight: 800;"
        )
        self.send_target_label.setFixedWidth(200)
        target_col.addWidget(self.send_target_label)
        layout.addLayout(target_col)

        self.message_edit = QTextEdit()
        self.message_edit.setPlaceholderText("메시지를 입력하세요")
        self.message_edit.setFixedHeight(44)
        self.message_edit.setStyleSheet(
            f"background: {COLORS['bg']}; border: 1px solid {COLORS['border']}; border-radius: 8px; padding: 8px;"
        )
        layout.addWidget(self.message_edit, stretch=1)

        emoji_btn = GhostButton("😊")
        emoji_btn.setFixedWidth(44)
        emoji_btn.clicked.connect(lambda: self.set_status("이모티콘 — 후속 구현"))
        layout.addWidget(emoji_btn)

        attach_icon = GhostButton("📎")
        attach_icon.setFixedWidth(44)
        attach_icon.clicked.connect(lambda: self.set_status("첨부 — 후속 구현"))
        layout.addWidget(attach_icon)

        img_btn = GhostButton("이미지")
        img_btn.clicked.connect(lambda: self.set_status("이미지 첨부 — 후속 구현"))
        layout.addWidget(img_btn)

        reserve_row = QHBoxLayout()
        reserve_row.addWidget(QLabel("예약(분)"))
        self.reserve_minutes_spin = QSpinBox()
        self.reserve_minutes_spin.setRange(1, 240)
        self.reserve_minutes_spin.setValue(10)
        self.reserve_minutes_spin.setFixedWidth(64)
        reserve_row.addWidget(self.reserve_minutes_spin)
        reserve_btn = GhostButton("예약 발송")
        reserve_btn.clicked.connect(self.add_reservation)
        reserve_row.addWidget(reserve_btn)
        layout.addLayout(reserve_row)

        send_btn = PrimaryButton("보내기")
        send_btn.clicked.connect(self.handle_send)
        layout.addWidget(send_btn)

        stop_btn = GhostButton("발송 중지")
        stop_btn.setStyleSheet(
            stop_btn.styleSheet()
            + f"QPushButton {{ color: {COLORS['danger']}; border-color: {COLORS['danger']}; }}"
        )
        stop_btn.clicked.connect(lambda: self.set_status("발송 중지 요청"))
        layout.addWidget(stop_btn)

        self.mode_label = QLabel("모의 보내기" if not self.allow_send else "실제 보내기")
        self.mode_label.setStyleSheet(f"color: {COLORS['muted']}; font-size: 12px;")
        layout.addWidget(self.mode_label)
        return bar

    # -------------------------------------------------------------------
    # Timers
    # -------------------------------------------------------------------

    def _wire_timers(self) -> None:
        self.timer = QTimer(self)
        self.timer.setInterval(self.interval_ms)
        self.timer.timeout.connect(self.refresh_preview_if_needed)
        self.timer.start()

        self.countdown_timer = QTimer(self)
        self.countdown_timer.setInterval(1000)
        self.countdown_timer.timeout.connect(self.refresh_reservation_views)
        self.countdown_timer.start()

        self.hub_status_timer = QTimer(self)
        self.hub_status_timer.setInterval(10000)
        self.hub_status_timer.timeout.connect(self._on_hub_status_tick)
        self.hub_status_timer.start()

        QTimer.singleShot(5000, self._schedule_auto_update_check)

    def _on_hub_status_tick(self) -> None:
        if self.registered_pcs:
            self._refresh_hub_pc_status()
            self._rebuild_pc_list()

    # -------------------------------------------------------------------
    # Auto update
    # -------------------------------------------------------------------

    def _show_settings_menu(self) -> None:
        QMessageBox.information(
            self,
            "설정",
            f"버전: {APP_VERSION}\n"
            f"Hub: {os.getenv('KAKAO_REMOTE_HUB_URL', '(미설정)')}\n"
            f"자동 업데이트: "
            f"{'OFF' if os.getenv('KAKAO_SENDER_DISABLE_AUTO_UPDATE') else 'ON'}\n\n"
            "원격 Hub:\n"
            "  python tools\\v3_spike\\kakao_remote_hub.py\n"
            "원격 Agent:\n"
            "  python tools\\v3_spike\\kakao_remote_agent.py",
        )

    def _schedule_auto_update_check(self) -> None:
        if os.getenv("KAKAO_SENDER_DISABLE_AUTO_UPDATE", "").strip().lower() in (
            "1", "true", "yes", "on",
        ):
            return
        self._bg_check_update(show_no_update=False)

    def _bg_check_update(self, show_no_update: bool = False) -> None:
        if self._update_in_progress:
            return

        def _worker() -> None:
            try:
                sys.path.insert(0, str(ROOT_DIR))
                from advanced_updater import AdvancedUpdater

                ver = APP_VERSION.lstrip("v")
                updater = AdvancedUpdater(current_version=ver)
                info = updater.check_for_updates()
                err = info.get("error", "")
                if err and show_no_update:
                    QTimer.singleShot(0, lambda: self.set_status(f"업데이트 확인: {err}"))
                    return
                if info.get("update_available") and info.get("download_url"):
                    latest = info.get("latest_version", "?")
                    QTimer.singleShot(
                        0,
                        lambda: self._prompt_apply_update(updater, info, latest),
                    )
                elif show_no_update:
                    QTimer.singleShot(0, lambda: self.set_status(f"최신 버전 ({APP_VERSION})"))
            except Exception as exc:
                log.debug("Update check failed: %s", exc)

        threading.Thread(target=_worker, daemon=True).start()

    def _check_updates_manual(self) -> None:
        self.set_status("업데이트 확인 중…")
        self._bg_check_update(show_no_update=True)

    def _prompt_apply_update(self, updater, info: dict, latest: str) -> None:
        reply = QMessageBox.question(
            self,
            "업데이트",
            f"새 버전 v{latest} (현재 {APP_VERSION})\n지금 다운로드·적용할까요?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._apply_update_async(updater, info)

    def _apply_update_async(self, updater, info: dict) -> None:
        if self._update_in_progress:
            return
        self._update_in_progress = True
        self.set_status("업데이트 다운로드 중…")

        def _worker() -> None:
            try:
                dl = info["download_url"]
                path = updater.download_update(dl)
                if path:
                    updater.apply_update_with_backup(path, restart_instance_count=1)
            except Exception as exc:
                QTimer.singleShot(0, lambda: self.set_status(f"업데이트 실패: {exc}"))
            finally:
                self._update_in_progress = False

        threading.Thread(target=_worker, daemon=True).start()

    # -------------------------------------------------------------------
    # Layout helpers
    # -------------------------------------------------------------------

    def _clear_layout(self, layout: QVBoxLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    # -------------------------------------------------------------------
    # PC list
    # -------------------------------------------------------------------

    def _is_local_pc_active(self) -> bool:
        return self.active_pc_id == LOCAL_PC_ID

    def _get_remote_client(self) -> RemoteKakaoClient | None:
        if self._is_local_pc_active():
            return None
        if self.remote_client is not None:
            return self.remote_client
        pc = next((p for p in self.registered_pcs if p["id"] == self.active_pc_id), None)
        if pc is None:
            return None
        client = RemoteKakaoClient.from_pc_record(pc)
        self.remote_client = client
        return client

    def _refresh_hub_pc_status(self) -> None:
        hub = (os.getenv("KAKAO_REMOTE_HUB_URL") or "").strip()
        token = (os.getenv("KAKAO_REMOTE_TOKEN") or "").strip()
        if not hub or not token:
            return
        try:
            probe = RemoteKakaoClient(hub, token, "probe")
            for row in probe.list_pcs():
                pid = row.get("pc_id", "")
                if pid:
                    self._hub_pc_status[pid] = row
        except Exception:
            log.debug("Hub PC list failed:\n%s", traceback.format_exc())

    def _remote_hwnd(self) -> int | None:
        client = self._get_remote_client()
        if client is None:
            return self.selected_hwnd
        status = client.get_pc_status() or self._hub_pc_status.get(client.remote_pc_id, {})
        hwnd = status.get("selected_hwnd") or self.selected_hwnd
        return int(hwnd) if hwnd else None

    def _rebuild_pc_list(self, local_status: str = "온라인", local_badge: int = 0) -> None:
        self._refresh_hub_pc_status()
        while self.pc_list_container.count():
            item = self.pc_list_container.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._pc_row_widgets.clear()

        local_row = PcRowWidget(
            self.pc_name,
            local_status,
            local_badge,
            selected=(self.active_pc_id == LOCAL_PC_ID),
        )
        local_row.clicked.connect(lambda: self._select_pc(LOCAL_PC_ID))
        self.pc_list_container.addWidget(local_row)
        self._pc_row_widgets[LOCAL_PC_ID] = local_row

        for pc in self.registered_pcs:
            pc_id = pc["id"]
            remote_id = pc.get("remote_pc_id", pc_id)
            hub_row = self._hub_pc_status.get(remote_id, {})
            status = "온라인" if hub_row.get("status") == "online" else "오프라인"
            row = PcRowWidget(
                pc["name"],
                status,
                0,
                selected=(self.active_pc_id == pc_id),
            )
            row.clicked.connect(lambda pid=pc_id: self._select_pc(pid))
            self.pc_list_container.addWidget(row)
            self._pc_row_widgets[pc_id] = row

    def _select_pc(self, pc_id: str) -> None:
        self.active_pc_id = pc_id
        self.remote_client = None
        for pid, row in self._pc_row_widgets.items():
            row.set_selected(pid == pc_id)
        if pc_id == LOCAL_PC_ID:
            self.header_label.setText(self.pc_name)
            self._update_status_strip_pc()
            self.refresh_candidates()
            return
        pc = next((p for p in self.registered_pcs if p["id"] == pc_id), None)
        name = pc["name"] if pc else "원격 PC"
        host = pc.get("host", "") if pc else ""
        self.header_label.setText(name)
        self._update_status_strip_pc()
        client = RemoteKakaoClient.from_pc_record(pc) if pc else None
        self.remote_client = client
        if client is None:
            self.set_status("원격 연결 실패 — Hub URL·KAKAO_REMOTE_TOKEN 확인", is_error=True)
            self._show_remote_pc_placeholder(name, host, "Hub URL 또는 토큰 미설정")
            return
        try:
            status = client.get_pc_status()
            if status:
                self.selected_hwnd = status.get("selected_hwnd")
                self.selected_title = status.get("selected_title", "")
                self.main_hwnd = status.get("main_hwnd")
            self.set_status(f"원격 PC · {name} ({status.get('status', '?') if status else '연결 중'})")
            client.refresh_candidates()
            self.refresh_remote_preview()
        except Exception as exc:
            log.error("Remote select failed: %s", exc)
            self.set_status(f"원격 연결 오류 · {exc}", is_error=True)
            self._show_remote_pc_placeholder(name, host, str(exc))

    def _show_remote_pc_placeholder(self, name: str, host: str, detail: str = "") -> None:
        text = (
            f"등록된 PC: {name}\n"
            f"호스트: {host or '(미지정)'}\n\n"
            f"{detail or '원격 Agent가 Hub에 연결되면 화면이 표시됩니다.'}\n"
            "원격 PC에서: python tools\\v3_spike\\kakao_remote_agent.py"
        )
        self.preview_label.setText(text)
        self.preview_label.setPixmap(QPixmap())

    def refresh_remote_preview(self) -> None:
        client = self._get_remote_client()
        if client is None:
            return
        try:
            image = client.get_frame_image()
            if image is None:
                self.preview_label.setText("원격 프레임 대기 중… (Agent 실행 확인)")
                return
            self.last_image = image
            cropped = crop_native_input_area(image, self.crop_spin.value())
            pixmap = pil_to_pixmap(cropped)
            scaled = pixmap.scaled(
                self.preview_label.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.preview_label.set_source_size(image.size)
            self.preview_label.setPixmap(scaled)
        except Exception as exc:
            log.error("Remote preview failed: %s\n%s", exc, traceback.format_exc())
            self.preview_label.setText(f"원격 갱신 실패: {exc}")

    def _show_help_dialog(self) -> None:
        QMessageBox.information(
            self,
            "카카오 매니저 사용법",
            "【작업】\n"
            "· 좌측 「열린 톡방」으로 방을 바꿉니다.\n"
            "· 미리보기 클릭·스크롤은 현재 선택된 창에 전달됩니다.\n"
            "· 더블클릭 전달은 기본 꺼짐 (잘못된 방 열림 방지).\n"
            "· 하단·작업 탭 입력창 → 선택 방에 반영, Enter 전송.\n\n"
            "【톡방 관리 / 발송·고급】\n"
            "· 닫힌 방·예약·AI·카카오 목록은 보조 탭입니다.\n\n"
            "【PC 등록 / 원격】\n"
            "· Hub: python tools\\v3_spike\\kakao_remote_hub.py\n"
            "· 원격 PC Agent: kakao_remote_agent.py (KAKAO_REMOTE_* env)\n"
            "· PC 등록 시 Hub URL·원격 PC ID 입력\n\n"
            "【자동 업데이트】\n"
            "· 상단 '업데이트' 또는 시작 후 주기 확인 (onedir zip)\n"
            "· KAKAO_SENDER_DISABLE_AUTO_UPDATE=1 로 비활성\n\n"
            "【파일 첨부】\n"
            "· 카카오톡에서 파일 첨부 시 탐색기 창이 자동으로 미리보기에 표시됩니다.\n\n"
            "【주의】\n"
            "· 기본은 모의 보내기입니다.\n"
            "· 상단 「실제 보내기」 체크로 전환 (확인 후 노란 경고 표시).\n"
            "· exe를 --allow-send 로 실행하면 시작 시 실제 보내기 ON.",
        )

    def _instance_display_name(self, pid: int, inst: dict | None = None) -> str:
        key = str(pid)
        if key in self.instance_names:
            return self.instance_names[key]
        if inst is None:
            inst = self.kakao_instances.get(pid) or {}
        title = str(inst.get("main_title") or "")
        if title and title.lower() not in ("카카오톡", "kakaotalk", ""):
            return title
        return f"세션 {pid}"

    def _rebuild_session_cards(self) -> None:
        self._clear_layout(self.sessions_container)
        if not self.kakao_instances:
            empty = QLabel("실행 중인 세션 없음")
            empty.setStyleSheet(
                f"color: {COLORS['muted']}; font-size: 12px; padding: 6px 4px;"
            )
            self.sessions_container.addWidget(empty)
        else:
            for pid in sorted(self.kakao_instances.keys()):
                inst = self.kakao_instances[pid]
                name = self._instance_display_name(pid, inst)
                chat_count = len(inst.get("chats") or [])
                selected = pid == self.active_instance_pid
                card = SessionCardWidget(
                    name, chat_count, selected=selected, online=True
                )
                card.clicked.connect(lambda p=pid: self._select_instance(p))
                card.close_clicked.connect(lambda p=pid: self._close_kakao_instance(p))
                card.rename_requested.connect(lambda p=pid: self._rename_instance(p))
                self.sessions_container.addWidget(card)
                self._session_card_widgets[pid] = card
        self.sessions_container.addStretch()

    def _select_instance(self, pid: int) -> None:
        self.active_instance_pid = pid
        inst = self.kakao_instances.get(pid)
        if not inst:
            self._rebuild_session_cards()
            self._update_status_strip_session()
            return
        hwnd = inst.get("main_hwnd") or inst.get("any_hwnd")
        if hwnd:
            self._select_kakao_window(hwnd)
        elif inst.get("chats"):
            h, t = inst["chats"][0]
            self.set_selected_room(h, t, focus_work_tab=True)
        else:
            self._rebuild_session_cards()
            self._update_status_strip_session()

    def _rename_instance(self, pid: int) -> None:
        current = self._instance_display_name(pid)
        name, ok = QInputDialog.getText(
            self, "세션 이름", "별명을 입력하세요:", text=current
        )
        if ok and name.strip():
            self.instance_names[str(pid)] = name.strip()
            self._queue_save_workspace_settings()
            self._rebuild_session_cards()
            self._rebuild_left_rooms()
            self._update_status_strip_session()

    def _close_kakao_instance(self, pid: int) -> None:
        display = self._instance_display_name(pid)
        reply = QMessageBox.question(
            self,
            "세션 종료",
            f"「{display}」 카카오톡을 종료할까요?\n(PID {pid})",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        ok, msg = terminate_kakao_instance(pid)
        if ok:
            self.instance_names.pop(str(pid), None)
            if self.active_instance_pid == pid:
                self.active_instance_pid = None
            self.set_status(msg)
            self.refresh_candidates()
        else:
            self.set_status(msg, is_error=True)
            QMessageBox.warning(self, "세션 종료", msg)

    def _pick_kakao_exe_path(self) -> bool:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "KakaoTalk.exe 선택",
            str(find_kakao_exe(self.kakao_exe_path or None) or ""),
            "Executable (*.exe)",
        )
        if not path:
            return False
        self.kakao_exe_path = path
        self._queue_save_workspace_settings()
        return True

    def _add_kakao_instance_one_click(self) -> None:
        self.set_status("카카오톡 추가 중… (세마포어 해제 → 실행)")
        QApplication.processEvents()
        ok, msg, new_pid = launch_new_instance(self.kakao_exe_path or None)
        if ok and not self.kakao_exe_path:
            found = find_kakao_exe(None)
            if found:
                self.kakao_exe_path = str(found)
                self._queue_save_workspace_settings()
        if not ok:
            self.set_status(msg, is_error=True)
            if "찾을 수 없습니다" in msg:
                if self._pick_kakao_exe_path():
                    self._add_kakao_instance_one_click()
            else:
                QMessageBox.warning(self, "카카오톡 추가", msg)
            return
        self._pending_launch_pid = new_pid
        self.set_status(msg)
        QTimer.singleShot(2000, self._after_kakao_launch)

    def _after_kakao_launch(self) -> None:
        self.refresh_candidates()
        pid = self._pending_launch_pid
        if pid and pid in self.kakao_instances:
            self._select_instance(pid)
        elif self.kakao_instances:
            newest = max(self.kakao_instances.keys())
            self._select_instance(newest)
        self._pending_launch_pid = None

    def _get_local_ip(self) -> str:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "127.0.0.1"

    def _quick_register_this_pc(self) -> None:
        ip = self._get_local_ip()
        name = self.pc_name
        p2p_port = int(os.getenv("KAKAO_P2P_PORT", "8765"))
        hub_url = f"http://{ip}:{p2p_port}"
        pc_id = os.getenv("KAKAO_REMOTE_PC_ID", "") or name
        token = os.getenv("KAKAO_REMOTE_TOKEN", "").strip()

        if not token:
            token = str(uuid.uuid4())[:8]
            os.environ["KAKAO_REMOTE_TOKEN"] = token

        if not os.getenv("KAKAO_P2P_PORT"):
            os.environ["KAKAO_P2P_PORT"] = str(p2p_port)

        already = any(
            p.get("remote_pc_id") == pc_id or p.get("host") == ip
            for p in self.registered_pcs
        )
        if already:
            self.set_status(f"이미 등록됨 · {name} ({ip})")
            return

        data = {
            "id": str(uuid.uuid4()),
            "name": name,
            "host": ip,
            "hub_url": hub_url,
            "remote_pc_id": pc_id,
            "note": "자동 등록",
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
        self.registered_pcs.append(data)
        save_registered_pcs(self.registered_pcs)
        log.info("Quick-registered PC: %s ip=%s hub=%s", name, ip, hub_url)
        self._rebuild_pc_list()

        if not self._embedded_hub:
            self._start_embedded_p2p()

        self.set_status(f"등록 완료 · {name} ({ip}:{p2p_port})")
        QMessageBox.information(
            self,
            "PC 등록 완료",
            f"이 PC가 등록되었습니다.\n\n"
            f"이름: {name}\n"
            f"IP: {ip}\n"
            f"Hub: {hub_url}\n"
            f"PC ID: {pc_id}\n"
            f"토큰: {token}\n\n"
            f"다른 PC에서 연결하려면:\n"
            f"  PC 등록 → Hub URL에 {hub_url} 입력\n"
            f"  또는 .env에:\n"
            f"  KAKAO_REMOTE_HUB_URL={hub_url}\n"
            f"  KAKAO_REMOTE_TOKEN={token}",
        )

    def _show_pc_register_dialog(self) -> None:
        dialog = PcRegisterDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        data = dialog.pc_data()
        if not data:
            QMessageBox.warning(self, "입력 필요", "PC 이름을 입력하세요.")
            return
        self.registered_pcs.append(data)
        save_registered_pcs(self.registered_pcs)
        log.info("Registered PC: %s", data["name"])
        self._rebuild_pc_list()
        self._select_pc(data["id"])
        self.set_status(f"PC 등록 완료 · {data['name']} (Hub: {data.get('hub_url', '')})")

    # -------------------------------------------------------------------
    # Candidate refresh & room list build
    # -------------------------------------------------------------------

    def refresh_candidates(self) -> None:
        if not self._is_local_pc_active():
            client = self._get_remote_client()
            if client is None:
                return
            try:
                client.refresh_candidates()
                items = client.get_candidates()
                self.chat_rooms = [
                    (int(c["hwnd"]), c.get("title", ""))
                    for c in items
                    if c.get("kind") == "chat"
                ]
                for c in items:
                    if c.get("kind") == "main":
                        self.main_hwnd = int(c["hwnd"])
                    if c.get("kind") == "chat" and self.selected_hwnd is None:
                        self.selected_hwnd = int(c["hwnd"])
                        self.selected_title = c.get("title", "")
                self._rebuild_pc_list()
                self._rebuild_room_lists()
                self.refresh_remote_preview()
                self.set_status(f"원격 목록 갱신 · 톡방 {len(self.chat_rooms)}개")
            except Exception as exc:
                log.error("Remote refresh_candidates: %s", exc)
                self.set_status(f"원격 목록 실패 · {exc}")
            return
        log.info("Refreshing candidates...")
        try:
            instance_groups = capture.list_candidates_by_instance()
        except Exception:
            log.error("list_candidates failed:\n%s", traceback.format_exc())
            self.set_status("카카오톡 창 감지 실패")
            return

        candidates: list = []
        for items in instance_groups.values():
            candidates.extend(items)

        self.kakao_instances = {}
        for pid, items in instance_groups.items():
            inst_mains = [i for i in items if i[1] == "main"]
            inst_chats = [(i[0], i[2]) for i in items if i[1] == "chat"]
            inst_others = [i for i in items if i[1] == "kakao_other"]
            main_title = inst_mains[0][2] if inst_mains else (
                inst_others[0][2] if inst_others else f"세션 {pid}"
            )
            self.kakao_instances[pid] = {
                "main_hwnd": inst_mains[0][0] if inst_mains else None,
                "main_title": main_title,
                "chats": inst_chats,
                "any_hwnd": (
                    inst_mains[0][0] if inst_mains
                    else inst_others[0][0] if inst_others
                    else None
                ),
            }

        num_instances = len(self.kakao_instances)
        if self.active_instance_pid not in self.kakao_instances:
            if self._pending_launch_pid and self._pending_launch_pid in self.kakao_instances:
                self.active_instance_pid = self._pending_launch_pid
            elif self.kakao_instances:
                self.active_instance_pid = next(iter(self.kakao_instances))
            else:
                self.active_instance_pid = None

        chats = [(hwnd, title) for hwnd, kind, title, _rect, _cls in candidates if kind == "chat"]
        mains = [item for item in candidates if item[1] == "main"]
        others = [item for item in candidates if item[1] == "kakao_other"]
        dialogs = [(hwnd, title) for hwnd, kind, title, _rect, _cls in candidates if kind == "dialog"]
        self.main_hwnd = mains[0][0] if mains else None
        self.active_dialog_hwnd = dialogs[0][0] if dialogs else None
        self.kakao_any_hwnd = (
            mains[0][0] if mains
            else others[0][0] if others
            else None
        )
        self.chat_rooms = chats

        open_titles = {title for _, title in chats}
        for _, title in chats:
            self.known_rooms[title] = True
        self._queue_save_workspace_settings()
        self.unregistered_rooms = [
            (0, title) for title in self.known_rooms if title not in open_titles
        ]

        badge = len(chats)
        kakao_found = bool(mains or others)
        kakao_status = "로그인 중" if (others and not mains) else ("실행 중" if mains else "꺼짐")
        online = "온라인" if kakao_found else "오프라인"
        inst_note = f" · {num_instances}세션" if num_instances > 1 else ""
        self._rebuild_pc_list(
            local_status=online,
            local_badge=badge,
        )
        self.summary_label.setText(
            f"{online} · 카카오 {kakao_status} · 열린 톡방 {badge}{inst_note}"
        )
        self.header_label.setText(self.pc_name)
        self._update_status_strip_pc()

        log.info(
            "Found %d chat(s), %d main(s), %d other(s), %d dialog(s), %d known room(s), %d instance(s)",
            len(chats), len(mains), len(others), len(dialogs), len(self.known_rooms),
            num_instances,
        )

        if self.selected_hwnd is not None:
            still_open = (
                any(h == self.selected_hwnd for h, _ in chats)
                or self.selected_hwnd == self.kakao_any_hwnd
                or self.selected_hwnd == self.active_dialog_hwnd
            )
            if not still_open:
                log.warning("Selected hwnd %d is gone, clearing selection", self.selected_hwnd)
                self.selected_hwnd = None
                self.selected_title = ""
                self._viewing_dialog = False
                self._update_input_target_labels()

        self._rebuild_session_cards()
        self._update_status_strip_session()
        self._rebuild_room_lists()
        self._rebuild_left_rooms()
        self._apply_dialog_priority(dialogs)

        if self.selected_hwnd is None:
            if chats:
                self.set_selected_room(chats[0][0], chats[0][1])
            elif self.kakao_any_hwnd is not None:
                self._select_kakao_window(self.kakao_any_hwnd)
            else:
                self.set_status("카카오톡이 실행되지 않았습니다.")
        self.refresh_main_preview()

    def _rebuild_room_lists(self) -> None:
        self._clear_layout(self.registered_container)
        self._clear_layout(self.unregistered_container)

        now_str = datetime.now().strftime("%H:%M")
        for hwnd, title in self.chat_rooms:
            preview = self._room_preview_text(title)
            row = RoomRowWidget(
                title=title,
                preview=preview,
                time_text=now_str,
                selected=(hwnd == self.selected_hwnd),
                show_close=True,
            )
            row.open_clicked.connect(lambda h=hwnd, t=title: self.set_selected_room(h, t))
            row.close_clicked.connect(lambda h=hwnd, t=title: self.close_chat_window(h, t))
            self.registered_container.addWidget(row)

        if not self.unregistered_rooms:
            empty = QLabel("닫힌 톡방 없음")
            empty.setStyleSheet(f"color: {COLORS['muted']}; font-size: 13px; padding: 8px;")
            self.unregistered_container.addWidget(empty)
        else:
            for _hwnd, title in self.unregistered_rooms:
                row = RoomRowWidget(
                    title=title,
                    preview="닫힘 · 카카오 목록에서 열기",
                    time_text="",
                    tags=["닫힘"],
                    show_register=False,
                    show_close=False,
                )
                row.open_clicked.connect(
                    lambda t=title: self._request_open_closed_room(t)
                )
                self.unregistered_container.addWidget(row)

    def _rebuild_left_rooms(self) -> None:
        self._clear_layout(self.left_rooms_container)
        num_instances = len(self.kakao_instances)
        if num_instances <= 1:
            for hwnd, title in self.chat_rooms:
                row = LeftRoomRow(title, selected=(hwnd == self.selected_hwnd))
                row.select_clicked.connect(
                    lambda h=hwnd, t=title: self.set_selected_room(h, t, focus_work_tab=True)
                )
                row.close_clicked.connect(lambda h=hwnd, t=title: self.close_chat_window(h, t))
                self.left_rooms_container.addWidget(row)
        else:
            for pid, inst in self.kakao_instances.items():
                session_name = self._instance_display_name(pid, inst)
                session_chats = inst.get("chats", [])
                header = QLabel(f"▸ {session_name} ({len(session_chats)})")
                header.setStyleSheet(
                    f"color: {COLORS['accent']}; font-size: 12px; "
                    "font-weight: 800; padding: 4px 2px 2px;"
                )
                header.setCursor(Qt.CursorShape.PointingHandCursor)
                main_hwnd = inst.get("main_hwnd") or inst.get("any_hwnd")
                if main_hwnd:
                    header.mousePressEvent = (
                        lambda ev, h=main_hwnd, t=session_name: self._select_kakao_window(h)
                    )
                self.left_rooms_container.addWidget(header)
                for hwnd, title in session_chats:
                    row = LeftRoomRow(title, selected=(hwnd == self.selected_hwnd))
                    row.select_clicked.connect(
                        lambda h=hwnd, t=title: self.set_selected_room(h, t, focus_work_tab=True)
                    )
                    row.close_clicked.connect(
                        lambda h=hwnd, t=title: self.close_chat_window(h, t)
                    )
                    self.left_rooms_container.addWidget(row)
        self.left_rooms_container.addStretch()

    def _room_preview_text(self, title: str) -> str:
        active = [r for r in self.reservations if r["room"] == title]
        if not active:
            return "열림"
        soonest = min(active, key=lambda item: item["send_at"])
        seconds = max(0, int((soonest["send_at"] - datetime.now()).total_seconds()))
        m, s = divmod(seconds, 60)
        return f"⏱ 예약 {m:02d}:{s:02d} · {soonest['message'][:36]}"

    # -------------------------------------------------------------------
    # Room selection & status
    # -------------------------------------------------------------------

    def _apply_dialog_priority(self, dialogs: list[tuple[int, str]]) -> None:
        if dialogs:
            dlg_hwnd, dlg_title = dialogs[0]
            if not self._viewing_dialog:
                if self.selected_hwnd and any(
                    h == self.selected_hwnd for h, _ in self.chat_rooms
                ):
                    self._restore_hwnd = self.selected_hwnd
                    self._restore_title = self.selected_title
                    log.info(
                        "Saving restore target before dialog: hwnd=%d title=%r",
                        self._restore_hwnd, self._restore_title,
                    )
            if self.selected_hwnd != dlg_hwnd:
                self._select_dialog(dlg_hwnd, dlg_title)
            return

        if self._viewing_dialog:
            self._viewing_dialog = False
            if self._restore_hwnd and capture.is_window_valid(self._restore_hwnd):
                log.info(
                    "Dialog closed, restoring chat: hwnd=%d title=%r",
                    self._restore_hwnd, self._restore_title,
                )
                self.set_selected_room(self._restore_hwnd, self._restore_title)
            elif self.chat_rooms:
                h, t = self.chat_rooms[0]
                self.set_selected_room(h, t)
            elif self.kakao_any_hwnd:
                self._select_kakao_window(self.kakao_any_hwnd)
            self._restore_hwnd = None
            self._restore_title = ""

    def _sync_dialog_focus(self) -> None:
        try:
            candidates = capture.list_candidates()
        except Exception:
            return
        dialogs = [(hwnd, title) for hwnd, kind, title, _r, _c in candidates if kind == "dialog"]
        self.active_dialog_hwnd = dialogs[0][0] if dialogs else None
        self._apply_dialog_priority(dialogs)

    def _select_dialog(self, hwnd: int, title: str) -> None:
        log.info("Selecting dialog: hwnd=%d title=%r", hwnd, title)
        self._viewing_dialog = True
        self.selected_hwnd = hwnd
        self.selected_title = title
        self.header_label.setText(title)
        self._refresh_error_count = 0
        self.set_status(f"파일 선택 중 · {title}")
        self._update_input_target_labels()
        self._rebuild_room_lists()
        self._rebuild_left_rooms()
        self.refresh_preview()

    def _select_kakao_window(self, hwnd: int) -> None:
        title = "카카오톡"
        try:
            import win32gui
            t = win32gui.GetWindowText(hwnd)
            if t:
                title = t
        except Exception:
            pass
        log.info("Selecting kakao window (non-chat): hwnd=%d title=%r", hwnd, title)
        self.selected_hwnd = hwnd
        self.selected_title = title
        pid = get_pid_for_hwnd(hwnd)
        if pid:
            self.active_instance_pid = pid
        self.header_label.setText(title)
        self._refresh_error_count = 0
        self.set_status(f"카카오톡 화면 표시 중 · {title}")
        self._update_input_target_labels()
        self._rebuild_session_cards()
        self._update_status_strip_session()
        self._rebuild_room_lists()
        self._rebuild_left_rooms()
        self.refresh_preview()

    def _update_input_target_labels(self) -> None:
        if self.selected_title:
            short = (
                self.selected_title
                if len(self.selected_title) <= 22
                else self.selected_title[:20] + "…"
            )
            self.work_target_label.setText(f"선택: {self.selected_title}")
            self.send_target_label.setText(f"「{short}」에 입력 중")
            self.screen_input.setPlaceholderText(
                f"「{short}」 — 메시지 (Enter 전송)"
            )
            self.message_edit.setPlaceholderText(f"「{short}」에 보낼 메시지")
        else:
            self.work_target_label.setText("선택: (없음)")
            self.send_target_label.setText("톡방을 선택하세요")
            self.screen_input.setPlaceholderText(
                "톡방 선택 후 메시지 입력 (Enter 전송)"
            )
            self.message_edit.setPlaceholderText("메시지를 입력하세요")
        self._update_status_strip_room()

    def set_selected_room(
        self, hwnd: int, title: str, *, focus_work_tab: bool = False
    ) -> None:
        log.info("Selecting room: hwnd=%d title=%r", hwnd, title)
        self._viewing_dialog = False
        self.selected_hwnd = hwnd
        self.selected_title = title
        pid = get_pid_for_hwnd(hwnd)
        if pid:
            self.active_instance_pid = pid
        self.header_label.setText(title)
        self._refresh_error_count = 0
        self.set_status(f"온라인 · 선택: {title}")
        self._update_input_target_labels()
        self._update_status_strip_room()
        self._rebuild_session_cards()
        self._update_status_strip_session()
        if focus_work_tab:
            self.tabs.setCurrentIndex(0)
        if any(h == hwnd for h, _ in self.chat_rooms):
            try:
                capture.bring_window_to_front(hwnd)
            except Exception:
                log.debug("bring_window_to_front failed", exc_info=True)
        self._rebuild_room_lists()
        self._rebuild_left_rooms()
        self.refresh_preview()

    # -------------------------------------------------------------------
    # Settings persistence (A)
    # -------------------------------------------------------------------

    def _apply_loaded_settings(self) -> None:
        ui = self._loaded_settings.get("ui") or {}
        ai = self._loaded_settings.get("ai") or {}
        self.auto_refresh_checkbox.setChecked(ui.get("auto_refresh", True))
        self.forward_click_checkbox.setChecked(ui.get("forward_click", True))
        self.forward_double_click_checkbox.setChecked(
            ui.get("forward_double_click", False)
        )
        self.background_input_checkbox.setChecked(ui.get("background_input", True))
        self.crop_spin.setValue(int(ui.get("hide_native_input_px", self.hide_native_input_px)))
        self.timer.setInterval(self.interval_ms)
        self.ai_mode_combo.setCurrentIndex(int(ai.get("mode_index", 1)))
        self.ai_level_spin.setValue(int(ai.get("level", 3)))
        self.ai_role_edit.setPlainText(ai.get("role", ""))
        self.ai_prompt_edit.setPlainText(ai.get("prompt", ""))
        self.ai_examples_edit.setPlainText(ai.get("examples", ""))
        self.ai_sources_edit.setPlainText(ai.get("sources", ""))
        self.ai_chat_context_edit.setPlainText(ai.get("chat_context", ""))
        self.ai_guardrails_edit.setPlainText(ai.get("guardrails", ""))
        if self.reservations:
            self.refresh_reservation_views()

    def _queue_save_workspace_settings(self, *_args) -> None:
        self._save_settings_timer.start()

    def _save_workspace_settings_now(self) -> None:
        ui = {
            "auto_refresh": self.auto_refresh_checkbox.isChecked(),
            "forward_click": self.forward_click_checkbox.isChecked(),
            "forward_double_click": self.forward_double_click_checkbox.isChecked(),
            "background_input": self.background_input_checkbox.isChecked(),
            "hide_native_input_px": self.crop_spin.value(),
            "interval_ms": self.timer.interval(),
        }
        ai = {
            "mode_index": self.ai_mode_combo.currentIndex(),
            "level": self.ai_level_spin.value(),
            "role": self.ai_role_edit.toPlainText(),
            "prompt": self.ai_prompt_edit.toPlainText(),
            "examples": self.ai_examples_edit.toPlainText(),
            "sources": self.ai_sources_edit.toPlainText(),
            "chat_context": self.ai_chat_context_edit.toPlainText(),
            "guardrails": self.ai_guardrails_edit.toPlainText(),
        }
        save_workspace_settings(
            ui=ui,
            ai=ai,
            known_room_titles=sorted(self.known_rooms.keys()),
            reservations=self.reservations,
            instance_names=self.instance_names,
            kakao_exe_path=self.kakao_exe_path,
        )

    def _verify_main_list_click(self, rooms_before: int) -> None:
        after = len(self.chat_rooms)
        if after > rooms_before:
            self.set_status(f"목록 클릭 확인 · 열린 톡방 {after}개 (+{after - rooms_before})")
        else:
            self.set_status(
                "목록 클릭 — 새 톡방이 안 열린 것 같습니다. 위치를 다시 클릭하세요.",
                is_error=True,
            )

    def _verify_preview_click(self, label: str) -> None:
        if self.selected_hwnd is None:
            self.set_status(f"{label} — 선택된 창 없음", is_error=True)

    # -------------------------------------------------------------------
    # Status
    # -------------------------------------------------------------------

    def set_status(self, message: str, *, is_error: bool | None = None) -> None:
        self.summary_label.setText(message)
        short = message if len(message) <= 72 else message[:70] + "…"
        self.status_action_label.setText(short)
        self.status_action_label.setStyleSheet(
            f"color: {COLORS['danger'] if is_error else COLORS['text']}; "
            "font-size: 12px; font-weight: 700;"
        )
        if is_error is None:
            is_error = any(k in message for k in ("실패", "오류", "ERROR"))
        if is_error:
            self._set_log_error_indicator(True)
        self._status_action_timer.stop()
        self._status_action_timer.start(8000)

    # -------------------------------------------------------------------
    # Preview refresh
    # -------------------------------------------------------------------

    def refresh_preview_if_needed(self) -> None:
        if not self._is_local_pc_active():
            if self.auto_refresh_checkbox.isChecked():
                self.refresh_remote_preview()
            return
        self._sync_dialog_focus()
        if self.auto_refresh_checkbox.isChecked():
            self.refresh_preview()

    def refresh_preview(self) -> None:
        if not self._is_local_pc_active():
            return
        if self.selected_hwnd is None:
            if self.kakao_any_hwnd is not None:
                self._select_kakao_window(self.kakao_any_hwnd)
            return

        if not capture.is_window_valid(self.selected_hwnd):
            if capture.is_window_minimized(self.selected_hwnd):
                log.info("hwnd %d (%s) is minimized, removing from active list",
                         self.selected_hwnd, self.selected_title)
            else:
                log.warning("hwnd %d (%s) is no longer valid",
                            self.selected_hwnd, self.selected_title)
            self.selected_hwnd = None
            self.selected_title = ""
            self._update_input_target_labels()
            QTimer.singleShot(200, self.refresh_candidates)
            return

        is_chat = (
            any(h == self.selected_hwnd for h, _ in self.chat_rooms)
            and not self._viewing_dialog
        )
        crop_px = self.crop_spin.value() if is_chat else 0

        try:
            image = capture.capture_with_printwindow(self.selected_hwnd)
            self.last_image = image
            self._refresh_error_count = 0
            cropped = crop_native_input_area(image, crop_px)
            pixmap = pil_to_pixmap(cropped)
            scaled = pixmap.scaled(
                self.preview_label.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.preview_label.set_source_size(cropped.size)
            self.preview_label.setPixmap(scaled)
            ts = datetime.now().strftime("%H:%M:%S")
            self.room_preview_status.setText(f"마지막 갱신 {ts}")
        except Exception as exc:
            self._refresh_error_count += 1
            log.error(
                "Preview refresh failed (count=%d, hwnd=%d, title=%r): %s\n%s",
                self._refresh_error_count, self.selected_hwnd, self.selected_title,
                exc, traceback.format_exc(),
            )
            if self._refresh_error_count >= 3:
                log.warning("Too many errors, resetting selection and refreshing")
                self.selected_hwnd = None
                self.selected_title = ""
                self._refresh_error_count = 0
                QTimer.singleShot(500, self.refresh_candidates)
            else:
                self.set_status(f"갱신 실패 ({self._refresh_error_count}/3) · {exc}")

    def refresh_main_preview(self) -> None:
        if self.main_hwnd is None:
            self.main_preview_label.setText("카카오톡 메인 창 없음")
            return
        if not capture.is_window_valid(self.main_hwnd):
            log.warning("Main hwnd %d is invalid", self.main_hwnd)
            self.main_hwnd = None
            self.main_preview_label.setText("카카오톡 메인 창이 닫혔습니다")
            return
        try:
            image = capture.capture_with_printwindow(self.main_hwnd)
            self.last_main_image = image
            pixmap = pil_to_pixmap(image)
            scaled = pixmap.scaled(
                self.main_preview_label.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.main_preview_label.set_source_size(image.size)
            self.main_preview_label.setPixmap(scaled)
        except Exception as exc:
            log.error("Main preview failed: %s\n%s", exc, traceback.format_exc())
            self.main_preview_label.setText(f"목록 갱신 실패: {exc}")

    # -------------------------------------------------------------------
    # Click / wheel forwarding
    # -------------------------------------------------------------------

    def _on_preview_mouse_move(self, image_x: int, image_y: int) -> None:
        self.cursor_pos_label.setText(f"마우스: ({image_x}, {image_y})")

    def handle_preview_drag(self, sx: int, sy: int, ex: int, ey: int) -> None:
        if not self.forward_click_checkbox.isChecked():
            return
        if not self._is_local_pc_active():
            return
        if self.selected_hwnd is None:
            return
        try:
            detail = post_background_drag(
                self.selected_hwnd, sx, sy, ex, ey, self.image_size_for_forwarding()
            )
            log.debug("Preview drag: %s", detail)
            self.set_status(f"드래그 전달 · {detail}")
            QTimer.singleShot(400, self.refresh_preview)
        except Exception as exc:
            log.error("Preview drag failed: %s\n%s", exc, traceback.format_exc())
            QMessageBox.warning(self, "드래그 전달 실패", str(exc))

    def image_size_for_forwarding(self) -> tuple[int, int]:
        if self.last_image is None:
            return (1, 1)
        return (self.last_image.width, max(1, self.last_image.height - self.crop_spin.value()))

    def handle_preview_click(self, image_x: int, image_y: int) -> None:
        if self._ignore_next_click:
            self._ignore_next_click = False
            return
        if not self._is_local_pc_active():
            client = self._get_remote_client()
            hwnd = self._remote_hwnd()
            if client and hwnd and self.forward_click_checkbox.isChecked():
                w, h = self.image_size_for_forwarding()
                client.click(hwnd, image_x, image_y, w, h)
                self.set_status(f"원격 클릭 전달 · ({image_x},{image_y})")
                QTimer.singleShot(400, self.refresh_remote_preview)
            return
        if self.selected_hwnd is None or not self.forward_click_checkbox.isChecked():
            return
        try:
            detail = post_background_click(
                self.selected_hwnd, image_x, image_y, self.image_size_for_forwarding()
            )
            log.debug("Preview click: %s", detail)
            self.set_status(f"클릭 전달 · {detail}")
            QTimer.singleShot(300, self.refresh_preview)
            QTimer.singleShot(500, lambda: self._verify_preview_click("클릭"))
        except Exception as exc:
            log.error("Preview click failed: %s\n%s", exc, traceback.format_exc())
            QMessageBox.warning(self, "클릭 전달 실패", str(exc))

    def handle_preview_double_click(self, image_x: int, image_y: int) -> None:
        if not self.forward_double_click_checkbox.isChecked():
            self.set_status("더블클릭 전달 꺼짐 — 필요 시 체크하세요")
            return
        self._ignore_next_click = True
        if not self._is_local_pc_active():
            client = self._get_remote_client()
            hwnd = self._remote_hwnd()
            if client and hwnd and self.forward_click_checkbox.isChecked():
                w, h = self.image_size_for_forwarding()
                client.click(hwnd, image_x, image_y, w, h, double=True)
                self.set_status(f"원격 더블클릭 · ({image_x},{image_y})")
                QTimer.singleShot(600, self.refresh_candidates)
            return
        if self.selected_hwnd is None or not self.forward_click_checkbox.isChecked():
            return
        try:
            detail = post_background_double_click(
                self.selected_hwnd, image_x, image_y, self.image_size_for_forwarding()
            )
            log.debug("Preview double-click: %s", detail)
            self.set_status(f"더블클릭 전달 · {detail}")
            QTimer.singleShot(300, self.refresh_preview)
        except Exception as exc:
            log.error("Preview double-click failed: %s\n%s", exc, traceback.format_exc())
            QMessageBox.warning(self, "더블클릭 전달 실패", str(exc))

    def handle_main_preview_double_click(self, image_x: int, image_y: int) -> None:
        if self.main_hwnd is None:
            return
        self._ignore_next_click = True
        try:
            size = self.last_main_image.size if self.last_main_image else (1, 1)
            detail = post_background_double_click(self.main_hwnd, image_x, image_y, size)
            self.set_status(f"목록 더블클릭 · {detail}")
            QTimer.singleShot(700, self.refresh_candidates)
        except Exception as exc:
            QMessageBox.warning(self, "목록 더블클릭 실패", str(exc))

    def handle_preview_wheel(self, image_x: int, image_y: int, delta: int) -> None:
        if not self._is_local_pc_active():
            client = self._get_remote_client()
            hwnd = self._remote_hwnd()
            if client and hwnd and self.forward_click_checkbox.isChecked():
                w, h = self.image_size_for_forwarding()
                client.wheel(hwnd, image_x, image_y, w, h, delta)
                self.set_status(f"원격 스크롤 · delta={delta}")
                QTimer.singleShot(400, self.refresh_remote_preview)
            return
        if self.selected_hwnd is None or not self.forward_click_checkbox.isChecked():
            return
        try:
            detail = post_background_wheel(
                self.selected_hwnd,
                image_x,
                image_y,
                self.image_size_for_forwarding(),
                delta,
            )
            log.debug("Preview wheel: %s", detail)
            self.set_status(f"스크롤 전달 · {detail}")
            QTimer.singleShot(300, self.refresh_preview)
        except Exception as exc:
            log.error("Preview wheel failed: %s\n%s", exc, traceback.format_exc())
            QMessageBox.warning(self, "스크롤 전달 실패", str(exc))

    def handle_main_preview_click(self, image_x: int, image_y: int) -> None:
        if self.main_hwnd is None:
            return
        rooms_before = len(self.chat_rooms)
        try:
            size = self.last_main_image.size if self.last_main_image else (1, 1)
            detail = post_background_click(self.main_hwnd, image_x, image_y, size)
            log.debug("Main list click: %s", detail)
            self.set_status(f"목록 클릭 · {detail}")
            QTimer.singleShot(700, self.refresh_candidates)
            QTimer.singleShot(900, lambda: self._verify_main_list_click(rooms_before))
        except Exception as exc:
            log.error("Main list click failed: %s\n%s", exc, traceback.format_exc())
            QMessageBox.warning(self, "목록 클릭 실패", str(exc))

    def handle_main_preview_wheel(self, image_x: int, image_y: int, delta: int) -> None:
        if self.main_hwnd is None:
            return
        try:
            size = self.last_main_image.size if self.last_main_image else (1, 1)
            detail = post_background_wheel(self.main_hwnd, image_x, image_y, size, delta)
            log.debug("Main list wheel: %s", detail)
            self.set_status(f"목록 스크롤 · {detail}")
            QTimer.singleShot(300, self.refresh_main_preview)
        except Exception as exc:
            log.error("Main list wheel failed: %s\n%s", exc, traceback.format_exc())
            QMessageBox.warning(self, "목록 스크롤 실패", str(exc))

    # -------------------------------------------------------------------
    # Screen tab inline input
    # -------------------------------------------------------------------

    def _on_screen_input_changed(self, text: str) -> None:
        if self._suppress_text_changed:
            return
        if not self._is_local_pc_active():
            client = self._get_remote_client()
            hwnd = self._remote_hwnd()
            if client and hwnd and text:
                client.type_text(hwnd, text)
                self.screen_typing_label.setText("원격 입력 중…")
                QTimer.singleShot(500, self.refresh_remote_preview)
            return
        if self.selected_hwnd is None:
            self.screen_typing_label.setText("톡방 없음")
            return
        if not text:
            self.screen_typing_label.setText("")
            return
        try:
            type_text_to_chat_background(self.selected_hwnd, text)
            self.screen_typing_label.setText("입력 중…")
            log.debug("Screen input typed: %s", text[:30])
        except Exception as exc:
            log.error("Screen input type failed: %s", exc)
            self.screen_typing_label.setText("입력 실패")

    def _clear_screen_input(self) -> None:
        self._suppress_text_changed = True
        self.screen_input.clear()
        self._suppress_text_changed = False

    def _on_screen_input_enter(self) -> None:
        if not self._is_local_pc_active():
            client = self._get_remote_client()
            hwnd = self._remote_hwnd()
            if client is None or hwnd is None:
                QMessageBox.information(self, "선택 필요", "원격 톡방을 먼저 선택하세요.")
                return
            text = self.screen_input.text().strip()
            if not text:
                return
            if not self.allow_send:
                self.screen_typing_label.setText("모의 전송")
                self._clear_screen_input()
                return
            client.press_enter(hwnd, allow_send=True)
            self._clear_screen_input()
            self.screen_typing_label.setText("원격 전송 요청")
            QTimer.singleShot(800, self.refresh_remote_preview)
            return
        if self.selected_hwnd is None:
            QMessageBox.information(self, "선택 필요", "톡방을 먼저 선택하세요.")
            return
        text = self.screen_input.text().strip()
        if not text:
            return
        if not self.allow_send:
            log.info("Mock screen send: %s", text[:50])
            self.screen_typing_label.setText("모의 전송")
            self._clear_screen_input()
            QTimer.singleShot(800, self.refresh_preview)
            return
        try:
            press_enter_in_chat(self.selected_hwnd)
            log.info("Screen Enter sent to %r: %s", self.selected_title, text[:50])
            self._clear_screen_input()
            self.screen_typing_label.setText("전송 완료")
            QTimer.singleShot(800, self.refresh_preview)
        except Exception as exc:
            log.error("Screen Enter failed: %s\n%s", exc, traceback.format_exc())
            QMessageBox.warning(self, "전송 실패", str(exc))

    # -------------------------------------------------------------------
    # Window actions
    # -------------------------------------------------------------------

    def bring_selected_to_front(self) -> None:
        if self.selected_hwnd is None:
            QMessageBox.information(self, "선택 필요", "톡방을 먼저 선택하세요.")
            return
        log.info("Bringing hwnd %d to front", self.selected_hwnd)
        capture.bring_window_to_front(self.selected_hwnd)
        QTimer.singleShot(300, self.refresh_preview)

    def _request_open_closed_room(self, title: str) -> None:
        self.set_status(f"'{title}' 열기 — 「발송·고급」의 카카오 목록을 펼쳐 클릭하세요")
        self.tabs.setCurrentIndex(2)
        self.kakao_advanced_group.setChecked(True)
        self.refresh_main_preview()

    def close_chat_window(self, hwnd: int, title: str) -> None:
        reply = QMessageBox.question(
            self,
            "톡방 닫기",
            f"'{title}' 대화창을 닫을까요?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        log.info("Closing chat window: hwnd=%d title=%r", hwnd, title)
        capture.close_window(hwnd)
        if self.selected_hwnd == hwnd:
            self.selected_hwnd = None
            self.selected_title = ""
            self._update_input_target_labels()
        QTimer.singleShot(500, self.refresh_candidates)

    # -------------------------------------------------------------------
    # Send
    # -------------------------------------------------------------------

    def handle_send(self) -> None:
        if not self._is_local_pc_active():
            client = self._get_remote_client()
            hwnd = self._remote_hwnd()
            if client is None or hwnd is None:
                QMessageBox.information(self, "선택 필요", "원격 톡방을 먼저 선택하세요.")
                return
            text = self.message_edit.toPlainText().strip()
            if not text:
                QMessageBox.warning(self, "입력 필요", "보낼 메시지를 입력하세요.")
                return
            if not self.allow_send:
                self.set_status("원격 모의 보내기 완료")
                return
            reply = QMessageBox.question(
                self, "실제 전송 확인", f"원격 PC에 실제 전송할까요?\n\n{text}",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
            client.send_text(hwnd, text, allow_send=True)
            self.message_edit.clear()
            self.set_status("원격 전송 요청 완료")
            QTimer.singleShot(1000, self.refresh_remote_preview)
            return
        if self.selected_hwnd is None:
            QMessageBox.information(self, "선택 필요", "톡방을 먼저 선택하세요.")
            return
        text = self.message_edit.toPlainText().strip()
        if not text:
            QMessageBox.warning(self, "입력 필요", "보낼 메시지를 입력하세요.")
            return
        if not self.allow_send:
            log.info("Mock send to %r: %s", self.selected_title, text[:50])
            self.set_status("모의 보내기 완료")
            QTimer.singleShot(1000, self.refresh_preview)
            return

        reply = QMessageBox.question(
            self,
            "실제 전송 확인",
            f"'{self.selected_title}'에 실제 전송할까요?\n\n{text}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            if self.background_input_checkbox.isChecked():
                send_text_to_chat_background(self.selected_hwnd, text)
            else:
                send_text_to_chat(self.selected_hwnd, text)
            log.info("Sent to %r: %s", self.selected_title, text[:50])
            self.message_edit.clear()
            self.set_status("전송 완료")
            QTimer.singleShot(1000, self.refresh_preview)
        except Exception as exc:
            log.error("Send failed: %s\n%s", exc, traceback.format_exc())
            QMessageBox.critical(self, "전송 실패", str(exc))

    # -------------------------------------------------------------------
    # Reservations
    # -------------------------------------------------------------------

    def add_reservation(self) -> None:
        if not self.selected_title:
            QMessageBox.information(self, "선택 필요", "예약할 톡방을 선택하세요.")
            return
        text = self.message_edit.toPlainText().strip()
        if not text:
            QMessageBox.warning(self, "입력 필요", "예약 메시지를 입력하세요.")
            return
        minutes = self.reserve_minutes_spin.value()
        if self.allow_send:
            reply = QMessageBox.question(
                self,
                "예약 실제 전송",
                f"{minutes}분 후 「{self.selected_title}」에 실제 전송합니다.\n계속할까요?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        self.reservations.append(
            {
                "room": self.selected_title,
                "message": text,
                "send_at": datetime.now() + timedelta(minutes=minutes),
                "created_at": datetime.now(),
                "hwnd": self.selected_hwnd,
            }
        )
        log.info("Reservation: %r in %d min", self.selected_title, minutes)
        mode = "실제" if self.allow_send else "모의"
        self.set_status(f"{self.selected_title} · {minutes}분 후 예약 ({mode})")
        self.refresh_reservation_views()
        self._rebuild_room_lists()
        self._save_workspace_settings_now()

    def _process_due_reservations(self) -> None:
        now = datetime.now()
        due = [r for r in self.reservations if r["send_at"] <= now]
        if not due:
            return
        for item in due:
            self._execute_reservation(item)
        self.reservations = [r for r in self.reservations if r["send_at"] > now]
        self._save_workspace_settings_now()
        self.refresh_reservation_views()
        self._rebuild_room_lists()

    def _execute_reservation(self, item: dict) -> None:
        title = item.get("room", "")
        text = item.get("message", "")
        hwnd = item.get("hwnd")
        target_hwnd: int | None = None
        if hwnd and capture.is_window_valid(int(hwnd)):
            target_hwnd = int(hwnd)
        else:
            for h, t in self.chat_rooms:
                if t == title:
                    target_hwnd = h
                    break
        if target_hwnd is None:
            self.set_status(f"예약 스킵 · '{title}' 창이 열려 있지 않음", is_error=True)
            log.warning("Reservation skipped, room not open: %r", title)
            return
        if not self._is_local_pc_active():
            self.set_status(f"예약 스킵 · 원격 PC는 자동 전송 미지원 ({title})", is_error=True)
            return
        self.set_selected_room(target_hwnd, title)
        if not self.allow_send:
            log.info("Reservation mock send to %r", title)
            self.set_status(f"예약 모의 전송 · {title}")
            return
        try:
            if self.background_input_checkbox.isChecked():
                send_text_to_chat_background(target_hwnd, text)
            else:
                send_text_to_chat(target_hwnd, text)
            log.info("Reservation sent to %r", title)
            self.set_status(f"예약 전송 완료 · {title}")
            QTimer.singleShot(800, self.refresh_preview)
        except Exception as exc:
            log.error("Reservation send failed: %s", exc)
            self.set_status(f"예약 전송 실패 · {title}: {exc}", is_error=True)

    def refresh_reservation_views(self) -> None:
        self._process_due_reservations()
        self._clear_layout(self.reservation_container)
        sorted_items = sorted(self.reservations, key=lambda item: item["send_at"])
        if not sorted_items:
            empty = QLabel("예약 없음")
            empty.setStyleSheet(f"color: {COLORS['muted']};")
            self.reservation_container.addWidget(empty)
            return
        for reservation in sorted_items:
            seconds = max(0, int((reservation["send_at"] - datetime.now()).total_seconds()))
            m, s = divmod(seconds, 60)
            preview = reservation["message"].replace("\n", " ")[:48]
            row = QLabel(f"⏱ {m:02d}:{s:02d} · {reservation['room']}\n{preview}")
            row.setStyleSheet(
                f"background: {COLORS['bg']}; padding: 10px; border-radius: 8px; "
                f"border: 1px solid {COLORS['border']};"
            )
            row.setWordWrap(True)
            self.reservation_container.addWidget(row)
        self._rebuild_room_lists()

    # -------------------------------------------------------------------
    # AI
    # -------------------------------------------------------------------

    def read_chat_into_ai_context(self) -> None:
        if not self._is_local_pc_active():
            QMessageBox.information(
                self, "로컬 전용", "채팅 읽기는 이 PC의 열린 톡방에서만 지원합니다."
            )
            return
        if self.selected_hwnd is None:
            QMessageBox.information(self, "선택 필요", "톡방을 먼저 선택하세요.")
            return
        text, hint = read_chat_context(self.selected_hwnd)
        if text:
            self.ai_chat_context_edit.setPlainText(text)
            self._queue_save_workspace_settings()
            self.set_status(f"채팅 읽기 완료 · {hint}")
        else:
            self.set_status(hint, is_error=True)

    @staticmethod
    def _clean_ai_reply_for_send(reply: str) -> str:
        lines = reply.strip().splitlines()
        if lines and lines[0].startswith("[REVIEW]"):
            return "\n".join(lines[1:]).strip() or reply
        if lines and lines[0].startswith("[AUTO]"):
            return "\n".join(lines[1:]).strip() or reply
        return reply.strip()

    def show_ai_reply_dialog(self) -> None:
        if not self.selected_title:
            QMessageBox.information(self, "선택 필요", "톡방을 먼저 선택하세요.")
            return
        mode = self.ai_mode_combo.currentText().split(" ", 1)[0]
        if mode == "off":
            QMessageBox.information(self, "AI 꺼짐", "semi 또는 on으로 변경하세요.")
            return
        if not self.ai_chat_context_edit.toPlainText().strip():
            self.read_chat_into_ai_context()
        try:
            reply = self.generate_ai_reply(
                mode=mode,
                role=self.ai_role_edit.toPlainText().strip() or "친절한 상담원",
                prompt=self.ai_prompt_edit.toPlainText().strip() or "짧고 친절하게",
                examples=self.ai_examples_edit.toPlainText().strip() or "",
                sources=self.ai_sources_edit.toPlainText().strip() or "",
                guardrails=self.ai_guardrails_edit.toPlainText().strip() or "",
                level=self.ai_level_spin.value(),
                chat_context=self.ai_chat_context_edit.toPlainText().strip(),
            )
        except Exception as exc:
            log.error("AI reply failed: %s\n%s", exc, traceback.format_exc())
            QMessageBox.warning(self, "AI 호출 실패", str(exc))
            return
        self._open_ai_reply_dialog(reply)

    def _open_ai_reply_dialog(self, reply: str) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle("AI 답변 후보")
        dlg.resize(640, 420)
        dlg.setStyleSheet(
            f"background: {COLORS['bg']}; color: {COLORS['text']};"
        )
        layout = QVBoxLayout(dlg)
        body = QTextEdit()
        body.setReadOnly(True)
        body.setPlainText(reply)
        body.setStyleSheet(
            f"background: {COLORS['card']}; border: 1px solid {COLORS['border']};"
        )
        layout.addWidget(body)
        btn_row = QHBoxLayout()
        apply_bottom = PrimaryButton("하단 입력창에 적용")
        apply_screen = GhostButton("작업 탭 입력에 적용")
        apply_both = GhostButton("둘 다 적용")
        close_btn = GhostButton("닫기")

        clean = self._clean_ai_reply_for_send(reply)

        def _apply_bottom() -> None:
            self.message_edit.setPlainText(clean)
            self.set_status(f"AI 후보 → 하단 입력 · {self.selected_title}")

        def _apply_screen() -> None:
            self._suppress_text_changed = True
            self.screen_input.setText(clean)
            self._suppress_text_changed = False
            if self._is_local_pc_active() and self.selected_hwnd:
                try:
                    type_text_to_chat_background(self.selected_hwnd, clean)
                except Exception as exc:
                    log.debug("AI apply to kakao failed: %s", exc)
            self.set_status(f"AI 후보 → 작업 입력 · {self.selected_title}")

        def _apply_both() -> None:
            _apply_bottom()
            _apply_screen()

        apply_bottom.clicked.connect(_apply_bottom)
        apply_screen.clicked.connect(_apply_screen)
        apply_both.clicked.connect(_apply_both)
        close_btn.clicked.connect(dlg.close)
        btn_row.addWidget(apply_bottom)
        btn_row.addWidget(apply_screen)
        btn_row.addWidget(apply_both)
        btn_row.addStretch()
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)
        dlg.exec()

    def current_capture_as_data_url(self) -> str | None:
        image = self.last_image
        if image is None and self.selected_hwnd is not None:
            try:
                image = capture.capture_with_printwindow(self.selected_hwnd)
            except Exception:
                log.error("Capture for AI data URL failed:\n%s", traceback.format_exc())
                return None
        if image is None:
            return None
        cropped = crop_native_input_area(image, self.crop_spin.value())
        buffer = io.BytesIO()
        cropped.save(buffer, format="JPEG", quality=80)
        encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
        return f"data:image/jpeg;base64,{encoded}"

    def build_ai_instruction(
        self,
        mode: str,
        role: str,
        prompt: str,
        examples: str,
        sources: str,
        guardrails: str,
        level: int,
        chat_context: str,
    ) -> str:
        semi_rule = (
            "semi: 명확한 질문·참고자료 충분 → [AUTO]. 부족·애매 → [REVIEW] + 이유."
        )
        on_rule = "on: 후보 생성. 불확실하면 [REVIEW]."
        mode_rule = semi_rule if mode == "semi" else on_rule
        return (
            f"역할:\n{role}\n\n답변 방식:\n{prompt}\n\n예시:\n{examples or '-'}\n\n"
            f"참고:\n{sources or '-'}\n\n주의:\n{guardrails or '-'}\n\n"
            f"적극성: {level}/5\n모드: {mode}\n{mode_rule}\n\n"
            f"톡방: {self.selected_title}\n채팅:\n{chat_context or '(캡처 이미지 참고)'}"
        )

    def generate_ai_reply(
        self,
        mode: str,
        role: str,
        prompt: str,
        examples: str,
        sources: str,
        guardrails: str,
        level: int,
        chat_context: str,
    ) -> str:
        if not self.openai_key_available():
            return (
                "[REVIEW] OPENAI_API_KEY 없음 — 모의 답변\n"
                "문의 감사합니다. 확인 후 안내드리겠습니다."
            )
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError("pip install openai 필요") from exc

        instruction = self.build_ai_instruction(
            mode, role, prompt, examples, sources, guardrails, level, chat_context
        )
        content: list[dict] = [{"type": "input_text", "text": instruction}]
        if not chat_context:
            image_url = self.current_capture_as_data_url()
            if image_url:
                content.append({"type": "input_image", "image_url": image_url})

        client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        response = client.responses.create(
            model=self.openai_model(),
            input=[{"role": "user", "content": content}],
        )
        return response.output_text.strip()

    # -------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------

    def _show_log_viewer(self) -> None:
        self._set_log_error_indicator(False)
        dlg = QDialog(self)
        dlg.setWindowTitle("로그 뷰어")
        dlg.resize(900, 600)
        dlg.setStyleSheet(
            f"background: {COLORS['bg']}; color: {COLORS['text']};"
        )
        layout = QVBoxLayout(dlg)

        log_text = QTextEdit()
        log_text.setReadOnly(True)
        log_text.setStyleSheet(
            f"background: {COLORS['card']}; color: {COLORS['text']}; "
            f"font-family: 'Consolas', monospace; font-size: 13px; "
            f"border: 1px solid {COLORS['border']}; border-radius: 4px;"
        )
        layout.addWidget(log_text)

        try:
            if LOG_FILE.exists():
                content = LOG_FILE.read_text(encoding="utf-8", errors="replace")
                lines = content.splitlines()
                tail = lines[-500:] if len(lines) > 500 else lines
                log_text.setPlainText("\n".join(tail))
                log_text.moveCursor(log_text.textCursor().MoveOperation.End)
            else:
                log_text.setPlainText(f"로그 파일 없음: {LOG_FILE}")
        except Exception as exc:
            log_text.setPlainText(f"로그 읽기 실패: {exc}")

        btn_row = QHBoxLayout()
        path_label = QLabel(str(LOG_FILE))
        path_label.setStyleSheet(f"color: {COLORS['muted']}; font-size: 12px;")
        btn_row.addWidget(path_label)
        btn_row.addStretch()
        refresh_btn = GhostButton("새로고침")

        def _refresh_log():
            try:
                if LOG_FILE.exists():
                    c = LOG_FILE.read_text(encoding="utf-8", errors="replace")
                    ls = c.splitlines()
                    log_text.setPlainText("\n".join(ls[-500:] if len(ls) > 500 else ls))
                    log_text.moveCursor(log_text.textCursor().MoveOperation.End)
            except Exception:
                pass

        refresh_btn.clicked.connect(_refresh_log)
        btn_row.addWidget(refresh_btn)
        close_btn = GhostButton("닫기")
        close_btn.clicked.connect(dlg.close)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

        dlg.show()

    # -------------------------------------------------------------------
    # Embedded P2P (Hub + Agent in-process)
    # -------------------------------------------------------------------

    def _start_embedded_p2p(self) -> None:
        p2p_port = int(os.getenv("KAKAO_P2P_PORT", "0"))
        p2p_token = os.getenv("KAKAO_REMOTE_TOKEN", "").strip()
        if p2p_port <= 0 or not p2p_token:
            return
        try:
            from kakao_remote_hub import HubStore, HubHandler
            from kakao_remote_agent import KakaoRemoteAgent
            from http.server import ThreadingHTTPServer
            import functools

            store_instance = HubStore()

            class _P2PHandler(HubHandler):
                pass

            _P2PHandler.store = store_instance

            import kakao_remote_hub as _hub_mod
            _original_store = _hub_mod.store
            _hub_mod.store = store_instance

            server = ThreadingHTTPServer(("0.0.0.0", p2p_port), _P2PHandler)
            hub_thread = threading.Thread(
                target=server.serve_forever, daemon=True, name="p2p-hub",
            )
            hub_thread.start()
            self._embedded_hub = server
            log.info("P2P Hub started on port %d", p2p_port)

            pc_id = os.getenv("KAKAO_REMOTE_PC_ID", "") or socket.gethostname()
            agent = KakaoRemoteAgent(
                f"http://127.0.0.1:{p2p_port}",
                p2p_token,
                pc_id,
                allow_send=self.allow_send,
                hide_native_input_px=self.hide_native_input_px,
            )

            def _agent_loop():
                agent.upload_candidates()
                agent.upload_frame()
                while True:
                    try:
                        agent.tick()
                    except Exception:
                        log.debug("P2P agent tick error:\n%s", traceback.format_exc())
                    import time
                    time.sleep(2)

            agent_thread = threading.Thread(
                target=_agent_loop, daemon=True, name="p2p-agent",
            )
            agent_thread.start()
            self._embedded_agent = agent
            log.info("P2P Agent started (pc_id=%s)", pc_id)
            self.set_status(f"P2P 모드 · port {p2p_port} · {pc_id}")
        except Exception as exc:
            log.error("P2P start failed: %s\n%s", exc, traceback.format_exc())

    def closeEvent(self, event) -> None:  # noqa: N802
        log.info("Window closing")
        self._save_workspace_settings_now()
        self.timer.stop()
        self.countdown_timer.stop()
        if self._embedded_hub:
            self._embedded_hub.shutdown()
        super().closeEvent(event)


def main() -> int:
    parser = argparse.ArgumentParser(description="V3 Kakao workspace GUI prototype.")
    parser.add_argument("--allow-send", action="store_true")
    parser.add_argument("--interval-ms", type=int, default=500)
    parser.add_argument("--hide-native-input-px", type=int, default=120)
    args = parser.parse_args()

    if sys.platform != "win32":
        print("Windows only.", file=sys.stderr)
        return 2

    if load_dotenv is not None:
        load_dotenv(ROOT_DIR / ".env")

    if str(ROOT_DIR) not in sys.path:
        sys.path.insert(0, str(ROOT_DIR))

    log.info("Starting workspace prototype: %s", args)

    try:
        from auto_update_runner import maybe_check_and_apply_auto_update
        maybe_check_and_apply_auto_update(interval_sec=3600)
    except Exception:
        pass

    capture.set_process_dpi_awareness()
    app = QApplication(sys.argv)
    family = load_pretendard_font()
    app.setFont(QFont(family, 14))

    window = WorkspaceWindow(
        allow_send=args.allow_send,
        interval_ms=args.interval_ms,
        hide_native_input_px=args.hide_native_input_px,
    )
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
