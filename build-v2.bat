@echo off
REM V2 게스트版 — 로그인 없음
pyinstaller --noconfirm --onefile --windowed --name KakaoSender-V2 --add-data "Pretendard-Regular.ttf;." entry_v2.py
echo.
echo 빌드 완료: dist\KakaoSender-V2.exe
