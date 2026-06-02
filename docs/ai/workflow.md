# AI Development Workflow — kakaotalk_autosender

> Cursor Agent와 사람이 함께 안전하게 개발하는 반복 가능한 절차입니다.

---

## 1. 기본 원칙

1. **Plan → Execute → Verify** — 구현 전 계획, 완료 후 검증.
2. **최소 변경** — 요청과 무관한 파일·리팩토링 금지.
3. **사람 승인** — High 위험 작업은 "진행" 전에 멈춤.
4. **실발송 기본 금지** — Agent 작업 중 카카오톡 실제 발송은 사용자 승인 없이 하지 않음.
5. **테스트 없이 완료 선언 금지** — 자동 테스트 없으면 수동 절차를 명시.

---

## 2. Plan → Execute → Verify

### Plan (분석·계획)

- 관련 파일 읽기 (`docs/ai/repo-map.md` 먼저 참고)
- 영향 범위·위험도(Small/Medium/Large, Low/Medium/High) 분류
- 수정 예정 파일 목록 + 이유
- 검증 방법(수동 포함) 명시
- **승인 요청:** "이 계획대로 진행해도 될까요?"

### Execute (구현)

- 사용자가 **"진행"**, **"적용해"**, **"파일 생성해"** 등으로 승인한 뒤에만 수정
- 승인된 파일만, 승인된 범위만 변경
- Large 작업은 `docs/specs/[feature-name].md` SPEC 작성 후 별도 승인

### Verify (검증)

| 가능한 검증 | 명령/방법 |
|-------------|-----------|
| 문법·import | `python -m py_compile main.py` (변경 파일 대상) |
| 앱 기동 | `python main.py` — UI만 확인, **발송 버튼 누르지 않음** |
| 린트 | 프로젝트 공통 설정 없음 — **확인 필요** |
| 단위 테스트 | 현재 없음 |
| Win32 발송 | **수동 only**, 테스트 방 1개, 짧은 메시지 1건 |

검증 못 한 항목은 이유와 사용자 실행 명령을 남긴다.

---

## 3. 작업 규모·위험도 (이 프로젝트 예시)

### Small (예)

- `local_store.py` 필드 추가
- 통계 탭 라벨 문구 수정
- `SettingsManager` 기본값 1개 변경

### Medium (예)

- DRY_RUN 플래그 추가
- 템플릿 검색 로직 변경
- 예약 발송 타이머 버그 수정

### Large (예)

- `main.py` 탭 구조 분리·모듈화
- `database.py` 재연결 + 로그인 복구
- Win32 창 탐색 로직 전면 교체

→ Large는 **SPEC 필수**, 한 번에 7파일 이상 무분별 변경 금지.

---

## 4. 카카오톡 발송 안전 규칙 (필수)

이 프로젝트의 가장 큰 운영 리스크는 **실제 메시지 발송**입니다.

### Agent( AI ) 행동

| 허용 | 금지 (승인 전) |
|------|----------------|
| 코드 읽기·계획·DRY_RUN 설계 | 여러 채팅방 일괄 발송 실행 |
| 발송 로직 diff 리뷰 | 랜덤 발송 start 호출 |
| mock/플래그 추가 **코드 작성** | 사용자 PC에서 `python main.py` 후 발송 버튼 클릭 |

### 사람( 개발자 ) 수동 검증 절차

1. 카카오톡 PC 실행
2. **본인 전용 테스트 채팅방** 1개만 목록에 등록
3. 메시지: 짧은 고정 문자열 (예: `[TEST] autosender`)
4. 일반 발송 1건만 실행
5. `logs_{user_id}.json`에 `is_sent` 기록 확인
6. 랜덤/예약 발송 테스트는 별도 승인 후, 간격을 길게(예: 60초+) 설정

### DRY_RUN (향후 도입 권장)

- `DRY_RUN=1` 또는 UI 체크박스: Win32 호출 생략, 로그만 기록
- Agent 구현 작업 시 **DRY_RUN 경로를 먼저** 만드는 것을 권장

---

## 5. Secret / DB 작업

- `database.py` 수정·DB 연결 테스트 → **High**, 별도 승인
- secret 값 답변·문서에 **전체 출력 금지** (마스킹: `AVNS_****`, `vultr-****.vultrdb.com`)
- `.env.example` 추가는 Medium — secret 정리 SPEC과 함께 진행 권장

---

## 6. 빌드·배포 작업

| 작업 | 위험도 |
|------|--------|
| `KakaoSender.spec` / `build.bat` 수정 | Medium |
| `dist/` exe 배포 | Medium — Windows Defender·경로 이슈 |
| `dist/`·`build/` git 커밋 | Low (용량·개인데이터) — 일반적으로 비권장 |

빌드 후 검증: exe 더블클릭 → UI 기동만 (발송 X).

---

## 7. Git 작업

- 사용자 변경 덮어쓰기 금지
- `git reset --hard`, `git clean` 등 파괴 명령은 명시적 요청 시만
- 커밋은 사용자 요청 시만
- 커밋 메시지 예: `fix:`, `feat:`, `docs:`, `refactor:`, `test:`

---

## 8. 작업 요청 시 Agent 응답 템플릿

개발 요청을 받으면 다음 순서로 답한다.

1. 요청 이해 (한 문단)
2. 관련 영역 탐색 (파일·디렉토리)
3. 작업 규모: Small / Medium / Large
4. 위험도: Low / Medium / High + 이유
5. 작업 계획 (Step 1, 2, 3…)
6. 수정 예정 파일 + 이유
7. 검증 계획
8. **승인 요청** — 멈춤

구현 완료 후:

1. 변경 요약
2. 수정 파일
3. 핵심 구현
4. 테스트/검증 결과
5. 남은 리스크
6. 사용자 직접 확인 사항
7. 다음 단계 A/B/C
8. 권장 선택

---

## 9. SDD (Medium 이상)

경로: `docs/specs/[feature-name].md`

포함: 배경, 목표, 요구사항, API/DB 변경, 보안, 테스트 계획, 구현 단계, 승인 필요 사항.

**SPEC 작성 후 구현하지 않고 승인 대기.**

---

## 10. 코드 리뷰 관점 (요약)

- Critical: 실발송 오류, secret 노출, 운영 DB 손상
- High: Win32 깨짐, 랜덤 무한 발송
- Medium: 설정 JSON 호환 깨짐
- Low: UI 문구, 스타일

---

## 11. 이 레포에서 자주 쓰는 명령

```powershell
# 문법 확인 (변경한 py 파일)
python -m py_compile main.py

# UI만 기동 (발송 금지)
python main.py

# 빌드
pyinstaller KakaoSender.spec
```

---

## 12. 문서 갱신 규칙

다음 변경 시 `repo-map.md` 또는 `AGENTS.md` 갱신을 제안한다.

- main 진입점·로그인 방식 변경
- LocalStore / Settings JSON 스키마 변경
- database.py 재연결
- DRY_RUN 또는 테스트 추가
