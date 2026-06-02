# 배포 가이드 — V1 / V2 exe

> Phase 0 기준. Windows 전용.

---

## 1. 빌드 산출물

| 스크립트 | exe 이름 | 모드 |
|----------|----------|------|
| `build-v1.bat` | `dist\KakaoSender-V1.exe` | V1 — ERP 로그인 |
| `build-v2.bat` | `dist\KakaoSender-V2.exe` | V2 — 게스트 |
| `build.bat` | V2와 동일 | 기본 = 게스트 |

빌드 전: 프로젝트 루트에 `Pretendard-Regular.ttf` 존재 확인.

```powershell
pip install -r requirements.txt
.\build-v2.bat
```

---

## 2. 배포 폴더 구성 (권장)

### V2 게스트 (로그인·DB 불필요)

```
배포폴더/
├── KakaoSender-V2.exe
└── (선택) Pretendard-Regular.ttf  ← exe에 번들되면 불필요
```

실행 후 같은 폴더에 자동 생성될 수 있음:

- `kakao_sender_settings_1.json`
- `templates_1.json`
- `logs_1.json`

### V1 일반 (DB 로그인 필요)

```
배포폴더/
├── KakaoSender-V1.exe
└── .live.env          ← 필수 (또는 .env)
```

**`.live.env`는 Git에 올리지 마세요.** 배포는 USB·사내 공유·암호화 zip 등으로만 전달.

---

## 3. `.live.env` 작성 (V1)

1. 개발 PC에서 `.env.example`을 복사:

   ```powershell
   copy .env.example .live.env
   ```

2. `배포폴더\.live.env`에 아래 항목을 실제 값으로 채움 (예시는 placeholder):

   ```ini
   SERVICE_TYPE=live
   DATABASE_HOST=your-host
   DATABASE_PORT=16751
   DATABASE_USER=your_user
   DATABASE_PASSWORD=your_password
   ERP_DATABASE_NAME=erpdb
   ```

3. `KakaoSender-V1.exe`와 **같은 폴더**에 `.live.env`를 둡니다.

`database.py`는 exe 실행 위치(현재 작업 디렉터리) 기준이 아니라 **exe가 풀리는 임시 경로**가 될 수 있어, PyInstaller onefile에서는 env를 exe 옆에 두고 **실행 시 그 폴더로 cd**하는 것이 안전합니다.

```powershell
cd C:\Apps\KakaoSender-V1
.\KakaoSender-V1.exe
```

**확인 필요:** onefile exe만 더블클릭할 때 cwd가 exe 폴더가 아닐 수 있음. 문제가 있으면 Phase 1에서 `sys._MEIPASS` / exe 디렉터리 기준 env 로드를 개선합니다.

---

## 4. DRY_RUN 배포

테스트·교육용으로 실발송 없이 배포할 때:

- UI에서 **「DRY_RUN」** 체크 후 사용
- 또는 배포 폴더에 `run-dryrun.bat`:

  ```bat
  @echo off
  set KAKAO_SENDER_DRY_RUN=1
  KakaoSender-V2.exe
  ```

---

## 5. 운영 체크리스트

| 항목 | V1 | V2 |
|------|----|----|
| 카카오톡 PC 설치·로그인 | ✅ | ✅ |
| `.live.env` | ✅ | — |
| Windows Defender 예외 | 권장 | 권장 |
| 테스트 방 1개로 1건 발송 검증 | ✅ | ✅ |
| DRY_RUN으로 1차 검증 | 권장 | 권장 |

---

## 6. 문제 해결

| 증상 | 조치 |
|------|------|
| V1 «DB 환경변수가 없습니다» | exe와 같은 폴더에 `.live.env`, 그 폴더에서 cmd로 실행 |
| 로그인 실패 | ERP `user` 계정·비밀번호, DB 방화벽(포트 16751) |
| 발송 안 됨 | 카카오톡 실행 여부, 채팅방 이름 정확도 |
| 폰트 깨짐 | `Pretendard-Regular.ttf` 번들·경로 확인 |

---

## 7. 관련 문서

- [README.md](../../README.md)
- [phase-0-dual-build.md](../specs/phase-0-dual-build.md)
- [repo-map.md](repo-map.md)
