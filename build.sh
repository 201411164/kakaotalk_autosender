#!/bin/bash
echo "카카오톡 메시지 발송기 빌드 시작..."
echo

# PyInstaller로 실행 파일 생성
pyinstaller --noconfirm --onefile --windowed --name KakaoSender --add-data "Pretendard-Regular.ttf:." --exclude-module PyQt6 --exclude-module PySide2 --exclude-module PySide6 --exclude-module tkinter main.py

echo
echo "빌드 완료!"
echo "생성된 파일: dist/KakaoSender"
echo

# 실행 권한 부여 (Linux/macOS)
chmod +x dist/KakaoSender
echo "실행 권한이 부여되었습니다."
