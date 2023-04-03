import sys
import time
import pyautogui
from PyQt5.QtWidgets import QApplication, QWidget, QVBoxLayout, QLabel, QLineEdit, QPushButton, QPlainTextEdit, QCheckBox, QHBoxLayout, QTableWidget, QTableWidgetItem, QDialog, QDialogButtonBox
import pygetwindow as gw
import pyperclip
from pywinauto import Application
import sqlite3


class AddReceiverDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('받는 사람 추가')

        vbox = QVBoxLayout()

        label = QLabel('받는 사람:')
        vbox.addWidget(label)

        self.receiver_line_edit = QLineEdit()
        vbox.addWidget(self.receiver_line_edit)

        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        vbox.addWidget(button_box)

        self.setLayout(vbox)

    def get_receiver_name(self):
        return self.receiver_line_edit.text()


class AddMessageDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('메시지 추가')

        vbox = QVBoxLayout()

        label = QLabel('메시지:')
        vbox.addWidget(label)

        self.message_text_edit = QPlainTextEdit()
        vbox.addWidget(self.message_text_edit)

        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        vbox.addWidget(button_box)

        self.setLayout(vbox)

    def get_message(self):
        return self.message_text_edit.toPlainText()

# 전송 로그를 보여주는 메소드 및 QDialog 추가
class SentMessageLogDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('전송 로그')

        vbox = QVBoxLayout()

        self.sent_message_table = QTableWidget()
        self.sent_message_table.setColumnCount(3)
        self.sent_message_table.setHorizontalHeaderLabels(['받는 사람', '메시지', '전송 시간'])
        vbox.addWidget(self.sent_message_table)

        self.setLayout(vbox)


