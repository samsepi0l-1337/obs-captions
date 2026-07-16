# SP1 — 설정 UX 간소화 + STT 개선 설계

> 상위 요청: 초보(터미널 미숙) 사용자도 쉽게 쓰도록 GUI/plugin 구성을 간단히 하고, 세부는 "고급"으로 숨기며, 로컬 STT 모델을 추천하고, env 키를 GUI/plugin에서 직접 입력·검증한다. 전체 요청은 SP1(이 문서)과 SP2(후처리 로컬 LLM, 별도)로 분해했고 SP1을 먼저 진행한다.

**날짜:** 2026-07-16
**대상 버전:** 0.1.4 (릴리스 단위)
**도메인:** LOGIC(STT/config/키검증) + FDW(GUI/plugin UI)

---

## 목표

기존 STT 파이프라인은 유지하면서 그 위에 (A) Simple/Advanced 설정 분리, (B) 로컬 모델 추천, (C) STT 사전 힌트, (D) 후처리 치환 간단 UI, (E) API 키 실제 연결 테스트를 얹는다. GUI(Python Tkinter)와 OBS 플러그인(C++ obs_properties) 양쪽에 적용한다.

## 비목표 (SP1에서 하지 않음)

- 후처리 로컬 LLM(Ollama 번역/정정) — SP2.
- 새 클라우드 STT 백엔드 추가.
- 오버레이/자막 렌더링 파이프라인 변경.
- STT 정확도 알고리즘 자체 변경(힌트 전달만 추가).

## Global Constraints (전 task 공통)

- 파일 ≤350줄. 순수 함수(계산/정규화/매핑)와 UI/IO 분리.
- API 키는 자식 프로세스 environment로만 전달, config TOML/로그에 절대 기록 금지(기존 계약 유지). OBS 속성 평문 저장은 경고 라벨과 함께 수용된 tradeoff.
- 키/설정 경로/env_var/choices 실제 값 불변 — 표시 문구만 한국어.
- 기존 테스트(현재 658 로컬 / 637 CI passed) 회귀 금지. GUI 테스트는 `importorskip("tkinter")` + no-display 가드 유지.
- `git add -A` 금지(명시적 add). 사용자 변경 되돌리기 금지.
- author≠verifier: 작성 후 별도 리뷰 패스.

---

## A. Simple/Advanced 재구성

### 데이터 모델
- `settings_types.py`의 `FieldSpec`(frozen dataclass)에 `tier: Literal["simple","advanced"] = "simple"` 추가. 기존 `FieldSpec(...)` 호출부는 무변경(기본 simple), advanced 필드에만 `tier="advanced"` 명시.

### 필드 분류 (초안 — plan에서 확정)
- **simple**: `engine`, `language`, `local.model_size`, 선택 엔진의 `providers.<e>` 키·`providers.<e>.model`, `overlay.position`, `overlay.font_size`, `overlay.color`, `obs.source_name`, `export.enabled`, `export.format`, STT 힌트(C의 기본 필드).
- **advanced**: `local.device/compute_type/cpu_threads/partial_interval_ms/max_buffer_s/vad_threshold/min_silence_ms`, `audio.*`, `server.*`, `overlay.*` 나머지, `obs.hotkey.*`, `text.*`(규칙류), `providers.google.mode/location/project_id`, `providers.azure.region`, `providers.openai.delay/target_language`.

### GUI (Tkinter)
- 컨트롤 바(`app.py` `controls`)에 "고급 설정 표시" 체크박스(기본 off) 추가.
- `sections.py`: 기존 `row_widgets`(label+widget+help) show/hide 인프라를 재사용. 한 위젯의 표시 = `(엔진 조건 충족) AND (tier=="simple" OR 고급토글 on)`. 엔진 visibility와 tier 토글은 독립적으로 AND 결합.
- 고급 필드가 하나도 없는 탭은 simple 모드에서 탭 자체를 숨기거나 비활성(plan에서 결정).

### Plugin (C++ obs_properties)
- `plugin-settings.cpp`에 순수 함수 `advanced_field_ids()` 추가(engine 무관, tier=advanced인 속성 id 목록). GUI FieldSpec의 tier와 동일 분류를 C++에 반영(정합 테스트로 고정).
- `build_captions_properties`에 `obs_properties_add_bool("show_advanced", "고급 설정 표시")` 추가, `modified_callback`에서 `apply_engine_visibility`와 동일 패턴으로 advanced 속성 `obs_property_set_visible` 토글.
- 주의: `obs_properties_get`은 즉시 객체만 검색하므로 flat 속성 구조 유지(그룹화로 옮기지 않음).

