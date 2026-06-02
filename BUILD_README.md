# 카카오톡 메시지 발송기 빌드 가이드

## 📋 사전 준비사항

1. **Python 3.7 이상** 설치
2. **필수 라이브러리** 설치:
   ```bash
   pip install pyinstaller PyQt6 pandas pillow pyperclip pywin32 pymysql python-dotenv
   ```
3. **Pretendard-Regular.ttf** 폰트 파일이 프로젝트 루트에 있는지 확인

## 🚀 빌드 방법

### Windows
```bash
# 배치 파일 실행
build.bat

# 또는 직접 명령어 실행
pyinstaller --noconfirm --onefile --noconsole --name KakaoSender --add-data "Pretendard-Regular.ttf;." --exclude-module PyQt6 --exclude-module PySide2 --exclude-module PySide6 --exclude-module tkinter main.py
```

### Linux/macOS
```bash
# 쉘 스크립트에 실행 권한 부여
chmod +x build.sh

# 쉘 스크립트 실행
./build.sh

# 또는 직접 명령어 실행
pyinstaller --noconfirm --onefile --windowed --name KakaoSender --add-data "Pretendard-Regular.ttf:." --exclude-module PyQt6 --exclude-module PySide2 --exclude-module PySide6 --exclude-module tkinter main.py
```

## 📁 빌드 결과

- **Windows**: `dist\KakaoSender.exe`
- **Linux/macOS**: `dist/KakaoSender`

## 🔧 PyInstaller 옵션 설명

- `--noconfirm`: 기존 파일 덮어쓰기 확인 없이 진행
- `--onefile`: 단일 실행 파일로 생성
- `--noconsole` (Windows) / `--windowed` (Linux/macOS): 콘솔 창 숨김
- `--name KakaoSender`: 실행 파일 이름 지정
- `--add-data`: Pretendard 폰트 파일 포함
- `--exclude-module`: 불필요한 Qt 라이브러리 제외하여 파일 크기 최적화

## ⚠️ 주의사항

1. **폰트 파일**: `Pretendard-Regular.ttf` 파일이 반드시 프로젝트 루트에 있어야 합니다.
2. **환경 설정**: `.env` 파일이 있다면 실행 파일과 같은 폴더에 배치하세요.
3. **Windows 전용**: 이 프로그램은 Windows 전용이므로 Linux/macOS에서는 실행되지 않습니다.

## 🐛 문제 해결

### 빌드 실패 시
1. Python 버전 확인 (3.7 이상)
2. 필수 라이브러리 재설치
3. Pretendard 폰트 파일 경로 확인

### 실행 파일 오류 시
1. Windows Defender 예외 처리 추가
2. 관리자 권한으로 실행
3. 실행 파일과 같은 폴더에 설정 파일들 배치
