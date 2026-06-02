# AGENTS.md — kakaotalk_autosender

> 이 파일은 Cursor Agent가 이 저장소에서 작업할 때 **항상 참고하는 프로젝트 헌법**입니다.

---

## 1. Project Overview

### 목적

Windows PC에서 **카카오톡 데스크톱 클라이언트**를 자동화하여, 지정한 채팅방에 메시지·이미지를 일괄 또는 랜덤 간격으로 발송하는 **내부용 발송 도구**입니다.

### 핵심 도메인

- 채팅방 목록 관리 (수동 입력, Excel import)
- 메시지·이미지 발송 (Win32 UI 자동화)
- 메시지 템플릿·발송 로그·통계
- 사용자별 설정 JSON persist

### 주요 기능 (현재 활성)

| 기능 | 설명 |
|------|------|
| 일반 발송 | 선택 방에 텍스트/이미지, 예약 시간 |
| 랜덤 발송 | 간격·시간대 내 반복 발송 |
| 템플릿 | 로컬 JSON CRUD |
| 통계 | 로그·템플릿 사용량 집계 |
| 설정 저장 | 탭·방·이미지 경로 등 |

### 운영 맥락

- **OS:** Windows only (`pywin32`, 카카오톡 PC 필수)
- **버전 (Phase 0):** V1 `entry_v1.py` (ERP 로그인), V2 `entry_v2.py` (게스트, 기본)
- **DRY_RUN:** UI 체크박스 또는 `KAKAO_SENDER_DRY_RUN=1` — Win32 발송 생략
- **데이터:** 로컬 JSON; V1은 로그인 시 `database.py` (env only, secret 하드코딩 금지)

상세 지도: [`docs/ai/repo-map.md`](docs/ai/repo-map.md)  
배포: [`docs/ai/deployment.md`](docs/ai/deployment.md)  
작업 절차: [`docs/ai/workflow.md`](docs/ai/workflow.md)  
V3 기획(질문형): [`docs/specs/phase-2-v3-mvp-requirements.md`](docs/specs/phase-2-v3-mvp-requirements.md)

---

## 2. Tech Stack

| 항목 | 기술 |
|------|------|
| 언어 | Python 3.11+ (확인: cpython-311 빌드 산출) |
| GUI | PyQt6 |
| OS 자동화 | pywin32 (`win32gui`, `win32api`, `win32clipboard`) |
| 데이터 | pandas (Excel), Pillow (이미지), pyperclip |
| DB (레거시) | PyMySQL, python-dotenv |
| 패키징 | PyInstaller (`KakaoSender.spec`) |
| 테스트 | **없음** (도입 전) |
| CI/CD | **없음** |

**확인 필요:** `requirements.txt` 부재 — 버전은 `BUILD_README.md` 참고.

---

## 3. Repository Structure

| 경로 | 역할 | 건드릴 때 |
|------|------|-----------|
| `main.py` | UI, 발송, Win32 | **High** — 단일 대형 파일 |
| `local_store.py` | templates/logs JSON | Medium |
| `settings_manager.py` | settings JSON | Low~Medium |
| `login_dialog.py` | ERP 로그인 UI | Medium (재활성화 시) |
| `database.py` | MySQL 레이어 | **Critical** (secret, SQL) |
| `style.py` | 스타일 | Low |
| `docs/ai/` | AI 워크플로 문서 | Low |
| `build/`, `dist/` | 빌드 산출 | 커밋 비권장 |
| `*.json` (settings/logs/templates) | 런타임·개인 데이터 | git 주의 |

---

## 4. Development Workflow

1. **분석** — `repo-map.md`, 관련 코드 읽기  
2. **계획** — 규모·위험도·수정 파일·검증 방법  
3. **승인** — 사용자 "진행" 전 코드 변경 금지  
4. **구현** — 최소 범위  
5. **검증** — py_compile, UI 기동, 수동 발송은 **사용자만** (승인 시)  
6. **문서화** — 구조·진입점 변경 시 `repo-map.md` 갱신 제안  

자세한 템플릿: `docs/ai/workflow.md`

---

## 5. Safety Rules

### 사전 승인 필요

