# V3 Spike 1 결과 — 카카오톡 창 감지 / 캡처

**실행일:** 2026-05-20  
**목적:** V3 작업대의 “PC 상태 / 열린 톡방 / 현재 화면” 기능을 위한 Win32 창 감지·캡처 가능성 확인  
**주의:** 메시지 발송, 클릭, 키보드 입력은 수행하지 않음

---

## 1. 실행 명령

```powershell
python tools\v3_spike\inspect_kakao_windows.py
python tools\v3_spike\capture_kakao_window.py --list
python tools\v3_spike\capture_kakao_window.py --title "카카오톡"
```

대화창 제목 캡처 재시도 중 제목 매칭 실패가 있어 `--hwnd` 옵션을 도구에 추가함.

---

## 2. 감지 결과

### 2.1 카카오톡 메인 창

감지 성공.

```text
main_windows: 1
hwnd=329640
title='카카오톡'
class='EVA_Window_Dblclk'
rect=(2041, 279, 2520, 928)
```

### 2.2 열린 대화창 후보

초기 감지 시 1개 확인.

```text
likely_chat_windows: 1
hwnd=659786
title='투투 스마트스토어 쿠팡 플레이스 리워드 정보공유'
class='EVA_Window_Dblclk'
rect=(1610, 149, 1990, 934)
```

단, 이후 캡처 후보 재조회 시 대화창 후보가 사라지고 메인 창만 남음.

가능한 원인:

- 대화창이 닫혔거나 숨겨짐
- 포커스/표시 상태 변화
- `RICHEDIT50W` 기반 감지 조건이 일시적으로 불안정

---

## 3. 캡처 결과

### 3.1 메인 창 캡처

성공.

```text
Capturing kind=main title='카카오톡' rect=(2041, 279, 2520, 928)
Saved: tools\v3_spike\output\20260520-115635-카카오톡.png
```

PowerShell 콘솔에서는 한글 파일명이 인코딩 문제로 깨져 보일 수 있음.

### 3.2 열린 대화창 캡처

첫 시도 실패.

```text
No target window found. Use --list to see candidates.
```

후속 조치:

- `capture_kakao_window.py`에 `--hwnd` 옵션 추가
- 다음 재검증 시 `--list`로 후보를 다시 확인한 뒤 `--hwnd <값>` 캡처 권장

---

## 4. 1차 판정

| 항목 | 결과 | MVP 영향 |
|------|------|----------|
| 카카오톡 메인 창 감지 | 성공 | PC별 “카카오 실행 중” 상태 가능 |
| 열린 대화창 감지 | 1회 성공, 재조회 실패 | “현재 열린 톡방”은 가능하지만 안정성 재검증 필요 |
| 메인 창 캡처 | 성공 | “현재 화면” 탭 MVP 가능성 높음 |
| 대화창 캡처 | 미완료 | `--hwnd`로 재시도 필요 |

---

## 5. UI/UX 반영 제안

현재 화면 탭은 MVP에 포함 가능성이 높음.

다만 열린 대화창 목록은 다음처럼 표현을 보수적으로 잡는 것이 안전함.

- 확정 표현: “현재 열린 톡방”
- 피할 표현: “전체 톡방”, “새 톡 전체”, “모든 미등록 톡방”

새 톡 개수, 미등록 톡방, 개인톡 구분은 아직 별도 Spike가 필요함.

---

## 6. 다음 검증

1. 대화창을 명확히 열어둔 상태에서:

   ```powershell
   python tools\v3_spike\capture_kakao_window.py --list
   python tools\v3_spike\capture_kakao_window.py --hwnd <chat_hwnd>
   ```

2. 카카오톡 메인 목록 child control 확인:

   ```powershell
   python tools\v3_spike\inspect_kakao_windows.py --children "카카오톡"
   ```

3. 새 톡/메시지 preview 감지를 위해 UI Automation 또는 `pywinauto` Spike로 진행.

---

## 7. 추가 실행 결과 — 대화창 캡처 / child control

**실행일:** 2026-05-20 추가 실행

### 7.1 열린 대화창 감지

대화창 2개가 감지됨.

```text
likely_chat_windows: 2
hwnd=5509400
class='EVA_Window_Dblclk'
rect=(1707, 73, 2088, 902)

hwnd=853900 또는 후속 재조회 시 다른 hwnd
class='EVA_Window_Dblclk'
```