---

## B. 로컬 STT 모델 추천 (VRAM 정교)

### 모델 목록 확장
- `settings_types.py` `LOCAL_MODEL_SIZES`에 `large-v3-turbo`, `distil-large-v3` 추가(정확 목록은 plan에서 faster-whisper 호환 확인 후 확정). `LocalConfig.model_size`는 자유 문자열이라 값 자체는 그대로 전달됨.

### 하드웨어 감지 (신규 순수 모듈)
- `src/obs_captions/stt/hardware.py`(신규): `detect_hardware() -> HardwareInfo`.
  - `cuda_available`: `stt/device.py::resolve_device` 로직 재사용.
  - `vram_mb`: NVIDIA는 `nvidia-ml-py`(pynvml) 또는 `nvidia-smi --query-gpu=memory.total` 파싱. 미설치/비NVIDIA/실패 시 `None`.
  - `ram_mb`, `cpu_count`: 표준 라이브러리(`os`, `psutil` 있으면 사용, 없으면 폴백).
- `recommend_model(info: HardwareInfo) -> str`: 순수 함수. VRAM/RAM 임계로 매핑(예: VRAM≥8GB→`large-v3-turbo`, ≥4GB→`large-v3`/`distil-large-v3`, CUDA無·RAM 충분→`small`/`medium`, 약함→`base`). 임계표는 plan에서 확정하고 단위 테스트로 고정.

### GUI/plugin 표시
- GUI: 모델 드롭다운 근처에 "추천: <model> (감지: GPU 8GB / CPU)" 라벨. 버튼 "추천값 적용"으로 선택만 변경(자동 저장·자동 강제 없음).
- plugin: 감지는 사이드카(Python)만 가능하므로, plugin은 사이드카가 산출한 추천을 info 텍스트로 노출(경로는 plan에서; 최소한 GUI 우선, plugin 표시는 가능 범위에서).

---

## C. STT 사전 힌트 (initial_prompt + hotwords + 파일)

### 데이터 모델
- `LocalConfig`에 `initial_prompt: str | None = None`, `hotwords: str | None = None` 추가.
- 파일 로드: FieldSpec `path` 위젯으로 `.txt` 선택 시 내용을 `initial_prompt`로 로드하거나, 경로를 저장하고 런타임에 읽기(둘 중 하나를 plan에서 확정 — 파일 경로 저장이 config 이식성에 유리하나 파일 이동 리스크; 직접 텍스트 저장이 자기완결적). **결정: 직접 텍스트 저장을 기본, 파일은 "불러오기" 편의 버튼으로 텍스트에 채워넣는다**(경로 의존 제거).

### 전달
- `stt/local_whisper.py::_transcribe_with_model`의 `model.transcribe(...)` 호출에 `initial_prompt=`, `hotwords=`를 전달(값이 있을 때만). 생성자에서 `LocalConfig`로부터 읽어 보관.
- `registry.py`의 local 백엔드 생성 분기에서 두 값을 전달.

### FieldSpec
- 지시 프롬프트(멀티라인, help로 예시 안내) — simple 접근(간단 노출) / 상세는 advanced.
- 강조 어휘(hotwords, 공백/줄 구분 단어 목록) — advanced.

## D. 후처리 치환 간단 UI

- 기존 `text.replacements`(현재 단일 라인 JSON 수동입력 — 이전 결함)을 **행 추가/삭제식 목록 편집 위젯**으로 교체.
  - `widgets.py`에 `ReplacementListEditor`(신규): 각 행 = (들리는 말, 교정) 입력 + 추가/삭제. collector가 `list[dict]`(ReplacementRule 형식) 반환. 잘못된 값은 저장 시 명확한 오류.
- Simple에서 "잘못 들리는 단어 교정" 명칭으로 접근 가능하게(정규식·대소문자 옵션은 advanced 상세).

## E. API 키 실제 연결 테스트

### 백엔드 인터페이스 (신규)
- 각 STT 백엔드(streaming/utterance)에 `validate_credentials(timeout: float) -> ValidationResult` 추가. 짧은 인증 전용 동작:
  - REST 백엔드(openrouter/replicate/groq, 그리고 가능한 openai/deepgram/elevenlabs): 인증 헤더로 가벼운 GET/HEAD(모델 목록 등) 호출 → 401/403이면 실패, 2xx면 성공.
  - websocket 백엔드: 연결 open 후 인증 거절(close code/첫 프레임)로 판별. Azure는 SDK 인증 확인.