- `database.py` 수정, DB 연결·쿼리 실행
- Win32 발송 핵심 (`open_chat`, `send_text`, `send_image`, `MessageSenderThread`) 동작 변경
- 랜덤/예약 타이머 로직 변경
- PyInstaller spec / 배포 산출물 변경
- `main.py` 7파일 이상 또는 대규모 리팩토링
- secret·env·자격증명 정리 및 git history 관련 작업

### 운영 장애 가능성

- 다수 채팅방 실발송
- 카카오톡 UI 변경으로 인한 잘못된 창 타겟
- SQL DELETE/UPDATE (database 재연결 시)

### 대규모 변경 기준

- **Large** → `docs/specs/[feature].md` 작성 후 승인

---

## 6. Testing and Verification

| 검증 | 명령/방법 |
|------|-----------|
| 문법 | `python -m py_compile <changed.py>` |
| 로컬 실행 | `python main.py` (UI만, Agent는 발송 클릭 금지) |
| 린트 | 미설정 — **확인 필요** |
| 타입체크 | 미설정 |
| 단위 테스트 | 없음 |
| 발송 E2E | 수동, 테스트 방 1개, 1메시지, 사용자 승인 |

변경 후 확인:

1. 앱이 기동되는가  
2. 설정 JSON이 깨지지 않았는가  
3. (발송 변경 시) 테스트 방 1건만 성공/실패 로그가 남는가  

---

## 7. Database Rules

**현재 main 경로는 DB 미사용.** `database.py` 작업 시에만 적용:

- WHERE 없는 UPDATE/DELETE 금지
- 운영 DB 직접 ALTER 금지 (승인·백업 후)
- 변경 전 SELECT로 영향 row 확인
- `verify_user`의 평문 비밀번호 비교 — 보안 개선 시 SPEC 필요
- migration 도구 없음 — 스키마 변경은 수동·별도 문서화

---

## 8. Secret and Config Rules

1. **실제 secret 값을 출력·커밋·문서에 전체 기재하지 않는다.**  
   마스킹 예: `AVNS_****`, `mysql://user:****@host:16751/db`
2. **코드에 운영 DB URL·비밀번호 하드코딩 금지.**  
   현재 `database.py`에 하드코딩 존재 → 제거 작업은 High, 별도 승인.
3. env는 `SERVICE_TYPE` → `.{live|local}.env` (파일은 레포에 없을 수 있음).
4. 테스트용 더미만 허용: `DATABASE_PASSWORD=dummy-for-test`
5. `.env.example` 추가 시 변수명만, 값은 placeholder.
6. 로그·JSON에 채팅방명·메시지·계정 정보 — 공유·커밋 주의.

---

## 9. Agent Behavior

- 먼저 **계획** 제시, 승인 후 구현  
- 변경 파일·이유를 명확히  
- 검증 결과를 솔직히 (못 하면 이유 + 사용자 명령)  
- 불확실한 것은 **「확인 필요」**  
- "완료" 과장 금지 — 테스트 없으면 수동 절차 명시  
- 카카오 **실발송은 Agent가 트리거하지 않음**  
- 작업 완료 후 **다음 단계 A/B/C** 제안 (막연한 조언 금지)

---

## 10. Human Approval Required

다음은 반드시 사람 승인:

- [ ] DB 스키마·데이터 변경·연결 테스트  
- [ ] 배포 exe 빌드·배포  
- [ ] 인증/로그인(`login_dialog`) 재활성화  
- [ ] 카카오톡 **실제** 메시지/이미지 발송 검증  
- [ ] secret 제거·키 로테이션·git history 정리  
- [ ] Win32 핵심 로직 대규모 변경  
- [ ] 랜덤 발송 장시간 운영 테스트  

---

## 11. Quick Commands

```powershell
python -m py_compile main.py
python main.py
pyinstaller KakaoSender.spec
```

---

## 12. Related Rules

- `.cursor/rules/00-project-constitution.mdc` — 공통 헌법  
- `.cursor/rules/40-secret-and-config-rules.mdc` — secret·env  

DB 재연결 시 추가 권장: `.cursor/rules/30-db-migration-rules.mdc` (아직 미생성)