후속 재조회 중 대화창 `hwnd`가 바뀌는 사례가 있었음. 따라서 Console/Agent 구현 시 **hwnd는 장기 ID로 쓰지 말고, 순간 작업 핸들로만 사용**해야 함.

### 7.2 대화창 캡처

`--hwnd 5509400` 기준 대화창 캡처 성공.

```text
Capturing kind=chat ... rect=(1707, 73, 2088, 902)
Saved: tools\v3_spike\output\20260520-115952-나는_SOLO_시청자_소통방.png
```

판정:

- “현재 화면” 탭에서 **대화창 화면 미리보기**는 MVP 가능성이 높음.
- 다만 캡처 이미지는 실제 대화 내용을 포함하므로 저장·전송·열람 정책 필요.

### 7.3 대화창 child control

대화창의 주요 child control:

```text
class='RICHEDIT50W' text='' rect=(1721, 795, 2088, 856)
class='EVA_VH_ListControl_Dblclk' text='' rect=(1708, 161, 2087, 785)
class='_EVA_CustomScrollCtrl' text=''
class='Edit' text=''
```

판정:

- 입력창(`RICHEDIT50W`)은 감지됨.
- 대화 내용 영역은 `EVA_VH_ListControl_Dblclk`로 보이나, Win32 `GetWindowText`로는 메시지 텍스트가 노출되지 않음.
- 따라서 **이전 대화 내용 / 마지막 대화 텍스트 추출**은 기본 Win32만으로는 어렵고, 다음 중 하나가 필요함:
  - UI Automation / `pywinauto`
  - OCR
  - 화면 캡처 기반 사람이 보는 preview

### 7.4 카카오톡 메인 창 child control

메인 창에서 아래 컨트롤들이 확인됨.

```text
ChatRoomListView
ChatRoomListCtrl
ContactListView
ContactListCtrl
SearchListCtrl
```

판정:

- 카카오톡 메인 목록이 내부적으로 `ChatRoomListCtrl` 형태로 존재함.
- 하지만 목록 item의 방 이름, 새 톡 수, 마지막 메시지 텍스트는 Win32 `GetWindowText` 결과로 직접 노출되지 않음.
- 다음 Spike는 `pywinauto`/UIA로 `ChatRoomListCtrl` children 또는 pattern 접근 가능성을 확인해야 함.

---

## 8. 업데이트된 1차 결론

| 기능 | 판정 |
|------|------|
| 카카오톡 메인 창 감지 | 가능 |
| 열린 대화창 감지 | 가능, 단 hwnd 변동 주의 |
| 메인 창 캡처 | 가능 |
| 대화창 캡처 | 가능 |
| 이전 대화 내용 “화면으로 보기” | 가능 (캡처) |
| 이전 대화 내용 “텍스트로 추출” | Win32 기본 방식으로는 불가/불확실 |
| 가장 마지막 대화 자동 추출 | UIA/OCR 추가 Spike 필요 |

---

## 9. 캡처 방식 보강

초기 캡처 방식은 `ImageGrab.grab(bbox=...)`였기 때문에 카카오톡 창이 다른 창 뒤에 있으면 가려진 화면이 캡처될 수 있음.

도구 수정:

- `--method auto` 기본값: `PrintWindow` 먼저 시도 후 실패 시 `ImageGrab` fallback
- `--method printwindow`: 창 DC 기반 캡처
- `--method imagegrab`: 기존 화면 좌표 기반 캡처
- `--bring-to-front`: 명시적으로 창을 앞으로 가져온 뒤 캡처

주의:

- `PrintWindow`는 다른 창에 가려진 경우에도 성공할 수 있지만, 앱 렌더링 방식에 따라 검은 화면/빈 화면이 나올 수 있음.
- `--bring-to-front`는 클릭·입력은 하지 않지만, 사용자 화면 포커스를 바꿈.

### 9.1 재실행 결과

초기 구현은 `win32gui.PrintWindow` 호출을 시도했으나, 현재 환경의 `pywin32`에는 해당 래퍼가 없어 실패함.

```text
AttributeError: module 'win32gui' has no attribute 'PrintWindow'
```

수정:

- `ctypes.windll.user32.PrintWindow`로 호출 변경