- 백엔드별 인증 실패 표면화가 미확인이므로 **각 구현 시 실제 401 응답을 관측해 확인**(리스크; plan에서 백엔드별 확인을 명시 task로). 확인 불가한 백엔드는 "형식 검증만"으로 degrade하고 그 사실을 UI에 표시(침묵 금지).
- 공통 진입점: `check_engine.py` 또는 신규 `stt/validate.py`에 `validate_engine(engine, key, extra) -> ValidationResult(ok, message)`.

### GUI
- API키 필드 옆 "테스트" 버튼 → `validate_engine`을 백그라운드 스레드로 호출(`runner`의 마샬링 패턴 재사용), 결과를 성공(초록)/실패(빨강) 메시지로 표시. 네트워크 예외는 사용자 친화 메시지.

### Plugin
- 사이드카 CLI에 `validate-key --engine <e>` 서브커맨드 추가(키는 env로 전달, stdout에 JSON 결과). plugin의 `obs_properties_add_button("키 테스트")` 콜백이 사이드카를 1회 실행해 결과를 상태 텍스트로 표시. (plugin C++이 직접 클라우드 인증하지 않고 사이드카 재사용 → 로직 SSOT 유지.)

---

## 아키텍처 / 파일 영향 요약

| 영역 | 파일 | 변경 |
|---|---|---|
| 스키마 | `settings_types.py` | `FieldSpec.tier`, `LOCAL_MODEL_SIZES` 확장 |
| 스키마 | `settings_fields.py` | tier 태깅, STT 힌트/치환 필드 추가 |
| config | `config.py` | `LocalConfig.initial_prompt/hotwords` |
| STT | `stt/local_whisper.py`, `registry.py` | 힌트 전달 |
| STT | `stt/hardware.py`(신규) | 하드웨어 감지 + `recommend_model` |
| STT | `stt/validate.py`(신규) + 각 백엔드 | `validate_credentials` |
| CLI | `cli.py`/`check_engine.py` | `validate-key` 서브커맨드 |
| GUI | `gui/app.py`, `gui/sections.py`, `gui/widgets.py`, `gui/runner.py` | 고급 토글, 추천 라벨, 치환 에디터, 테스트 버튼 |
| Plugin | `plugin-settings.cpp/.hpp`, `obs-captions-properties.cpp`, `obs-captions-setting-ids.hpp` | advanced 토글, 키 테스트 버튼 |
| 문서 | `DOCS.md`, `HOWTOUSE.md` | 신규 기능 반영 |

## 테스트 전략

- 순수 로직 우선 TDD: `recommend_model`(하드웨어→모델 매핑), tier 분류 정합(GUI FieldSpec ↔ plugin `advanced_field_ids`), 힌트 전달(local_whisper가 값 있을 때만 인자 전달), 치환 collector, `validate_engine`의 결과 매핑(모킹된 HTTP 응답 401→실패/200→성공).
- GUI: fake runner/monkeypatch로 고급 토글·추천 라벨·테스트 버튼 흐름(headless skip 가드 유지).
- plugin: `plugin_settings_test.cpp` 확장(advanced_field_ids, env_for), libobs-gated glue는 Windows CI로 검증.
- 네트워크 테스트는 실호출 금지(모킹). 실제 백엔드 401 관측은 개발 중 수동 1회 확인(문서화), CI는 모킹.

## 리스크 / 오픈 이슈

1. **키 검증 백엔드별 편차**: 일부 백엔드는 인증 실패를 즉시 안 던질 수 있음 → 해당 백엔드는 형식 검증으로 degrade + UI 표기. plan에서 백엔드별 확인 task 분리.
2. **VRAM 감지 의존성**: `pynvml`/`nvidia-smi` 부재 시 폴백. 추가 의존성 최소화(선택적 import).
3. **GUI↔plugin 정합 갭(기존)**: 일부 필드가 plugin 미구현 상태. tier 태깅 시 이 갭을 악화시키지 않도록 정합 테스트 추가.
4. **plugin 추천 표시**: 하드웨어 감지는 사이드카(Python)만 가능 → plugin은 사이드카 경유. 실시간성 제약은 plan에서.

## 완료 정의 (SP1)

- GUI/plugin 모두 기본이 최소 구성, "고급"으로 세부 노출.
- 로컬 모델 추천이 하드웨어(VRAM 포함) 기반으로 표시.
- STT 지시 프롬프트·강조 어휘가 실제 transcribe에 전달.
- 후처리 치환을 행 편집 UI로 쉽게.
- API 키 "테스트"가 GUI/plugin에서 실제 결과를 반환(불가 백엔드는 명시적 degrade).
- 전체 테스트 green, ruff clean, 파일 ≤350줄, 문서 갱신, author≠verifier 검증 통과.
