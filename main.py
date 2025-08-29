import time
import sys
import random
import io
import pyperclip
import win32api
import win32con
import win32gui
import win32clipboard
import pandas as pd
from PyQt5.QtWidgets import (
    QApplication,
    QMainWindow,
    QTabWidget,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QLabel,
    QListWidget,
    QTextEdit,
    QFileDialog,
    QGroupBox,
    QRadioButton,
    QButtonGroup,
    QTimeEdit,
    QMessageBox,
    QSpinBox,
    QCheckBox
)
from PyQt5.QtCore import (
    QThread,
    pyqtSignal,
    QTimer,
    QDateTime,
    QTime,
    Qt
)
from PyQt5.QtGui import QFontDatabase, QFont
from PIL import Image

###############################################################################
# Pretendard 폰트 로드 및 스타일시트 적용 함수 (선택사항)
###############################################################################
def load_pretendard():
    font_id = QFontDatabase.addApplicationFont("Pretendard-Regular.ttf")
    if font_id != -1:
        family = QFontDatabase.applicationFontFamilies(font_id)[0]
        return QFont(family)
    return QFont("Arial")

def get_app_stylesheet(main_color="#0064FF", secondary_color="#202632", text_color="#FFFFFF"):
    return f"""
        QWidget {{
            background-color: {secondary_color};
            color: {text_color};
            font-family: 'Pretendard';
            font-size: 14px;
        }}

        QTextEdit, QComboBox, QLineEdit {{
            background-color: #2A2F3A;
            color: {text_color};
            border: 1px solid #444;
            padding: 6px;
            border-radius: 4px;
        }}

        QPushButton {{
            background-color: {main_color};
            color: {text_color};
            padding: 8px;
            border-radius: 4px;
            font-weight: bold;
        }}

        QPushButton:hover {{
            background-color: #0050D0;
        }}

        QTabBar::tab {{
            background: #2A2F3A;
            color: {text_color};
            padding: 8px 16px;
            border-top-left-radius: 4px;
            border-top-right-radius: 4px;
        }}

        QTabBar::tab:selected {{
            background: {main_color};
            color: white;
        }}

        QHeaderView::section {{
            background-color: #3A3F4A;
            padding: 4px;
            border: 1px solid #555;
        }}

        QTableWidget {{
            background-color: #1F232B;
            alternate-background-color: #2A2F3A;
            selection-background-color: #394BFF;
            selection-color: white;
            gridline-color: #555;
        }}

        QTableWidget::item:hover {{
            background-color: #444B5A;
        }}

        QTableWidget::item:selected {{
            background-color: #394BFF;
            color: white;
        }}

        QTableWidget QTableCornerButton::section {{
            background-color: #2A2F3A;
        }}
    """


###############################################################################
# 유틸 함수들
###############################################################################
def load_rooms_from_excel(filepath, sheet_name='Sheet1', column_name='채팅방명'):
    try:
        df = pd.read_excel(filepath, sheet_name=sheet_name)
        rooms = df[column_name].dropna().tolist()
        if not rooms:
            raise ValueError("채팅방 목록이 비어있습니다.")
        return rooms
    except Exception as e:
        raise Exception(f"엑셀 파일 읽기 실패: {str(e)}")

def wait_for_window(window_name, timeout=5):
    start_time = time.time()
    while time.time() - start_time < timeout:
        if win32gui.FindWindow(None, window_name) != 0:
            return True
        time.sleep(0.2)
    return False

def close_chat(room_name):
    hwnd = win32gui.FindWindow(None, room_name)
    if hwnd != 0:
        win32api.PostMessage(hwnd, win32con.WM_KEYDOWN, win32con.VK_ESCAPE, 0)
        time.sleep(0.05)
        win32api.PostMessage(hwnd, win32con.WM_KEYUP, win32con.VK_ESCAPE, 0)
        time.sleep(0.2)

def press_enter(edit_hwnd):
    win32api.PostMessage(edit_hwnd, win32con.WM_KEYDOWN, win32con.VK_RETURN, 0)
    time.sleep(0.05)
    win32api.PostMessage(edit_hwnd, win32con.WM_KEYUP, win32con.VK_RETURN, 0)