재실행 결과:

```text
Capturing kind=chat title='✅️나는 SOLO 시청자 소통방' rect=(1707, 66, 2088, 895) method=printwindow
Saved: tools\v3_spike\output\20260520-120641-나는_SOLO_시청자_소통방.png
```

판정:

- `PrintWindow` 방식 대화창 캡처 성공.
- 다른 창에 가려진 카카오톡 화면 문제는 우선 `--method printwindow`로 개선 가능.
- 캡처 내용이 실제 대화 영역까지 정상인지 육안 확인 필요.

### 9.2 DPI 스케일링 보정

`PrintWindow` 캡처는 성공했지만 이미지가 실제 창 전체보다 작게 잘리는 문제가 확인됨. 원인은 Windows 디스플레이 배율(DPI scaling)로 인해 `GetWindowRect` 좌표가 실제 픽셀보다 작게 반환된 것으로 추정.

수정:

- 프로세스 시작 시 DPI awareness 설정
- `DwmGetWindowAttribute(DWMWA_EXTENDED_FRAME_BOUNDS)`로 보이는 창 프레임 좌표 우선 사용
- `PrintWindow` bitmap 크기는 DPI 보정된 window rect 기준 사용

재실행 결과:

```text
후보 rect 예시: (2922, 446, 3492, 1406)
캡처 크기: 570x960

후보 rect 예시: (2296, 354, 2868, 1635)
캡처 크기: 572x1281
```

이전 캡처 크기:

```text
381x829
```

판정:

- DPI 보정 후 대화창 전체 높이/너비에 가까운 캡처가 가능해짐.
- V3 현재 화면 기능은 DPI awareness + PrintWindow 조합을 기본 전략으로 삼는 것이 좋음.

---

## 10. 실제 보내기 전/후 캡처 테스트

**실행일:** 2026-05-20  
**대상:** 사용자가 활성화한 1개 대화창  
**주의:** 사용자 승인 후 실제 테스트 메시지 1건 전송함.

### 10.1 실행 흐름

1. 대화창 후보 확인
2. 보내기 전 캡처
3. 테스트 메시지 1건 전송
4. 1초 대기
5. 보내기 후 캡처

테스트 메시지:

```text
[V3 Spike] 실제 발송 테스트입니다. 캡처 갱신 확인용입니다.
```

### 10.2 감지 대상

```text
likely_chat_windows: 1
hwnd=987176
title='고동욱'
rect=(1688, 195, 2258, 1155)
```

### 10.3 생성 파일

```text
tools\v3_spike\output\before-send-test.png
tools\v3_spike\output\after-send-test.png
```

이미지 크기:

```text
before-send-test.png  570x960  51041 bytes
after-send-test.png   570x960  48991 bytes
```

### 10.4 판정

- 실제 메시지 전송 전/후 캡처 흐름 성공.
- `보내기 → 1초 뒤 자동 캡처 → 이후 1초 갱신` UX는 기술적으로 가능성이 높음.
- V3 MVP에서 “현재 화면 자동 갱신”은 선택 PC/발송 중 PC 중심으로 설계하는 것이 적절함.

### 10.5 운영 정책 반영

확정 정책:

- 선택 PC: 1초 자동 캡처
- 보내기 직후: 1초 뒤 자동 캡처
- 해당 PC를 보고 있는 동안: 1초 갱신 유지
- 다른 PC 선택 시: 이전 PC 캡처 중지
- 미선택 PC: 상태 정보만 유지

---

## 11. 1초 캡처 스트림 benchmark

**실행일:** 2026-05-20  
**대상:** 사용자가 새로 열어둔 대화창  
**주의:** 메시지 발송 없음. 캡처만 수행.

### 11.1 실행 명령

```powershell
python tools\v3_spike\stream_capture_benchmark.py --hwnd 1052712 --duration 60 --interval 1 --method printwindow --save-format JPEG --quality 80
```

### 11.2 대상

```text
title='✅️나는 SOLO 시청자 소통방'
hwnd=1052712
rect=(2561, 99, 3132, 1342)
```

### 11.3 결과

```text
attempted: 61
success: 61
failed: 0

latency avg: 81.71 ms
latency min: 44.08 ms
latency max: 130.24 ms

avg PNG:  237,011.97 bytes
avg JPEG:  83,842.48 bytes
avg WEBP:  49,389.74 bytes
```

