# 카카오톡 메시지 발송기

Windows PC의 **카카오톡 데스크톱**을 자동화해 여러 채팅방에 메시지·이미지를 보내는 PyQt6 앱입니다.

## 버전 (Phase 0)

| 버전 | 진입점 | 설명 |
|------|--------|------|
| **V1 일반** | `entry_v1.py` | ERP DB 로그인 후 사용 |
| **V2 게스트** | `entry_v2.py` | 로그인 없이 즉시 사용 (기본) |

개발 실행:

```powershell
pip install -r requirements.txt

# V2 (게스트)
python entry_v2.py

# V1 (로그인) — .live.env 또는 .env 필요
copy .env.example .live.env
# .live.env 편집 후
python entry_v1.py
```

환경변수로도 모드 지정 가능:

```powershell
$env:KAKAO_SENDER_MODE="v1"
python main.py
```

## DRY_RUN

실제 카카오 발송 없이 로그만 남깁니다.

- UI 상단 **「DRY_RUN」** 체크박스
- 또는 `KAKAO_SENDER_DRY_RUN=1`

```powershell
$env:KAKAO_SENDER_DRY_RUN="1"
python entry_v2.py
```

## 빌드 (Windows)

```powershell
build-v1.bat   # dist\KakaoSender-V1.exe
build-v2.bat   # dist\KakaoSender-V2.exe
```

자세한 옵션: [BUILD_README.md](BUILD_README.md)

## 배포 (exe + env)

V1 exe 옆에 `.live.env`가 필요합니다. 자세한 폴더 구성·체크리스트:

- [docs/ai/deployment.md](docs/ai/deployment.md)

## AI 개발 문서

- [AGENTS.md](AGENTS.md)
- [docs/ai/repo-map.md](docs/ai/repo-map.md)
- [docs/ai/workflow.md](docs/ai/workflow.md)
- [docs/ai/deployment.md](docs/ai/deployment.md)
- [docs/specs/phase-0-dual-build.md](docs/specs/phase-0-dual-build.md)
- [docs/specs/phase-2-v3-mvp-requirements.md](docs/specs/phase-2-v3-mvp-requirements.md) ← V3 MVP 요구사항(검토용)
- [docs/specs/phase-2-v3-uiux-brief.md](docs/specs/phase-2-v3-uiux-brief.md) ← **V3 UI/UX 디자이너 브리프**
- [docs/specs/phase-2-v3-workspace-plan.md](docs/specs/phase-2-v3-workspace-plan.md) ← V3 작업대 구현 계획
- [docs/specs/phase-2-v3-technical-spike.md](docs/specs/phase-2-v3-technical-spike.md) ← V3 기술 검증 계획

## 주의

- 카카오톡 PC가 실행 중이어야 합니다 (DRY_RUN 제외).
- V1은 DB 자격증명을 **코드에 넣지 말고** `.live.env`만 사용하세요.
- 로컬 `templates_*.json`, `logs_*.json`에는 채팅 내용이 들어갈 수 있습니다.