def send_text(room_name, text):
    room_hwnd = win32gui.FindWindow(None, room_name)
    if room_hwnd == 0:
        raise Exception(f"'{room_name}' 대화창을 찾을 수 없습니다.")
    edit_hwnd = win32gui.FindWindowEx(room_hwnd, None, "RICHEDIT50W", None)
    if edit_hwnd == 0:
        raise Exception("입력창(RICHEDIT50W)을 찾을 수 없습니다.")

    pyperclip.copy(text)
    time.sleep(0.2)

    # Ctrl+V
    win32api.keybd_event(win32con.VK_CONTROL, 0, 0, 0)
    win32api.keybd_event(ord('V'), 0, 0, 0)
    win32api.keybd_event(ord('V'), 0, win32con.KEYEVENTF_KEYUP, 0)
    win32api.keybd_event(win32con.VK_CONTROL, 0, win32con.KEYEVENTF_KEYUP, 0)

    time.sleep(0.2)
    press_enter(edit_hwnd)

def open_chat(room_name):
    kakao_main_hwnd = win32gui.FindWindow(None, "카카오톡")
    if kakao_main_hwnd == 0:
        raise Exception("카카오톡 메인 창을 찾지 못했습니다. 카카오톡이 실행 중인지 확인하세요.")
    child_hwnd = win32gui.FindWindowEx(kakao_main_hwnd, None, "EVA_ChildWindow", None)
    first_eva_hwnd = win32gui.FindWindowEx(child_hwnd, None, "EVA_Window", None)
    second_eva_hwnd = win32gui.FindWindowEx(child_hwnd, first_eva_hwnd, "EVA_Window", None)
    edit_hwnd = win32gui.FindWindowEx(second_eva_hwnd, None, "Edit", None)
    if edit_hwnd == 0:
        raise Exception("검색 입력창(Edit)을 찾을 수 없습니다.")

    win32api.SendMessage(edit_hwnd, win32con.WM_SETTEXT, 0, room_name)
    time.sleep(0.5)

    press_enter(edit_hwnd)
    time.sleep(0.8)

    if not wait_for_window(room_name):
        raise Exception(f"채팅방 '{room_name}'을 열 수 없습니다.")
    time.sleep(0.3)

def send_image(room_name, image_path):
    room_hwnd = win32gui.FindWindow(None, room_name)
    if room_hwnd == 0:
        raise Exception(f"'{room_name}' 대화창을 찾을 수 없습니다.")
    edit_hwnd = win32gui.FindWindowEx(room_hwnd, None, "RICHEDIT50W", None)
    if edit_hwnd == 0:
        raise Exception("입력창(RICHEDIT50W)을 찾을 수 없습니다.")

    image = Image.open(image_path)
    image_stream = io.BytesIO()
    image.convert('RGB').save(image_stream, 'BMP')
    bmp_data = image_stream.getvalue()[14:]
    image_stream.close()

    win32clipboard.OpenClipboard()
    win32clipboard.EmptyClipboard()
    win32clipboard.SetClipboardData(win32clipboard.CF_DIB, bmp_data)
    win32clipboard.CloseClipboard()

    time.sleep(0.2)

    win32api.keybd_event(win32con.VK_CONTROL, 0, 0, 0)
    win32api.keybd_event(ord('V'), 0, 0, 0)
    win32api.keybd_event(ord('V'), 0, win32con.KEYEVENTF_KEYUP, 0)
    win32api.keybd_event(win32con.VK_CONTROL, 0, win32con.KEYEVENTF_KEYUP, 0)

    time.sleep(0.2)
    win32api.keybd_event(win32con.VK_RETURN, 0, 0, 0)
    time.sleep(0.05)
    win32api.keybd_event(win32con.VK_RETURN, 0, win32con.KEYEVENTF_KEYUP, 0)