출력:

```text
tools\v3_spike\output\stream_benchmark\20260520-122742
```

### 11.4 판정

- 1초 단위 캡처는 60초 동안 실패 없이 동작.
- 평균 캡처 시간 약 82ms로 1초 갱신에는 충분한 여유가 있음.
- JPEG quality 80 기준 평균 약 84KB/frame.
- 1대 PC 1초 갱신 시 대략 5MB/min 수준.
- WEBP는 평균 약 49KB/frame로 더 효율적이나, PyQt 표시/인코딩 호환성을 추가 확인해야 함.

### 11.5 V3 설계 반영

확정 정책은 현실적임:

- 선택 PC: 1초 자동 캡처
- 보내기 직후: 1초 뒤 자동 캡처
- 해당 PC를 보고 있는 동안: 1초 갱신 유지
- 다른 PC 선택 시: 이전 PC 캡처 중지
- 미선택 PC: 상태 정보만 유지

모든 PC 동시 1초 캡처는 여전히 비권장. 5대 동시 캡처 시 JPEG 기준 약 25MB/min 이상이 될 수 있음.

---

## 12. PyQt live preview prototype

**목적:** V3의 “현재 화면 1초 갱신 + 우리 UI 입력창” 경험을 빠르게 확인.

추가 파일:

```text
tools\v3_spike\pyqt_live_preview.py
```

동작:

- 선택한 카카오톡 대화창을 1초 단위로 캡처
- 캡처 이미지 하단의 카카오 입력 영역을 crop
- 하단에 V3 자체 입력창/보내기 버튼 표시
- 기본은 모의 보내기
- `--allow-send` 옵션이 있을 때만 실제 전송 가능
- 실제 전송 전 확인 팝업 표시
- 보내기 후 1초 뒤 자동 갱신

실행:

```powershell
python tools\v3_spike\pyqt_live_preview.py --list
python tools\v3_spike\pyqt_live_preview.py --hwnd <chat_hwnd>
python tools\v3_spike\pyqt_live_preview.py --hwnd <chat_hwnd> --allow-send
```

주의:

- 열린 대화창이 없으면 `--list`에는 메인 창만 보일 수 있음.
- 대화창을 하나 열어둔 뒤 다시 `--list` 실행 필요.
- `--allow-send`는 실제 메시지를 보낼 수 있으므로 테스트 방에서만 사용.

---

## 13. 백그라운드 휠 스크롤 테스트

**실행일:** 2026-05-20  
**대상:** 열린 카카오톡 대화창  
**주의:** 메시지 발송 없음. 휠 스크롤 이벤트와 캡처만 수행.

### 13.1 실행 흐름

1. 스크롤 전 캡처
2. 대화창 중앙 좌표에 `WM_MOUSEWHEEL` 5회 전송
3. 1초 대기
4. 스크롤 후 캡처

### 13.2 대상

```text
title='[인증] 힐스테이트 더 운정 오피스텔 입주민 모임'
hwnd=1511438
rect=(2296, 354, 2868, 1635)
```

### 13.3 이벤트 로그

```text
image 572 1281
wheel delta=-120 screen=(2582,994) target=4132324
wheel delta=-120 screen=(2582,994) target=4132324
wheel delta=-120 screen=(2582,994) target=4132324
wheel delta=-120 screen=(2582,994) target=4132324
wheel delta=-120 screen=(2582,994) target=4132324
```

### 13.4 생성 파일

```text
tools\v3_spike\output\before-wheel-test.png  572x1281  102597 bytes
tools\v3_spike\output\after-wheel-test.png   572x1281   98034 bytes
```

### 13.5 판정

- 미리보기 좌표를 실제 카카오톡 창 좌표로 변환해 백그라운드 휠 이벤트를 전송하는 흐름은 동작.
- 실제 시각적 스크롤 변화는 캡처 전/후 이미지를 육안 비교해야 함.
- PyQt 프로토타입에는 `wheelEvent` 전달을 추가함.

---

## 14. V3 작업대형 GUI Prototype

**목적:** 수정 기획안의 실제 화면 구조(좌측 PC/톡방 목록, 중앙 탭, 하단 입력)를 한 화면에서 검증.

추가 파일:

```text
tools\v3_spike\pyqt_workspace_prototype.py
```

