# HOWTOUSE — OBS 실시간 자막 사용 가이드

마이크나 시스템 소리를 실시간 음성 인식(STT)으로 바꿔 **OBS 화면에 자막**으로 띄우는 프로그램의 사용법을 처음부터 끝까지 정리한다.

> 더 깊은 레퍼런스: 설치·플랫폼은 [`README.md`](./README.md), 기능 상세는 [`DOCS.md`](./DOCS.md), 윈도우 exe 배포물은 [`docs/WINDOWS.md`](./docs/WINDOWS.md).

---

## 0. 한눈에 보기

```
[소리 입력]           [음성 인식]              [자막 가공]          [출력 sink]
마이크 / 시스템소리 ─▶ STT 엔진(로컬 Whisper ─▶ 줄수·필터·치환·CSS ─▶ ┌ browser: 웹 오버레이 → OBS 브라우저 소스
                       또는 OpenAI/Google 등)                        └ obs: obs-websocket → OBS 텍스트 소스
```

가장 흔한 흐름: **설치 → `config.toml` 준비 → `check-engine`로 점검 → `run`으로 방송**.

---

## 1. 설치

두 가지 방법 중 하나를 고른다.

### 방법 A — 소스에서 실행 (개발·디버깅, Python 불필요, `uv`만 설치)

```powershell
# uv 설치 (한 번만) — Windows PowerShell
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"

git clone https://github.com/samsepi0l-1337/obs-captions.git
cd obs-captions

uv sync --extra local --extra loopback --extra obs   # 로컬 STT + 시스템소리 + OBS 연동
copy config.example.toml config.toml
copy .env.example .env                                # 클라우드 STT 쓸 때만 키 입력
```

- macOS/Linux는 `copy` 대신 `cp`, 그리고 `--extra loopback`은 Windows 전용이라 제외한다.
- GPU(NVIDIA CUDA) 가속: `--extra gpu`를 추가한다.

### 방법 B — 빌드된 exe 사용 (Python/uv 없이)

`dist\obs-captions\` 폴더를 통째로 받아 원하는 위치에 둔다. **`_internal\` 폴더가 있어야 하며, exe만 떼면 실행되지 않는다.** 이후 명령은 `obs-captions.exe <명령>` 형태로 쓴다(아래 예시의 `uv run python -m obs_captions`를 `obs-captions.exe`로 대체).

exe 직접 빌드:

```powershell
.\scripts\build_windows.ps1        # → dist\obs-captions\obs-captions.exe
```

---

## 2. 명령어(CLI)

`uv run python -m obs_captions <명령>` (또는 `uv run obs-captions <명령>`, exe면 `obs-captions.exe <명령>`).

| 명령 | 하는 일 |
|---|---|
| `list-devices` | 마이크(입력 장치) 목록 + 인덱스 |
| `list-loopback-devices` | 시스템 소리 캡처용 WASAPI 루프백 장치 목록 (**Windows 전용**) |
| `config` | 현재 적용 설정 확인 (API 키는 마스킹) |
| `serve --demo` | 실제 음성 없이 가짜 자막으로 오버레이 배치·CSS 테스트 |
| `check-engine <engine> [--wav a.wav]` | 엔진 준비 상태·API 키 점검 (+ WAV 스트리밍 스모크 테스트) |
| `run [--sink browser\|obs\|both]` | **실제 자막 생성 시작** (소리 → STT → 자막) |

---

## 3. 설정 파일 (`config.toml`)

`config.example.toml`을 복사해 필요한 값만 바꾼다. 핵심 항목:

```toml
engine   = "local"     # 음성 인식 엔진 (아래 표)
language = "ko"

[audio]
source = "mic"         # mic(마이크) | loopback(시스템 소리, Windows 전용)
device = ""            # 빈값=기본 장치. 특정 장치는 이름/인덱스

[server]
port = 8765            # 오버레이 서버 포트 (경로 A)

[local]
model_size = "small"   # tiny | base | small | medium | large-v3
device     = "auto"    # auto(GPU 있으면 GPU, 없으면 CPU) | cpu | cuda
```

클라우드 엔진은 `.env`에 키를 넣는다. `config` 명령으로 값이 잘 잡혔는지 확인한다.

---

## 4. STT 엔진 선택

`engine` 값으로 고른다. 클라우드는 `.env`에 키가 필요하다.

| engine | 모드 | 키(.env) | 비고 |
|---|---|---|---|
| `local` (기본) | 스트리밍 에뮬 | — (불필요) | faster-whisper, 오프라인·무료. Windows/Linux는 NVIDIA CUDA 가속 |
| `openai` | 실시간 스트리밍 | `OPENAI_API_KEY` | Realtime 모델 |
| `elevenlabs` | 실시간(~150ms) | `ELEVENLABS_API_KEY` | Scribe v2 Realtime |
| `google` | 실시간 스트리밍 | `GEMINI_API_KEY` 또는 서비스계정 | Gemini Live / Speech-to-Text v2 |
| `xai` | 실시간 스트리밍 | `XAI_API_KEY` | Grok transcribe |
| `deepgram` | 실시간 스트리밍 | `DEEPGRAM_API_KEY` | Nova-3 |
| `assemblyai` | 실시간 스트리밍 | `ASSEMBLYAI_API_KEY` | 기본 모델은 영어 전용 |
| `azure` | 실시간(SDK) | `AZURE_SPEECH_KEY` + `AZURE_SPEECH_REGION` | `--extra azure` 필요 |
| `openrouter` | 배치(발화 단위) | `OPENROUTER_API_KEY` | Whisper, 약간의 지연 |
| `replicate` | 배치(발화 단위) | `REPLICATE_API_TOKEN` | 콜드스타트 지연 |
| `groq` | 배치(발화 단위) | `GROQ_API_KEY` | Whisper 배치 |

- 저지연이 핵심이면 `local` / `openai` / `elevenlabs` / `google` / `xai` 권장.
- 키 설정 후 점검: `check-engine deepgram` (연결만) 또는 `check-engine deepgram --wav sample.wav` (실제 전사).

---

## 5. 오디오 입력: 마이크 vs 시스템 소리

- **마이크**: `[audio] source = "mic"` (기본). 전 플랫폼.
- **시스템 소리(게임·영상)**: **Windows 전용**.
  1. `uv sync --extra loopback`
  2. `list-loopback-devices`로 장치 확인
  3. `config.toml`에 `[audio] source = "loopback"` (특정 출력은 `device`에 이름/인덱스)

> macOS/Linux는 OS 루프백이 없어 BlackHole 등 가상 출력 장치를 만들어 `source="mic"`로 잡아야 한다.

---

## 6. 실행 & OBS 연동

### 경로 A — Browser Source (권장: 스타일 자유·저지연)

```bash
uv run python -m obs_captions run                 # (또는 run --sink browser)
```

1. OBS → Sources → **Browser** 추가
2. URL: `http://127.0.0.1:8765/overlay.html`, 크기: 캔버스에 맞게(예 1920×1080)
3. 배경은 자동 투명 — 영상 위에 자막만 표시

