# OBS 실시간 자막 (OBS Live Captions)

방송인의 마이크 음성을 실시간 STT로 변환해 **OBS 화면에 자막 오버레이**로 띄우는 서비스. 한국어 전사를 낮은 지연으로 렌더링하는 것이 목표.

- 공유 **Caption Engine**(오디오 캡처 → VAD → STT → 자막 상태머신) + 렌더러 2종:
  - **경로 A** — localhost 서버 + HTML 오버레이를 OBS **Browser Source**로 표시 (스타일 자유·저지연·무빌드)
  - **경로 B** — **obs-websocket(v5)** 으로 OBS **네이티브 Text 소스** 실시간 갱신 (브라우저 없음·무빌드)
- STT는 **로컬 + 다중 클라우드 provider**를 설정으로 선택하는 확장형 pluggable backend.
- 전체 설계: `~/.claude/plans/melodic-frolicking-axolotl.md`, 코딩 계약: `AGENTS.md`.

> ⚙️ **구현 현황 (Status)**
> - ✅ M0 부트스트랩(설정/CLI) · ✅ M1a 엔진 코어(상태머신·WS 서버) · ✅ M1b 로컬 STT
> - ✅ M2 오버레이(CSS 커스터마이즈) · ✅ M3 멀티 provider · ✅ M4 경로 B(obs-websocket) · ✅ M5 비교/벤치마크

---

## 요구 사항

- **Windows 10/11** 또는 **macOS (Apple Silicon)** — 둘 다 1급 지원 플랫폼(Linux도 동작)
- **Python 3.12** (`uv`로 핀), `ffmpeg`, OBS Studio
- **로컬 STT GPU 가속(권장, Windows/Linux)**: NVIDIA GPU + 최신 드라이버. CUDA/cuDNN 런타임은 `--extra gpu` 휠로 자동 설치(아래 "로컬 STT: GPU/CPU" 참고). GPU가 없으면 자동으로 CPU로 폴백.
- macOS는 로컬 STT가 CPU로 동작(현재 NVIDIA CUDA 미지원).
- 클라우드 provider 사용 시 해당 API 키(아래 표)

## 빠른 시작

```bash
uv sync                            # 코어 의존성
uv sync --extra local              # + 로컬 STT(faster-whisper, CPU)
uv sync --extra local --extra gpu  # + Windows/Linux NVIDIA CUDA 런타임 (macOS는 gpu extra 자동 제외)
cp config.example.toml config.toml
cp .env.example .env        # 클라우드 provider 키 입력(쓸 때만)

uv run python -m obs_captions list-devices   # 입력 장치 목록 + 인덱스
uv run python -m obs_captions config         # 현재 설정 확인(키는 *** 마스킹)
uv run python -m obs_captions serve --demo   # 가짜 자막으로 오버레이 서버만 띄워 보기
uv run python -m obs_captions run            # 마이크 → STT → 자막(경로 A 서버 동시 실행)
```

오버레이 URL: `http://127.0.0.1:8765/overlay.html` (포트는 `[server]`에서 변경)

---

## 오디오 입력: 마이크 vs 시스템 사운드(loopback)

`config.toml [audio] source`로 캡처 소스를 고릅니다.

| `source` | 캡처 대상 | 플랫폼 | 설치 |
|---|---|---|---|
| `mic`(기본) | 마이크 입력(sounddevice) | 전 플랫폼 | 기본 의존성 |
| `loopback` | 데스크톱/시스템 사운드(WASAPI 루프백) — 게임·미디어 소리 자막 | **Windows 전용** | `uv sync --extra loopback` |

```toml
[audio]
source = "loopback"   # mic | loopback
device = ""           # 빈값=기본 루프백 장치. 특정 장치는 이름/인덱스 지정
```

**Windows 시스템 사운드 자막 설정**

1. `uv sync --extra loopback` — `pyaudiowpatch`(PyAudio 포크) 설치(Windows 휠만, macOS/Linux는 환경 마커로 자동 제외).
2. `uv run python -m obs_captions list-loopback-devices` — 루프백 장치 이름/인덱스 확인(필요 시).
3. `config.toml`에 `[audio] source = "loopback"`(특정 출력은 `device`에 이름/인덱스).
4. `uv run python -m obs_captions run` — 스피커로 나가는 소리가 STT→자막으로.

