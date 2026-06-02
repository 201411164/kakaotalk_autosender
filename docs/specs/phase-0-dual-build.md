# SPEC: Phase 0 — V1/V2 이원화 · DRY_RUN · Secret 정리

**상태:** 구현 완료 (2026-05-19)  
**다음:** Phase 1 (V1 DB 템플릿 동기화 등) — 별도 승인

---

## 1. 배경

- V2 게스트 모드만 `main.py`에 하드코딩되어 있었음.
- V1 로그인(`login_dialog.py`)은 코드만 존재하고 진입점 미연결.
- `database.py`에 운영 DB secret 하드코딩.
- 실발송 테스트가 위험하여 DRY_RUN 필요.

## 2. 목표

| ID | 목표 | 완료 |
|----|------|------|
| G1 | V1/V2 exe(진입점) 분리 | ✅ `entry_v1.py`, `entry_v2.py`, `build-v1/v2.bat` |
| G2 | secret env 전환 | ✅ `database.py` 하드코딩 제거, `.env.example` |
| G3 | DRY_RUN | ✅ `config.py` + UI 체크박스 + Win32 스킵 |
| G4 | 온보딩 | ✅ `requirements.txt`, `README.md`, `.gitignore` |

## 3. 구현 요약

### 3.1 config.py

- `KAKAO_SENDER_MODE`: `v1` | `v2` (기본 `v2`)
- `KAKAO_SENDER_DRY_RUN`: env 기본값
- UI 체크박스 → `set_dry_run_override`

### 3.2 진입

```
entry_v1.py  → KAKAO_SENDER_MODE=v1 → LoginDialog → MainWindow
entry_v2.py  → KAKAO_SENDER_MODE=v2 → 게스트 → MainWindow
main.py      → run_app() (env 모드 따름)
```

### 3.3 database.py

- `BASE_DIR` = 프로젝트 루트
- `.live.env` / `.env` 로드
- `assert_db_configured()` — 연결 전 검증
- 스키마명은 env 없을 때 기본값만 (비밀 아님)

### 3.4 DRY_RUN 동작

- `open_chat`, `send_text`, `send_image`, `close_chat` — Win32 스킵
- `check_kakao_running` — DRY_RUN 시 항상 True
- `MessageSenderThread` — 로그는 `LocalStore`에 기록 (`is_sent=True`)

## 4. 비요구사항 (Phase 0)

- main.py 패키지 분리
- V3 Hub/Agent
- V1 템플릿 DB 동기화
- 자동 테스트

## 5. 검증 계획

| # | 시나리오 | 방법 |
|---|----------|------|
| T1 | V2 기동 | `python entry_v2.py` → 게스트 타이틀 |
| T2 | V1 로그인 실패( env 없음) | `python entry_v1.py` → DB 설정 오류 메시지 |
| T3 | DRY_RUN | 체크 후 1방 발송 → 카카오 미실행해도 로그 생성 |
| T4 | 문법 | `python -m py_compile config.py entry_v1.py entry_v2.py main.py database.py` |
| T5 | V1 빌드 | `build-v1.bat` (로컬 PyInstaller) |

## 6. 운영 리스크 (잔존)

- **이미 Git에 secret이 커밋된 이력**이 있으면 키 로테이션·history 정리 필요 (별도 High 작업).
- DRY_RUN도 로그 JSON에 메시지 본문 저장.
- `main.py` 여전히 단일 대형 파일 — Phase 1+ 분리 권장.

## 7. 승인 필요 (Phase 1 후보)

- [ ] V1 템플릿/로그 DB 저장 (C vs A 정책)
- [ ] 게스트 `user_id` 설치 시 자동 생성
- [ ] Phase 2 V3 MVP SPEC
