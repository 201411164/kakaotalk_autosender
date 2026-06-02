"""V3 Kakao workspace GUI prototype — 카카오 매니저 작업대.

- 좌측: PC 목록 + 열린 톡방 (유일한 방 선택기)
- 중앙: 작업(미리보기·입력) / 톡방 관리 / 발송·고급
- 하단: 선택 방 대상 표시 + 메시지·예약·보내기

항상 실제 전송. 선택한 톡방에 메시지가 바로 전달됩니다.
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
import time
import traceback
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QFontDatabase, QKeySequence, QPainter, QPixmap, QShortcut
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QFileDialog,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QStackedWidget,
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
from chat_monitor import ChatMonitor
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
    capture_image_hash,
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
    return "v1.5.0"


APP_VERSION = _load_app_version()
PC_REGISTRY_PATH = ROOT_DIR / "v3_registered_pcs.json"
LOCAL_PC_ID = "__local__"
MAX_KAKAO_SESSIONS = 10
NARROW_WIDTH_FOR_TAB_LAYOUT = 1000
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

# Status feedback styling per kind: (color, icon prefix)
STATUS_KIND_STYLE = {
    "info": (COLORS["text"], ""),
    "success": (COLORS["online"], "✓ "),
    "error": (COLORS["danger"], "⚠ "),
    "busy": (COLORS["accent"], "⏳ "),
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

class _AsyncWorker(QThread):
    """Run a blocking callable off the UI thread and report the result.

    Used for slow operations (clipboard chat reading, OpenAI calls) so the
    window never freezes. The callable must NOT touch Qt widgets — only return
    data that the main thread applies in the `done` slot.
    """

    done = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, fn, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._fn = fn

    def run(self) -> None:  # noqa: D401 - QThread entry point
        try:
            result = self._fn()
        except Exception as exc:  # pragma: no cover - surfaced to UI
            log.error("async worker failed: %s\n%s", exc, traceback.format_exc())
            self.failed.emit(str(exc))
            return
        self.done.emit(result)


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
    remove_clicked = pyqtSignal()

    def __init__(
        self,
        name: str,
        status: str,
        badge: int,
        selected: bool = False,
        removable: bool = False,
        parent: QWidget | None = None,
    ) -> None:
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
        self._remove_btn: CloseXButton | None = None
        if removable:
            self._remove_btn = CloseXButton()
            self._remove_btn.clicked.connect(self.remove_clicked.emit)
            layout.addWidget(self._remove_btn)
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
        session_state: str = "main",
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

        state_icons = {"chatting": ("●", COLORS["online"]), "main": ("○", COLORS["text"]), "login": ("△", COLORS["muted"])}
        icon_char, icon_color = state_icons.get(session_state, ("○", COLORS["text"]))
        if not online:
            icon_char, icon_color = ("○", COLORS["muted"])
        dot = QLabel(icon_char)
        dot.setStyleSheet(f"color: {icon_color}; font-size: 14px; font-weight: 900;")
        dot.setFixedWidth(16)
        dot.setToolTip({"chatting": "채팅 중", "main": "메인 화면", "login": "로그인 화면"}.get(session_state, ""))
        layout.addWidget(dot)

        text_col = QVBoxLayout()
        text_col.setSpacing(2)
        name_label = QLabel(display_name)
        name_label.setStyleSheet("font-size: 13px; font-weight: 800;")
        text_col.addWidget(name_label)
        state_desc = {"chatting": f"톡방 {chat_count}개", "main": "메인 화면", "login": "로그인 대기"}
        sub = QLabel(state_desc.get(session_state, f"열린 톡방 {chat_count}개"))
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
# Per-session preview + input pane (multi-instance grid)
# ---------------------------------------------------------------------------

class SessionPane(QFrame):
    """One KakaoTalk instance: mini preview, inline input, independent hwnd."""

    focused = pyqtSignal()
    preview_clicked = pyqtSignal(int, int)
    preview_double_clicked = pyqtSignal(int, int)
    preview_wheeled = pyqtSignal(int, int, int)
    preview_dragged = pyqtSignal(int, int, int, int)
    input_changed = pyqtSignal(str)
    enter_pressed = pyqtSignal()
    expand_requested = pyqtSignal()
    collapse_requested = pyqtSignal()

    def __init__(self, pid: int, display_name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.pid = pid
        self.display_name = display_name
        self.selected_hwnd: int | None = None
        self.selected_title = ""
        self.last_image = None
        self._suppress_input = False
        self._expanded = False

        self.setObjectName("session_pane")
        self._apply_focus_style(False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        header = QHBoxLayout()
        self.title_label = QLabel(display_name)
        self.title_label.setStyleSheet("font-weight: 800; font-size: 13px;")
        header.addWidget(self.title_label)
        self.room_label = QLabel("(방 없음)")
        self.room_label.setStyleSheet(f"color: {COLORS['muted']}; font-size: 11px;")
        header.addWidget(self.room_label, stretch=1)
        self.status_label = QLabel("")
        self.status_label.setStyleSheet(f"color: {COLORS['muted']}; font-size: 10px;")
        header.addWidget(self.status_label)
        self.expand_btn = GhostButton("⤢")
        self.expand_btn.setFixedWidth(36)
        self.expand_btn.setToolTip("이 세션 확대 (집중 작업)")
        self.expand_btn.clicked.connect(self._on_expand_btn_clicked)
        header.addWidget(self.expand_btn)
        layout.addLayout(header)

        self.preview_label = ClickablePreviewLabel()
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setMinimumHeight(200)
        self.preview_label.setToolTip(
            "클릭·스크롤은 상단 「클릭/스크롤 전달」이 켜져 있을 때 카카오톡에 전달됩니다.\n"
            "방 목록(왼쪽)·채팅 본문을 클릭하세요. 입력창 영역은 차단됩니다."
        )
        self.preview_label.setStyleSheet(
            f"background:#090909; border:1px solid {COLORS['border']}; border-radius:6px;"
        )
        self.preview_label.imageClicked.connect(
            lambda x, y: self.preview_clicked.emit(x, y)
        )
        self.preview_label.imageDoubleClicked.connect(
            lambda x, y: self.preview_double_clicked.emit(x, y)
        )
        self.preview_label.imageWheeled.connect(
            lambda x, y, d: self.preview_wheeled.emit(x, y, d)
        )
        self.preview_label.imageDragged.connect(
            lambda sx, sy, ex, ey: self.preview_dragged.emit(sx, sy, ex, ey)
        )
        layout.addWidget(self.preview_label, stretch=1)

        input_row = QHBoxLayout()
        self.screen_input = QLineEdit()
        self.screen_input.setPlaceholderText("메시지 (Enter 전송)")
        self.screen_input.setStyleSheet(
            f"background: {COLORS['bg']}; border: 1px solid {COLORS['border']}; "
            f"border-radius: 6px; padding: 6px; font-size: 13px;"
        )
        self.screen_input.textChanged.connect(self.input_changed.emit)
        self.screen_input.returnPressed.connect(self.enter_pressed.emit)
        input_row.addWidget(self.screen_input, stretch=1)
        layout.addLayout(input_row)

    def _apply_focus_style(self, focused: bool) -> None:
        border = f"2px solid {COLORS['accent']}" if focused else f"1px solid {COLORS['border']}"
        self.setStyleSheet(
            f"QFrame#session_pane {{ background: {COLORS['card']}; border: {border}; "
            f"border-radius: 8px; }}"
        )

    def set_display_name(self, name: str) -> None:
        self.display_name = name
        self.title_label.setText(name)

    def set_focused(self, focused: bool) -> None:
        self._apply_focus_style(focused)

    def _on_expand_btn_clicked(self) -> None:
        if self._expanded:
            self.collapse_requested.emit()
        else:
            self.expand_requested.emit()

    def set_expanded(self, expanded: bool) -> None:
        self._expanded = expanded
        self.expand_btn.setText("⤡" if expanded else "⤢")
        self.expand_btn.setToolTip(
            "격자로 돌아가기 (Esc)" if expanded else "이 세션 확대 (집중 작업)"
        )
        self.preview_label.setMinimumHeight(480 if expanded else 200)
        if expanded:
            self.title_label.setStyleSheet("font-weight: 900; font-size: 16px;")
            self.room_label.setStyleSheet(f"color: {COLORS['text']}; font-size: 13px;")
        else:
            self.title_label.setStyleSheet("font-weight: 800; font-size: 13px;")
            self.room_label.setStyleSheet(f"color: {COLORS['muted']}; font-size: 11px;")

    def set_selection(self, hwnd: int | None, title: str) -> None:
        self.selected_hwnd = hwnd
        self.selected_title = title or ""
        short = self.selected_title if len(self.selected_title) <= 18 else (
            self.selected_title[:16] + "…"
        )
        self.room_label.setText(short if hwnd else "(방 없음)")

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self.focused.emit()
        super().mousePressEvent(event)

    def image_size_for_forwarding(self, crop_px: int) -> tuple[int, int]:
        if self.last_image is None:
            return (1, 1)
        return (self.last_image.width, max(1, self.last_image.height - crop_px))

    def refresh_preview(
        self,
        *,
        crop_px: int,
        is_chat: bool,
    ) -> bool:
        hwnd = self.selected_hwnd
        if hwnd is None:
            self.status_label.setText("창 없음")
            return False
        if not capture.is_window_alive(hwnd):
            self.status_label.setText("창 사라짐")
            self.selected_hwnd = None
            return False
        if capture.is_window_minimized(hwnd):
            self.status_label.setText("최소화")
            return False
        try:
            image = capture.capture_with_printwindow(hwnd)
            self.last_image = image
            cropped = crop_native_input_area(image, crop_px if is_chat else 0)
            pixmap = pil_to_pixmap(cropped)
            scaled = pixmap.scaled(
                self.preview_label.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.preview_label.setUpdatesEnabled(False)
            self.preview_label.set_source_size(cropped.size)
            self.preview_label.setPixmap(scaled)
            self.preview_label.setUpdatesEnabled(True)
            ts = datetime.now().strftime("%H:%M:%S")
            self.status_label.setText(ts)
            return True
        except Exception as exc:
            self.status_label.setText("갱신 실패")
            log.debug("SessionPane refresh pid=%s: %s", self.pid, exc)
            return False


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
    def __init__(self, interval_ms: int, hide_native_input_px: int) -> None:
        super().__init__()
        self._loaded_settings = load_workspace_settings()
        _ui = self._loaded_settings.get("ui") or {}
        self.allow_send = True
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
        self._session_panes: dict[int, SessionPane] = {}
        self._focused_pane_pid: int | None = None
        self._expanded_pane_pid: int | None = None
        self._session_layout_pref: str = "auto"  # auto | grid | tabs
        self._multi_pane_grid_host: QWidget | None = None
        self._multi_pane_tab_widget: QTabWidget | None = None
        self._multi_pane_expanded_host: QWidget | None = None
        self._pending_launch_pid: int | None = None
        self._launch_feedback_until: float = 0.0
        self.last_image = None
        self.last_main_image = None
        self.reservations: list[dict] = list(
            self._loaded_settings.get("reservations") or []
        )
        self.ai_presets: dict[str, dict] = dict(
            self._loaded_settings.get("ai_presets") or {}
        )
        self._last_ai_preset: str = str(
            (self._loaded_settings.get("ai") or {}).get("last_preset", "")
        )
        self._chat_monitors: dict[int, ChatMonitor] = {}
        self._monitor_titles: dict[int, str] = {}
        self._monitor_event_count: int = 0
        self._monitor_generating: bool = False
        self._send_signatures: dict[int, tuple[str, int, str]] = {}
        self._guard_blocks: list[dict] = []
        self._suggestion_queue: list[dict] = []
        self._monitor_keywords_by_title: dict[str, list[str]] = {}
        self._saved_monitor_rooms: list[dict] = list(
            self._loaded_settings.get("monitor_rooms") or []
        )
        self._monitors_restored: bool = False
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
        self._last_dialog_hwnds: tuple[int, ...] = ()
        self._reservation_room_rebuild_tick = 0
        self._last_reservation_count = -1
        self._hub_status_snapshot: str = ""
        self._last_pc_list_key: str = ""
        self._last_session_cards_key: str = ""
        self._last_left_rooms_key: str = ""
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
        self._status_action_timer = QTimer(self)
        self._status_action_timer.setSingleShot(True)
        self._status_action_timer.timeout.connect(self._fade_status_action)
        self._refreshing_candidates = False
        self._pending_refresh_scheduled = False

        log.info("WorkspaceWindow init: interval=%dms", interval_ms)

        self.setWindowTitle(f"카카오 매니저 {APP_VERSION}")
        self.resize(1400, 920)
        self.setMinimumSize(900, 700)
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

        body_wrap = QWidget()
        body_wrap.setStyleSheet(f"background: {COLORS['bg']};")
        body = QHBoxLayout(body_wrap)
        body.setContentsMargins(12, 12, 12, 8)
        body.setSpacing(12)

        self._left_panel = self._build_left_panel()
        body.addWidget(self._left_panel, stretch=0)
        body.addLayout(self._build_center_panel(), stretch=1)
        root.addWidget(body_wrap, stretch=1)

        root.addWidget(self._build_bottom_bar())
        self.setCentralWidget(central)

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
        self.guard_btn = GhostButton("가드 0")
        self.guard_btn.setToolTip("발송 가드가 차단한 내역을 봅니다(오발송 방지).")
        self.guard_btn.clicked.connect(self._show_guard_log)
        layout.addWidget(self.guard_btn)
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

        self.sess_title_label = QLabel("카카오톡 세션")
        self.sess_title_label.setStyleSheet("font-weight: 900; font-size: 14px; margin-top: 4px;")
        layout.addWidget(self.sess_title_label)

        self.sessions_scroll = QScrollArea()
        self.sessions_scroll.setWidgetResizable(True)
        self.sessions_scroll.setMaximumHeight(300)
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

        self.left_room_filter = QLineEdit()
        self.left_room_filter.setPlaceholderText("🔍 톡방 검색…")
        self.left_room_filter.setClearButtonEnabled(True)
        self.left_room_filter.setStyleSheet(
            f"background: {COLORS['card']}; border: 1px solid {COLORS['border']}; "
            "border-radius: 6px; padding: 5px 8px; font-size: 13px;"
        )
        self.left_room_filter.textChanged.connect(self._on_left_room_filter_changed)
        layout.addWidget(self.left_room_filter)

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
        stop_btn.clicked.connect(lambda: self.set_status("발송 중지"))
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

        preset_row = QHBoxLayout()
        preset_row.addWidget(QLabel("프리셋"))
        self.ai_preset_combo = QComboBox()
        self.ai_preset_combo.setMinimumWidth(160)
        preset_row.addWidget(self.ai_preset_combo, stretch=1)
        preset_load_btn = GhostButton("불러오기")
        preset_load_btn.clicked.connect(self._load_selected_ai_preset)
        preset_row.addWidget(preset_load_btn)
        preset_save_btn = GhostButton("저장")
        preset_save_btn.setToolTip("현재 AI 설정(역할·프롬프트·예시·참고·주의·적극성·모드)을 이름 붙여 저장")
        preset_save_btn.clicked.connect(self._save_ai_preset)
        preset_row.addWidget(preset_save_btn)
        preset_del_btn = GhostButton("삭제")
        preset_del_btn.clicked.connect(self._delete_selected_ai_preset)
        preset_row.addWidget(preset_del_btn)
        ai_layout.addLayout(preset_row)
        self._refresh_ai_preset_combo()

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
        self.read_chat_btn = GhostButton("채팅 읽기")
        self.read_chat_btn.setToolTip(
            "선택한 톡방의 대화를 읽어옵니다.\n"
            "카카오톡 창이 잠깐 앞으로 나왔다가 돌아옵니다(클립보드는 자동 복원)."
        )
        self.read_chat_btn.clicked.connect(self.read_chat_into_ai_context)
        ai_actions.addWidget(self.read_chat_btn)
        self.ai_btn = PrimaryButton("AI 답변 후보 생성")
        self.ai_btn.clicked.connect(self.show_ai_reply_dialog)
        ai_actions.addWidget(self.ai_btn)
        ai_actions.addStretch()
        ai_layout.addLayout(ai_actions)

        # --- New-message monitoring (foundation for auto-reply; never sends) ---
        monitor_row = QHBoxLayout()
        self.monitor_btn = GhostButton("이 방 모니터링 시작")
        self.monitor_btn.setToolTip(
            "선택한 톡방을 모니터링 대상에 추가/제거합니다(여러 방 동시 가능).\n"
            "감지 시 카카오톡 창이 잠깐 앞으로 나옵니다. 자동 전송은 하지 않습니다."
        )
        self.monitor_btn.clicked.connect(self._toggle_chat_monitor)
        monitor_row.addWidget(self.monitor_btn)
        monitor_row.addWidget(QLabel("간격(초)"))
        self.monitor_interval_spin = QSpinBox()
        self.monitor_interval_spin.setRange(5, 600)
        self.monitor_interval_spin.setValue(15)
        monitor_row.addWidget(self.monitor_interval_spin)
        self.monitor_status_label = QLabel("모니터링 꺼짐")
        self.monitor_status_label.setStyleSheet(f"color: {COLORS['muted']}; font-size: 12px;")
        monitor_row.addWidget(self.monitor_status_label, stretch=1)
        ai_layout.addLayout(monitor_row)

        monitor_opts = QHBoxLayout()
        monitor_opts.addWidget(QLabel("키워드"))
        self.monitor_keyword_edit = QLineEdit()
        self.monitor_keyword_edit.setPlaceholderText("쉼표로 구분, 비우면 전체 감지")
        self.monitor_keyword_edit.setStyleSheet(
            f"background: {COLORS['card']}; border: 1px solid {COLORS['border']}; "
            "border-radius: 6px; padding: 4px 8px; font-size: 12px;"
        )
        monitor_opts.addWidget(self.monitor_keyword_edit, stretch=1)
        self.monitor_autogen_chk = QCheckBox("신규 시 AI 후보 자동생성(전송X)")
        self.monitor_autogen_chk.setToolTip(
            "모드가 semi/on일 때, 새 메시지가 오면 AI 답변 후보를 자동 생성해 대기열에 쌓습니다.\n"
            "모달로 가로채지 않으며, 실제 전송은 하지 않습니다."
        )
        monitor_opts.addWidget(self.monitor_autogen_chk)
        self.suggestion_btn = PrimaryButton("AI 후보 0건")
        self.suggestion_btn.setToolTip("자동 생성된 AI 답변 후보를 확인합니다(전송 안 함).")
        self.suggestion_btn.clicked.connect(self._open_next_suggestion)
        self.suggestion_btn.setVisible(False)
        monitor_opts.addWidget(self.suggestion_btn)
        ai_layout.addLayout(monitor_opts)

        self.monitor_events = QListWidget()
        self.monitor_events.setMaximumHeight(90)
        self.monitor_events.setStyleSheet(
            f"background: {COLORS['card']}; border: 1px solid {COLORS['border']}; font-size: 12px;"
        )
        ai_layout.addWidget(self.monitor_events)

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
            "멀티 세션: 격자/탭에서 각 카카오톡을 동시에 보고, 포커스된 세션에 발송·예약이 적용됩니다.\n"
            "좌측 톡방·세션 카드를 클릭해 전환하세요."
        )
        work_hint.setWordWrap(True)
        work_hint.setStyleSheet(f"color: {COLORS['muted']}; font-size: 13px;")
        layout.addWidget(work_hint)

        layout_mode_row = QHBoxLayout()
        self.layout_mode_combo = QComboBox()
        self.layout_mode_combo.addItems(["자동 (창 크기)", "격자 분할", "세션 탭"])
        self.layout_mode_combo.currentIndexChanged.connect(self._on_layout_mode_changed)
        layout_mode_row.addWidget(QLabel("레이아웃:"))
        layout_mode_row.addWidget(self.layout_mode_combo)
        layout_mode_row.addStretch()
        self.multi_session_status_label = QLabel("")
        self.multi_session_status_label.setStyleSheet(
            f"color: {COLORS['accent']}; font-size: 12px; font-weight: 700;"
        )
        layout_mode_row.addWidget(self.multi_session_status_label)
        layout.addLayout(layout_mode_row)

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
        self.forward_click_checkbox.setToolTip(
            "켜면 미리보기 클릭·스크롤이 카카오톡 창으로 전달됩니다.\n"
            "DirectUI(EVA) 영역은 필요 시 실커서 클릭으로 자동 폴백합니다.\n"
            "입력창 영역 클릭은 실수 발송 방지를 위해 차단됩니다."
        )
        controls.addWidget(self.forward_click_checkbox)
        self.forward_click_checkbox.toggled.connect(self._update_control_mode_label)
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

        self.control_mode_label = QLabel("조작: ON — 미리보기 클릭이 카카오톡에 전달됩니다")
        self.control_mode_label.setStyleSheet(
            f"color: {COLORS['accent']}; font-size: 12px; font-weight: 700;"
        )
        layout.addWidget(self.control_mode_label)

        self._session_view_stack = QStackedWidget()

        single_page = QWidget()
        single_layout = QVBoxLayout(single_page)
        single_layout.setContentsMargins(0, 0, 0, 0)
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
        single_layout.addWidget(self.preview_label, stretch=1)

        self.cursor_pos_label = QLabel("마우스: (—, —)")
        self.cursor_pos_label.setStyleSheet(
            f"color: {COLORS['accent']}; font-size: 13px; font-weight: 700;"
        )
        single_layout.addWidget(self.cursor_pos_label)

        input_frame = QFrame()
        input_frame.setStyleSheet(
            f"background: {COLORS['card']}; border: 1px solid {COLORS['border']}; border-radius: 8px;"
        )
        input_layout = QHBoxLayout(input_frame)
        input_layout.setContentsMargins(10, 8, 10, 8)
        input_layout.setSpacing(8)
        self.screen_input = QLineEdit()
        self.screen_input.setPlaceholderText(
            "메시지 입력 (실시간 반영, Enter 전송)"
        )
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
        single_layout.addWidget(input_frame)
        self._session_view_stack.addWidget(single_page)

        self._multi_pane_outer = QWidget()
        multi_outer_layout = QVBoxLayout(self._multi_pane_outer)
        multi_outer_layout.setContentsMargins(0, 0, 0, 0)
        self._multi_pane_stack = QStackedWidget()
        self._multi_pane_grid_host = QWidget()
        self._multi_pane_tab_widget = QTabWidget()
        self._multi_pane_stack.addWidget(self._multi_pane_grid_host)
        self._multi_pane_stack.addWidget(self._multi_pane_tab_widget)
        self._multi_pane_expanded_host = QWidget()
        QVBoxLayout(self._multi_pane_expanded_host).setContentsMargins(0, 0, 0, 0)
        self._multi_pane_stack.addWidget(self._multi_pane_expanded_host)
        multi_outer_layout.addWidget(self._multi_pane_stack)
        self._session_view_stack.addWidget(self._multi_pane_outer)

        self._collapse_pane_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        self._collapse_pane_shortcut.activated.connect(self._collapse_session_pane)

        for i, key in enumerate(
            [Qt.Key.Key_1, Qt.Key.Key_2, Qt.Key.Key_3, Qt.Key.Key_4],
        ):
            sc = QShortcut(QKeySequence(Qt.Modifier.CTRL | key), self)
            sc.activated.connect(lambda idx=i: self._switch_session_by_index(idx))

        self._setup_global_shortcuts()

        layout.addWidget(self._session_view_stack, stretch=1)
        self.tabs.addTab(tab, "작업")

    def _setup_global_shortcuts(self) -> None:
        """Window-wide keyboard shortcuts for frequent actions."""
        specs = [
            ("F5", self.refresh_candidates),
            ("F1", self._show_help_dialog),
            ("Ctrl+F", self._focus_room_search),
            ("Ctrl+L", self.read_chat_into_ai_context),
            ("Ctrl+G", self.show_ai_reply_dialog),
        ]
        self._global_shortcuts = []
        for seq, slot in specs:
            sc = QShortcut(QKeySequence(seq), self)
            sc.activated.connect(slot)
            self._global_shortcuts.append(sc)

    def _focus_room_search(self) -> None:
        if hasattr(self, "left_room_filter"):
            self.left_room_filter.setFocus()
            self.left_room_filter.selectAll()

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
        if not self.registered_pcs:
            return
        self._refresh_hub_pc_status()
        snapshot = json.dumps(self._hub_pc_status, sort_keys=True, ensure_ascii=False)
        if snapshot != self._hub_status_snapshot:
            self._hub_status_snapshot = snapshot
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
            f"{'OFF (KAKAO_SENDER_DISABLE_AUTO_UPDATE=1)' if os.getenv('KAKAO_SENDER_DISABLE_AUTO_UPDATE', '').strip().lower() in ('1', 'true', 'yes', 'on') else 'ON (1시간 주기)'}\n\n"
            "원격 Hub:\n"
            "  python tools\\v3_spike\\kakao_remote_hub.py\n"
            "원격 Agent:\n"
            "  python tools\\v3_spike\\kakao_remote_agent.py",
        )

    def _schedule_auto_update_check(self) -> None:
        disable = os.getenv("KAKAO_SENDER_DISABLE_AUTO_UPDATE", "").strip().lower()
        if disable in ("1", "true", "yes", "on"):
            log.info("Auto-update disabled (KAKAO_SENDER_DISABLE_AUTO_UPDATE=1)")
            return
        log.info("Auto-update enabled — checking now, then every 1h")
        self._bg_check_update(show_no_update=False)
        self._auto_update_timer = QTimer(self)
        self._auto_update_timer.setInterval(3600 * 1000)
        self._auto_update_timer.timeout.connect(
            lambda: self._bg_check_update(show_no_update=False)
        )
        self._auto_update_timer.start()

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
                    notes = (info.get("release_notes") or "")[:200]
                    QTimer.singleShot(
                        0,
                        lambda: self._prompt_apply_update(updater, info, latest, notes),
                    )
                elif show_no_update:
                    QTimer.singleShot(0, lambda: self.set_status(f"최신 버전 ({APP_VERSION})"))
            except Exception as exc:
                log.debug("Update check failed: %s", exc)

        threading.Thread(target=_worker, daemon=True).start()

    def _check_updates_manual(self) -> None:
        self.set_status("업데이트 확인 중…")
        self._bg_check_update(show_no_update=True)

    def _prompt_apply_update(self, updater, info: dict, latest: str, notes: str = "") -> None:
        msg = f"새 버전 v{latest} (현재 {APP_VERSION})\n"
        if notes:
            msg += f"\n{notes}\n"
        msg += "\n지금 다운로드·적용할까요?\n(자동으로 재시작됩니다)"
        reply = QMessageBox.question(
            self,
            "업데이트 발견",
            msg,
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

        def _on_progress(downloaded, total, percent, speed):
            if total > 0:
                mb_dl = downloaded / (1024 * 1024)
                mb_total = total / (1024 * 1024)
                QTimer.singleShot(0, lambda: self.set_status(
                    f"업데이트 다운로드 {percent:.0f}% ({mb_dl:.1f}/{mb_total:.1f} MB) — {speed:.1f} MB/s"
                ))

        def _worker() -> None:
            try:
                dl = info["download_url"]
                path = updater.download_update(dl, progress_callback=_on_progress)
                if path:
                    QTimer.singleShot(0, lambda: self.set_status("업데이트 적용 중… 잠시 후 재시작됩니다"))
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
        key = f"{local_status}|{local_badge}|{self.active_pc_id}|{self._hub_status_snapshot}"
        if key == self._last_pc_list_key:
            return
        self._last_pc_list_key = key
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
                removable=True,
            )
            row.clicked.connect(lambda pid=pc_id: self._select_pc(pid))
            row.remove_clicked.connect(lambda pid=pc_id: self._unregister_pc(pid))
            self.pc_list_container.addWidget(row)
            self._pc_row_widgets[pc_id] = row

    def _unregister_pc(self, pc_id: str) -> None:
        if pc_id == LOCAL_PC_ID:
            return
        pc = next((p for p in self.registered_pcs if p["id"] == pc_id), None)
        if pc is None:
            return
        reply = QMessageBox.question(
            self,
            "PC 등록 해제",
            f"「{pc['name']}」을(를) 목록에서 제거할까요?\n"
            "원격 Agent/Hub 프로세스는 계속 실행될 수 있습니다.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        name = pc["name"]
        self.registered_pcs = [p for p in self.registered_pcs if p["id"] != pc_id]
        save_registered_pcs(self.registered_pcs)
        if self.active_pc_id == pc_id:
            self.remote_client = None
            self._select_pc(LOCAL_PC_ID)
        else:
            self._rebuild_pc_list()
        self.set_status(f"등록 해제 · {name}")

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
            "【단축키】\n"
            "· F5: 새로고침   · F1: 도움말\n"
            "· Ctrl+F: 톡방 검색   · Ctrl+L: 채팅 읽기\n"
            "· Ctrl+G: AI 답변 생성   · Ctrl+1~4: 세션 전환   · Esc: 확대 해제\n\n"
            "【작업】\n"
            "· 좌측 「열린 톡방」으로 방을 바꿉니다(검색창으로 빠르게 찾기).\n"
            "· 「클릭/스크롤 전달」 ON일 때 미리보기 조작이 카카오톡에 전달됩니다.\n"
            "· 방 목록·채팅 본문을 클릭하세요. 입력창 영역 클릭은 차단됩니다.\n"
            "· 더블클릭 전달은 기본 꺼짐 (잘못된 방 열림 방지).\n"
            "· 하단·작업 탭 입력창 → 선택 방에 반영, Enter 전송.\n\n"
            "【톡방 관리 / 발송·고급】\n"
            "· 닫힌 방·예약·AI·카카오 목록은 보조 탭입니다.\n\n"
            "【PC 등록 / 원격】\n"
            "· Hub: python tools\\v3_spike\\kakao_remote_hub.py\n"
            "· 원격 PC Agent: kakao_remote_agent.py (KAKAO_REMOTE_* env)\n"
            "· PC 등록 시 Hub URL·원격 PC ID 입력\n\n"
            "【자동 업데이트】\n"
            "· 시작 시 + 1시간마다 GitHub Releases에서 자동 확인\n"
            "· 상단 '업데이트' 버튼으로 수동 확인 가능\n"
            "· KAKAO_SENDER_DISABLE_AUTO_UPDATE=1 로 비활성\n\n"
            "【파일 첨부】\n"
            "· 카카오톡에서 파일 첨부 시 탐색기 창이 자동으로 미리보기에 표시됩니다.\n\n"
            "【주의】\n"
            "· 메시지·Enter·보내기는 카카오톡에 실제 전송됩니다.\n"
            "· 테스트 방 1개로 먼저 확인하는 것을 권장합니다.",
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
        n = len(self.kakao_instances)
        self.sess_title_label.setText(f"카카오톡 세션 ({n}/{MAX_KAKAO_SESSIONS})")
        if n > 1:
            self.multi_session_status_label.setText(f"{n}개 세션 관리 중")
        elif hasattr(self, "multi_session_status_label"):
            self.multi_session_status_label.setText("")
        pids = sorted(self.kakao_instances.keys())
        chats_per_pid = tuple(len(self.kakao_instances.get(p, {}).get("chats") or []) for p in pids)
        key = f"{tuple(pids)}|{chats_per_pid}|{self.active_instance_pid}"
        if key == self._last_session_cards_key:
            return
        self._last_session_cards_key = key
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
                chats = inst.get("chats") or []
                chat_count = len(chats)
                selected = pid == self.active_instance_pid
                if chat_count > 0:
                    state = "chatting"
                elif inst.get("main_hwnd"):
                    state = "main"
                else:
                    state = "login"
                card = SessionCardWidget(
                    name, chat_count, selected=selected, online=True,
                    session_state=state,
                )
                card.clicked.connect(lambda p=pid: self._select_instance(p))
                card.close_clicked.connect(lambda p=pid: self._close_kakao_instance(p))
                card.rename_requested.connect(lambda p=pid: self._rename_instance(p))
                self.sessions_container.addWidget(card)
                self._session_card_widgets[pid] = card
        self.sessions_container.addStretch()

    def _select_instance(self, pid: int) -> None:
        if self._use_multi_session_panes():
            self._focus_session_pane(pid)
        else:
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
        if len(self.kakao_instances) >= MAX_KAKAO_SESSIONS:
            QMessageBox.information(
                self,
                "세션 제한",
                f"최대 {MAX_KAKAO_SESSIONS}개 세션까지 추가할 수 있습니다.",
            )
            return
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
        self._launch_feedback_until = time.time() + 8.0
        self.set_status(msg)
        QMessageBox.information(
            self,
            "카카오톡 추가",
            f"{msg}\n\n로그인 화면이 미리보기에 표시되면 클릭·입력으로 로그인하세요.\n"
            "로그인 후 세션 목록에 자동으로 나타납니다.",
        )
        QTimer.singleShot(2000, self._after_kakao_launch)

    def _after_kakao_launch(self) -> None:
        self.refresh_candidates()
        pid = self._pending_launch_pid
        if pid and pid in self.kakao_instances:
            self._select_instance(pid)
            self.set_status("새 카카오톡 세션 감지 — 로그인을 완료하세요.")
        elif self.kakao_instances:
            newest = max(self.kakao_instances.keys())
            self._select_instance(newest)
            self.set_status("새 카카오톡 세션 감지 — 로그인을 완료하세요.")
        elif get_kakao_instance_count() > 0:
            self.set_status(
                "카카오톡 실행됨 — 로그인 화면이 보이면 미리보기에서 입력하세요."
            )
        else:
            self.set_status("카카오톡 실행 대기 중 — 잠시 후 새로고침하세요.")
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
        self._pending_refresh_scheduled = False
        if self._refreshing_candidates:
            return
        self._refreshing_candidates = True
        try:
            self._refresh_candidates_inner()
        finally:
            self._refreshing_candidates = False

    def _refresh_candidates_inner(self) -> None:
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
            has_visible = any(i[1] in ("main", "chat", "kakao_other") for i in items)
            if not has_visible:
                log.debug("Skipping PID %d — no visible KakaoTalk windows", pid)
                continue
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

        # B: snapshot a (class, pid, title) signature for each chat window at
        # discovery time. The send guard compares against this to catch HWND
        # reuse (a stale handle now pointing at a different window).
        new_sigs: dict[int, tuple[str, int, str]] = {}
        for c_hwnd, c_kind, c_title, _c_rect, c_cls in candidates:
            if c_kind == "chat":
                try:
                    new_sigs[c_hwnd] = (c_cls, get_pid_for_hwnd(c_hwnd), c_title)
                except Exception:
                    pass
        self._send_signatures = new_sigs

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
        if time.time() >= self._launch_feedback_until:
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
                or capture.is_window_alive(self.selected_hwnd)
            )
            if not still_open:
                log.warning("Selected hwnd %d is gone, clearing selection", self.selected_hwnd)
                self.selected_hwnd = None
                self.selected_title = ""
                self._viewing_dialog = False
                self._update_input_target_labels()

        self._left_panel.setUpdatesEnabled(False)
        self._rebuild_session_cards()
        self._update_status_strip_session()
        self._rebuild_session_pane_layout()
        self._rebuild_room_lists()
        self._rebuild_left_rooms()
        self._left_panel.setUpdatesEnabled(True)
        self._apply_dialog_priority(dialogs)

        if self._use_multi_session_panes():
            self._refresh_all_session_panes()
        if not self._use_multi_session_panes() and self.selected_hwnd is None:
            if chats:
                self.set_selected_room(chats[0][0], chats[0][1])
            elif self.kakao_any_hwnd is not None and capture.is_window_valid(self.kakao_any_hwnd):
                self._select_kakao_window(self.kakao_any_hwnd)
            elif get_kakao_instance_count() > 0:
                self.set_status(
                    "카카오톡 시작 중 — 로그인하면 세션 목록에 표시됩니다."
                )
            else:
                self.set_status("카카오톡이 실행되지 않았습니다.")
        self.refresh_main_preview()
        if hasattr(self, "monitor_btn"):
            self._restore_monitors_if_needed()

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

    def _on_left_room_filter_changed(self, _text: str) -> None:
        self._last_left_rooms_key = ""  # force rebuild
        self._rebuild_left_rooms()

    def _left_room_filter_text(self) -> str:
        if not hasattr(self, "left_room_filter"):
            return ""
        return self.left_room_filter.text().strip().lower()

    def _matches_left_filter(self, title: str, needle: str) -> bool:
        return not needle or needle in (title or "").lower()

    def _add_left_rooms_empty_hint(self, text: str) -> None:
        hint = QLabel(text)
        hint.setWordWrap(True)
        hint.setStyleSheet(
            f"color: {COLORS['muted']}; font-size: 13px; padding: 12px 8px;"
        )
        self.left_rooms_container.addWidget(hint)

    def _rebuild_left_rooms(self) -> None:
        needle = self._left_room_filter_text()
        room_hwnds = tuple((h, t) for h, t in self.chat_rooms)
        inst_chats = tuple(
            (pid, tuple(inst.get("chats") or []))
            for pid, inst in sorted(self.kakao_instances.items())
        )
        key = f"{room_hwnds}|{inst_chats}|{self.selected_hwnd}|{needle}"
        if key == self._last_left_rooms_key:
            return
        self._last_left_rooms_key = key
        self._clear_layout(self.left_rooms_container)
        num_instances = len(self.kakao_instances)
        shown = 0
        if num_instances <= 1:
            for hwnd, title in self.chat_rooms:
                if not self._matches_left_filter(title, needle):
                    continue
                row = LeftRoomRow(title, selected=(hwnd == self.selected_hwnd))
                row.select_clicked.connect(
                    lambda h=hwnd, t=title: self.set_selected_room(h, t, focus_work_tab=True)
                )
                row.close_clicked.connect(lambda h=hwnd, t=title: self.close_chat_window(h, t))
                self.left_rooms_container.addWidget(row)
                shown += 1
            if shown == 0:
                if num_instances == 0:
                    self._add_left_rooms_empty_hint(
                        "카카오톡 세션이 없습니다.\n위 「+ 카카오톡 추가」로 시작하세요."
                    )
                elif not self.chat_rooms:
                    self._add_left_rooms_empty_hint(
                        "열린 톡방이 없습니다.\n카카오톡에서 대화방을 열면 여기에 표시됩니다."
                    )
                elif needle:
                    self._add_left_rooms_empty_hint(f"'{needle}' 검색 결과 없음")
        else:
            for pid, inst in self.kakao_instances.items():
                session_name = self._instance_display_name(pid, inst)
                session_chats = inst.get("chats", [])
                filtered_chats = [
                    (h, t) for h, t in session_chats
                    if self._matches_left_filter(t, needle)
                ]
                if needle and not filtered_chats:
                    continue
                header = QLabel(f"▸ {session_name} ({len(filtered_chats)})")
                header.setStyleSheet(
                    f"color: {COLORS['accent']}; font-size: 12px; "
                    "font-weight: 800; padding: 4px 2px 2px;"
                )
                header.setCursor(Qt.CursorShape.PointingHandCursor)
                header.mousePressEvent = lambda ev, p=pid: self._select_instance(p)
                self.left_rooms_container.addWidget(header)
                if not session_chats and not needle:
                    main_hwnd = inst.get("main_hwnd") or inst.get("any_hwnd")
                    if main_hwnd:
                        hint = LeftRoomRow(
                            "메인 화면 (톡방 없음)",
                            selected=(main_hwnd == self.selected_hwnd),
                        )
                        hint.select_clicked.connect(
                            lambda h=main_hwnd, t=session_name, p=pid: self._select_room_in_session(p, h, t)
                        )
                        self.left_rooms_container.addWidget(hint)
                for hwnd, title in filtered_chats:
                    row = LeftRoomRow(title, selected=(hwnd == self.selected_hwnd))
                    row.select_clicked.connect(
                        lambda h=hwnd, t=title, p=pid: self._select_room_in_session(p, h, t)
                    )
                    row.close_clicked.connect(
                        lambda h=hwnd, t=title: self.close_chat_window(h, t)
                    )
                    self.left_rooms_container.addWidget(row)
                    shown += 1
            if shown == 0 and needle:
                self._add_left_rooms_empty_hint(f"'{needle}' 검색 결과 없음")
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
        dialog_hwnds = tuple(h for h, _ in dialogs)
        if dialog_hwnds == self._last_dialog_hwnds:
            return
        self._last_dialog_hwnds = dialog_hwnds
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
        if not capture.is_window_alive(hwnd):
            log.warning("_select_kakao_window: hwnd %d is not alive, skipping", hwnd)
            return
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
        self,
        hwnd: int,
        title: str,
        *,
        focus_work_tab: bool = False,
        instance_pid: int | None = None,
    ) -> None:
        log.info("Selecting room: hwnd=%d title=%r", hwnd, title)
        self._viewing_dialog = False
        self.selected_hwnd = hwnd
        self.selected_title = title
        pid = instance_pid or get_pid_for_hwnd(hwnd)
        if pid:
            self.active_instance_pid = pid
        if pid and pid in self._session_panes:
            self._session_panes[pid].set_selection(hwnd, title)
            if self._use_multi_session_panes():
                self._focused_pane_pid = pid
                for p, pane in self._session_panes.items():
                    pane.set_focused(p == pid)
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
        if hasattr(self, "monitor_btn"):
            self._update_monitor_btn_text()
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
        if hasattr(self, "control_mode_label"):
            self._update_control_mode_label()

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
            "last_preset": getattr(self, "_last_ai_preset", ""),
        }
        save_workspace_settings(
            ui=ui,
            ai=ai,
            known_room_titles=sorted(self.known_rooms.keys()),
            reservations=self.reservations,
            instance_names=self.instance_names,
            kakao_exe_path=self.kakao_exe_path,
            ai_presets=getattr(self, "ai_presets", {}),
            monitor_rooms=self._active_monitor_rooms(),
        )

    def _active_monitor_rooms(self) -> list[dict]:
        rooms = [
            {"title": title, "keywords": self._monitor_keywords_by_title.get(title, [])}
            for title in self._monitor_titles.values()
        ]
        if rooms:
            return rooms
        # Keep last-saved set when nothing is active yet (e.g. before restore).
        return self._saved_monitor_rooms if not self._monitors_restored else []

    def _verify_main_list_click(self, rooms_before: int) -> None:
        after = len(self.chat_rooms)
        if after > rooms_before:
            self.set_status(f"목록 클릭 확인 · 열린 톡방 {after}개 (+{after - rooms_before})")
        else:
            self.set_status(
                "목록 클릭 — 새 톡방이 안 열린 것 같습니다. 위치를 다시 클릭하세요.",
                is_error=True,
            )

    def _verify_preview_click(self, label: str, hash_before: str) -> None:
        if self.selected_hwnd is None:
            self.set_status(f"{label} — 선택된 창 없음", is_error=True)
            return
        try:
            after = capture_image_hash(
                self.selected_hwnd, self.image_size_for_forwarding()
            )
        except Exception:
            return
        if hash_before and after and hash_before == after:
            self.set_status(
                f"{label} — 화면 변화 없음. 방 목록·채팅 본문을 클릭하거나 "
                "「창 앞으로」 후 다시 시도하세요.",
                is_error=True,
            )

    def _summarize_forward_detail(self, detail: str) -> str:
        low = detail.lower()
        if "차단" in detail or "unsafe" in low:
            return "차단"
        if "physical" in low or "fallback" in low or "eva/directui" in low:
            return "실커서"
        if "role=room_list" in low or detail.startswith("A+"):
            return "방목록직접"
        if "uia" in low:
            return "UIA"
        if "toplevel" in low:
            return "백그라운드(루트)"
        return "백그라운드"

    def _update_control_mode_label(self, checked: bool | None = None) -> None:
        on = self.forward_click_checkbox.isChecked() if checked is None else bool(checked)
        if on:
            self.control_mode_label.setText(
                "조작: ON — 미리보기 클릭·스크롤이 카카오톡에 전달됩니다"
            )
            self.control_mode_label.setStyleSheet(
                f"color: {COLORS['accent']}; font-size: 12px; font-weight: 700;"
            )
            border = COLORS["accent"]
        else:
            self.control_mode_label.setText(
                "조작: OFF — 「클릭/스크롤 전달」을 켜야 카카오톡 조작이 됩니다"
            )
            self.control_mode_label.setStyleSheet(
                f"color: {COLORS['muted']}; font-size: 12px; font-weight: 700;"
            )
            border = COLORS["border"]
        for label in (
            getattr(self, "preview_label", None),
            getattr(self, "main_preview_label", None),
        ):
            if label is not None:
                label.setStyleSheet(
                    f"background:#090909; border:2px solid {border}; border-radius:6px;"
                )

    # -------------------------------------------------------------------
    # Status
    # -------------------------------------------------------------------

    def set_status(
        self, message: str, *, is_error: bool | None = None, kind: str | None = None
    ) -> None:
        if kind is None:
            if is_error is True:
                kind = "error"
            elif any(k in message for k in ("실패", "오류", "ERROR")):
                kind = "error"
            elif message.rstrip().endswith("중…") or "중… " in message:
                kind = "busy"
            elif any(k in message for k in ("완료", "성공", "읽었습니다", "적용", "확인")):
                kind = "success"
            else:
                kind = "info"
        color, icon = STATUS_KIND_STYLE.get(kind, (COLORS["text"], ""))
        display = f"{icon}{message}"

        self.summary_label.setText(display)
        self.summary_label.setStyleSheet(
            f"color: {color}; font-weight: 600; font-size: 14px;"
        )
        short = display if len(display) <= 72 else display[:70] + "…"
        self.status_action_label.setText(short)
        self.status_action_label.setStyleSheet(
            f"color: {color}; font-size: 12px; font-weight: 700;"
        )
        if kind == "error":
            self._set_log_error_indicator(True)
        self._status_action_timer.stop()
        self._status_action_timer.start(8000)

    # -------------------------------------------------------------------
    # Async helpers (keep the UI responsive during slow operations)
    # -------------------------------------------------------------------

    def _run_async(self, fn, on_done, *, busy_button=None, busy_label="", busy_text=""):
        """Run `fn` on a worker thread; call on_done(result) on the UI thread.

        Shows a busy cursor + status and disables `busy_button` until finished.
        Errors are surfaced to the status bar (and on_done is not called).
        """
        if not hasattr(self, "_active_workers"):
            self._active_workers = []
        if busy_button is not None:
            busy_button.setEnabled(False)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        if busy_text:
            self.set_status(busy_text)

        worker = _AsyncWorker(fn, self)

        def _restore() -> None:
            QApplication.restoreOverrideCursor()
            if busy_button is not None:
                busy_button.setEnabled(True)
                if busy_label:
                    busy_button.setText(busy_label)
            if worker in self._active_workers:
                self._active_workers.remove(worker)

        def _on_done(result) -> None:
            _restore()
            try:
                on_done(result)
            except Exception as exc:  # pragma: no cover - UI callback
                log.error("async on_done failed: %s\n%s", exc, traceback.format_exc())
                self.set_status(f"처리 실패 · {exc}", is_error=True)

        def _on_fail(msg: str) -> None:
            _restore()
            self.set_status(f"실패 · {msg}", is_error=True)

        worker.done.connect(_on_done)
        worker.failed.connect(_on_fail)
        self._active_workers.append(worker)
        worker.start()

    # -------------------------------------------------------------------
    # Multi-session panes (grid / tabs)
    # -------------------------------------------------------------------

    def _use_multi_session_panes(self) -> bool:
        return len(self.kakao_instances) > 1

    def _effective_layout_mode(self) -> str:
        if not self._use_multi_session_panes():
            return "single"
        idx = self.layout_mode_combo.currentIndex()
        if idx == 1:
            return "grid"
        if idx == 2:
            return "tabs"
        n = len(self.kakao_instances)
        if n >= 5 or self.width() < NARROW_WIDTH_FOR_TAB_LAYOUT:
            return "tabs"
        return "grid"

    def _on_layout_mode_changed(self, _index: int) -> None:
        if self._expanded_pane_pid is not None:
            self._collapse_session_pane()
        self._rebuild_session_pane_layout()
        if self.auto_refresh_checkbox.isChecked():
            self._refresh_all_session_panes()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self._expanded_pane_pid is not None:
            return
        if self._use_multi_session_panes() and self.layout_mode_combo.currentIndex() == 0:
            self._rebuild_session_pane_layout()

    def _get_focused_pane(self) -> SessionPane | None:
        if self._focused_pane_pid is None:
            return None
        return self._session_panes.get(self._focused_pane_pid)

    def _sync_globals_from_focused_pane(self) -> None:
        pane = self._get_focused_pane()
        if pane is None:
            return
        self.selected_hwnd = pane.selected_hwnd
        self.selected_title = pane.selected_title
        self.active_instance_pid = pane.pid
        self.last_image = pane.last_image
        self._update_input_target_labels()
        self._update_status_strip_session()

    def _focus_session_pane(self, pid: int) -> None:
        if pid not in self._session_panes:
            return
        self._focused_pane_pid = pid
        for p, pane in self._session_panes.items():
            pane.set_focused(p == pid)
        mode = self._effective_layout_mode()
        if mode == "tabs" and self._multi_pane_tab_widget is not None:
            for i in range(self._multi_pane_tab_widget.count()):
                w = self._multi_pane_tab_widget.widget(i)
                if isinstance(w, SessionPane) and w.pid == pid:
                    self._multi_pane_tab_widget.setCurrentIndex(i)
                    break
        self._sync_globals_from_focused_pane()
        self._rebuild_session_cards()
        self._rebuild_left_rooms()
        self._rebuild_room_lists()

    def _wire_session_pane(self, pane: SessionPane) -> None:
        pid = pane.pid
        pane.focused.connect(lambda p=pid: self._focus_session_pane(p))
        pane.preview_clicked.connect(
            lambda x, y, p=pid: self._on_pane_preview_click(p, x, y)
        )
        pane.preview_double_clicked.connect(
            lambda x, y, p=pid: self._on_pane_preview_double_click(p, x, y)
        )
        pane.preview_wheeled.connect(
            lambda x, y, d, p=pid: self._on_pane_preview_wheel(p, x, y, d)
        )
        pane.preview_dragged.connect(
            lambda sx, sy, ex, ey, p=pid: self._on_pane_preview_drag(p, sx, sy, ex, ey)
        )
        pane.input_changed.connect(lambda t, p=pid: self._on_pane_input_changed(p, t))
        pane.enter_pressed.connect(lambda p=pid: self._on_pane_enter_pressed(p))
        pane.expand_requested.connect(lambda p=pid: self._expand_session_pane(p))
        pane.collapse_requested.connect(self._collapse_session_pane)

    def _show_expanded_pane(self, pid: int) -> None:
        host = self._multi_pane_expanded_host
        if host is None or pid not in self._session_panes:
            return
        lay = host.layout()
        if lay is None:
            lay = QVBoxLayout(host)
            lay.setContentsMargins(0, 0, 0, 0)
        else:
            while lay.count():
                item = lay.takeAt(0)
                w = item.widget()
                if w is not None:
                    w.setParent(None)
        for p, pane in self._session_panes.items():
            pane.set_expanded(p == pid)
        pane = self._session_panes[pid]
        lay.addWidget(pane)
        if self._multi_pane_stack is not None:
            self._multi_pane_stack.setCurrentIndex(2)
        inst = self.kakao_instances.get(pid, {})
        name = self._instance_display_name(pid, inst)
        self.multi_session_status_label.setText(
            f"확대 · {name} — Esc 또는 ⤡ 로 격자 복귀"
        )

    def _expand_session_pane(self, pid: int) -> None:
        if pid not in self._session_panes or not self._use_multi_session_panes():
            return
        self._expanded_pane_pid = pid
        self._focus_session_pane(pid)
        self._show_expanded_pane(pid)
        if self.auto_refresh_checkbox.isChecked():
            self._refresh_session_pane(self._session_panes[pid])

    def _collapse_session_pane(self) -> None:
        if self._expanded_pane_pid is None:
            return
        for pane in self._session_panes.values():
            pane.set_expanded(False)
        self._expanded_pane_pid = None
        self._rebuild_session_pane_layout()
        if self.auto_refresh_checkbox.isChecked():
            self._refresh_all_session_panes()

    def _switch_session_by_index(self, index: int) -> None:
        pids = sorted(self._session_panes.keys())
        if index < 0 or index >= len(pids):
            return
        pid = pids[index]
        if self._expanded_pane_pid is not None:
            self._expand_session_pane(pid)
        else:
            self._focus_session_pane(pid)
            self._select_instance(pid)

    def _ensure_session_panes(self) -> None:
        current_pids = set(self.kakao_instances.keys())
        if self._expanded_pane_pid is not None and self._expanded_pane_pid not in current_pids:
            self._expanded_pane_pid = None
        for pid in list(self._session_panes.keys()):
            if pid not in current_pids:
                self._session_panes.pop(pid).deleteLater()
        for pid, inst in self.kakao_instances.items():
            name = self._instance_display_name(pid, inst)
            if pid not in self._session_panes:
                pane = SessionPane(pid, name)
                self._wire_session_pane(pane)
                self._session_panes[pid] = pane
            else:
                self._session_panes[pid].set_display_name(name)
            pane = self._session_panes[pid]
            if pane.selected_hwnd is None:
                chats = inst.get("chats") or []
                if chats:
                    pane.set_selection(chats[0][0], chats[0][1])
                else:
                    hwnd = inst.get("main_hwnd") or inst.get("any_hwnd")
                    if hwnd:
                        title = inst.get("main_title", name)
                        pane.set_selection(hwnd, title)
        if self._focused_pane_pid not in current_pids:
            self._focused_pane_pid = next(iter(current_pids), None)
        if self._focused_pane_pid is not None:
            self._focus_session_pane(self._focused_pane_pid)

    def _rebuild_session_pane_layout(self) -> None:
        n = len(self.kakao_instances)
        if not self._use_multi_session_panes():
            self._expanded_pane_pid = None
            self._session_view_stack.setCurrentIndex(0)
            self.multi_session_status_label.setText("")
            return
        self._session_view_stack.setCurrentIndex(1)
        self._ensure_session_panes()
        if (
            self._expanded_pane_pid is not None
            and self._expanded_pane_pid in self._session_panes
        ):
            self._show_expanded_pane(self._expanded_pane_pid)
            return
        pids = sorted(self.kakao_instances.keys())
        mode = self._effective_layout_mode()
        self.multi_session_status_label.setText(
            f"멀티 세션 {n}개 · {'탭' if mode == 'tabs' else '격자'} 보기"
        )
        if mode == "tabs":
            self._multi_pane_stack.setCurrentIndex(1)
            tabs = self._multi_pane_tab_widget
            if tabs is None:
                return
            while tabs.count():
                w = tabs.widget(0)
                tabs.removeTab(0)
                if w is not None:
                    w.setParent(None)
            for pid in pids:
                pane = self._session_panes[pid]
                tabs.addTab(pane, self._instance_display_name(pid))
        else:
            self._multi_pane_stack.setCurrentIndex(0)
            host = self._multi_pane_grid_host
            if host is None:
                return
            lay = host.layout()
            if lay is None:
                lay = QVBoxLayout(host)
                lay.setContentsMargins(0, 0, 0, 0)
            else:
                while lay.count():
                    item = lay.takeAt(0)
                    w = item.widget()
                    if w is not None:
                        w.setParent(None)
            if n == 2:
                splitter = QSplitter(Qt.Orientation.Horizontal)
                for pid in pids:
                    splitter.addWidget(self._session_panes[pid])
                splitter.setSizes([1, 1])
                lay.addWidget(splitter)
            else:
                grid = QGridLayout()
                grid.setContentsMargins(0, 0, 0, 0)
                grid.setSpacing(6)
                grid_host = QWidget()
                grid_host.setLayout(grid)
                cols = 2
                for i, pid in enumerate(pids[:4]):
                    grid.addWidget(self._session_panes[pid], i // cols, i % cols)
                if n > 4:
                    note = QLabel(f"+ {n - 4}개 세션 — 레이아웃을 「세션 탭」으로 전환하세요")
                    note.setStyleSheet(f"color: {COLORS['muted']}; font-size: 11px;")
                    grid.addWidget(note, 2, 0, 1, 2)
                lay.addWidget(grid_host)

    def _select_room_in_session(self, pid: int, hwnd: int, title: str) -> None:
        self._focus_session_pane(pid)
        pane = self._session_panes.get(pid)
        if pane:
            pane.set_selection(hwnd, title)
        self.set_selected_room(hwnd, title, focus_work_tab=True, instance_pid=pid)

    def _pane_is_chat(self, pane: SessionPane) -> bool:
        if pane.selected_hwnd is None:
            return False
        if self._viewing_dialog and pane.selected_hwnd == self.active_dialog_hwnd:
            return False
        return any(h == pane.selected_hwnd for h, _ in self.chat_rooms)

    def _refresh_session_pane(self, pane: SessionPane) -> None:
        crop = self.crop_spin.value() if self._pane_is_chat(pane) else 0
        pane.refresh_preview(crop_px=crop, is_chat=self._pane_is_chat(pane))

    def _refresh_all_session_panes(self) -> None:
        if not self._use_multi_session_panes():
            return
        for pane in self._session_panes.values():
            self._refresh_session_pane(pane)
        self._sync_globals_from_focused_pane()

    def _validate_pane_hwnd(self, pane: SessionPane) -> bool:
        if pane.selected_hwnd is None:
            return False
        if not capture.is_window_valid(pane.selected_hwnd):
            self.set_status(f"[{pane.display_name}] 창이 유효하지 않음 — 새로고침 중")
            pane.selected_hwnd = None
            self._schedule_refresh_candidates()
            return False
        return True

    def _on_pane_preview_click(self, pid: int, image_x: int, image_y: int) -> None:
        self._focus_session_pane(pid)
        pane = self._session_panes.get(pid)
        if pane is None or not self._validate_pane_hwnd(pane):
            return
        if not self.forward_click_checkbox.isChecked():
            pane.status_label.setText("조작 OFF")
            self.set_status("조작 꺼짐 — 「클릭/스크롤 전달」을 켜세요", is_error=True)
            return
        img_size = pane.image_size_for_forwarding(self.crop_spin.value())
        hash_before = ""
        try:
            hash_before = capture_image_hash(pane.selected_hwnd, img_size)
        except Exception:
            pass
        try:
            detail = post_background_click(
                pane.selected_hwnd,
                image_x,
                image_y,
                img_size,
            )
            mode = self._summarize_forward_detail(detail)
            pane.status_label.setText(f"클릭 ({image_x},{image_y}) · {mode}")
            self.set_status(
                f"[{pane.display_name}] 클릭 ({image_x},{image_y}) · {mode} · {detail}"
            )
            log.info("Pane click pid=%s: %s", pid, detail)
            QTimer.singleShot(300, lambda: self._refresh_session_pane(pane))
            if hash_before:
                QTimer.singleShot(
                    500,
                    lambda: self._verify_pane_click(pane, hash_before, img_size),
                )
        except Exception as exc:
            log.error("Pane click failed pid=%s: %s", pid, exc)
            pane.status_label.setText("클릭 실패")
            self.set_status(f"[{pane.display_name}] 클릭 실패 · {exc}", is_error=True)
            QMessageBox.warning(
                self,
                "클릭 전달 실패",
                f"{exc}\n\n방 목록·채팅 본문을 클릭하세요. 입력창 영역은 차단됩니다.",
            )

    def _verify_pane_click(
        self,
        pane: SessionPane,
        hash_before: str,
        image_size: tuple[int, int],
    ) -> None:
        if pane.selected_hwnd is None:
            return
        try:
            after = capture_image_hash(pane.selected_hwnd, image_size)
        except Exception:
            return
        if hash_before and after and hash_before == after:
            pane.status_label.setText("변화 없음?")
            self.set_status(
                f"[{pane.display_name}] 클릭 — 화면 변화 없음. 위치를 조정하거나 「창 앞으로」",
                is_error=True,
            )

    def _on_pane_preview_double_click(self, pid: int, image_x: int, image_y: int) -> None:
        if not self.forward_double_click_checkbox.isChecked():
            return
        self._focus_session_pane(pid)
        pane = self._session_panes.get(pid)
        if pane is None or not self._validate_pane_hwnd(pane):
            return
        try:
            detail = post_background_double_click(
                pane.selected_hwnd,
                image_x,
                image_y,
                pane.image_size_for_forwarding(self.crop_spin.value()),
            )
            self.set_status(f"[{pane.display_name}] 더블클릭 · {detail}")
            QTimer.singleShot(300, lambda: self._refresh_session_pane(pane))
        except Exception as exc:
            QMessageBox.warning(self, "더블클릭 전달 실패", str(exc))

    def _on_pane_preview_wheel(self, pid: int, image_x: int, image_y: int, delta: int) -> None:
        self._focus_session_pane(pid)
        pane = self._session_panes.get(pid)
        if pane is None or not self._validate_pane_hwnd(pane):
            return
        if not self.forward_click_checkbox.isChecked():
            return
        try:
            detail = post_background_wheel(
                pane.selected_hwnd,
                image_x,
                image_y,
                pane.image_size_for_forwarding(self.crop_spin.value()),
                delta,
            )
            self.set_status(f"[{pane.display_name}] 스크롤 · {detail}")
            QTimer.singleShot(300, lambda: self._refresh_session_pane(pane))
        except Exception as exc:
            QMessageBox.warning(self, "스크롤 전달 실패", str(exc))

    def _on_pane_preview_drag(
        self, pid: int, sx: int, sy: int, ex: int, ey: int
    ) -> None:
        if not self.forward_click_checkbox.isChecked():
            return
        self._focus_session_pane(pid)
        pane = self._session_panes.get(pid)
        if pane is None or not self._validate_pane_hwnd(pane):
            return
        try:
            detail = post_background_drag(
                pane.selected_hwnd,
                sx,
                sy,
                ex,
                ey,
                pane.image_size_for_forwarding(self.crop_spin.value()),
            )
            self.set_status(f"[{pane.display_name}] 드래그 · {detail}")
            QTimer.singleShot(400, lambda: self._refresh_session_pane(pane))
        except Exception as exc:
            QMessageBox.warning(self, "드래그 전달 실패", str(exc))

    def _on_pane_input_changed(self, pid: int, text: str) -> None:
        self._focus_session_pane(pid)
        pane = self._session_panes.get(pid)
        if pane is None or pane.selected_hwnd is None:
            return
        if not text:
            return
        try:
            type_text_to_chat_background(pane.selected_hwnd, text)
        except Exception as exc:
            log.debug("Pane input failed pid=%s: %s", pid, exc)

    def _on_pane_enter_pressed(self, pid: int) -> None:
        self._focus_session_pane(pid)
        pane = self._session_panes.get(pid)
        if pane is None or not self._validate_pane_hwnd(pane):
            return
        text = pane.screen_input.text().strip()
        if not text:
            return
        if not self._verify_send_target(
            pane.selected_hwnd, pane.selected_title, label=f"[{pane.display_name}] 전송"
        ):
            return
        try:
            press_enter_in_chat(pane.selected_hwnd)
            pane.screen_input.clear()
            self.set_status(f"[{pane.display_name}] 전송 완료", kind="success")
            QTimer.singleShot(500, lambda: self._refresh_session_pane(pane))
        except Exception as exc:
            QMessageBox.warning(self, "전송 실패", str(exc))

    def _send_text_to_hwnd(self, hwnd: int, text: str) -> None:
        if self.background_input_checkbox.isChecked():
            send_text_to_chat_background(hwnd, text)
        else:
            send_text_to_chat(hwnd, text)

    def _is_known_chat_hwnd(self, hwnd: int) -> bool:
        if any(h == hwnd for h, _ in self.chat_rooms):
            return True
        for inst in self.kakao_instances.values():
            if any(h == hwnd for h, _ in (inst.get("chats") or [])):
                return True
        return False

    def _verify_send_target(
        self, hwnd: int | None, expected_title: str, *, label: str = "전송"
    ) -> bool:
        """Three-layer misfire guard before any real send.

        1) window is alive/valid, 2) current window title EXACTLY matches the
        expected room (catches HWND reuse / wrong-window), 3) the window is a
        tracked chat room (not the main/list window). Layer 3 asks for explicit
        confirmation rather than hard-blocking, to avoid false negatives.
        Returns True only if it is safe to send.
        """
        if hwnd is None or not capture.is_window_valid(hwnd):
            self._record_guard_block(label, "창 유효하지 않음(닫힘/최소화)", expected_title, "")
            self.set_status(
                f"{label} 차단 · 대상 창이 유효하지 않습니다(닫힘/최소화).", is_error=True
            )
            self._schedule_refresh_candidates()
            return False
        current_title = capture._safe_title(hwnd)
        if expected_title and current_title != expected_title:
            self._record_guard_block(label, "제목 불일치", expected_title, current_title)
            self.set_status(
                f"{label} 차단 · 창 제목 불일치(기대 '{expected_title}' ≠ 실제 '{current_title}')",
                is_error=True,
            )
            self._schedule_refresh_candidates()
            return False
        sig = self._send_signatures.get(hwnd)
        if sig is not None:
            current_sig = (capture._safe_class_name(hwnd), get_pid_for_hwnd(hwnd))
            if (sig[0], sig[1]) != current_sig:
                self._record_guard_block(
                    label, "시그니처 불일치(핸들 재사용)", str((sig[0], sig[1])), str(current_sig)
                )
                self.set_status(
                    f"{label} 차단 · 창 시그니처 불일치(핸들 재사용 의심).", is_error=True
                )
                self._schedule_refresh_candidates()
                return False
        if not self._is_known_chat_hwnd(hwnd):
            confirm = QMessageBox.question(
                self,
                "발송 대상 확인",
                f"'{current_title}' 창이 열린 톡방 목록에 없습니다.\n그래도 전송할까요?",
            )
            if confirm != QMessageBox.StandardButton.Yes:
                self._record_guard_block(label, "대상 미확인(사용자 취소)", expected_title, current_title)
                self.set_status(f"{label} 취소 · 대상 미확인", is_error=True)
                return False
        return True

    def _record_guard_block(
        self, label: str, reason: str, expected: str, actual: str
    ) -> None:
        rec = {
            "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "label": label,
            "reason": reason,
            "expected": expected,
            "actual": actual,
        }
        self._guard_blocks.append(rec)
        log.warning(
            "Send guard blocked: %s · %s · 기대=%r 실제=%r",
            label, reason, expected, actual,
        )
        if hasattr(self, "guard_btn"):
            self.guard_btn.setText(f"가드 {len(self._guard_blocks)}")
        try:
            with (ROOT_DIR / "v3_guard_blocks.jsonl").open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception:
            log.debug("guard block persist failed", exc_info=True)

    def _show_guard_log(self) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle("발송 가드 차단 로그")
        dlg.resize(720, 460)
        dlg.setStyleSheet(f"background: {COLORS['bg']}; color: {COLORS['text']};")
        layout = QVBoxLayout(dlg)
        summary = QLabel(f"누적 차단 {len(self._guard_blocks)}건 (오발송 방지)")
        summary.setStyleSheet("font-weight: 800; font-size: 14px;")
        layout.addWidget(summary)
        viewer = QTextEdit()
        viewer.setReadOnly(True)
        viewer.setStyleSheet(
            f"background: {COLORS['card']}; border: 1px solid {COLORS['border']}; "
            "font-family: 'Consolas', monospace; font-size: 12px;"
        )
        if self._guard_blocks:
            lines = [
                f"[{r['ts']}] {r['label']} · {r['reason']}\n"
                f"    기대='{r['expected']}'  실제='{r['actual']}'"
                for r in self._guard_blocks[-200:]
            ]
            viewer.setPlainText("\n".join(lines))
            viewer.moveCursor(viewer.textCursor().MoveOperation.End)
        else:
            viewer.setPlainText("차단 내역 없음 — 안전하게 발송되고 있습니다.")
        layout.addWidget(viewer)
        btn_row = QHBoxLayout()
        clear_btn = GhostButton("기록 초기화")

        def _clear() -> None:
            self._guard_blocks.clear()
            self.guard_btn.setText("가드 0")
            viewer.setPlainText("차단 내역 없음 — 안전하게 발송되고 있습니다.")

        clear_btn.clicked.connect(_clear)
        close_btn = GhostButton("닫기")
        close_btn.clicked.connect(dlg.close)
        btn_row.addWidget(clear_btn)
        btn_row.addStretch()
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)
        dlg.exec()

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
            if self._use_multi_session_panes():
                self._refresh_all_session_panes()
            else:
                self.refresh_preview()

    def _schedule_refresh_candidates(self, delay_ms: int = 2000) -> None:
        if self._pending_refresh_scheduled:
            return
        self._pending_refresh_scheduled = True
        QTimer.singleShot(delay_ms, self.refresh_candidates)

    def refresh_preview(self) -> None:
        if not self._is_local_pc_active():
            return
        if self.selected_hwnd is None:
            placeholder = (
                "왼쪽 「열린 톡방」에서 대화방을 선택하세요.\n"
                "선택하면 여기에 실시간 미리보기가 표시됩니다."
            )
            if self.preview_label.text() != placeholder:
                self.preview_label.setText(placeholder)
            return

        if not capture.is_window_alive(self.selected_hwnd):
            log.warning("hwnd %d (%s) is no longer valid",
                        self.selected_hwnd, self.selected_title)
            self.selected_hwnd = None
            self.selected_title = ""
            self._update_input_target_labels()
            self._schedule_refresh_candidates()
            return

        if capture.is_window_minimized(self.selected_hwnd):
            self.preview_label.setText("창이 최소화 상태입니다")
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
            self.preview_label.setUpdatesEnabled(False)
            self.preview_label.set_source_size(cropped.size)
            self.preview_label.setPixmap(scaled)
            self.preview_label.setUpdatesEnabled(True)
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
                self._schedule_refresh_candidates()
            else:
                self.set_status(f"갱신 실패 ({self._refresh_error_count}/3) · {exc}")

    def refresh_main_preview(self) -> None:
        if self.main_hwnd is None:
            self.main_preview_label.setText("카카오톡 메인 창 없음")
            return
        if not capture.is_window_alive(self.main_hwnd):
            log.warning("Main hwnd %d is no longer alive", self.main_hwnd)
            self.main_hwnd = None
            self.main_preview_label.setText("카카오톡 메인 창이 닫혔습니다")
            return
        if capture.is_window_minimized(self.main_hwnd):
            self.main_preview_label.setText("카카오톡 메인 창 최소화 상태")
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
            self.main_preview_label.setUpdatesEnabled(False)
            self.main_preview_label.set_source_size(image.size)
            self.main_preview_label.setPixmap(scaled)
            self.main_preview_label.setUpdatesEnabled(True)
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
        if not capture.is_window_valid(self.selected_hwnd):
            self.set_status("선택된 창이 유효하지 않음")
            self._schedule_refresh_candidates()
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
            self.set_status(f"드래그 전달 실패 · {exc}")

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
        if not self.forward_click_checkbox.isChecked():
            self.set_status("조작 꺼짐 — 「클릭/스크롤 전달」을 켜세요", is_error=True)
            return
        if self.selected_hwnd is None:
            return
        if not capture.is_window_valid(self.selected_hwnd):
            self.set_status("선택된 창이 유효하지 않음")
            self._schedule_refresh_candidates()
            return
        img_size = self.image_size_for_forwarding()
        hash_before = ""
        try:
            hash_before = capture_image_hash(self.selected_hwnd, img_size)
        except Exception:
            pass
        try:
            detail = post_background_click(
                self.selected_hwnd, image_x, image_y, img_size
            )
            mode = self._summarize_forward_detail(detail)
            log.info("Preview click (%s,%s): %s", image_x, image_y, detail)
            self.set_status(
                f"클릭 ({image_x},{image_y}) · {mode} · {detail}"
            )
            self.room_preview_status.setText(f"클릭 · {mode}")
            QTimer.singleShot(300, self.refresh_preview)
            if hash_before:
                QTimer.singleShot(
                    500, lambda: self._verify_preview_click("클릭", hash_before)
                )
        except Exception as exc:
            log.error("Preview click failed: %s\n%s", exc, traceback.format_exc())
            self.set_status(f"클릭 전달 실패 · {exc}", is_error=True)
            QMessageBox.warning(
                self,
                "클릭 전달 실패",
                f"{exc}\n\n입력창 영역은 차단됩니다. 방 목록·채팅 본문을 클릭하세요.",
            )

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
        if not capture.is_window_valid(self.selected_hwnd):
            self.set_status("선택된 창이 유효하지 않음")
            self._schedule_refresh_candidates()
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
            self.set_status(f"더블클릭 전달 실패 · {exc}")

    def handle_main_preview_double_click(self, image_x: int, image_y: int) -> None:
        if self.main_hwnd is None or not capture.is_window_valid(self.main_hwnd):
            return
        self._ignore_next_click = True
        try:
            size = self.last_main_image.size if self.last_main_image else (1, 1)
            detail = post_background_double_click(self.main_hwnd, image_x, image_y, size)
            self.set_status(f"목록 더블클릭 · {detail}")
            QTimer.singleShot(700, self.refresh_candidates)
        except Exception as exc:
            self.set_status(f"목록 더블클릭 실패 · {exc}")

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
        if not self.forward_click_checkbox.isChecked():
            self.set_status("조작 꺼짐 — 스크롤 전달이 꺼져 있습니다", is_error=True)
            return
        if self.selected_hwnd is None:
            return
        if not capture.is_window_valid(self.selected_hwnd):
            self.set_status("선택된 창이 유효하지 않음")
            self._schedule_refresh_candidates()
            return
        try:
            detail = post_background_wheel(
                self.selected_hwnd,
                image_x,
                image_y,
                self.image_size_for_forwarding(),
                delta,
            )
            mode = self._summarize_forward_detail(detail)
            log.info("Preview wheel: %s", detail)
            self.set_status(f"스크롤 ({image_x},{image_y}) · {mode} · {detail}")
            self.room_preview_status.setText(f"스크롤 · {mode}")
            QTimer.singleShot(300, self.refresh_preview)
        except Exception as exc:
            log.error("Preview wheel failed: %s\n%s", exc, traceback.format_exc())
            self.set_status(f"스크롤 전달 실패 · {exc}", is_error=True)

    def handle_main_preview_click(self, image_x: int, image_y: int) -> None:
        if self.main_hwnd is None or not capture.is_window_valid(self.main_hwnd):
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
            self.set_status(f"목록 클릭 실패 · {exc}")

    def handle_main_preview_wheel(self, image_x: int, image_y: int, delta: int) -> None:
        if self.main_hwnd is None or not capture.is_window_valid(self.main_hwnd):
            return
        try:
            size = self.last_main_image.size if self.last_main_image else (1, 1)
            detail = post_background_wheel(self.main_hwnd, image_x, image_y, size, delta)
            log.debug("Main list wheel: %s", detail)
            self.set_status(f"목록 스크롤 · {detail}")
            QTimer.singleShot(300, self.refresh_main_preview)
        except Exception as exc:
            log.error("Main list wheel failed: %s\n%s", exc, traceback.format_exc())
            self.set_status(f"목록 스크롤 실패 · {exc}")

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
        if not self._verify_send_target(self.selected_hwnd, self.selected_title, label="Enter 전송"):
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
        if self._use_multi_session_panes():
            self._sync_globals_from_focused_pane()
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
        if not self._verify_send_target(self.selected_hwnd, self.selected_title, label="보내기"):
            return
        try:
            self._send_text_to_hwnd(self.selected_hwnd, text)
            log.info("Sent to %r: %s", self.selected_title, text[:50])
            self.message_edit.clear()
            self.set_status("전송 완료", kind="success")
            QTimer.singleShot(1000, self.refresh_preview)
        except Exception as exc:
            log.error("Send failed: %s\n%s", exc, traceback.format_exc())
            QMessageBox.critical(self, "전송 실패", str(exc))

    # -------------------------------------------------------------------
    # Reservations
    # -------------------------------------------------------------------

    def add_reservation(self) -> None:
        if self._use_multi_session_panes():
            self._sync_globals_from_focused_pane()
        if not self.selected_title:
            QMessageBox.information(self, "선택 필요", "예약할 톡방을 선택하세요.")
            return
        text = self.message_edit.toPlainText().strip()
        if not text:
            QMessageBox.warning(self, "입력 필요", "예약 메시지를 입력하세요.")
            return
        minutes = self.reserve_minutes_spin.value()
        instance_pid = self.active_instance_pid
        pane = self._get_focused_pane()
        if pane is not None:
            instance_pid = pane.pid
        self.reservations.append(
            {
                "room": self.selected_title,
                "message": text,
                "send_at": datetime.now() + timedelta(minutes=minutes),
                "created_at": datetime.now(),
                "hwnd": self.selected_hwnd,
                "instance_pid": instance_pid,
            }
        )
        log.info("Reservation: %r in %d min", self.selected_title, minutes)
        self.set_status(f"{self.selected_title} · {minutes}분 후 예약")
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
        instance_pid = item.get("instance_pid")
        target_hwnd: int | None = None
        if hwnd and capture.is_window_valid(int(hwnd)):
            target_hwnd = int(hwnd)
        elif instance_pid is not None:
            inst = self.kakao_instances.get(int(instance_pid))
            if inst:
                for h, t in inst.get("chats") or []:
                    if t == title:
                        target_hwnd = h
                        break
        if target_hwnd is None:
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
        pid = int(instance_pid) if instance_pid is not None else None
        self.set_selected_room(
            target_hwnd, title, instance_pid=pid if pid else None
        )
        if not self._verify_send_target(target_hwnd, title, label=f"예약 전송({title})"):
            log.warning("Reservation send blocked by guard: %r", title)
            return
        try:
            self._send_text_to_hwnd(target_hwnd, text)
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
        else:
            for reservation in sorted_items:
                seconds = max(0, int((reservation["send_at"] - datetime.now()).total_seconds()))
                m, s = divmod(seconds, 60)
                preview = reservation["message"].replace("\n", " ")[:48]
                sess_prefix = ""
                ipid = reservation.get("instance_pid")
                if ipid is not None and int(ipid) in self.kakao_instances:
                    sn = self._instance_display_name(
                        int(ipid), self.kakao_instances[int(ipid)]
                    )
                    short_sn = sn if len(sn) <= 10 else sn[:8] + "…"
                    sess_prefix = f"[{short_sn}] "
                row = QLabel(
                    f"⏱ {m:02d}:{s:02d} · {sess_prefix}{reservation['room']}\n{preview}"
                )
                row.setStyleSheet(
                    f"background: {COLORS['bg']}; padding: 10px; border-radius: 8px; "
                    f"border: 1px solid {COLORS['border']};"
                )
                row.setWordWrap(True)
                self.reservation_container.addWidget(row)
        # 톡방 목록 전체 재구성은 30초마다만 (1초마다 하면 좌측/탭 UI가 떨림)
        count = len(self.reservations)
        force = count != self._last_reservation_count
        self._last_reservation_count = count
        self._reservation_room_rebuild_tick += 1
        if force or self._reservation_room_rebuild_tick >= 30:
            self._reservation_room_rebuild_tick = 0
            self._rebuild_room_lists()

    # -------------------------------------------------------------------
    # AI
    # -------------------------------------------------------------------

    # -------------------------------------------------------------------
    # AI presets
    # -------------------------------------------------------------------

    def _refresh_ai_preset_combo(self) -> None:
        if not hasattr(self, "ai_preset_combo"):
            return
        self.ai_preset_combo.blockSignals(True)
        self.ai_preset_combo.clear()
        names = sorted(self.ai_presets.keys())
        if names:
            self.ai_preset_combo.addItems(names)
            last = getattr(self, "_last_ai_preset", "")
            if last and last in names:
                self.ai_preset_combo.setCurrentText(last)
        else:
            self.ai_preset_combo.addItem("(저장된 프리셋 없음)")
        self.ai_preset_combo.blockSignals(False)

    def _collect_ai_preset(self) -> dict:
        return {
            "mode_index": self.ai_mode_combo.currentIndex(),
            "level": self.ai_level_spin.value(),
            "role": self.ai_role_edit.toPlainText(),
            "prompt": self.ai_prompt_edit.toPlainText(),
            "examples": self.ai_examples_edit.toPlainText(),
            "sources": self.ai_sources_edit.toPlainText(),
            "guardrails": self.ai_guardrails_edit.toPlainText(),
        }

    def _apply_ai_preset(self, cfg: dict) -> None:
        self.ai_mode_combo.setCurrentIndex(int(cfg.get("mode_index", 1)))
        self.ai_level_spin.setValue(int(cfg.get("level", 3)))
        self.ai_role_edit.setPlainText(cfg.get("role", ""))
        self.ai_prompt_edit.setPlainText(cfg.get("prompt", ""))
        self.ai_examples_edit.setPlainText(cfg.get("examples", ""))
        self.ai_sources_edit.setPlainText(cfg.get("sources", ""))
        self.ai_guardrails_edit.setPlainText(cfg.get("guardrails", ""))

    def _save_ai_preset(self) -> None:
        suggested = self.ai_preset_combo.currentText()
        if suggested.startswith("("):
            suggested = ""
        name, ok = QInputDialog.getText(
            self, "AI 프리셋 저장", "프리셋 이름:", text=suggested
        )
        name = (name or "").strip()
        if not ok or not name:
            return
        if name in self.ai_presets:
            confirm = QMessageBox.question(
                self, "덮어쓰기", f"'{name}' 프리셋을 덮어쓸까요?"
            )
            if confirm != QMessageBox.StandardButton.Yes:
                return
        self.ai_presets[name] = self._collect_ai_preset()
        self._last_ai_preset = name
        self._refresh_ai_preset_combo()
        idx = self.ai_preset_combo.findText(name)
        if idx >= 0:
            self.ai_preset_combo.setCurrentIndex(idx)
        self._save_workspace_settings_now()
        self.set_status(f"AI 프리셋 저장 · {name}", kind="success")

    def _load_selected_ai_preset(self) -> None:
        name = self.ai_preset_combo.currentText()
        cfg = self.ai_presets.get(name)
        if not cfg:
            self.set_status("불러올 프리셋이 없습니다.", is_error=True)
            return
        self._apply_ai_preset(cfg)
        self._last_ai_preset = name
        self._queue_save_workspace_settings()
        self.set_status(f"AI 프리셋 불러옴 · {name}", kind="success")

    def _delete_selected_ai_preset(self) -> None:
        name = self.ai_preset_combo.currentText()
        if name not in self.ai_presets:
            self.set_status("삭제할 프리셋이 없습니다.", is_error=True)
            return
        confirm = QMessageBox.question(self, "프리셋 삭제", f"'{name}' 프리셋을 삭제할까요?")
        if confirm != QMessageBox.StandardButton.Yes:
            return
        self.ai_presets.pop(name, None)
        self._refresh_ai_preset_combo()
        self._save_workspace_settings_now()
        self.set_status(f"AI 프리셋 삭제 · {name}", kind="success")

    # -------------------------------------------------------------------
    # New-message monitoring (foundation for auto-reply; never sends)
    # -------------------------------------------------------------------

    def _parse_monitor_keywords(self) -> list[str]:
        raw = self.monitor_keyword_edit.text()
        return [k.strip() for k in raw.split(",") if k.strip()]

    def _update_monitor_status_label(self) -> None:
        n = len(self._chat_monitors)
        if n == 0:
            self.monitor_status_label.setText(
                f"모니터링 꺼짐 · 오늘 감지 {self._monitor_event_count}건"
            )
        else:
            names = ", ".join(self._monitor_titles.get(h, "?") for h in self._chat_monitors)
            self.monitor_status_label.setText(
                f"{n}개 방 모니터링 중: {names[:40]} · 감지 {self._monitor_event_count}건"
            )

    def _toggle_chat_monitor(self) -> None:
        if not self._is_local_pc_active():
            QMessageBox.information(self, "로컬 전용", "모니터링은 이 PC의 톡방에서만 지원합니다.")
            return
        hwnd = self.selected_hwnd
        if hwnd is None or not self._is_known_chat_hwnd(hwnd):
            QMessageBox.information(self, "선택 필요", "모니터링할 톡방을 먼저 선택하세요.")
            return
        # Toggle: if already monitoring this room, stop it.
        if hwnd in self._chat_monitors:
            self._chat_monitors[hwnd].stop()
            self.monitor_status_label.setText("모니터링 중지 중…")
            return
        keywords = self._parse_monitor_keywords()
        self._monitor_keywords_by_title[self.selected_title] = keywords
        self._start_monitor_for(hwnd, self.selected_title, keywords)
        self._queue_save_workspace_settings()

    def _start_monitor_for(self, hwnd: int, title: str, keywords: list[str]) -> None:
        if hwnd in self._chat_monitors:
            return
        monitor = ChatMonitor(
            hwnd,
            title,
            interval_sec=self.monitor_interval_spin.value(),
            keywords=keywords,
        )
        monitor.new_messages.connect(lambda msgs, h=hwnd: self._on_monitor_new_messages(h, msgs))
        monitor.status.connect(self._on_monitor_status)
        monitor.failed.connect(lambda err, h=hwnd: self._on_monitor_failed(h, err))
        monitor.finished.connect(lambda h=hwnd: self._on_monitor_finished(h))
        self._chat_monitors[hwnd] = monitor
        self._monitor_titles[hwnd] = title
        self._update_monitor_btn_text()
        self._update_monitor_status_label()
        monitor.start()

    def _find_open_hwnd_by_title(self, title: str) -> int | None:
        for h, t in self.chat_rooms:
            if t == title:
                return h
        for inst in self.kakao_instances.values():
            for h, t in inst.get("chats") or []:
                if t == title:
                    return h
        return None

    def _restore_monitors_if_needed(self) -> None:
        if self._monitors_restored or not self._saved_monitor_rooms:
            return
        if not self._is_local_pc_active():
            return
        restored_any = False
        for room in self._saved_monitor_rooms:
            title = room.get("title", "")
            if not title:
                continue
            hwnd = self._find_open_hwnd_by_title(title)
            if hwnd is None or hwnd in self._chat_monitors:
                continue
            keywords = list(room.get("keywords") or [])
            self._monitor_keywords_by_title[title] = keywords
            self._start_monitor_for(hwnd, title, keywords)
            restored_any = True
        # Only finalize once we had a chance to see open rooms.
        if self.chat_rooms or self.kakao_instances:
            self._monitors_restored = True
        if restored_any:
            self.set_status("모니터링 상태 복원됨", kind="success")

    def _update_monitor_btn_text(self) -> None:
        hwnd = self.selected_hwnd
        if hwnd is not None and hwnd in self._chat_monitors:
            self.monitor_btn.setText("이 방 모니터링 중지")
        else:
            self.monitor_btn.setText("이 방 모니터링 시작")

    def _on_monitor_finished(self, hwnd: int) -> None:
        self._chat_monitors.pop(hwnd, None)
        self._monitor_titles.pop(hwnd, None)
        self._update_monitor_btn_text()
        self._update_monitor_status_label()
        self._queue_save_workspace_settings()

    def _on_monitor_status(self, text: str) -> None:
        self.monitor_status_label.setText(text)

    def _on_monitor_failed(self, hwnd: int, text: str) -> None:
        self.set_status(f"모니터링 실패 · {text}", is_error=True)
        self._chat_monitors.pop(hwnd, None)
        self._monitor_titles.pop(hwnd, None)
        self._update_monitor_btn_text()
        self._update_monitor_status_label()

    def _persist_monitor_events(self, title: str, messages: list) -> None:
        try:
            path = ROOT_DIR / "v3_monitor_events.jsonl"
            with path.open("a", encoding="utf-8") as fh:
                for m in messages:
                    rec = {
                        "ts": datetime.now().isoformat(timespec="seconds"),
                        "room": title,
                        "sender": m.get("sender", ""),
                        "time": m.get("time", ""),
                        "text": m.get("text", ""),
                    }
                    fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception:
            log.debug("monitor event persist failed", exc_info=True)

    def _on_monitor_new_messages(self, hwnd: int, messages: list) -> None:
        title = self._monitor_titles.get(hwnd, "?")
        ts = datetime.now().strftime("%H:%M:%S")
        for msg in messages:
            sender = msg.get("sender", "?")
            text = (msg.get("text", "") or "").replace("\n", " ")
            self.monitor_events.addItem(f"[{ts}] [{title[:10]}] {sender}: {text[:50]}")
        self.monitor_events.scrollToBottom()
        self._monitor_event_count += len(messages)
        self._update_monitor_status_label()
        self.set_status(f"새 메시지 {len(messages)}건 · {title}", kind="success")
        self._persist_monitor_events(title, messages)

        joined = "\n".join(
            f"[{m.get('time','')}] {m.get('sender','')}: {m.get('text','')}"
            for m in messages
        )
        if not joined:
            return
        # Feed into AI context (manual reply by default).
        existing = self.ai_chat_context_edit.toPlainText().strip()
        merged = (existing + "\n" + joined).strip() if existing else joined
        self.ai_chat_context_edit.setPlainText(merged[-4000:])

        # A: optional auto-generation of a reply candidate (NEVER sends).
        if self.monitor_autogen_chk.isChecked():
            self._auto_generate_for_monitor(title, joined)

    def _enqueue_suggestion(self, title: str, reply: str, context: str, params: dict) -> None:
        self._suggestion_queue.append(
            {"title": title, "reply": reply, "context": context, "params": params}
        )
        self._update_suggestion_btn()

    def _update_suggestion_btn(self) -> None:
        if not hasattr(self, "suggestion_btn"):
            return
        n = len(self._suggestion_queue)
        self.suggestion_btn.setText(f"AI 후보 {n}건")
        self.suggestion_btn.setVisible(n > 0)

    def _open_next_suggestion(self) -> None:
        if not self._suggestion_queue:
            return
        item = self._suggestion_queue.pop(0)
        self._update_suggestion_btn()
        self._open_ai_reply_dialog(
            item["reply"], context=item.get("context", ""), params=item.get("params")
        )

    def _auto_generate_for_monitor(self, title: str, context: str) -> None:
        mode = self.ai_mode_combo.currentText().split(" ", 1)[0]
        if mode == "off":
            return
        if self._monitor_generating:
            return
        self._monitor_generating = True
        params = dict(
            mode=mode,
            role=self.ai_role_edit.toPlainText().strip() or "친절한 상담원",
            prompt=self.ai_prompt_edit.toPlainText().strip() or "짧고 친절하게",
            examples=self.ai_examples_edit.toPlainText().strip() or "",
            sources=self.ai_sources_edit.toPlainText().strip() or "",
            guardrails=self.ai_guardrails_edit.toPlainText().strip() or "",
            level=self.ai_level_spin.value(),
        )

        def _done(reply: str) -> None:
            self._monitor_generating = False
            summary = (reply.strip().splitlines() or ["(빈 응답)"])[0][:50]
            self.monitor_events.addItem(f"  💡 AI 후보: {summary}")
            self.monitor_events.scrollToBottom()
            # Non-modal: enqueue the suggestion instead of popping a dialog.
            self._enqueue_suggestion(title, reply, context, params)
            self.set_status(
                f"AI 후보 대기 {len(self._suggestion_queue)}건 · {title} (전송 안 함)",
                kind="success",
            )

        def _fail_reset(*_a) -> None:
            self._monitor_generating = False

        worker = _AsyncWorker(lambda: self.generate_ai_reply(chat_context=context, **params), self)
        worker.done.connect(_done)
        worker.failed.connect(lambda msg: (_fail_reset(), self.set_status(f"AI 자동생성 실패 · {msg}", is_error=True)))
        if not hasattr(self, "_active_workers"):
            self._active_workers = []
        self._active_workers.append(worker)
        worker.finished.connect(lambda: self._active_workers.remove(worker) if worker in self._active_workers else None)
        self.set_status(f"AI 후보 자동생성 중… · {title}", kind="busy")
        worker.start()

    def read_chat_into_ai_context(self) -> None:
        if not self._is_local_pc_active():
            QMessageBox.information(
                self, "로컬 전용", "채팅 읽기는 이 PC의 열린 톡방에서만 지원합니다."
            )
            return
        if self.selected_hwnd is None:
            QMessageBox.information(self, "선택 필요", "톡방을 먼저 선택하세요.")
            return
        hwnd = self.selected_hwnd

        def _done(result) -> None:
            text, hint = result
            if text:
                self.ai_chat_context_edit.setPlainText(text)
                self._queue_save_workspace_settings()
                self.set_status(f"채팅 읽기 완료 · {hint}")
            else:
                self.set_status(hint, is_error=True)

        self._run_async(
            lambda: read_chat_context(hwnd),
            _done,
            busy_button=self.read_chat_btn,
            busy_label="채팅 읽기",
            busy_text="채팅 읽는 중… (카카오톡 창이 잠깐 앞으로 나옵니다)",
        )

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

        # Snapshot all UI inputs on the main thread; the worker must not touch widgets.
        params = dict(
            mode=mode,
            role=self.ai_role_edit.toPlainText().strip() or "친절한 상담원",
            prompt=self.ai_prompt_edit.toPlainText().strip() or "짧고 친절하게",
            examples=self.ai_examples_edit.toPlainText().strip() or "",
            sources=self.ai_sources_edit.toPlainText().strip() or "",
            guardrails=self.ai_guardrails_edit.toPlainText().strip() or "",
            level=self.ai_level_spin.value(),
        )
        existing_ctx = self.ai_chat_context_edit.toPlainText().strip()
        hwnd = self.selected_hwnd if self._is_local_pc_active() else None

        def _work():
            ctx = existing_ctx
            read_hint = ""
            if not ctx and hwnd:
                text, read_hint = read_chat_context(hwnd)
                if text:
                    ctx = text
            reply = self.generate_ai_reply(chat_context=ctx, **params)
            return reply, ctx, read_hint

        def _done(result) -> None:
            reply, ctx, _read_hint = result
            if ctx and not existing_ctx:
                self.ai_chat_context_edit.setPlainText(ctx)
                self._queue_save_workspace_settings()
            self._open_ai_reply_dialog(reply, context=ctx, params=params)

        self._run_async(
            _work,
            _done,
            busy_button=self.ai_btn,
            busy_label="AI 답변 후보 생성",
            busy_text="AI 답변 생성 중…",
        )

    def _open_ai_reply_dialog(
        self, reply: str, *, context: str = "", params: dict | None = None
    ) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle("AI 답변 후보")
        dlg.resize(680, 580)
        dlg.setStyleSheet(f"background: {COLORS['bg']}; color: {COLORS['text']};")
        layout = QVBoxLayout(dlg)

        # --- Context (editable): what the AI based its answer on ---
        ctx_label = QLabel("AI가 참고한 대화 (수정 후 「다른 후보 생성」하면 반영)")
        ctx_label.setStyleSheet(
            f"color: {COLORS['muted']}; font-weight: 700; font-size: 12px;"
        )
        layout.addWidget(ctx_label)
        ctx_view = QTextEdit()
        ctx_view.setPlainText(context)
        ctx_view.setPlaceholderText("참고할 대화 내용 (비어 있으면 캡처 이미지 사용)")
        ctx_view.setMaximumHeight(110)
        ctx_view.setStyleSheet(
            f"background: {COLORS['card']}; border: 1px solid {COLORS['border']}; "
            f"color: {COLORS['muted']}; font-size: 12px;"
        )
        layout.addWidget(ctx_view)

        candidates: list[str] = [reply]

        cand_header = QHBoxLayout()
        cand_label = QLabel("후보 (클릭해서 선택)")
        cand_label.setStyleSheet("font-weight: 700; font-size: 13px;")
        cand_header.addWidget(cand_label)
        cand_header.addStretch()
        compare_chk = QCheckBox("전체 비교 보기")
        compare_chk.setToolTip("모든 후보를 한 화면에서 비교합니다.")
        cand_header.addWidget(compare_chk)
        layout.addLayout(cand_header)
        cand_list = QListWidget()
        cand_list.setMaximumHeight(110)
        cand_list.setStyleSheet(
            f"background: {COLORS['card']}; border: 1px solid {COLORS['border']};"
        )
        layout.addWidget(cand_list)

        body = QTextEdit()
        body.setReadOnly(True)
        body.setStyleSheet(
            f"background: {COLORS['card']}; border: 1px solid {COLORS['border']};"
        )
        layout.addWidget(body, stretch=1)

        def _summary(text: str) -> str:
            stripped = text.strip()
            lines = [ln for ln in stripped.splitlines() if ln.strip()]
            head = lines[0] if lines else "(빈 응답)"
            badge = ""
            upper = stripped[:10].upper()
            if upper.startswith("[AUTO]"):
                badge = "✅ "
                head = head[len("[AUTO]"):].strip() or head
            elif upper.startswith("[REVIEW]"):
                badge = "🔎 "
                head = head[len("[REVIEW]"):].strip() or head
            return f"{badge}{head[:60]}"

        def _refresh_list() -> None:
            cand_list.blockSignals(True)
            cand_list.clear()
            for i, c in enumerate(candidates):
                cand_list.addItem(f"{i + 1}. {_summary(c)}")
            cand_list.setCurrentRow(len(candidates) - 1)
            cand_list.blockSignals(False)
            _show_current()

        def _show_current() -> None:
            if compare_chk.isChecked():
                blocks = [
                    f"──── 후보 {i + 1} ────\n{c}" for i, c in enumerate(candidates)
                ]
                body.setPlainText("\n\n".join(blocks))
                return
            i = cand_list.currentRow()
            if 0 <= i < len(candidates):
                body.setPlainText(candidates[i])

        compare_chk.toggled.connect(lambda _checked: _show_current())

        def _current_clean() -> str:
            i = cand_list.currentRow()
            text = candidates[i] if 0 <= i < len(candidates) else reply
            return self._clean_ai_reply_for_send(text)

        cand_list.currentRowChanged.connect(lambda _i: _show_current())

        btn_row = QHBoxLayout()
        regen_btn = GhostButton("다른 후보 생성")
        apply_bottom = PrimaryButton("하단 입력창에 적용")
        apply_screen = GhostButton("작업 탭 입력에 적용")
        apply_both = GhostButton("둘 다 적용")
        close_btn = GhostButton("닫기")

        def _regenerate() -> None:
            if not params:
                return
            edited_ctx = ctx_view.toPlainText().strip()

            def _on_new(new_reply: str) -> None:
                candidates.append(new_reply)
                _refresh_list()
                self.set_status(f"AI 후보 {len(candidates)}개 · {self.selected_title}", kind="success")

            self._run_async(
                lambda: self.generate_ai_reply(chat_context=edited_ctx, **params),
                _on_new,
                busy_button=regen_btn,
                busy_label="다른 후보 생성",
                busy_text="AI 후보 추가 생성 중…",
            )

        def _apply_bottom() -> None:
            self.message_edit.setPlainText(_current_clean())
            self.set_status(f"AI 후보 → 하단 입력 · {self.selected_title}", kind="success")

        def _apply_screen() -> None:
            clean = _current_clean()
            self._suppress_text_changed = True
            self.screen_input.setText(clean)
            self._suppress_text_changed = False
            if self._is_local_pc_active() and self.selected_hwnd:
                try:
                    type_text_to_chat_background(self.selected_hwnd, clean)
                except Exception as exc:
                    log.debug("AI apply to kakao failed: %s", exc)
            self.set_status(f"AI 후보 → 작업 입력 · {self.selected_title}", kind="success")

        def _apply_both() -> None:
            _apply_bottom()
            _apply_screen()

        regen_btn.clicked.connect(_regenerate)
        apply_bottom.clicked.connect(_apply_bottom)
        apply_screen.clicked.connect(_apply_screen)
        apply_both.clicked.connect(_apply_both)
        close_btn.clicked.connect(dlg.close)

        if params:
            btn_row.addWidget(regen_btn)
        btn_row.addWidget(apply_bottom)
        btn_row.addWidget(apply_screen)
        btn_row.addWidget(apply_both)
        btn_row.addStretch()
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

        _refresh_list()
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
        for monitor in list(self._chat_monitors.values()):
            try:
                monitor.stop()
                monitor.wait(2000)
            except Exception:
                pass
        if self._embedded_hub:
            self._embedded_hub.shutdown()
        super().closeEvent(event)


def main() -> int:
    parser = argparse.ArgumentParser(description="V3 Kakao workspace GUI prototype.")
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
        interval_ms=args.interval_ms,
        hide_native_input_px=args.hide_native_input_px,
    )
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