> 루프백 장치는 보통 48kHz stereo이며, 내부에서 16kHz mono로 자동 다운믹스·리샘플됩니다(마이크 경로와 동일 파이프라인).
> macOS/Linux는 OS 차원의 시스템 오디오 루프백이 없어 BlackHole/PulseAudio monitor 같은 **가상 출력 장치**를 만들어 `source="mic"` + 해당 가상 장치로 잡아야 합니다(본 extra 범위 밖).

---

## STT 백엔드 / provider 선택

`config.toml`의 `engine` 값으로 선택하고, 클라우드는 `.env`에 키를 넣습니다. provider별 모델·옵션은 `[providers.<name>]`로 조정합니다.

| engine | 모드 | 키(.env) | 비고 |
|---|---|---|---|
| `local` (기본) | 스트리밍 에뮬(LocalAgreement-2) | — (키 불필요) | `faster-whisper`. Windows/Linux는 **NVIDIA CUDA** 가속(`device="auto"`/`"cuda"`), 그 외엔 CPU int8. `uv sync --extra local`(+GPU는 `--extra gpu`). 오프라인·무료 |
| `openai` | **실시간 스트리밍** | `OPENAI_API_KEY` | Realtime API(`gpt-realtime-whisper`) |
| `elevenlabs` | **실시간 스트리밍**(~150ms) | `ELEVENLABS_API_KEY` | Scribe v2 Realtime |
| `google` | **실시간 스트리밍** | `GOOGLE_APPLICATION_CREDENTIALS`(+`GOOGLE_CLOUD_PROJECT`) 또는 `GEMINI_API_KEY` | Speech-to-Text v2 `chirp_2` 스트리밍(서비스계정, `--extra google`, 5분 한계 전 자동 재시작) 또는 Gemini Live(API 키, 세션 15분 제한 자동 재연결) |
| `xai` | **실시간 스트리밍** | `XAI_API_KEY` | Grok `grok-transcribe` (wss) |
| `openrouter` | 배치/근실시간(utterance) | `OPENROUTER_API_KEY` | Whisper(`whisper-large-v3-turbo` 등). 스트리밍 미지원 → VAD 세그먼트 단위 |
| `replicate` | 배치/근실시간(utterance) | `REPLICATE_API_TOKEN` | Whisper / incredibly-fast-whisper. 콜드스타트 지연 있음 |
| `assemblyai` | **실시간 스트리밍** | `ASSEMBLYAI_API_KEY` | Universal Streaming v3 (`universal-streaming-english`). 한국어 스트리밍은 미확인 |
| `deepgram` | **실시간 스트리밍** | `DEEPGRAM_API_KEY` | Nova-3 live streaming (기본 모델 `nova-3`) |
| `groq` | utterance-mode | `GROQ_API_KEY` | Whisper(`whisper-large-v3-turbo`) 배치 전사. VAD 세그먼트 단위 |
| `azure` | **실시간 스트리밍**(SDK) | `AZURE_SPEECH_KEY` + `AZURE_SPEECH_REGION` | Azure Cognitive Services Speech SDK. `uv sync --extra azure` 필요 |

- **실시간 스트리밍**: 발화 중 partial 자막이 실시간 갱신.
- **utterance-mode**(openrouter/replicate/groq): VAD가 끊은 발화 구간을 전송→확정 자막만 표시(약간의 지연). 저지연이 핵심이면 `local`/`openai`/`elevenlabs`/`google`/`xai` 권장.
- **assemblyai**: 기본 모델(`universal-streaming-english`)은 영어 전용입니다. 한국어 스트리밍은 미확인(미지원 가능성 있음).
- 모든 백엔드는 내부적으로 16kHz mono PCM으로 정규화됩니다(`assemblyai`는 한국어 미확인 — 위 주의사항 참고).

예시:
```toml
engine = "elevenlabs"          # 또는 local/openai/google/xai/openrouter/replicate/assemblyai/deepgram/groq/azure
language = "ko"

[providers.openrouter]
model = "openai/whisper-large-v3-turbo"

[providers.google]
mode = "gemini"                # "gemini"(API 키) 또는 "speech_v2"(서비스계정)
model = "gemini-3.1-flash-live-preview"

# speech_v2(Speech-to-Text v2 스트리밍, chirp_2): `uv sync --extra google` 필요.
# .env에 GOOGLE_APPLICATION_CREDENTIALS(서비스계정 JSON 경로) + GOOGLE_CLOUD_PROJECT.
# mode       = "speech_v2"
# model      = "chirp_2"        # chirp은 지역 엔드포인트 필수("global" 불가)
# location   = "us-central1"    # 모델을 제공하는 리전(엔드포인트가 이 리전과 일치)
# project_id = "my-gcp-project" # 미설정 시 env GOOGLE_CLOUD_PROJECT 사용
```