def list_open_kakao_chats():
    """
    현재 화면에 떠 있는 카톡 대화창(텍스트 입력창 RICHEDIT50W 보유)을 찾아 제목을 리스트로 반환.
    - 최상위/가시성 있는 창만 검사
    - '카카오톡' 메인 타이틀은 제외
    """
    found_titles = []

    def enum_cb(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        title = win32gui.GetWindowText(hwnd)
        if not title:
            return
        if title.strip() in ("카카오톡", "KakaoTalk"):
            return
        # 대화창 판별: 내부에 RICHEDIT50W 가 있으면 메시지 입력 가능한 대화창으로 판단
        edit = win32gui.FindWindowEx(hwnd, None, "RICHEDIT50W", None)
        if edit != 0:
            found_titles.append(title)

    win32gui.EnumWindows(enum_cb, None)
    # 중복 및 공백 제거
    deduped = []
    for t in found_titles:
        t2 = t.strip()
        if t2 and t2 not in deduped:
            deduped.append(t2)
    return deduped


###############################################################################
# 쓰레드
###############################################################################
class MessageSenderThread(QThread):
    progress_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(int, int)

    def __init__(self, room_list, message_text, image_paths=None, image_first=False):
        super().__init__()
        self.room_list = room_list
        self.message_text = message_text
        self.image_paths = image_paths if image_paths else []
        self.image_first = image_first

    def run(self):
        success_count = 0
        failure_count = 0

        for room_name in self.room_list:
            try:
                self.progress_signal.emit(f"'{room_name}' 채팅방에 메시지 전송 중...")
                open_chat(room_name)

                if self.image_paths:
                    if self.image_first:
                        for img_path in self.image_paths:
                            send_image(room_name, img_path)
                            time.sleep(0.5)
                        if self.message_text:
                            send_text(room_name, self.message_text)
                    else:
                        if self.message_text:
                            send_text(room_name, self.message_text)
                            time.sleep(0.5)
                        for img_path in self.image_paths:
                            send_image(room_name, img_path)
                            time.sleep(0.5)
                else:
                    if self.message_text:
                        send_text(room_name, self.message_text)

                time.sleep(0.2)
                success_count += 1

                close_chat(room_name)
                self.progress_signal.emit(f"'{room_name}' 채팅방 메시지 전송 완료")
                time.sleep(0.3)
            except Exception as e:
                self.progress_signal.emit(f"채팅방 '{room_name}'에 메시지 전송 실패: {str(e)}")
                failure_count += 1

        self.finished_signal.emit(success_count, failure_count)


###############################################################################
# 메인 윈도우
###############################################################################
class KakaoSenderMainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.normal_chat_rooms = []
        self.random_chat_rooms = []
        self.normal_images = []
        self.random_images = []
        self.reserved_timer = QTimer()
        self.reserved_timer.timeout.connect(self.check_reserved_time)
        self.reserved_message_text = ""
        self.reserved_send_time = None

        # For random sending
        self.random_sending_active = False
        self.random_send_timer = None

        # 랜덤 발송 활성 시간대
        self.random_time_window_enabled = False

        self.setup_ui()

    def setup_ui(self):
        self.setWindowTitle('카카오톡 메시지 발송기')
        self.setGeometry(100, 100, 900, 640)

        self.tab_widget = QTabWidget()
        self.setCentralWidget(self.tab_widget)

        # Normal tab
        self.normal_tab = QWidget()
        self.setup_normal_tab()
        self.tab_widget.addTab(self.normal_tab, "일반 발송")

        # Random tab
        self.random_tab = QWidget()
        self.setup_random_tab()
        self.tab_widget.addTab(self.random_tab, "랜덤 발송")

    #################
    # Normal Tab
    #################
    def setup_normal_tab(self):
        main_layout = QVBoxLayout(self.normal_tab)
        top_layout = QHBoxLayout()

        # Left side: Chat Room List
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.addWidget(QLabel('채팅방 목록'))

        self.normal_listWidget_rooms = QListWidget()
        left_layout.addWidget(self.normal_listWidget_rooms)

        # 버튼들
        btn_row = QHBoxLayout()
        load_rooms_btn = QPushButton('엑셀 파일에서 채팅방 불러오기')
        load_rooms_btn.clicked.connect(self.load_normal_rooms_from_excel)
        btn_row.addWidget(load_rooms_btn)

        add_from_open_btn = QPushButton('현재 열린 채팅방 추가')
        add_from_open_btn.clicked.connect(self.add_open_chats_to_normal)
        btn_row.addWidget(add_from_open_btn)

        left_layout.addLayout(btn_row)

        # 예제 엑셀 저장 버튼
        download_example_excel_btn = QPushButton('예제 엑셀파일 저장')
        download_example_excel_btn.clicked.connect(self.download_example_excel)
        left_layout.addWidget(download_example_excel_btn)

        top_layout.addWidget(left_widget)

        # Right side: Message/Images/Settings
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)

        # Message input
        right_layout.addWidget(QLabel('보낼 메시지'))
        self.normal_message_textedit = QTextEdit()
        right_layout.addWidget(self.normal_message_textedit)

        # Image selection
        image_group_box = QGroupBox('이미지 선택')
        image_layout = QVBoxLayout()
        self.normal_images_listWidget = QListWidget()
        image_layout.addWidget(self.normal_images_listWidget)

        add_image_btn = QPushButton('이미지 추가')
        add_image_btn.clicked.connect(self.add_normal_images)
        image_layout.addWidget(add_image_btn)

        remove_image_btn = QPushButton('선택한 이미지 삭제')
        remove_image_btn.clicked.connect(self.remove_selected_normal_image)
        image_layout.addWidget(remove_image_btn)

        image_group_box.setLayout(image_layout)
        right_layout.addWidget(image_group_box)

        # Send order
        order_group = QGroupBox('전송 순서')
        order_layout = QHBoxLayout()
        self.normal_order_group = QButtonGroup()

        self.normal_radio_message_first = QRadioButton('메시지 먼저')
        self.normal_radio_image_first = QRadioButton('이미지 먼저')
        self.normal_radio_message_first.setChecked(True)

        self.normal_order_group.addButton(self.normal_radio_message_first)
        self.normal_order_group.addButton(self.normal_radio_image_first)
        order_layout.addWidget(self.normal_radio_message_first)
        order_layout.addWidget(self.normal_radio_image_first)
        order_group.setLayout(order_layout)
        right_layout.addWidget(order_group)

        # Reservation Time
        time_layout = QHBoxLayout()
        time_layout.addWidget(QLabel('전송 시간:'))
        self.normal_timeEdit = QTimeEdit()
        self.normal_timeEdit.setDisplayFormat("HH:mm")
        self.normal_timeEdit.setTime(QTime.currentTime())
        time_layout.addWidget(self.normal_timeEdit)
        right_layout.addLayout(time_layout)

        # Buttons
        button_layout = QHBoxLayout()
        send_now_btn = QPushButton('즉시 전송')
        send_now_btn.clicked.connect(self.send_normal_immediately)
        button_layout.addWidget(send_now_btn)

        reserve_btn = QPushButton('예약 전송')
        reserve_btn.clicked.connect(self.reserve_normal_send)
        button_layout.addWidget(reserve_btn)

        right_layout.addLayout(button_layout)

        # Log
        right_layout.addWidget(QLabel('전송 로그'))
        self.normal_log_textedit = QTextEdit()
        self.normal_log_textedit.setReadOnly(True)
        right_layout.addWidget(self.normal_log_textedit)

        top_layout.addWidget(right_widget)
        main_layout.addLayout(top_layout)

    def load_normal_rooms_from_excel(self):
        file_path, _ = QFileDialog.getOpenFileName(self, '엑셀 파일 선택', '', 'Excel files (*.xlsx *.xls)')
        if file_path:
            try:
                self.normal_chat_rooms = load_rooms_from_excel(file_path)
                self.normal_listWidget_rooms.clear()
                self.normal_listWidget_rooms.addItems(self.normal_chat_rooms)
                self.normal_log_textedit.append(f"총 {len(self.normal_chat_rooms)}개의 채팅방을 불러왔습니다.")
            except Exception as e:
                QMessageBox.critical(self, '오류', f'엑셀 파일 읽기 실패: {str(e)}')

    def add_open_chats_to_normal(self):
        titles = list_open_kakao_chats()
        if not titles:
            QMessageBox.information(self, '안내', '현재 열린 카카오톡 대화창을 찾지 못했습니다.')
            return
        added = 0
        for t in titles:
            if t not in self.normal_chat_rooms:
                self.normal_chat_rooms.append(t)
                self.normal_listWidget_rooms.addItem(t)
                added += 1
        self.normal_log_textedit.append(f"현재 열린 대화창 {len(titles)}개 중 새로 {added}개를 추가했습니다.")

    # 예제 엑셀파일 저장 함수
    def download_example_excel(self):
        file_path, _ = QFileDialog.getSaveFileName(self, '예제 엑셀 파일 저장', '', 'Excel files (*.xlsx *.xls)')
        if file_path:
            try:
                df = pd.DataFrame({'채팅방명': ['예시채팅방1', '예시채팅방2', '예시채팅방3']})
                df.to_excel(file_path, index=False)
                QMessageBox.information(self, '완료', '예제 엑셀파일이 저장되었습니다.')
            except Exception as e:
                QMessageBox.critical(self, '오류', f'엑셀 파일 저장 중 오류: {str(e)}')

    def add_normal_images(self):
        file_paths, _ = QFileDialog.getOpenFileNames(self, '이미지 선택', '', 'Image files (*.jpg *.jpeg *.png *.gif)')
        if file_paths:
            for path in file_paths:
                self.normal_images.append(path)
                self.normal_images_listWidget.addItem(path)

    def remove_selected_normal_image(self):
        index = self.normal_images_listWidget.currentRow()
        if index >= 0:
            self.normal_images.pop(index)
            self.normal_images_listWidget.takeItem(index)

    def send_normal_immediately(self):
        if not self.normal_chat_rooms:
            QMessageBox.warning(self, '경고', '채팅방 목록을 먼저 불러와주세요.')
            return

        message_text = self.normal_message_textedit.toPlainText().strip()
        if not message_text and not self.normal_images:
            QMessageBox.warning(self, '경고', '메시지나 이미지 중 하나는 입력해주세요.')
            return

        self.start_sending_thread(message_text)

    def reserve_normal_send(self):
        if not self.normal_chat_rooms:
            QMessageBox.warning(self, '경고', '채팅방 목록을 먼저 불러와주세요.')
            return

        message_text = self.normal_message_textedit.toPlainText().strip()
        if not message_text and not self.normal_images:
            QMessageBox.warning(self, '경고', '메시지나 이미지 중 하나는 입력해주세요.')
            return

        today_date = QDateTime.currentDateTime().date()
        selected_time = self.normal_timeEdit.time()
        reserve_datetime = QDateTime(today_date, selected_time)

        current_datetime = QDateTime.currentDateTime()
        if reserve_datetime <= current_datetime:
            reserve_datetime = reserve_datetime.addDays(1)

        self.reserved_message_text = message_text
        self.reserved_send_time = reserve_datetime

        self.reserved_timer.start(1000)
        self.normal_log_textedit.append(
            f"메시지 예약 완료: {reserve_datetime.toString('MM-dd HH:mm')}에 전송됩니다."
        )

    def check_reserved_time(self):
        if QDateTime.currentDateTime() >= self.reserved_send_time:
            self.reserved_timer.stop()
            self.normal_log_textedit.append("예약된 시간이 되어 메시지 전송을 시작합니다.")
            self.start_sending_thread(self.reserved_message_text)

    def start_sending_thread(self, message_text):
        image_first = self.normal_radio_image_first.isChecked()
        self.thread = MessageSenderThread(
            self.normal_chat_rooms,
            message_text,
            self.normal_images,
            image_first
        )
        self.thread.progress_signal.connect(self.on_normal_progress)
        self.thread.finished_signal.connect(self.on_normal_finished)
        self.thread.start()

    def on_normal_progress(self, log_message):
        self.normal_log_textedit.append(log_message)

    def on_normal_finished(self, success_count, failure_count):
        QMessageBox.information(
            self, '완료',
            f'전송 완료: {success_count}개 성공, {failure_count}개 실패'
        )

    #################
    # Random Tab
    #################
    def setup_random_tab(self):
        main_layout = QVBoxLayout(self.random_tab)
        top_layout = QHBoxLayout()

        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.addWidget(QLabel('채팅방 목록'))

        self.random_listWidget_rooms = QListWidget()
        left_layout.addWidget(self.random_listWidget_rooms)

        btn_row = QHBoxLayout()
        load_rooms_btn = QPushButton('엑셀 파일에서 채팅방 불러오기')
        load_rooms_btn.clicked.connect(self.load_random_rooms_from_excel)
        btn_row.addWidget(load_rooms_btn)

        add_from_open_btn = QPushButton('현재 열린 채팅방 추가')
        add_from_open_btn.clicked.connect(self.add_open_chats_to_random)
        btn_row.addWidget(add_from_open_btn)

        left_layout.addLayout(btn_row)

        top_layout.addWidget(left_widget)

        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)

        # 발송 간격
        interval_group = QGroupBox('발송 간격 설정(분)')
        interval_layout = QHBoxLayout()
        self.random_min_interval_spinBox = QSpinBox()
        self.random_min_interval_spinBox.setRange(1, 1440)
        self.random_min_interval_spinBox.setValue(60)

        self.random_max_interval_spinBox = QSpinBox()
        self.random_max_interval_spinBox.setRange(1, 1440)
        self.random_max_interval_spinBox.setValue(120)

        interval_layout.addWidget(QLabel('최소:'))
        interval_layout.addWidget(self.random_min_interval_spinBox)
        interval_layout.addWidget(QLabel('최대:'))
        interval_layout.addWidget(self.random_max_interval_spinBox)
        interval_group.setLayout(interval_layout)
        right_layout.addWidget(interval_group)

        # 활성 시간대(매일) 설정
        timewin_group = QGroupBox('활성 시간대 (매일)')
        timewin_layout = QHBoxLayout()
        self.random_time_window_checkbox = QCheckBox('활성 시간대 사용')
        self.random_time_window_checkbox.stateChanged.connect(self.on_random_time_window_toggle)
        timewin_layout.addWidget(self.random_time_window_checkbox)

        timewin_layout.addWidget(QLabel('시작:'))
        self.random_start_time_edit = QTimeEdit()
        self.random_start_time_edit.setDisplayFormat("HH:mm")
        self.random_start_time_edit.setTime(QTime(9, 0))
        timewin_layout.addWidget(self.random_start_time_edit)

        timewin_layout.addWidget(QLabel('종료:'))
        self.random_end_time_edit = QTimeEdit()
        self.random_end_time_edit.setDisplayFormat("HH:mm")
        self.random_end_time_edit.setTime(QTime(18, 0))
        timewin_layout.addWidget(self.random_end_time_edit)
        timewin_group.setLayout(timewin_layout)
        right_layout.addWidget(timewin_group)

        # 메시지/이미지
        right_layout.addWidget(QLabel('보낼 메시지'))
        self.random_message_textedit = QTextEdit()
        right_layout.addWidget(self.random_message_textedit)

        image_group_box = QGroupBox('이미지 선택')
        image_layout = QVBoxLayout()
        self.random_images_listWidget = QListWidget()
        image_layout.addWidget(self.random_images_listWidget)

        add_image_btn = QPushButton('이미지 추가')
        add_image_btn.clicked.connect(self.add_random_images)
        image_layout.addWidget(add_image_btn)

        remove_image_btn = QPushButton('선택한 이미지 삭제')
        remove_image_btn.clicked.connect(self.remove_selected_random_image)
        image_layout.addWidget(remove_image_btn)

        image_group_box.setLayout(image_layout)
        right_layout.addWidget(image_group_box)

        order_group = QGroupBox('전송 순서')
        order_layout = QHBoxLayout()
        self.random_order_group = QButtonGroup()

        self.random_radio_message_first = QRadioButton('메시지 먼저')
        self.random_radio_image_first = QRadioButton('이미지 먼저')
        self.random_radio_message_first.setChecked(True)

        self.random_order_group.addButton(self.random_radio_message_first)
        self.random_order_group.addButton(self.random_radio_image_first)
        order_layout.addWidget(self.random_radio_message_first)
        order_layout.addWidget(self.random_radio_image_first)
        order_group.setLayout(order_layout)
        right_layout.addWidget(order_group)

        button_layout = QHBoxLayout()
        self.random_start_button = QPushButton('랜덤 발송 시작')
        self.random_start_button.clicked.connect(self.start_random_sending)
        button_layout.addWidget(self.random_start_button)

        self.random_stop_button = QPushButton('랜덤 발송 중지')
        self.random_stop_button.clicked.connect(self.stop_random_sending)
        self.random_stop_button.setEnabled(False)
        button_layout.addWidget(self.random_stop_button)

        right_layout.addLayout(button_layout)

        right_layout.addWidget(QLabel('전송 로그'))
        self.random_log_textedit = QTextEdit()
        self.random_log_textedit.setReadOnly(True)
        right_layout.addWidget(self.random_log_textedit)

        top_layout.addWidget(right_widget)
        main_layout.addLayout(top_layout)

    def on_random_time_window_toggle(self, state):
        self.random_time_window_enabled = (state == Qt.Checked)

    def load_random_rooms_from_excel(self):
        file_path, _ = QFileDialog.getOpenFileName(self, '엑셀 파일 선택', '', 'Excel files (*.xlsx *.xls)')
        if file_path:
            try:
                self.random_chat_rooms = load_rooms_from_excel(file_path)
                self.random_listWidget_rooms.clear()
                self.random_listWidget_rooms.addItems(self.random_chat_rooms)
                self.random_log_textedit.append(f"총 {len(self.random_chat_rooms)}개의 채팅방을 불러왔습니다.")
            except Exception as e:
                QMessageBox.critical(self, '오류', f'엑셀 파일 읽기 실패: {str(e)}')

    def add_open_chats_to_random(self):
        titles = list_open_kakao_chats()
        if not titles:
            QMessageBox.information(self, '안내', '현재 열린 카카오톡 대화창을 찾지 못했습니다.')
            return
        added = 0
        for t in titles:
            if t not in self.random_chat_rooms:
                self.random_chat_rooms.append(t)
                self.random_listWidget_rooms.addItem(t)
                added += 1
        self.random_log_textedit.append(f"현재 열린 대화창 {len(titles)}개 중 새로 {added}개를 추가했습니다.")

    def add_random_images(self):
        file_paths, _ = QFileDialog.getOpenFileNames(self, '이미지 선택', '', 'Image files (*.jpg *.jpeg *.png *.gif)')
        if file_paths:
            for path in file_paths:
                self.random_images.append(path)
                self.random_images_listWidget.addItem(path)

    def remove_selected_random_image(self):
        index = self.random_images_listWidget.currentRow()
        if index >= 0:
            self.random_images.pop(index)
            self.random_images_listWidget.takeItem(index)

    def start_random_sending(self):
        if not self.random_chat_rooms:
            QMessageBox.warning(self, '경고', '채팅방 목록을 먼저 불러와주세요.')
            return

        message_text = self.random_message_textedit.toPlainText().strip()
        if not message_text and not self.random_images:
            QMessageBox.warning(self, '경고', '메시지나 이미지 중 하나는 입력해주세요.')
            return

        min_interval = self.random_min_interval_spinBox.value()
        max_interval = self.random_max_interval_spinBox.value()
        if min_interval >= max_interval:
            QMessageBox.warning(self, '경고', '최대 시간은 최소 시간보다 커야 합니다.')
            return

        self.random_sending_active = True
        self.random_start_button.setEnabled(False)
        self.random_stop_button.setEnabled(True)

        self.schedule_next_random_send(initial=True)

    def stop_random_sending(self):
        self.random_sending_active = False
        self.random_start_button.setEnabled(True)
        self.random_stop_button.setEnabled(False)
        if hasattr(self, 'random_send_timer') and self.random_send_timer:
            self.random_send_timer.stop()

    def _is_now_within_window(self, now: QDateTime, start_t: QTime, end_t: QTime) -> bool:
        """
        now 가 start_t~end_t(일 단위) 범위에 있는지 판단.
        - start == end 이면 24시간 활성으로 간주.
        - start < end: 같은 날 범위
        - start > end: 자정 넘어가는 범위 (예: 22:00 ~ 02:00)
        """
        if start_t == end_t:
            return True
        today = now.date()
        start_dt = QDateTime(today, start_t)
        end_dt = QDateTime(today, end_t)
        if start_t < end_t:
            return start_dt <= now < end_dt
        else:
            # 자정 넘김
            return not (end_dt <= now < start_dt)

    def _next_window_start_after(self, ref: QDateTime, start_t: QTime, end_t: QTime) -> QDateTime:
        """
        기준 시각 ref 이후의 '다음 활성 시작 시각' 계산
        - start == end: 항상 활성 -> 즉시(ref) 반환
        - 자정 넘김/동일일 범위 모두 처리
        """
        if start_t == end_t:
            return ref

        today = ref.date()
        start_today = QDateTime(today, start_t)

        if self._is_now_within_window(ref, start_t, end_t):
            # 이미 창구 안: 바로 ref
            return ref

        if start_t < end_t:
            # 같은 날 범위
            if ref < start_today:
                return start_today
            else:
                # 내일 시작
                return QDateTime(today.addDays(1), start_t)
        else:
            # 자정 넘김 (예: 22~02)
            # ref가 낮시간(창구 밖)일 때: 오늘 start_t
            # ref가 이미 start_t 이전이면 오늘 start_t, 지난 후면 내일 start_t
            if ref < start_today:
                return start_today
            else:
                return QDateTime(today.addDays(1), start_t)

    def schedule_next_random_send(self, initial=False):
        if not self.random_sending_active:
            return

        min_interval = self.random_min_interval_spinBox.value()
        max_interval = self.random_max_interval_spinBox.value()
        interval_min = random.randint(min_interval, max_interval)

        now = QDateTime.currentDateTime()
        next_candidate = now.addSecs(interval_min * 60)

        if self.random_time_window_enabled:
            start_t = self.random_start_time_edit.time()
            end_t = self.random_end_time_edit.time()

            # 후보 시점이 창구 안인지 확인; 아니면 창구 시작으로 점프
            if not self._is_now_within_window(next_candidate, start_t, end_t):
                next_candidate = self._next_window_start_after(next_candidate, start_t, end_t)

        # 타이머 설정
        msec = max(0, int(now.msecsTo(next_candidate)))
        if self.random_send_timer:
            self.random_send_timer.stop()
        self.random_send_timer = QTimer()
        self.random_send_timer.setSingleShot(True)
        self.random_send_timer.timeout.connect(self.perform_random_send)

        self.random_log_textedit.append(f"다음 발송 예정: {next_candidate.toString('MM-dd HH:mm')}")
        self.random_send_timer.start(msec)

        if initial and self.random_time_window_enabled:
            # 시작 시점이 이미 창구 안이면 바로 첫 발송을 땡겨도 되지만,
            # 여기서는 지정 간격 로직을 유지
            if not self._is_now_within_window(now, start_t, end_t):
                jump_to = self._next_window_start_after(now, start_t, end_t)
                self.random_log_textedit.append(f"현재 시간은 활성 시간대 밖입니다. 첫 발송을 {jump_to.toString('MM-dd HH:mm')}로 예약합니다.")

    def perform_random_send(self):
        # 활성 시간대 체크 (활성 시간대를 사용하는 경우)
        if self.random_time_window_enabled:
            now = QDateTime.currentDateTime()
            start_t = self.random_start_time_edit.time()
            end_t = self.random_end_time_edit.time()
            if not self._is_now_within_window(now, start_t, end_t):
                # 창구 밖이면 다음 창구 시작으로 재예약
                next_start = self._next_window_start_after(now, start_t, end_t)
                self.random_log_textedit.append(f"활성 시간대 밖입니다. 다음 창 시작 {next_start.toString('MM-dd HH:mm')}로 재예약합니다.")
                if self.random_send_timer:
                    self.random_send_timer.stop()
                self.random_send_timer = QTimer()
                self.random_send_timer.setSingleShot(True)
                self.random_send_timer.timeout.connect(self.perform_random_send)
                self.random_send_timer.start(max(0, int(now.msecsTo(next_start))))
                return

        message_text = self.random_message_textedit.toPlainText().strip()
        image_first = self.random_radio_image_first.isChecked()

        self.random_send_thread = MessageSenderThread(
            self.random_chat_rooms,
            message_text,
            self.random_images,
            image_first
        )
        self.random_send_thread.progress_signal.connect(self.on_random_progress)
        self.random_send_thread.finished_signal.connect(self.on_random_finished)
        self.random_send_thread.start()

    def on_random_progress(self, log_message):
        self.random_log_textedit.append(log_message)

    def on_random_finished(self, success_count, failure_count):
        self.random_log_textedit.append(f'전송 완료: {success_count}개 성공, {failure_count}개 실패')
        if self.random_sending_active:
            self.schedule_next_random_send()


if __name__ == '__main__':
    app = QApplication(sys.argv)

    # (선택사항) Pretendard 폰트 사용
    font = load_pretendard()
    app.setFont(font)

    # (선택사항) 스타일시트 적용
    app.setStyleSheet(get_app_stylesheet())

    window = KakaoSenderMainWindow()
    window.show()
    sys.exit(app.exec_())