먼저 배치만 맞추려면: `serve --demo`로 가짜 자막을 띄운 뒤 위 URL을 연다.

### 경로 B — obs-websocket → 네이티브 Text 소스 (브라우저 없음)

1. OBS → Tools → **WebSocket Server Settings**에서 서버 활성화(포트·비밀번호 확인)
2. 설정:

```toml
[obs]
host        = "localhost"
port        = 4455
source_name = "LiveCaptions"   # OBS에서 만든 Text 소스 이름
```

```bash
# .env
OBS_WS_PASSWORD=your_obs_websocket_password
```

3. 실행:

```bash
uv run python -m obs_captions run --sink obs      # 둘 다면 --sink both
```

---

## 7. 오버레이 스타일 (경로 A)

`config.toml [overlay]` 값이 오버레이에 **CSS 변수로 즉시 주입**된다(코드 수정 불필요).

```toml
[overlay]
font_family   = "Pretendard, 'Noto Sans KR', sans-serif"
font_size     = 48
color         = "#ffffff"           # 확정 자막 색
partial_color = "#aaaaaa"           # 진행 중 자막 색
background    = "rgba(0,0,0,0.35)"
position      = "bottom"            # top | middle | bottom
max_lines     = 3
max_chars_per_line = 30             # 0 = 줄바꿈 없음
```

더 자유로운 스타일은 `custom.css`를 만들고 `[overlay] custom_css = "custom.css"`로 지정한다(기본 스타일 뒤에 로드되어 덮어씀). DOM 구조: `.caption-box > .caption > (.committed, .partial)`.

---

## 8. 자막 텍스트 가공 (모두 기본 OFF)

`config.toml`에 해당 섹션을 넣을 때만 동작한다.

```toml
# 치환(오인식 교정)
[[text.replacements]]
match = "whisper"
replace = "Whisper"

[text]
filter_words   = ["badword"]        # 단어 필터
filter_mode    = "mask"             # mask | remove
suppress_blank = true               # 환각(무음 구간 헛문장) 억제
suppress_regex = ["\\[.*\\]", "thank you.*"]

[export]                            # 자막 파일 저장
enabled = true
path    = "captions.srt"
format  = "srt"                     # txt | srt | vtt
```

적용 순서는 치환 → 필터.

---

## 9. OBS 핫키 (일시정지 / 재개 / 초기화)

obs-websocket이 핫키 이벤트를 못 주므로, **센티넬 오디오 소스의 음소거 변경**을 핫키 캐리어로 쓴다.

```toml
[obs.hotkey]
enabled     = true
pause_input = "_CaptionPause"
clear_input = "_CaptionClear"
```

OBS 설정:
1. Sources에 **Audio Input Capture** 2개 추가 → 이름 `_CaptionPause` / `_CaptionClear` (config와 일치, 장면 배치 불필요)
2. 두 소스 모두 Audio Mixer에서 **기본 음소거**
3. Settings → Hotkeys:
   - `_CaptionPause` Mute·Unmute를 같은 키(예 F9)에 → 일시정지/재개 토글
   - `_CaptionClear` Mute만(예 F10) → 자막 초기화(앱이 자동 Unmute)

---

## 10. 자주 겪는 문제

| 증상 | 해결 |
|---|---|
| exe만 복사했더니 실행 안 됨 | `_internal\` 폴더째 복사 필요 |
| 자막이 OBS에 안 보임 | 경로 A는 브라우저 소스 URL 확인, 경로 B는 obs-websocket 접속 정보·Text 소스 이름 확인 |
| 시스템 소리에 자막을 달고 싶음 | `list-loopback-devices` 후 `[audio] source = "loopback"` (Windows 전용) |
| API 키 오류 | `check-engine <engine>`으로 점검, `.env` 키 확인 |
| GPU가 안 잡힘 | NVIDIA 드라이버 최신화 + `--extra gpu`. 미탐지 시 CPU로 조용히 폴백(로그 확인) |

---

## 11. 개발 / 테스트

```bash
uv run pytest -q            # 단위 테스트
uv run pytest -q -m slow    # 모델 다운로드 포함 통합 테스트
uv run ruff check .         # 린트
uv run ruff format .        # 포맷
```
