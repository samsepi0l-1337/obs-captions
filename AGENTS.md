# AGENTS.md

## Communication

- 한국어로 짧게. 결과 먼저, 근거는 로그/응답코드/실패값 중심.
- 추측 금지. 불확실하면 확인하고, 확인 못 한 건 못 했다고 말한다.
- tool first. 설명보다 실행·검증·결과.

## MODEL

- gpt-5.5
- gpt-5.4-mini
- gpt-5.3-codex-spark

## Stack & Versions (확정)

- 런타임: Python 3.12 (uv 핀, `.venv`). **Py3.14 금지** — PyAudio·webrtcvad 휠 없음, CTranslate2 macOS 버그(#2063).
- 의존성: **uv** + `pyproject.toml`.
- 오디오: **sounddevice** (PyAudio 금지). 16kHz mono float32 콜백. 단, **선택적 Windows 시스템 사운드 캡처**(`[audio] source="loopback"`)만 예외 — `pyaudiowpatch`(PyAudio 포크, `--extra loopback`)를 `audio/loopback.py` 어댑터 1곳에서 sounddevice 스타일 콜백/포맷(paFloat32→`callback(indata,...)`)으로 감싸고, MicCapture 콜백/포맷 파이프라인은 불변(네이티브 48k stereo→16k mono 다운믹스·리샘플 재사용).
- VAD: **Silero VAD (ONNX 경로)** (webrtcvad 금지).
- 플랫폼: **Windows 10/11**(NVIDIA CUDA 로컬 STT 가속, 1급) + **macOS Apple Silicon**(CPU) 공동 1급, Linux 동작.
- STT(pluggable): `local` faster-whisper — `[local].device`(auto|cpu|cuda)·`compute_type`로 제어. Windows/Linux는 **CUDA**(auto→탐지/float16, 미탐지 시 CPU 폴백), macOS는 CPU int8. `device` 분기는 순수 함수 `stt/device.py:resolve_device`(CUDA 미탐지=빈 set→CPU) / `openai` Realtime(`gpt-realtime-whisper`) / `elevenlabs` Scribe v2 Realtime / `google` mode `gemini`(Gemini Live, API 키) 또는 `speech_v2`(Speech-to-Text v2 `chirp_2` gRPC 스트리밍, 서비스계정·`--extra google`, asyncio.Queue 브리지·270s 선제 재시작·client 주입). 내부 정규화 16kHz PCM16(OpenAI 어댑터만 24kHz 업샘플).
- 서버: FastAPI + uvicorn + websockets. HTTP 정적 오버레이 + `/ws`.
- 오버레이: 정적 HTML/CSS/JS. 투명 배경, committed/partial 2-tier, diff push.
- OBS 플러그인: C++ + obs-plugintemplate + **Qt6 QWebSocket**. 내장 `text_ft2_source`(.version=2) 소유·갱신(approach b, 직접 래스터화 금지).
- 테스트: **pytest**. 린트/포맷: **ruff**. (이 프로젝트엔 TS/JS/pnpm/vitest/playwright/bun 없음.)

## Hard Constraints (어기면 빌드/런타임 깨짐)

- sounddevice 사용·PyAudio 금지(마이크 경로). 예외: `source="loopback"`(Windows 전용)만 PyAudioWPatch를 `audio/loopback.py` 어댑터로 캡슐화(비Windows는 lazy-import라 미설치여도 패키지 import 정상). Silero VAD 사용·webrtcvad 금지.
- faster-whisper: macOS는 CPU 전용, Windows/Linux NVIDIA는 CUDA(`device="auto"/"cuda"`). GPU 런타임은 `--extra gpu`(`nvidia-cublas-cu12`/`nvidia-cudnn-cu12`, macOS는 env marker로 제외). Windows DLL 경로는 `platform_dll.add_cuda_dll_directories`가 CLI 진입 시 등록(비Windows no-op). `cpu_threads`(intra)는 CPU 경로에서 유지.
- 오버레이는 `http://localhost` URL로 서빙(**file:// 금지** — CORS/getUserMedia 문제).
- 플러그인: `obs_source_update`를 소켓 스레드에서 직접 호출 금지 → `video_tick`(graphics thread) dirty-flag 또는 `obs_queue_task(OBS_TASK_GRAPHICS)`.
- API 키는 `.env`로만. 절대 커밋/로그 금지.

## Project Structure (목표)

- `src/obs_captions/`: `cli.py`, `config.py`, `audio/{capture,devices,loopback}.py`, `vad.py`, `stt/{base,local_whisper,openai_realtime,elevenlabs_realtime}.py`, `pipeline.py`, `server/{app,hub}.py`
- `web/overlay/`: `overlay.{html,css,js}`
- `obs-plugin/`: `buildspec.json`, `CMakeLists.txt`, `CMakePresets.json`, `src/{plugin-main,caption-source,caption-ws-client}.cpp`
- `tests/`: `test_{pipeline,stt_base,config,server}.py`
- `scripts/audio_check.py`

## Key Interfaces (계약)

- `STTBackend(ABC)`: `start_stream()/feed_audio(pcm16)/flush()/stop_stream()` + `on_partial`/`on_final` 콜백. `Transcript{text,is_final,start_ms?,end_ms?,lang?}`. `on_partial`은 항상 **현재 전체 가설**을 전달(OpenAI delta는 어댑터에서 누적).
- WS JSON: `{"type":"caption","partial":str,"committed":[str,...]}` (diff push, 전체 재전송 금지).
- 자막 상태머신: committed(불변)+partial(가변). 로컬 LocalAgreement-2, 클라우드는 final 이벤트로 확정. VAD 무음/문장부호에서 확정 후 버퍼 트림.

## Work Rules

- 코드 바꾸면 관련 docs/테스트/타입 함께 갱신. 작은 단위 커밋, 리팩토링·정책변경을 한 커밋에 섞지 않음.
- 계산/정규화/매핑은 순수 함수로 분리. 일회성·미래용 추상화·무의미 wrapper 금지.
- 파일 350~400줄 이하. 큰 함수에 분기 누적 금지.
- FP, SOLID, DRY, KISS, YAGNI, Clean Code/Architecture, TDD.
- 파일을 하나하나 만들지 말고 가능한 명령으로 일괄 처리.
- 검증: 변경 범위 targeted `uv run pytest tests/<file>` → 완료 전 `uv run pytest` 전체. 코드 변경 시 `uv run ruff check .` · `uv run ruff format .`.
- coverage는 ignore/skip/약화가 아니라 의미 있는 nearest test로 높인다. 중복/항상통과/무의미 테스트 금지.
- 보안: 인증·secret·외부 URL·명령 실행 경로 변경 시 diff 범위 점검. Critical/High는 수정 전 완료 선언 금지. plaintext password/session token/provider API key 저장 금지. OWASP Top 10 기준.

## Codex(omx) 실행 계약

- 이 파일은 omx(oh-my-codex) 호출 시 실행 계약. omx는 Claude main orchestration이 분할해 넘긴 scope를 직접·효율적으로 수행하는 실행자다.
- 넘겨받은 범위만 처리. 범위를 임의로 넓히지 않고, 다른 작업과 겹치는 파일은 건드리지 않는다.
- 하위 subagent로 재위임하지 않는다. 추가 분해가 필요하면 결과에 사유를 한 줄로 적어 orchestration에 돌려준다.
- 조사·웹·문서 검색은 자체 도구로 처리하고, 결론+근거 링크만 간결히 반환. 긴 원문 금지.
- TDD: 대상 테스트 먼저 작성·실패 확인 후 구현. targeted `uv run pytest` → 전체 → `uv run ruff`.
- 검증 실패는 원인 진단 후 최소 수정. 추측으로 넓게 고치지 않는다.
- 완료 시 변경 파일 + 실행한 검증 명령/결과(통과·실패 값)만 결과 먼저로 보고.
- 같은 패스에서 self-approve 금지. 비판 점검은 별도 리뷰 패스(`omx exec review` 또는 omc code-reviewer).

## Folder AGENTS.md Memory / 실패 대응

- `uv sync`만 실행하면 local extra(torch/silero/faster-whisper)가 제거되어 VAD/local 테스트가 실패할 수 있으니 로컬 STT 검증 전 `uv sync --extra local`로 환경을 복구한다.
- 각 도메인 폴더 `AGENTS.md`엔 즉시 따라야 하는 실패 지식·필수 운영 계약을 담는다. 세션 끝 전 새로 확인한 실패원인·안전 수정·필수 검증 명령을 갱신한다.
- 작업이 실패하면 같은 실패를 반복하지 않도록 반대되는 안전 작업/선행 대응을 한 문장으로 해당 폴더 `AGENTS.md`에 추가한다(파괴적 롤백 아님).
- 추측·일회성 로그·변할 수 있는 provider/model/가격 사실은 단정 금지. `AGENTS.md`는 200줄 이하.

## Learned User Preferences

- 지침은 짧은 명령형 bullet. 불필요한 소제목·번호목록·장문 금지.
- provider 통합 검증·수정은 제공사별 공식 문서를 대조하고 한 provider만 기준 삼지 않는다.
- 프로덕션 분석은 로그·DevTools Network 증거로 원인을 확인하고 추측하지 않는다. git commit은 관련 파일끼리 묶는다.