### 로컬 STT: GPU/CPU 선택 (`[local]`)

`engine = "local"`일 때 디바이스/정밀도를 `config.toml [local]`로 제어합니다.

```toml
[local]
model_size   = "small"   # tiny | base | small | medium | large-v3 …
device       = "auto"    # auto | cpu | cuda
compute_type = ""        # "" = 디바이스별 기본값(cuda→float16, cpu→int8)
```

| 키 | 값 | 동작 |
|---|---|---|
| `device` | `auto`(기본) | NVIDIA CUDA를 탐지하면 GPU, 없으면 자동으로 CPU 폴백 |
| | `cuda` | GPU 강제(런타임 미탐지여도 시도). Windows/Linux NVIDIA 전용 |
| | `cpu` | CPU 강제(모든 플랫폼) |
| `compute_type` | `""`(기본) | 디바이스별 기본값: GPU `float16`, CPU `int8` |
| | `float16` / `int8_float16` / `int8` … | 직접 지정(미래 값 `bfloat16` 등 허용) |

**Windows/Linux GPU 설정**

1. NVIDIA 그래픽 드라이버 최신화(별도 CUDA Toolkit 설치 불필요 — 런타임은 휠로 제공).
2. `uv sync --extra local --extra gpu` — `nvidia-cublas-cu12`/`nvidia-cudnn-cu12` 런타임 설치(macOS에선 환경 마커로 자동 제외).
3. `config.toml`에서 `device = "auto"`(기본) 또는 `"cuda"`. CLI 진입 시 pip로 설치된 `nvidia-*` DLL 디렉터리를 자동 등록하므로 별도 `PATH` 설정이 불필요합니다.
4. `uv run python -m obs_captions run` 실행 시 GPU가 잡히지 않으면 CPU로 조용히 폴백됩니다(로그 확인).

> macOS(Apple Silicon)는 CUDA 미지원이라 항상 CPU로 동작합니다(`device="auto"`→CPU). Apple GPU(MLX/Metal) 가속은 추후 과제로 분리되어 있습니다.

---

## 오버레이 스타일 커스터마이즈 (글자 크기 · 폰트 · 효과)

두 단계로 자유롭게 꾸밀 수 있습니다.

### 1) 설정 노브 (`config.toml [overlay]`)
서버가 이 값들을 오버레이 페이지에 **CSS 변수**(`:root{ --cap-* }`)로 주입하므로 코드 수정 없이 즉시 반영됩니다.

```toml
[overlay]
font_family   = "Pretendard, 'Noto Sans KR', sans-serif"
font_size     = 48            # px
font_weight   = 700
color         = "#ffffff"     # 확정(committed) 자막 색
partial_color = "#aaaaaa"     # 진행(partial) 자막 색
background    = "rgba(0,0,0,0.35)"   # 자막 박스 배경(투명도 포함)
outline_width = 2             # 글자 외곽선(px)
outline_color = "#000000"
shadow        = "0 2px 6px rgba(0,0,0,0.6)"  # text-shadow
position      = "bottom"      # top | middle | bottom
align         = "center"      # left | center | right
max_lines     = 3            # 동시에 보일 줄 수
line_height   = 1.3
padding       = 24           # px
letter_spacing = 0           # px
fade_ms       = 200          # 새 자막 fade-in 시간
uppercase     = false
```

| 키 | 효과 |
|---|---|
| `font_family` / `font_size` / `font_weight` | 폰트 종류·크기·굵기 |
| `color` / `partial_color` | 확정/진행 자막 색 분리 |
| `background` | 자막 박스 배경(투명 가능) |
| `outline_*` / `shadow` | 가독성용 외곽선·그림자 |
| `position` / `align` / `padding` | 화면 위치·정렬·여백 |
| `max_lines` / `line_height` | 줄 수·줄간격 |
| `fade_ms` / `uppercase` / `letter_spacing` | 애니메이션·대문자·자간 |

웹폰트는 `font_family`에 설치된 폰트명을 적거나, 커스텀 CSS(`@font-face`)로 불러옵니다.