class MyApp(QWidget):
    def __init__(self):
        super().__init__()
        self.initUI()

    def initUI(self):

        conn = sqlite3.connect('messages.db')
        cur = conn.cursor()

        # 만약 receivers 테이블이 존재한다면 저장된 받는 사람 목록을 불러옴
        try:
            cur.execute('SELECT name FROM receivers')
            receiver_names = [name[0] for name in cur.fetchall()]

        except sqlite3.OperationalError:
            receiver_names = []
            cur.execute('CREATE TABLE IF NOT EXISTS receivers (name text primary key)')

            # 만약 sent_messages 테이블이 없다면 생성
            cur.execute(
                'CREATE TABLE IF NOT EXISTS sent_messages (id INTEGER PRIMARY KEY AUTOINCREMENT, receiver_name TEXT, message TEXT, sent_time TEXT)')
            conn.commit()

        vbox = QVBoxLayout()

        label1 = QLabel('받는 사람(쉼표로 구분):')
        vbox.addWidget(label1)

        self.repeat_line_edit = QLineEdit()
        vbox.addWidget(QLabel('반복 횟수:'))
        vbox.addWidget(self.repeat_line_edit)

        view_log_button = QPushButton('전송 로그 보기', self)
        view_log_button.clicked.connect(self.view_sent_messages)
        vbox.addWidget(view_log_button)

        # # 기존 코드:
        # self.receiver_line_edit = QLineEdit()
        # self.receiver_line_edit.setText(', '.join(receiver_names))
        # vbox.addWidget(self.receiver_line_edit)

        # 수정 코드:
        self.receiver_table = QTableWidget()
        self.receiver_table.setColumnCount(1)
        self.receiver_table.setHorizontalHeaderLabels(['받는 사람'])
        self.receiver_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.update_receiver_table()
        vbox.addWidget(self.receiver_table)

        add_receiver_button = QPushButton('받는 사람 추가', self)
        add_receiver_button.clicked.connect(self.add_receiver)
        vbox.addWidget(add_receiver_button)

        label2 = QLabel('보낼 메시지:')
        vbox.addWidget(label2)

        self.message_text_edit = QPlainTextEdit()
        vbox.addWidget(self.message_text_edit)

        add_message_button = QPushButton('메시지 추가', self)
        add_message_button.clicked.connect(self.add_message)
        vbox.addWidget(add_message_button)

        label3 = QLabel('주기(분):')
        vbox.addWidget(label3)

        self.interval_line_edit = QLineEdit()
        vbox.addWidget(self.interval_line_edit)

        self.start_button = QPushButton('시작', self)
        self.start_button.clicked.connect(self.start_sending)
        vbox.addWidget(self.start_button)

        self.setLayout(vbox)

        delete_receiver_button = QPushButton('받는 사람 삭제', self)
        delete_receiver_button.clicked.connect(self.delete_receiver)
        vbox.addWidget(delete_receiver_button)

        self.setWindowTitle('카톡 자동 메시지 보내기')
        self.setGeometry(600, 600, 1000, 600)
        self.show()

    def activate_kakao(self):
        print('카카오톡 활성화')
        kakao_title = '카카오톡'

        try:
            kakao_app = gw.getWindowsWithTitle(kakao_title)[0]
        except IndexError:
            print(f"{kakao_title} 창을 찾을 수 없습니다.")
            return False

        if not kakao_app.isActive:
            kakao_app.activate()
            time.sleep(1)

        return True

    def view_sent_messages(self):


        dialog = SentMessageLogDialog(self)
        conn = sqlite3.connect('messages.db')
        cur = conn.cursor()
        # 만약 전송 로그가 없다면 메시지를 띄움
        try:
            cur.execute('SELECT receiver_name, message, sent_time FROM sent_messages')
            sent_messages = cur.fetchall()
        except sqlite3.OperationalError:
            QMessageBox.information(self, '전송 로그', '전송 로그가 없습니다.')
            return

        for i, (receiver_name, message, sent_time) in enumerate(sent_messages):
            dialog.sent_message_table.insertRow(i)
            dialog.sent_message_table.setItem(i, 0, QTableWidgetItem(receiver_name))
            dialog.sent_message_table.setItem(i, 1, QTableWidgetItem(message))
            dialog.sent_message_table.setItem(i, 2, QTableWidgetItem(sent_time))

        dialog.exec_()

    def delete_receiver(self, row):
        receiver_name = self.receiver_table.item(row, 0).text()

        conn = sqlite3.connect('messages.db')
        cur = conn.cursor()
        cur.execute('DELETE FROM receivers WHERE name = ?', (receiver_name,))
        conn.commit()

        self.update_receiver_table()

    def update_receiver_table(self):
        self.receiver_table.setRowCount(0)
        conn = sqlite3.connect('messages.db')
        cur = conn.cursor()
        cur.execute('SELECT name FROM receivers')
        receiver_names = [name[0] for name in cur.fetchall()]
        for i, name in enumerate(receiver_names):
            self.receiver_table.insertRow(i)
            self.receiver_table.setItem(i, 0, QTableWidgetItem(name))

            # 삭제 버튼 추가
            delete_button = QPushButton('삭제')
            delete_button.clicked.connect(lambda checked, row=i: self.delete_receiver(row))
            self.receiver_table.setCellWidget(i, 1, delete_button)
        conn.close()

    def paste_text(self, text):
        pyperclip.copy(text)
        pyautogui.hotkey('ctrl', 'v')

    def add_receiver(self):
        dialog = AddReceiverDialog(self)
        if dialog.exec_() == QDialog.Accepted:
            receiver_name = dialog.get_receiver_name()
            if receiver_name:
                conn = sqlite3.connect('messages.db')
                cur = conn.cursor()
                cur.execute('SELECT name FROM receivers WHERE name = ?', (receiver_name,))
                existing_receiver = cur.fetchone()

                if existing_receiver is None:
                    cur.execute('INSERT INTO receivers (name) VALUES (?)', (receiver_name,))
                    conn.commit()
                    self.update_receiver_table()  # receiver_table을 업데이트하는 라인 추가
                else:
                    print("이미 등록된 받는 사람입니다.")

    def add_message(self):
        dialog = AddMessageDialog(self)
        if dialog.exec_() == QDialog.Accepted:
            message = dialog.get_message()
            if message:
                self.message_text_edit.appendPlainText(message)

    def start_sending(self):

        repeat_count = int(self.repeat_line_edit.text())
        for _ in range(repeat_count):
            # 기존 코드
            # 몇회차인지 출력
            print(f'{_ + 1}회차 전송')

            # 수정된 코드:
            receiver_list = []
            for i in range(self.receiver_table.rowCount()):
                receiver_list.append(self.receiver_table.item(i, 0).text())

            print(receiver_list)
            message = self.message_text_edit.toPlainText()
            print(message)
            interval = int(self.interval_line_edit.text()) * 60

            if not self.activate_kakao():
                print('카카오톡 창 활성화 도중에는 마우스를 움직이지 말아주세요')
                return

            for i, receiver in enumerate(receiver_list):
                print(f'{i + 1}번째 전송: {receiver}에게 {message} 전송')
                # checkbox = QCheckBox(receiver)
                # self.checkbox_layout.addWidget(checkbox)

                pyautogui.hotkey('ctrl', 'f')
                time.sleep(1)


                time.sleep(1)
                self.paste_text(receiver)
                time.sleep(1)
                pyautogui.press('enter')
                time.sleep(1)
                self.paste_text(message)
                pyautogui.press('enter')
                time.sleep(1)
                print(f'{receiver}에게 전송 완료')

                try:
                    print('DB 로그 저장 시작')
                    conn = sqlite3.connect('messages.db', isolation_level=None)
                    cur = conn.cursor()

                    sent_time = time.strftime('%Y-%m-%d %H:%M:%S')
                    cur.execute('INSERT INTO sent_messages (receiver_name, message, sent_time) VALUES (?, ?, ?)',
                                (receiver, message, sent_time))
                    conn.commit()

                except Exception as e:
                    print(e)
                    print('DB에 저장하는 도중에 오류가 발생했습니다.')
                finally:
                    conn.close()
                    print('DB에 저장 완료')

                pyautogui.hotkey('ctrl', 'w')
                time.sleep(1)
                pyautogui.press('esc')



            # interval 동안 대기한다. 5초마다 한번씩 남은 시간을 초로 출력한다.
            for i in range(interval, 0, -5):
                print(f'{i}초 남음')
                time.sleep(5)



if __name__ == '__main__':
    app = QApplication(sys.argv)
    ex = MyApp()
    sys.exit(app.exec_())
