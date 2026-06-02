# V3 Spike Tools

V3 “멀티 PC 카카오 작업대”의 기술 가능성을 확인하기 위한 **읽기/캡처 전용** 스크립트입니다.

`inspect_*`, `capture_*`, `stream_*` 등 저수준 스크립트는 기본적으로 발송·입력을 하지 않습니다.  
**작업대 GUI**(`pyqt_workspace_prototype.py`, `KakaoManager-V3.exe`)는 설정에 따라 클릭·입력·발송을 수행할 수 있습니다.

## 전제

- Windows 10/11
- 카카오톡 PC 실행
- Python 환경에 `pywin32`, `Pillow` 설치

```powershell
pip install -r requirements.txt
```

## 1. 카카오톡 창 감지

```powershell
python tools\v3_spike\inspect_kakao_windows.py
```

JSON 출력:

```powershell
python tools\v3_spike\inspect_kakao_windows.py --json
```

특정 창의 child control 확인:

```powershell
python tools\v3_spike\inspect_kakao_windows.py --children "카카오톡"
```

제목 매칭이 어렵거나 hwnd가 이미 있을 때:

```powershell
python tools\v3_spike\inspect_kakao_windows.py --children-hwnd 5509400
```

확인할 것:

- `main_windows`가 1개 이상인지
- `likely_chat_windows`에 현재 열린 대화창 제목이 보이는지
- child control에 `EVA_`, `RICHEDIT50W`, `Edit` 등이 어떻게 잡히는지

## 2. 현재 화면 캡처

후보 목록:

```powershell
python tools\v3_spike\capture_kakao_window.py --list
```

기본 대상 캡처:

```powershell
python tools\v3_spike\capture_kakao_window.py
```

다른 창 뒤에 가려진 경우에도 우선 창 내용을 직접 캡처하도록 `PrintWindow`를 사용합니다.
스크립트는 Windows DPI 스케일링으로 좌표가 작게 잡히지 않도록 DPI awareness를 먼저 설정합니다.

```powershell
python tools\v3_spike\capture_kakao_window.py --hwnd 5509400 --method printwindow
```

`PrintWindow`가 검은 화면/빈 화면을 만들면, 명시적으로 창을 앞으로 가져온 뒤 화면 픽셀을 캡처할 수 있습니다.

```powershell
python tools\v3_spike\capture_kakao_window.py --hwnd 5509400 --method imagegrab --bring-to-front
```

`--bring-to-front`는 클릭/입력/발송은 하지 않지만, Windows 포커스를 바꿀 수 있습니다.

## 3. 1초 자동 갱신 benchmark

메시지를 보내지 않고 선택 창을 1초 간격으로 캡처합니다.

후보 확인:

```powershell
python tools\v3_spike\stream_capture_benchmark.py --list
```

60초 동안 1초 간격 캡처:

```powershell
python tools\v3_spike\stream_capture_benchmark.py --hwnd 1052712 --duration 60 --interval 1 --method printwindow --save-format JPEG --quality 80
```

결과:

- `tools/v3_spike/output/stream_benchmark/<timestamp>/frame-*.jpg`
- `tools/v3_spike/output/stream_benchmark/<timestamp>/summary.json`

이 테스트는 캡처만 수행하며 클릭/입력/발송은 하지 않습니다.

## 4. PyQt live preview prototype

캡처 이미지의 하단 카카오 입력 영역을 잘라내고, 우리 UI 입력창/보내기 버튼을 붙인 프로토타입입니다.

후보 확인:

```powershell
python tools\v3_spike\pyqt_live_preview.py --list
```

대화창 선택 UI 실행:

```powershell
python tools\v3_spike\pyqt_live_preview.py
```

모의 보내기 모드(실제 발송 없음):

```powershell
python tools\v3_spike\pyqt_live_preview.py --hwnd 123456
```

실제 전송 허용:

```powershell
python tools\v3_spike\pyqt_live_preview.py --hwnd 123456 --allow-send
```

주의:

- `--allow-send` 없이 실행하면 보내기 버튼은 UI 느낌만 확인합니다.
- `--allow-send`를 켜도 전송 전 확인 팝업이 뜹니다.
- 하단 카카오 입력창이 아직 보이면 UI에서 “하단 입력 영역 숨김(px)” 값을 조정하세요.
- 미리보기 안의 아래로 스크롤 버튼/새 메시지 버튼을 클릭하면 백그라운드 클릭으로 전달됩니다.
- 미리보기 위에서 마우스 휠을 굴리면 실제 카카오톡 대화창에도 동일하게 휠 스크롤을 전달합니다.
- “백그라운드 입력”이 켜져 있으면 가능한 경우 창이 뒤에 있어도 `WM_SETTEXT/Enter` 방식으로 입력합니다.
- 이미 실행 중인 프로토타입에는 코드 변경이 반영되지 않으므로, 닫고 다시 실행하세요.

## 5. V3 workspace GUI prototype (카카오 매니저)

좌측 PC/톡방 목록, 중앙 작업 탭, 하단 메시지 입력을 합친 작업대형 GUI입니다.

### 실행

모의 보내기 (기본):

```powershell
python tools\v3_spike\pyqt_workspace_prototype.py
```

실제 전송 허용:

```powershell
python tools\v3_spike\pyqt_workspace_prototype.py --allow-send
```

프로젝트 루트 `.env`에 OpenAI 키 설정 시 AI 후보 생성 가능.

### exe 빌드

```powershell
build-v3.bat
```

산출물: `dist\KakaoManager-V3\KakaoManager-V3.exe` 및 `KakaoManager-V3_v{version}.zip`

