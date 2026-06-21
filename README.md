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

- macOS (Apple Silicon), **Python 3.12** (`uv`로 핀), `ffmpeg`, OBS Studio
- 클라우드 provider 사용 시 해당 API 키(아래 표)

## 빠른 시작

```bash
uv sync                     # 코어 의존성 (로컬 STT는: uv sync --extra local)
cp config.example.toml config.toml
cp .env.example .env        # 클라우드 provider 키 입력(쓸 때만)

uv run python -m obs_captions list-devices   # 입력 장치 목록 + 인덱스
uv run python -m obs_captions config         # 현재 설정 확인(키는 *** 마스킹)
uv run python -m obs_captions serve --demo   # 가짜 자막으로 오버레이 서버만 띄워 보기
uv run python -m obs_captions run            # 마이크 → STT → 자막(경로 A 서버 동시 실행)
```

오버레이 URL: `http://127.0.0.1:8765/overlay.html` (포트는 `[server]`에서 변경)

---

## STT 백엔드 / provider 선택

`config.toml`의 `engine` 값으로 선택하고, 클라우드는 `.env`에 키를 넣습니다. provider별 모델·옵션은 `[providers.<name>]`로 조정합니다.

| engine | 모드 | 키(.env) | 비고 |
|---|---|---|---|
| `local` (기본) | 스트리밍 에뮬(LocalAgreement-2) | — (키 불필요) | `faster-whisper`(CPU int8). `uv sync --extra local` 필요. 오프라인·무료 |
| `openai` | **실시간 스트리밍** | `OPENAI_API_KEY` | Realtime API(`gpt-realtime-whisper`) |
| `elevenlabs` | **실시간 스트리밍**(~150ms) | `ELEVENLABS_API_KEY` | Scribe v2 Realtime |
| `google` | **실시간 스트리밍** | `GOOGLE_APPLICATION_CREDENTIALS` 또는 `GEMINI_API_KEY` | Speech-to-Text v2 `chirp_3`(서비스계정) 또는 Gemini Live(API 키, 세션 15분 제한 자동 재연결) |
| `xai` | **실시간 스트리밍** | `XAI_API_KEY` | Grok `grok-transcribe` (wss) |
| `openrouter` | 배치/근실시간(utterance) | `OPENROUTER_API_KEY` | Whisper(`whisper-large-v3-turbo` 등). 스트리밍 미지원 → VAD 세그먼트 단위 |
| `replicate` | 배치/근실시간(utterance) | `REPLICATE_API_TOKEN` | Whisper / incredibly-fast-whisper. 콜드스타트 지연 있음 |

- **실시간 스트리밍**: 발화 중 partial 자막이 실시간 갱신.
- **utterance-mode**(openrouter/replicate): VAD가 끊은 발화 구간을 전송→확정 자막만 표시(약간의 지연). 저지연이 핵심이면 `local`/`openai`/`elevenlabs`/`google`/`xai` 권장.
- 모든 백엔드는 한국어(`language="ko"`)를 지원하며 내부적으로 16kHz mono PCM으로 정규화됩니다.

예시:
```toml
engine = "elevenlabs"          # 또는 local/openai/google/xai/openrouter/replicate
language = "ko"

[providers.openrouter]
model = "openai/whisper-large-v3-turbo"

[providers.google]
mode = "gemini"                # "gemini"(API 키) 또는 "speech_v2"(서비스계정)
model = "gemini-3.1-flash-live-preview"
```

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
노브로 부족하면 **직접 CSS**를 작성합니다. `web/overlay/custom.css`(또는 `[overlay] custom_css = "경로"`)를 두면 기본 스타일 **다음에 로드**되어 무엇이든 덮어쓸 수 있습니다.

```css
/* web/overlay/custom.css */
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
| `config` | 현재 설정 출력(API 키 마스킹) |
| `serve [--demo]` | 오버레이 서버만 실행(`--demo`는 가짜 자막) |
| `run [--sink browser\|obs\|both]` | 마이크 → STT → 자막 전체 파이프라인 실행 |

## 개발 / 테스트

```bash
uv run pytest -q            # 단위 테스트(빠름)
uv run pytest -q -m slow    # 모델 다운로드 포함 통합 테스트(한국어 샘플 전사)
uv run ruff check .         # 린트
uv run ruff format .        # 포맷
```

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

## 라이선스 / 기여
내부 프로젝트. STT provider 키는 `.env`로만 주입하며 절대 커밋하지 않습니다.