### 2) 완전 커스텀 CSS (`custom.css`)
노브로 부족하면 **직접 CSS**를 작성합니다. `[overlay] custom_css = "경로"`로 CSS 파일을 지정하면(예: 작업 폴더의 `custom.css`) 기본 스타일 **다음에 로드**되어 무엇이든 덮어쓸 수 있습니다. (오버레이 정적 에셋 자체는 패키지 안 `src/obs_captions/web/overlay/`로 이동했으므로, 커스텀 CSS는 패키지가 아니라 `custom_css` 경로로 둡니다.)

```css
/* custom.css — [overlay] custom_css = "custom.css" 로 지정 */
@import url('https://fonts.googleapis.com/css2?family=Black+Han+Sans&display=swap');

.caption        { font-family: 'Black Han Sans', sans-serif; }
.caption .committed { color: #00e5ff; }
.caption .partial   { color: #80deea; opacity: .7; }
.caption-box    { background: linear-gradient(transparent, rgba(0,0,0,.6)); }
```

자막 DOM 구조: `.caption-box > .caption > (.committed, .partial)` — 이 클래스들을 타깃하면 됩니다.

---

## OBS 연동

### 경로 A — Browser Source (권장: 스타일 자유·저지연)
1. `uv run python -m obs_captions run` (또는 `serve`)로 서버 실행
2. OBS → Sources → **Browser** 추가
3. URL: `http://127.0.0.1:8765/overlay.html`, 크기: 캔버스에 맞게(예: 1920×1080)
4. 배경은 자동 투명 — 영상 위에 자막만 표시됩니다

### 경로 B — obs-websocket → 네이티브 Text 소스 (브라우저 없음)
1. OBS → Tools → **WebSocket Server Settings**에서 서버 활성화(포트/비밀번호 확인)
2. `config.toml [obs]`에 host/port/source_name 지정, `.env`에 `OBS_WS_PASSWORD`

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

3. `uv run python -m obs_captions run --sink obs` 실행 → 지정한 Text 소스가 실시간 갱신
4. 폰트/색은 OBS Text 소스 속성에서 조정(경로 B는 텍스트 소스 옵션 수준의 스타일)
5. 두 경로 동시 사용: `--sink both`

---

## CLI

| 명령 | 설명 |
|---|---|
| `list-devices` | 입력 오디오 장치 목록(인덱스/이름/채널) |
| `list-loopback-devices` | WASAPI 루프백(시스템 사운드) 장치 목록 — Windows 전용(`--extra loopback`) |
| `config` | 현재 설정 출력(API 키 마스킹) |
| `serve [--demo]` | 오버레이 서버만 실행(`--demo`는 가짜 자막) |
| `run [--sink browser\|obs\|both]` | 마이크 → STT → 자막 전체 파이프라인 실행 |
| `check-engine ENGINE [--wav PATH] [--seconds N] [--language CODE]` | 엔진 연결·API 키 검증 + 선택적 WAV 스트리밍 스모크 테스트 |

## 스모크 테스트 (check-engine)

API 키를 설정한 뒤 `check-engine` 명령으로 각 provider가 정상 작동하는지 빠르게 확인합니다.

```bash
# .env에 키 입력
cp .env.example .env
# DEEPGRAM_API_KEY=dg_xxxx 등 입력

# 연결만 확인 (WAV 없음 — 키/region 검증 + start/stop)
uv run obs-captions check-engine deepgram

# WAV 파일을 스트리밍해 실제 전사 결과 확인
uv run obs-captions check-engine deepgram --wav sample.wav

# AssemblyAI
uv run obs-captions check-engine assemblyai --wav sample.wav

# Groq
uv run obs-captions check-engine groq --wav sample.wav

# Azure (AZURE_SPEECH_KEY + AZURE_SPEECH_REGION 필요)
uv run obs-captions check-engine azure --wav sample.wav

# 언어·대기시간 조정
uv run obs-captions check-engine deepgram --wav sample.wav --language en --seconds 15
```

`[partial]` / `[final]` 줄이 출력되면 정상. 키 미설정 시 명확한 오류 메시지와 함께 비정상 종료합니다.

## 개발 / 테스트

```bash
uv run pytest -q            # 단위 테스트(빠름)
uv run pytest -q -m slow    # 모델 다운로드 포함 통합 테스트(한국어 샘플 전사)
uv run ruff check .         # 린트
uv run ruff format .        # 포맷
```

## Windows 배포 (PyInstaller)