지원 기능:

- 좌측 PC 카드: 현재 로컬 PC 상태와 열린 톡방 개수
- 좌측/중앙 톡방 목록: 현재 열린 카카오톡 대화창
- `카카오 목록` 탭: 메인 목록 캡처 표시
- 메인 목록 이미지 클릭/스크롤 전달로 비활성 채팅방 열기 검증
- 중앙 탭:
  - 카카오 목록
  - 톡방 보기
  - 발송하기
  - 현재 화면
- 현재 화면 1초 자동 갱신
- 캡처 이미지 하단 입력 영역 crop
- 미리보기 클릭 전달
- 미리보기 휠 스크롤 전달
- 하단 V3 자체 입력창
- 기본 모의 보내기
- `--allow-send` 시 확인 팝업 후 실제 보내기
- 예약 발송 타이머 모의 등록
- 톡방 목록 옆 예약 카운트다운 표시
- 예약 항목 클릭 시 보낼 메시지 미리보기
- AI 자동 응답 역할/적극성 설정 모의 UI

실행:

```powershell
python tools\v3_spike\pyqt_workspace_prototype.py
python tools\v3_spike\pyqt_workspace_prototype.py --allow-send
```

판정:

- 단일 대화창 미리보기에서 한 단계 확장해, V3 작업대 UX의 뼈대를 확인할 수 있음.
- 아직 Hub/Agent/다른 PC 연동은 없음.
- 다음 단계는 이 UI 구조를 기준으로 Agent/Hub 데이터 모델과 연결하는 것.

### 14.1 예약 / AI 모의 UI 추가

추가 동작:

- 하단 입력창에서 메시지 작성
- `예약(분)` 값 지정
- `예약 발송` 클릭
- 선택 톡방 옆에 `⏱ mm:ss` 표시
- `예약 / AI` 탭에서 전체 예약 목록 확인
- 예약 항목 클릭 시 보낼 메시지 미리보기
- AI 역할과 답변 적극성 설정 후 모의 답변 후보 확인
- AI off/semi/on 모드 설정
- 직접 프롬프트, 예시 응답, 참고 자료, 금지/주의사항 입력
- 채팅 텍스트가 있으면 우선 사용, 없으면 현재 캡처 이미지를 Vision 입력으로 사용
- `OPENAI_API_KEY` / `OPENAI_MODEL` 기반 실제 OpenAI 호출 연결

주의:

- 현재 예약은 실제 발송되지 않는 모의 UI.
- API 키가 없으면 AI는 모의 답변 표시.
- API 키가 있으면 답변 후보 생성 API를 호출할 수 있음.
- 실제 예약 발송/AI 자동 발송은 별도 승인과 안전장치 필요.

---

## 15. UI Automation 비활성 채팅방 목록 검증

**목적:** 열린 대화창이 아닌 카카오톡 메인 목록의 채팅방 이름/마지막 메시지/새 톡을 텍스트로 읽을 수 있는지 확인.

추가 파일:

```text
tools\v3_spike\inspect_kakao_uia.py
```

읽기 전용 실행:

```powershell
python tools\v3_spike\inspect_kakao_uia.py --text-only --limit 300 --max-depth 8
```

결과 요약:

```text
KakaoTalk UIA nodes: hwnd=329640 total=18 shown=6
Window text='카카오톡'
Pane text='ChatRoomListView_...'
Pane text='ChatRoomListCtrl_...'
```

판정:

- 카카오톡 메인 목록 컨테이너(`ChatRoomListCtrl`)는 UIA로 확인됨.
- 하지만 개별 채팅방 이름, 마지막 메시지, 새 톡 배지는 UIA 텍스트로 직접 노출되지 않음.
- 따라서 비활성 채팅방 목록은 현재 단계에서 “텍스트 추출”보다 “메인 목록 캡처 + 이미지 기반 클릭” 방식이 현실적임.

V3 Prototype 반영:

- `pyqt_workspace_prototype.py`에 `카카오 목록` 탭 추가.
- 메인 목록 캡처를 보여주고, 사용자가 이미지 위의 방을 클릭하면 실제 카카오톡 메인 목록으로 클릭 전달.
- 방이 열리면 기존 “열린 톡방” 목록/현재 화면 갱신으로 이어지는 UX를 검증.