exe와 **같은 폴더**에 `.env`를 두면 환경 변수를 읽습니다.  
PC 등록: `v3_registered_pcs.json` (Hub URL·원격 PC ID 포함).  
자동 업데이트: GitHub Release의 `KakaoManager-V3_v*.zip` (onedir robocopy 방식, roboTraffic focus_upgraded와 동일 패턴).

릴리스 태그 예: `git tag v1.4.3 && git push origin v1.4.3` → Actions가 zip 업로드.

### 현재 화면 — 클릭 / 더블클릭 / 스크롤

| 동작 | 설명 |
|------|------|
| 클릭 | 미리보기 위치 → 카카오톡 해당 좌표 클릭 |
| 더블클릭 | 채팅방 열기·항목 실행 등 |
| 휠 | 대화/목록 스크롤 |
| 하단 입력 | 타이핑 시 카카오톡 입력창에 실시간 반영 |
| Enter | 전송 (`--allow-send` 시) |

`클릭/스크롤 전달` 체크가 켜져 있어야 합니다.

### 원격 PC 제어 (Hub + Agent)

**1) Hub 서버** (중앙 PC 또는 VPS, stdlib HTTP — FastAPI 불필요):

```powershell
set KAKAO_REMOTE_TOKEN=your-secret
python tools\v3_spike\kakao_remote_hub.py --port 8765
```

**2) 원격 PC** (카카오톡 실행 중):

```powershell
set KAKAO_REMOTE_HUB_URL=http://hub-ip:8765
set KAKAO_REMOTE_TOKEN=your-secret
set KAKAO_REMOTE_PC_ID=office-pc-1
python tools\v3_spike\kakao_remote_agent.py
```

**3) 관리자 GUI** (이 PC):

- `.env`에 `KAKAO_REMOTE_HUB_URL`, `KAKAO_REMOTE_TOKEN` 설정
- **PC 등록** → Hub URL·원격 PC ID 입력
- 등록 PC 선택 → 미리보기·클릭·더블클릭·입력이 Hub 경유로 전달

Agent 기본값은 `allow_send=False` — 원격 Enter/실발송은 Controller에서 `--allow-send` + 확인 시에만 허용됩니다.

### PC 등록

1. 좌측 **PC 등록**
2. PC 이름, 호스트, **Hub URL**, **원격 PC ID** 입력
3. Hub에서 Agent heartbeat 시 **온라인** 표시

### 톡방 없을 때

열린 대화창이 없어도 카카오톡 **메인/로그인** 화면이 미리보기에 표시됩니다.  
목록에서 채팅방을 클릭해 열거나, 로그인 화면에서 직접 조작할 수 있습니다.

### 파일 첨부 대화 상자

카카오톡에서 파일 첨부 시 Windows **열기** 창(`#32770`)이 자동 감지되어 미리보기가 탐색기로 전환됩니다.  
닫으면 이전 채팅방으로 복귀합니다.

### 지원 기능 요약

- 0.5초 자동 갱신 (DPI 보정 캡처)
- 열린 톡방 / 메인 / 로그인 / 파일 대화 상자 감지
- 미리보기 클릭·더블클릭·휠 전달
- PC 등록 + 원격 Hub/Agent 제어
- GitHub Releases 자동 업데이트 (onedir zip)
- 예약 발송·AI 후보 (모의/API)
- 로그: `tools/v3_spike/output/workspace.log`

### 주의

- 실제 메시지 전송은 `--allow-send`와 확인 팝업 필요
- OpenAI 키는 `.env`에만 (`OPENAI_API_KEY`, `OPENAI_MODEL`)
- 캡처 이미지에 채팅 내용 포함 — 공유 주의

## 6. KakaoTalk UI Automation inspection

비활성 채팅방 목록이 UI Automation으로 노출되는지 확인합니다.

```powershell
python tools\v3_spike\inspect_kakao_uia.py --text-only
python tools\v3_spike\inspect_kakao_uia.py --contains "검색어"
```

클릭 테스트:

```powershell
python tools\v3_spike\inspect_kakao_uia.py --click-text "톡방 이름 일부"
```

주의:

- 현재 Spike 결과상 카카오톡 메인 목록은 `ChatRoomListCtrl`까지는 보이지만, 개별 방 이름/마지막 메시지가 UIA 텍스트로 바로 노출되지 않을 수 있습니다.
- 이 경우 `pyqt_workspace_prototype.py`의 `카카오 목록` 탭에서 이미지 기반 클릭으로 검증합니다.

특정 제목 캡처:

```powershell
python tools\v3_spike\capture_kakao_window.py --title "카카오톡"
```

제목 매칭이 실패할 때 후보 목록의 `hwnd`로 캡처:

```powershell
python tools\v3_spike\capture_kakao_window.py --hwnd 329640
```

저장 위치 지정:

```powershell
python tools\v3_spike\capture_kakao_window.py --title "카카오톡" --output tools\v3_spike\output\kakao-main.png
```

기본 출력 폴더:

```text
tools/v3_spike/output/
```

## 개인정보 주의

캡처 이미지에는 채팅방명, 메시지 본문, 개인톡 내용이 포함될 수 있습니다.

- 공유 전 반드시 민감정보를 가립니다.
- `tools/v3_spike/output/`은 gitignore 대상입니다.
- 고객/개인 대화가 보이는 상태에서는 외부 공유용 캡처를 만들지 마세요.

## Spike 결과 기록

실행 결과는 아래 문서에 요약하는 것을 권장합니다.

```text
docs/specs/results/v3-spike-1-window-detection.md
```

기록 항목:

- Windows 버전
- 카카오톡 PC 버전
- 메인 창 감지 여부
- 열린 대화창 감지 여부
- 캡처 성공 여부
- 실패 시 오류 메시지