OBS를 쓰는 일반 사용자에게 Python/uv 설치 없이 **단일 폴더 실행파일**로 배포합니다. 빌드는 **Windows에서** 수행해야 합니다(macOS/Linux에서 만든 번들은 해당 OS용이라 .exe가 아님).

```powershell
# Windows, 레포 루트에서 (PowerShell)
.\scripts\build_windows.ps1
# 또는 (크로스플랫폼 파이썬 래퍼)
python scripts\build_windows.py
```

- **출력(onedir)**: `dist\obs-captions\obs-captions.exe` (+ `_internal\` 의존성). onefile 대신 **onedir**를 쓰는 이유는 CUDA/cuDNN DLL 같은 큰 네이티브 의존성에서 훨씬 안정적이고 시작이 빠르기 때문입니다.
- **CPU 기본 / GPU 옵트인**: 기본 번들은 CPU 전용(작고 NVIDIA 의존성 없음). GPU 가속(NVIDIA)이 필요하면 `python scripts\build_windows.py --gpu`(= `uv sync --extra gpu`)로 빌드하고, `obs_captions.spec`의 **GPU 블록 주석을 해제**해 `nvidia-*` DLL을 번들에 포함시킵니다.
- **오버레이 에셋 경로**: 정적 오버레이(`overlay.{html,css,js}`)는 패키지 안(`src/obs_captions/web/overlay/`)에 들어 있어 pip 설치·번들 모두에 함께 실려 갑니다. 경로 해석은 `obs_captions/packaging.py`의 `resolve_web_dir()`가 담당합니다(개발/설치: `__file__` 기준, 프리즈: `sys._MEIPASS/obs_captions/web`).
- **첫 실행 모델 다운로드 vs 오프라인 사전 번들**: 로컬 엔진은 기본적으로 첫 실행 시 HuggingFace에서 Whisper 모델을 내려받습니다(인터넷 필요). 완전 오프라인 배포는 모델을 미리 받아 `obs_captions.spec`의 `datas`에 추가하고 `[local] model`을 그 경로로 지정합니다(스펙 하단 주석 예시 참고).
- **선택 extra**: 시스템 사운드(WASAPI 루프백) 자막은 `--extra loopback`(`pyaudiowpatch`, Windows 휠만), GPU 가속은 `--extra gpu`. 빌드 스크립트는 `local`+`loopback`을 기본 동기화합니다.
- **스모크 테스트**: 빌드 스크립트가 마지막에 `obs-captions.exe list-devices`를 실행해 번들이 최소한 오디오 장치를 열거하는지 확인합니다.

> 참고: 이 레포의 macOS/Linux 호스트에서는 실제 .exe 빌드·실행을 검증할 수 없습니다. 경로 해석 로직(`resolve_web_dir`)과 휠 에셋 포함은 단위 테스트/`uv build`로 검증되며, PyInstaller 번들 동작 자체는 Windows 빌드에서 확인해야 합니다.

## 벤치마크 & 비교

경로 A vs 경로 B 정량 측정(지연/CPU/RSS) + 정성 비교 매트릭스 + 추천:

→ **[COMPARISON.md](./COMPARISON.md)** 참조

요약:
- **Path A** emit→WS-receive p50 = **0.14 ms** (실측, n=200)
- **Path B** 로컬 파이프라인 p50 = **135 ms** (디바운스 120 ms 포함, 실측) + obs-websocket 왕복(~100 ms 이상, 라이브 OBS 필요)

벤치마크 재실행:
```bash
uv sync --extra bench
uv run python scripts/benchmark.py --n 200
```

---

---

## 라이브 캡션 텍스트 변환 기능

세 가지 기능은 모두 **기본값 OFF** — `config.toml`에 해당 섹션이 없으면 기존 동작과 완전히 동일합니다.

### 기능 1 — 텍스트 치환 / 사용자 사전 (Text Replacement)

STT가 잘못 인식하는 브랜드명, 전문 용어 등을 실시간으로 교정합니다. 치환 → 필터 순서로 적용되므로, 치환 결과를 필터가 다시 처리할 수 있습니다.

```toml
[[text.replacements]]
match   = "whisper"    # 찾을 문자열 (기본: 대소문자 무시)
replace = "Whisper"

[[text.replacements]]
match   = "\\bw\\w+"   # regex=true 시 정규식 사용 가능
replace = "WORD"
regex   = true
ignore_case = true
whole_word  = false    # true 시 \b…\b 자동 추가
```

옵션 | 기본값 | 설명
-----|--------|-----
`match` | (필수) | 찾을 문자열 또는 정규식 패턴
`replace` | (필수) | 치환 문자열
`regex` | `false` | `true` 시 `match`를 정규식으로 해석 (잘못된 패턴은 설정 로드 시 즉시 오류)
`ignore_case` | `true` | 대소문자 무시
`whole_word` | `false` | 단어 경계(`\b`) 적용

### 기능 2 — 단어 필터 / 비속어 차단 (Word Filter)

지정한 단어를 마스크로 교체하거나 제거합니다 (전체 단어, 대소문자 무시).

```toml
[text]
filter_words = ["badword", "슬랭"]
filter_mode  = "mask"    # "mask" | "remove"
filter_mask  = "***"
```

옵션 | 기본값 | 설명
-----|--------|-----
`filter_words` | `[]` | 필터링할 단어 목록
`filter_mode` | `"mask"` | `"mask"`: 마스크 문자열로 교체 / `"remove"`: 단어 삭제 + 공백 정규화
`filter_mask` | `"***"` | `filter_mode="mask"` 시 사용할 문자열

### 기능 3 — 트랜스크립트 내보내기 (Transcript Export)

최종 확정된 자막을 실시간으로 파일에 저장합니다. 스트리밍 중에도 플러시되므로 도중에 종료되어도 기록이 남습니다.

```toml
[export]
enabled = true
path    = "captions.srt"   # 출력 파일 경로
format  = "srt"            # "txt" | "srt" | "vtt"
```

포맷 | 설명
-----|-----
`txt` | 확정 자막 한 줄씩 기록
`srt` | SubRip 형식 (타임스탬프 포함)
`vtt` | WebVTT 형식 (WEBVTT 헤더 자동 작성, 타임스탬프 포함)

타임스탬프는 STT 백엔드가 `start_ms`/`end_ms`를 제공하면 그 값을 사용하고, 없으면 세션 시작부터의 경과 시간으로 대체합니다.

### 기능 4 — 환각 억제 (Hallucination Suppression)

Whisper는 무음 구간에서 "thank you for watching"처럼 존재하지 않는 문구를 출력하는 경우가 있습니다. 이 기능은 해당 자막을 캡션 상태로 전달하기 전에 차단합니다.

```toml
[text]
suppress_blank = true          # (기본 ON) 공백/빈 자막 자동 차단 — 항상 켜두는 것이 안전
suppress_regex = [             # re.fullmatch, 대소문자 무시. 잘못된 정규식은 로드 시 오류
  "\\[.*\\]",                  # [Music], [Applause] 등 Whisper 태그
  "thank you.*",
]
suppress_exact = [             # 대소문자 무시, 앞뒤 공백 제거 후 전체 일치
  "please like and subscribe",
]
```

키 | 기본값 | 설명
---|--------|-----
`suppress_blank` | `true` | `true`: 빈 자막(공백 포함) 차단
`suppress_regex` | `[]` | 정규식 패턴 목록 (전체 일치, 대소문자 무시). 잘못된 패턴 → 로드 시 ValueError
`suppress_exact` | `[]` | 정확한 문자열 목록 (대소문자 무시, strip 후 비교)

### 기능 5 — 줄당 글자 수 제한 (Per-Line Character Wrap)

자막 한 줄이 너무 길 경우 강제로 줄 바꿈합니다. 한국어 한글 음절(1코드포인트)에도 정확하게 동작합니다.

```toml
[overlay]
max_chars_per_line = 30   # 0 = 비활성 (기본)
```

- `max_chars_per_line = 0` (기본): 줄 바꿈 없음 — 기존 동작 유지
- 양수 값: 각 자막 줄을 해당 코드포인트 수로 강제 분할
- `max_lines`와의 관계: `max_lines`는 확정 자막 **기록** 수를 제한하고, `max_chars_per_line`은 **표시** 단계에서만 동작합니다. 한 줄이 3개 표시 줄로 나뉘어도 `max_lines` 카운트에는 1로 기록됩니다.
- Path A(Browser Source WS)·Path B(OBS Text 소스) 양쪽 모두 동일한 순수 함수(`wrap_text`)로 처리됩니다.

---

## 라이선스 / 기여
내부 프로젝트. STT provider 키는 `.env`로만 주입하며 절대 커밋하지 않습니다.
