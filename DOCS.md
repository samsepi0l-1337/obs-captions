# obs-captions 기능 문서

방송인 마이크(또는 데스크톱 오디오)를 실시간 STT로 인식해 OBS 화면에 자막을 띄우는 시스템.  
Python 3.12 / uv. 플랫폼 1급: Windows 10/11 (CUDA), macOS Apple Silicon (CPU).

---

## 목차

1. [아키텍처 개요](#1-아키텍처-개요)
2. [설치](#2-설치)
3. [CLI 명령어](#3-cli-명령어)
4. [설정 레퍼런스 (config.toml)](#4-설정-레퍼런스-configtoml)
5. [STT 백엔드 12종](#5-stt-백엔드-12종)
6. [오디오 캡처](#6-오디오-캡처)
7. [자막 출력 경로 (Path A / Path B)](#7-자막-출력-경로-path-a--path-b)
8. [텍스트 처리 파이프라인](#8-텍스트-처리-파이프라인)
9. [OBS 핫키](#9-obs-핫키)
10. [트랜스크립트 내보내기](#10-트랜스크립트-내보내기)
11. [Windows 빌드 / 배포](#11-windows-빌드--배포)
12. [환경 변수 (.env)](#12-환경-변수-env)
13. [모듈 구조 맵](#13-모듈-구조-맵)
14. [설정 UX (간단/고급 · 모델 추천 · 키 검증)](#14-설정-ux-간단고급--모델-추천--키-검증)

---

## 1. 아키텍처 개요

```
마이크/루프백
    │
    ▼
[audio/capture.py | audio/loopback.py]  ← sounddevice | PyAudioWPatch
    │ PCM16 청크
    ▼
[vad.py] SileroVAD (ONNX)              ← 발화 구간 감지
    │ 발화 세그먼트
    ▼
[stt/*]  STT 백엔드 12종               ← 로컬 또는 클라우드
    │ on_partial / on_final 콜백
    ▼
[text.py]  변환 파이프라인             ← 치환 → 필터 → 억제
    │
    ├──▶ [pipeline.py] CaptionState    ← partial/final 상태머신
    │         │
    │    ┌────┴──────────────────┐
    │    ▼                       ▼
    │ [server/] Path A        [obs_sink.py] Path B
    │ FastAPI + WebSocket     obs-websocket v5
    │ overlay.html            SetInputSettings
    │
    ├──▶ [obs_hotkey.py]  OBS 핫키 리스너 (pause/clear)
    └──▶ [export_sink.py] SRT/VTT/TXT 내보내기
```

**Path A** (Browser Source): 저지연 (p50 = 0.14 ms), CSS 스타일 자유도 높음  
**Path B** (obs-websocket): OBS 네이티브 Text 소스, 디바운스 120 ms (p50 = 135 ms)

---

## 2. 설치

```bash
# 기본 (로컬 Whisper)
uv pip install -e ".[local]"

# CUDA 가속 (Windows/Linux)
uv pip install -e ".[local,gpu]"

# 클라우드 provider 별 extra
uv pip install -e ".[openai]"
uv pip install -e ".[elevenlabs]"
uv pip install -e ".[google]"          # Google Speech-to-Text v2
uv pip install -e ".[azure]"
uv pip install -e ".[obs]"             # Path B (obs-websocket)
uv pip install -e ".[loopback]"        # Windows WASAPI 루프백

# 전체
uv pip install -e ".[local,gpu,openai,elevenlabs,google,obs,loopback,azure]"
```

설정 파일 준비:

```bash
cp config.example.toml config.toml
cp .env.example .env
# .env에 사용할 provider의 API 키 입력
```

---

## 3. CLI 명령어

진입점: `obs-captions` (또는 `uv run obs-captions`)

### `list-devices`

```bash
obs-captions list-devices
```

마이크 입력 장치 목록 출력 (인덱스, 이름, 최대 채널 수).  
`config.toml`의 `[audio] device` 값으로 쓸 이름/인덱스 확인용.

### `list-loopback-devices`

```bash
obs-captions list-loopback-devices
```

Windows WASAPI 루프백 장치 목록. `--extra loopback` 설치 필요.

### `config`

```bash
obs-captions config [--config PATH]
```

현재 적용 설정을 출력. API 키는 `***`으로 마스킹.

### `serve`

```bash
obs-captions serve [--config PATH] [--demo]
```

오버레이 서버만 실행 (STT 없음).  
`--demo`: 가짜 한국어 자막으로 UI 테스트.  
OBS에서 Browser Source URL: `http://127.0.0.1:8765/overlay`

### `run`

```bash
obs-captions run [--config PATH] [--sink browser|obs|both]
```

전체 파이프라인 실행.

| `--sink`         | 동작                             |
| ---------------- | -------------------------------- |
| `browser` (기본) | Path A — 웹서버 + HTML 오버레이  |
| `obs`            | Path B — obs-websocket Text 소스 |
| `both`           | 양쪽 동시                        |

### `check-engine`

```bash
obs-captions check-engine ENGINE [--wav PATH] [--seconds N] [--language CODE] [--config PATH]
```

STT 엔진 연결·인증 스모크 테스트.

```bash
# 연결만 확인 (10초 대기)
obs-captions check-engine openai

# WAV 파일로 실제 전사 결과 확인
obs-captions check-engine local --wav sample.wav

# 언어 오버라이드
obs-captions check-engine deepgram --wav sample.wav --language en
```

`ENGINE`: `local` | `openai` | `elevenlabs` | `google` | `xai` | `openrouter` | `replicate` | `assemblyai` | `deepgram` | `groq` | `azure`

### `recommend-model`

```bash
obs-captions recommend-model
```

하드웨어(CUDA/VRAM/RAM/CPU)를 감지해 적합한 로컬 모델을 JSON으로 출력. GUI/플러그인의 "추천값 적용" 버튼과 동일한 판정 로직.

```json
{"recommended": "large-v3-turbo",
 "hardware": {"cuda_available": true, "vram_mb": 12288, "ram_mb": 32768, "cpu_count": 16}}
```

추천 규칙 ([`stt/hardware.py`](#13-모듈-구조-맵)):

| 조건                             | 추천 모델          |
| -------------------------------- | ------------------ |
| VRAM ≥ 8000 MB                   | `large-v3-turbo`   |
| VRAM 4000–7999 MB / VRAM 미상+CUDA | `large-v3`         |
| CUDA 없음 · RAM ≥ 8000 · CPU ≥ 8 | `medium`           |
| RAM ≥ 4000                       | `small`            |
| 그 외                            | `base`             |

감지는 절대 예외를 던지지 않으며, 값을 못 구하면 `null`로 두고 보수적으로 판정한다.

### `validate-key`

```bash
obs-captions validate-key --engine ENGINE
```

선택한 엔진의 API 키 유효성을 검사해 `{ok, mode, message}` JSON을 출력한다. 성공 시 exit 0, 실패 시 exit 1.  
키는 **엔진별 환경 변수에서만 읽고 CLI 인자로 받지 않는다** — 명령줄·출력·로그에 노출되지 않는다.

```bash
DEEPGRAM_API_KEY=... obs-captions validate-key --engine deepgram
# {"ok": true, "mode": "network", "message": "..."}
```

| `mode`        | 의미                                                                                     |
| ------------- | ---------------------------------------------------------------------------------------- |
| `network`     | 실제 인증 요청을 보내 성공/실패 확인. 지원: `openai` `deepgram` `elevenlabs` `groq` `openrouter` `xai` `replicate` `google` |
| `format`      | 형식만 검사 (키 비어 있음, `azure` region 누락 등)                                        |
| `unsupported` | 자동 검증 미지원 — 실행 시 확인. `assemblyai`(스트리밍 전용), `azure`(SDK 인증)          |

`assemblyai`는 REST 인증 실패면(surface)이 확인되지 않아 미지원, `azure`는 SDK(subscription key + region) 인증이라 단순 `GET`으로 검증 불가하다(사유는 `stt/validate.py` 주석에 문서화).

---

## 4. 설정 레퍼런스 (config.toml)

전체 예시: `config.example.toml`

### 전역

```toml
engine = "local"    # STT 백엔드 선택
language = "ko"     # ISO 639-1 언어 코드
```

### `[audio]`

```toml
source = "mic"       # mic | loopback (Windows WASAPI)
device = ""          # 빈값=기본 장치 | 장치 이름 | 인덱스
samplerate = 16000
channels = 1
```

### `[server]`

```toml
host = "127.0.0.1"
port = 8765
```

Browser Source URL: `http://<host>:<port>/overlay`

### `[overlay]` — 자막 스타일

| 키                   | 기본값                                     | 설명                                                 |
| -------------------- | ------------------------------------------ | ---------------------------------------------------- |
| `font_family`        | `"Pretendard, 'Noto Sans KR', sans-serif"` | 폰트 (CSS font-family)                               |
| `font_size`          | `48`                                       | px                                                   |
| `font_weight`        | `700`                                      | 100~900                                              |
| `color`              | `"#ffffff"`                                | 확정 자막 색                                         |
| `partial_color`      | `"#aaaaaa"`                                | 진행 중 자막 색                                      |
| `background`         | `"rgba(0,0,0,0.35)"`                       | 박스 배경 (투명도 가능)                              |
| `outline_width`      | `2`                                        | 텍스트 외곽선 px                                     |
| `outline_color`      | `"#000000"`                                | 외곽선 색                                            |
| `shadow`             | `"0 2px 6px rgba(0,0,0,0.6)"`              | CSS text-shadow                                      |
| `position`           | `"bottom"`                                 | `top` \| `middle` \| `bottom`                        |
| `align`              | `"center"`                                 | `left` \| `center` \| `right`                        |
| `max_lines`          | `3`                                        | 화면에 동시 표시할 최대 줄 수                        |
| `line_height`        | `1.3`                                      | CSS line-height                                      |
| `padding`            | `24`                                       | px                                                   |
| `letter_spacing`     | `0`                                        | CSS letter-spacing (em)                              |
| `fade_ms`            | `200`                                      | 자막 등장 fade-in 시간                               |
| `uppercase`          | `false`                                    | 대문자 강제 변환                                     |
| `custom_css`         | `""`                                       | 외부 CSS 파일 경로 (추가 오버라이드)                 |
| `max_chars_per_line` | `0`                                        | `0`=비활성 \| N>0 → N 글자(codepoint) 초과 시 줄바꿈 |

### `[local]` — faster-whisper

```toml
model_size = "small"          # tiny | base | small | medium | large-v3 | large-v3-turbo | distil-large-v3
device = "auto"               # auto | cpu | cuda
compute_type = ""             # 빈값=auto | float16 | int8_float16 | int8
cpu_threads = 1
partial_interval_ms = 500     # 부분 자막 갱신 주기
max_buffer_s = 30.0           # rolling window 최대 길이
vad_threshold = 0.5
min_silence_ms = 500
initial_prompt = ""           # 인식 힌트 문장 (방송 주제·고유명사) — faster-whisper에 그대로 전달
hotwords = ""                 # 강조 단어 (특정 발음/용어 인식 개선)
```

`device = "auto"`: CUDA 탐지 후 가능하면 CUDA, 없으면 CPU graceful fallback.  
macOS는 항상 `cpu` + `int8` (결정론적).  
모델 추천은 `recommend-model` CLI([3번](#recommend-model)) 또는 GUI "추천값 적용" 버튼으로 자동 판정.

**STT 인식 힌트** (`initial_prompt` / `hotwords`): 방송 주제·고유명사·자주 쓰는 용어를 넣으면 특정 발음/전문용어 인식이 개선된다. 값이 있을 때만 faster-whisper의 `transcribe()`에 전달된다 (빈값=미전달). `initial_prompt`는 문맥 힌트 문장, `hotwords`는 강조 단어 목록.

### `[providers.<engine>]` — 클라우드 옵션

```toml
[providers.openrouter]
model = "openai/whisper-large-v3-turbo"

[providers.replicate]
model = "openai/whisper"

[providers.openai]
# gpt-realtime-whisper | gpt-realtime-translate | gpt-realtime-2.1
model = "gpt-realtime-whisper"
# delay = "low"              # whisper: minimal|low|medium|high|xhigh
# target_language = "en"     # translate: output language

[providers.elevenlabs]
model = "scribe_v2_realtime"

[providers.xai]
model = "grok-transcribe"

[providers.google]
mode = "gemini"                        # gemini | speech_v2
model = "gemini-3.1-flash-live-preview"
# speech_v2 전용:
# location = "us-central1"
# project_id = "my-gcp-project"

[providers.assemblyai]
model = "universal-streaming-english"

[providers.deepgram]
model = "nova-3"

[providers.groq]
model = "whisper-large-v3-turbo"

[providers.azure]
region = "eastus"
```

### `[obs]` — Path B

```toml
host = "localhost"
port = 4455
source_name = "LiveCaptions"    # OBS Text 소스 이름 (없으면 자동 생성)
password = ""                   # OBS_WS_PASSWORD .env 권장
```

### `[obs.hotkey]`

```toml
enabled = false
pause_input = "_CaptionPause"   # OBS Audio Input 이름 (센티넬)
clear_input  = "_CaptionClear"  # OBS Audio Input 이름 (센티넬)
```

### `[text]` — 텍스트 처리

```toml
# Feature 2: 단어 필터
filter_words = ["badword", "금지어"]
filter_mode = "mask"            # mask | remove
filter_mask = "***"

# Feature 4: 환각 억제
suppress_blank = true           # 공백 자막 차단 (기본 ON)
suppress_regex = ["thank you.*", "구독과 좋아요.*"]   # re.fullmatch
suppress_exact = ["please like and subscribe"]

# Feature 1: 텍스트 치환 (복수 규칙)
[[text.replacements]]
match = "whisper"
replace = "Whisper"
regex = false
ignore_case = true
whole_word = false
```

### `[export]` — 트랜스크립트 내보내기

```toml
enabled = false
path = "captions.srt"
format = "srt"                  # txt | srt | vtt
```

---

## 5. STT 백엔드 12종

### 5.1 로컬 — `local`

| 항목          | 값                                     |
| ------------- | -------------------------------------- |
| 라이브러리    | faster-whisper (CTranslate2)           |
| 스트리밍 방식 | LocalAgreement-2 rolling window 재전사 |
| 부분 자막     | O (partial_interval_ms 주기)           |
| CUDA          | O (자동 탐지, Windows/Linux)           |
| extra         | `local` (+ `gpu` for CUDA)             |
| 환경 변수     | 없음                                   |

**LocalAgreement-2**: 같은 위치의 토큰이 2번 연속 동일하면 확정(final). rolling window가 max_buffer_s를 넘으면 앞부분 트림 + rebase.

**모델 크기**: `tiny`~`large-v3` 외 `large-v3-turbo`, `distil-large-v3` 지원. 하드웨어 적합 모델은 `recommend-model`([3번](#recommend-model))로 추천받는다.  
**인식 힌트**: `[local] initial_prompt`(문맥 힌트 문장), `[local] hotwords`(강조 단어)를 채우면 방송 주제·고유명사·전문용어 인식이 개선된다 ([4번 `[local]`](#local--faster-whisper) 참조).

---

### 5.2 실시간 스트리밍 백엔드 (StreamingBackend)

공통 구현: `stt/streaming.py`

- WebSocket 연결 → 오디오 base64 인코딩 전송 → partial/final 이벤트 파싱
- 재연결: 지수백오프 (base=0.5s, max=8s, max_reconnects=5)
- `is_delta=True` 이벤트: 내부에서 축적 후 완성 텍스트 전달

| 엔진                 | extra        | 환경 변수                                  | 부분 자막 | 오디오                                                        |
| -------------------- | ------------ | ------------------------------------------ | --------- | ------------------------------------------------------------- |
| `openai`             | `openai`     | `OPENAI_API_KEY`                           | O         | 24kHz PCM16; model=`gpt-realtime-whisper`\|`translate`\|`2.1` |
| `elevenlabs`         | `elevenlabs` | `ELEVENLABS_API_KEY`                       | O         | 16kHz PCM16                                                   |
| `google` (gemini)    | —            | `GOOGLE_API_KEY`                           | O         | 16kHz PCM16                                                   |
| `google` (speech_v2) | `google`     | GCP 서비스 계정 JSON                       | O         | 16kHz PCM16                                                   |
| `xai`                | —            | `XAI_API_KEY`                              | O         | 16kHz PCM16                                                   |
| `assemblyai`         | —            | `ASSEMBLYAI_API_KEY`                       | O         | 16kHz PCM16                                                   |
| `deepgram`           | —            | `DEEPGRAM_API_KEY`                         | O         | 16kHz PCM16                                                   |
| `azure`              | `azure`      | `AZURE_SPEECH_KEY` + `AZURE_SPEECH_REGION` | O         | SDK 내부                                                      |

**Google Speech-to-Text v2 추가 설정**:

```toml
[providers.google]
mode = "speech_v2"
model = "chirp_2"
location = "us-central1"
project_id = "my-gcp-project"
```

서비스 계정 JSON 경로: `GOOGLE_APPLICATION_CREDENTIALS` 환경 변수.

---

### 5.3 배치(발화 단위) 백엔드 (UtteranceBackend)

공통 구현: `stt/utterance.py`  
VAD가 발화 종료를 감지하면 flush() 호출 → 버퍼 전체를 HTTP POST → 최종 자막만 출력 (부분 자막 없음).

| 엔진         | extra | 환경 변수             | 특이사항                          |
| ------------ | ----- | --------------------- | --------------------------------- |
| `openrouter` | —     | `OPENROUTER_API_KEY`  | OpenAI-호환 엔드포인트            |
| `replicate`  | —     | `REPLICATE_API_TOKEN` | 콜드스타트 지연 가능              |
| `groq`       | —     | `GROQ_API_KEY`        | whisper-large-v3-turbo, 빠른 응답 |

---

## 6. 오디오 캡처

### 마이크 (`source = "mic"`)

```toml
[audio]
source = "mic"
device = ""          # 빈값 = OS 기본 마이크
samplerate = 16000
channels = 1
```

장치 이름 확인:

```bash
obs-captions list-devices
```

### Windows WASAPI 루프백 (`source = "loopback"`)

데스크톱 오디오(게임 소리, 브라우저 등) 캡처. Windows 전용.

```bash
uv pip install -e ".[loopback]"
```

```toml
[audio]
source = "loopback"
device = ""          # 빈값 = 기본 출력 장치의 루프백
```

장치 이름 확인:

```bash
obs-captions list-loopback-devices
```

---

## 7. 자막 출력 경로 (Path A / Path B)

### Path A — Browser Source (권장)

**설정**: `obs-captions run --sink browser`

1. FastAPI 서버 (`host:port`) 실행
2. OBS에서 **Browser Source** 추가 → URL: `http://127.0.0.1:8765/overlay`
3. 서버가 WebSocket으로 자막 실시간 푸시

**특징**:

- 스타일: `[overlay]` 설정이 CSS 변수로 자동 주입 (재시작 없이 반영)
- 지연: p50 = 0.14 ms
- 커스텀 CSS: `custom_css = "my_style.css"` 로 완전 오버라이드 가능

### Path B — obs-websocket Text 소스

**설정**: `obs-captions run --sink obs`

사전 준비:

1. OBS → 도구 → obs-websocket 설정 → 활성화
2. 비밀번호를 `.env`의 `OBS_WS_PASSWORD`에 저장
3. `[obs] source_name`에 Text 소스 이름 지정 (없으면 자동 생성)

```toml
[obs]
host = "localhost"
port = 4455
source_name = "LiveCaptions"
```

**특징**:

- 브라우저 소스 불필요, OBS 네이티브 Text 렌더링
- 디바운스 120 ms (지연 p50 = 135 ms)
- 연결 끊김 시 자동 재연결 (지수백오프 4회)

---

## 8. 텍스트 처리 파이프라인

STT 출력 → `transform_text()` → `should_suppress()` → `CaptionState`

처리 순서: **치환 → 필터 → (억제 판정)**

### Feature 1 — 텍스트 치환 (`text.py: apply_replacements`)

```toml
[[text.replacements]]
match = "gpt"
replace = "GPT"
regex = false
ignore_case = true
whole_word = true

[[text.replacements]]
match = "(\d+)원"
replace = "$1 원"
regex = true
```

| 옵션          | 설명                                            |
| ------------- | ----------------------------------------------- |
| `match`       | 검색 문자열 또는 정규식 패턴                    |
| `replace`     | 치환 문자열 (정규식 백레퍼런스 `\1`, `$1` 지원) |
| `regex`       | `true`: 정규식 모드                             |
| `ignore_case` | 대소문자 무시                                   |
| `whole_word`  | 단어 경계 매칭                                  |

**잘못 들리는 단어 교정(치환 에디터)**: GUI/플러그인에서 `text.replacements`를 행 추가/삭제식 UI로 편집한다 — "들리는 말 → 교정" 규칙을 TOML 직접 수정 없이 관리. 저장 시 위 `[[text.replacements]]` 배열로 직렬화된다.

### Feature 2 — 단어 필터 (`text.py: apply_filter`)

```toml
[text]
filter_words = ["욕설", "금지어"]
filter_mode = "mask"     # mask | remove
filter_mask = "***"
```

- `mask`: 단어를 `filter_mask`로 교체
- `remove`: 단어 제거 + 주변 공백 정규화
- 대소문자 무시 whole-word 매칭

### Feature 3 — 트랜스크립트 내보내기 (`export_sink.py`)

별도 섹션 [10번](#10-트랜스크립트-내보내기) 참조.

### Feature 4 — 환각 억제 (`text.py: should_suppress`)

STT 모델이 반복·무의미하게 출력하는 텍스트를 차단.

```toml
[text]
suppress_blank = true                          # 공백/빈 자막 차단 (기본 ON)
suppress_regex = ["thank you.*", "\\[음악\\]"] # re.fullmatch, 대소문자 무시
suppress_exact = ["please subscribe"]          # strip 후 exact match
```

`should_suppress()` 반환 `True` → `on_partial` / `on_final` 이벤트 발행하지 않음.

### Feature 5 — 줄바꿈 (`text.py: wrap_text`, `obs_display.py`)

```toml
[overlay]
max_chars_per_line = 20    # 0=비활성, N>0 = N codepoint 초과 시 줄바꿈
```

- codepoint 기준 분할 → 한글 음절 정확 처리 (한 글자 = 1 codepoint)
- Path A / Path B 양쪽 공유 순수 함수

---

## 9. OBS 핫키

`[obs.hotkey] enabled = true` 설정 시 활성화.

### 동작 원리

OBS에 **센티넬 Audio Input 소스** 두 개를 생성하고, 해당 소스를 Mute/Unmute하는 OBS 핫키를 등록한다.  
`obs_hotkey.py`가 `InputMuteStateChanged` 이벤트를 구독해 동작을 실행한다.

### 설정 순서

1. OBS → 소스 추가 → **오디오 입력 캡처** 두 개 생성
   - 이름: `_CaptionPause` (일시정지/재개용)
   - 이름: `_CaptionClear` (초기화용)
2. OBS → 설정 → 단축키 → 각 소스의 **음소거** 에 원하는 키 등록
3. `config.toml`:

```toml
[obs.hotkey]
enabled = true
pause_input = "_CaptionPause"
clear_input = "_CaptionClear"
```

### 핫키 동작

| 소스                   | 동작          | 효과                             |
| ---------------------- | ------------- | -------------------------------- |
| `_CaptionPause` Mute   | 자막 일시정지 | 오디오 캡처 중지, 화면 자막 고정 |
| `_CaptionPause` Unmute | 자막 재개     | 오디오 캡처 재시작               |
| `_CaptionClear` Mute   | 자막 초기화   | 화면 자막 즉시 지움, 자동 Unmute |

---

## 10. 트랜스크립트 내보내기

최종 확정 자막(`on_final`)을 파일로 저장.

```toml
[export]
enabled = true
path = "captions.srt"    # 저장 경로
format = "srt"           # txt | srt | vtt
```

### 포맷

**TXT** — 한 줄씩 기록:

```
안녕하세요 오늘 방송에 오신 것을 환영합니다
```

**SRT** — SubRip 타임코드:

```
1
00:00:01,200 --> 00:00:03,800
안녕하세요 오늘 방송에 오신 것을 환영합니다
```

**VTT** — WebVTT:

```
WEBVTT

00:00:01.200 --> 00:00:03.800
안녕하세요 오늘 방송에 오신 것을 환영합니다
```

**타임스탬프**: STT 백엔드가 `start_ms`/`end_ms`를 제공하면 사용, 없으면 세션 시작부터의 wall-clock 경과 시간. 음수·역순 자동 clamp.  
**플러시**: `on_final()` 즉시 flush (비정상 종료 시에도 기록 보존).

---

## 11. Windows 빌드 / 배포

PyInstaller 6.14.1 onedir 방식. Python 설치 불필요. Windows 빌드는 기본으로 `local` + `loopback` + `obs` extras를 포함한다.

```powershell
# PowerShell
.\scripts\build_windows.ps1

# 또는 Python
python scripts/build_windows.py
```

결과물: `dist/obs-captions/obs-captions.exe`  
배포 시 `dist/obs-captions/` 폴더 전체 전달.

릴리스 zip:

```powershell
.\scripts\package_windows_release.ps1
```

결과물: `dist/release/obs-captions-windows-x64.zip`

CI:

- `.github/workflows/ci.yml`: Python 테스트, native IPC 테스트, Windows exe 빌드, release zip 생성.
- `.github/workflows/release.yml`: GitHub Release용 Windows 패키지 워크플로.

네이티브 OBS 플러그인:

- `native-plugin/`에는 row6 경로가 트리에 결선되어 있다: OBS 오디오 필터 → `ipc-bridge` → Python `ipc-sidecar` → `caption-output` → OBS Text 소스.
- Windows DLL은 OBS/libobs SDK 경로(`CMAKE_PREFIX_PATH`, `OBS_STUDIO_DIR`, `OBS_BUILD_DIR`)가 있을 때 `scripts/build_plugin_windows.ps1`로 빌드한다.
- CI는 SDK/secret이 없는 환경에서도 Python·순수 native IPC·exe·zip 검증을 계속하며, libobs SDK가 없으면 플러그인 DLL 산출을 건너뛴다.
- 사용자용 Windows exe 안내는 `docs/WINDOWS.md`, 네이티브 플러그인 빌드/상태는 `native-plugin/README.md` 참조.

에셋 경로 해석 (`packaging.py: resolve_overlay_dir`):

- **dev**: `src/obs_captions/web/overlay/`
- **pip installed**: site-packages 내 패키지 경로
- **frozen (PyInstaller)**: `sys._MEIPASS/obs_captions/web/`

---

## 12. 환경 변수 (.env)

프로젝트 루트 `.env` 파일 (`.env.example` 복사 후 작성).  
**절대 커밋 금지** (`.gitignore` 등록됨).

```dotenv
# OpenAI
OPENAI_API_KEY=sk-...

# ElevenLabs
ELEVENLABS_API_KEY=...

# Google (Gemini Live)
GOOGLE_API_KEY=...

# Google Speech-to-Text v2 (서비스 계정)
GOOGLE_APPLICATION_CREDENTIALS=/path/to/service_account.json

# xAI
XAI_API_KEY=...

# OpenRouter
OPENROUTER_API_KEY=...

# Replicate
REPLICATE_API_TOKEN=r8_...

# AssemblyAI
ASSEMBLYAI_API_KEY=...

# Deepgram
DEEPGRAM_API_KEY=...

# Groq
GROQ_API_KEY=gsk_...

# Azure Speech
AZURE_SPEECH_KEY=...
AZURE_SPEECH_REGION=eastus

# OBS WebSocket (Path B)
OBS_WS_PASSWORD=your_obs_password
```

---

## 13. 모듈 구조 맵

```
src/obs_captions/
├── __main__.py          진입점 (cli.cli 호출)
├── cli.py               Click CLI 명령어 그룹
├── config.py            TOML 파싱 + Pydantic 모델 (AppConfig)
├── pipeline.py          CaptionState 상태머신 (partial/final/clear/subscribe)
├── text.py              텍스트 변환 순수 함수 (Feature 1~2, 4~5)
├── export_sink.py       TranscriptExportSink (Feature 3, TXT/SRT/VTT)
├── obs_sink.py          ObsTextSink — Path B (obs-websocket v5)
├── obs_hotkey.py        ObsHotkeyListener + CaptionController — 핫키
├── obs_display.py       _build_display_text() — wrapping 포함 표시 텍스트 빌더
├── check_engine.py      check-engine CLI 스모크 테스트
├── vad.py               SileroVAD (ONNX) + UtteranceSegmenter
├── packaging.py         resolve_overlay_dir() — dev/pip/frozen 경로 해석
├── platform_dll.py      add_cuda_dll_directories() — Windows CUDA DLL
├── audio/
│   ├── capture.py       MicCapture (sounddevice)
│   ├── devices.py       오디오 장치 열거
│   └── loopback.py      LoopbackCapture (PyAudioWPatch, Windows)
├── stt/
│   ├── base.py          STTBackend ABC + Transcript 타입
│   ├── registry.py      create_backend(engine, config) 팩토리
│   ├── device.py        resolve_device() — CUDA/CPU 판정 순수 함수
│   ├── streaming.py     StreamingBackend ABC (WebSocket 공통)
│   ├── utterance.py     UtteranceBackend ABC (배치 공통)
│   ├── local_whisper.py LocalWhisperBackend (faster-whisper, LocalAgreement-2)
│   ├── openai_realtime.py
│   ├── elevenlabs_realtime.py
│   ├── google.py        Gemini Live / Speech-v2 분기
│   ├── google_speech_v2.py  SpeechAsyncClient + asyncio.Queue gRPC 브릿지
│   ├── xai.py
│   ├── assemblyai.py
│   ├── deepgram.py
│   ├── groq.py
│   ├── azure.py         Azure SDK 기반 (비-WebSocket)
│   ├── openrouter.py
│   ├── replicate.py
│   └── fake.py          테스트/데모용 가짜 백엔드
└── server/
    ├── app.py           FastAPI 앱 (Path A 서버)
    ├── hub.py           WebSocket 브로드캐스터
    └── overlay_style.py CSS 변수 주입 (:root { --cap-* })
```

관련 SP1 모듈:

```
src/obs_captions/
├── settings_types.py    FieldSpec + Tier + ENGINES/LOCAL_MODEL_SIZES
├── settings_fields.py   FIELDS 정의 + 간단/고급 tier 분류 (simple/advanced)
└── stt/
    ├── hardware.py      detect_hardware() + recommend_model() — 모델 추천
    └── validate.py      validate_engine() — API 키 유효성 검증 (network/format/unsupported)
```

---

## 14. 설정 UX (간단/고급 · 모델 추천 · 키 검증)

초보자가 최소 구성만으로 시작하고, 필요할 때만 세부 튜닝을 여는 흐름.

### 간단/고급 설정 분리

각 설정 필드는 `simple`/`advanced` 두 tier로 나뉜다(`settings_fields.py`의 `FieldSpec.tier`). GUI와 OBS 플러그인 속성에 **"고급 설정 표시" 토글**이 있고, 기본은 `simple` 필드만 보인다.

- **간단(simple, 기본 표시)**: 엔진 · 언어 · 로컬 모델 크기 · 선택 엔진의 API 키/모델 · 자막 위치/크기/색(overlay position·font_size·color) · OBS 소스명(obs.source_name) · 내보내기(export enabled·format) · 인식 힌트(initial_prompt).
- **고급(advanced, 토글 시 표시)**: device/compute_type/cpu_threads · VAD/버퍼(partial_interval_ms·max_buffer_s·vad_threshold·min_silence_ms) · 오디오(source·device·samplerate·channels) · 서버(host·port) · 오버레이 세부(font_family·weight·outline·shadow·align·max_lines·… ) · 핫키 · 텍스트 규칙(replacements·filter·suppress) 등.

키 집합은 `simple_field_keys()` / `advanced_field_keys()`로 조회한다.

### 로컬 모델 추천

하드웨어(CUDA/VRAM/RAM/CPU)를 감지해 적합한 로컬 모델을 표시하고 "추천값 적용" 버튼으로 `local.model_size`에 반영한다. 모델 목록에 `large-v3-turbo`, `distil-large-v3`가 추가됐다. CLI는 `recommend-model`([3번](#recommend-model)), 판정 로직은 `stt/hardware.py`.

### API 키 유효성 검증

선택한 엔진에 실제 인증 요청을 보내 키의 성공/실패/미지원을 확인한다.

- **GUI** "키 테스트" 버튼 · **OBS 플러그인** "Test API Key" 버튼 · **CLI** `validate-key --engine <e>`([3번](#validate-key)).
- 키는 **자식 프로세스 env로만 전달**되며 config/로그에 기록하지 않는다.
- network 검증 지원: `openai` `deepgram` `elevenlabs` `groq` `openrouter` `xai` `replicate` `google`. `assemblyai`/`azure`는 형식/미지원(`stt/validate.py` 참조).

### 잘못 들리는 단어 교정

`text.replacements`를 행 추가/삭제식 에디터로 편집("들리는 말 → 교정"). 상세는 [8번 Feature 1](#feature-1--텍스트-치환-textpy-apply_replacements).

---

_최종 갱신: 2026-07-16 — SP1 설정 UX + STT 기능(간단/고급 분리, 모델 추천, 인식 힌트, 치환 에디터, 키 검증) 반영_
