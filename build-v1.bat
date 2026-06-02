@echo off
REM V1 일반版 — ERP 로그인 필요
pyinstaller --noconfirm --onefile --windowed --name KakaoSender-V1 --hidden-import pymysql --add-data "Pretendard-Regular.ttf;." entry_v1.py
echo.
echo 빌드 완료: dist\KakaoSender-V1.exe
echo V1 실행 시 .live.env 또는 .env 가 exe 와 같은 폴더 또는 프로젝트 루트에 필요합니다.
