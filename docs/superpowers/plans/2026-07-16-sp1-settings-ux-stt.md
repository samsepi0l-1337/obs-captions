# SP1 — 설정 UX 간소화 + STT 개선 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development(권장) 또는 executing-plans. Steps는 체크박스(`- [ ]`) 문법. 상위 설계: `docs/superpowers/specs/2026-07-16-sp1-settings-ux-stt-design.md`.

**Goal:** 초보자가 기본 화면만으로 쓰도록 GUI/plugin을 최소 구성 + "고급" 토글로 세부 분리하고, 로컬 STT 모델을 하드웨어(VRAM 포함) 기반으로 추천하며, STT 지시 프롬프트·강조 어휘를 전달하고, 후처리 치환을 행 편집 UI로 쉽게 하며, API 키를 실제 연결로 검증한다.

**Architecture:** 순수 로직(하드웨어 감지·모델 추천·tier 분류·키 검증 결과 매핑)을 UI/IO와 분리해 TDD로 고정하고, GUI(Tkinter)와 OBS 플러그인(C++)은 기존 show/hide·사이드카 인프라를 재사용해 그 위에 얹는다. 키 검증 로직은 사이드카(Python)에 두고 plugin은 CLI 서브커맨드로 재사용(SSOT).

**Tech Stack:** Python 3.12 / pydantic AppConfig / faster-whisper / Tkinter ttk / httpx / C++17 obs_properties.

## Global Constraints

- 파일 ≤350줄. 순수 함수와 UI/IO 분리. FP/SOLID/DRY/KISS/YAGNI/TDD.
- API 키는 자식 프로세스 env로만 전달, config TOML/로그에 절대 기록 금지. OBS 속성 평문 저장은 경고 라벨과 함께 수용.
- 키/설정 경로/env_var/choices 실제 값 불변 — 표시 문구만 한국어.
- 기존 테스트 회귀 금지(현재 로컬 658 / CI 637 passed). GUI 테스트는 `importorskip("tkinter")` + no-display 가드 유지. 네트워크는 모킹(실호출 금지).
- `git add -A` 금지(명시적 add). 사용자 변경 되돌리기 금지. author≠verifier.

## File Structure

- `src/obs_captions/settings_types.py` (수정) — `FieldSpec.tier`, `LOCAL_MODEL_SIZES` 확장.
- `src/obs_captions/settings_fields.py` (수정) — tier 태깅, STT 힌트 필드, 치환 필드 위젯 변경.
- `src/obs_captions/config.py` (수정) — `LocalConfig.initial_prompt/hotwords`.
- `src/obs_captions/stt/hardware.py` (신규) — 하드웨어 감지 + `recommend_model`(순수).
- `src/obs_captions/stt/validate.py` (신규) — `validate_engine` 진입점 + 백엔드별 경량 인증.
- `src/obs_captions/stt/local_whisper.py`, `registry.py` (수정) — 힌트 전달.
- `src/obs_captions/cli.py` (수정) — `validate-key`, `recommend-model` 서브커맨드.
- `src/obs_captions/gui/{app,sections,widgets,runner}.py` (수정) — 고급 토글, 추천 라벨, 치환 에디터, 테스트 버튼.
- `native-plugin/src/{plugin-settings,obs-captions-properties,obs-captions-setting-ids}.*` (수정) — advanced 토글, 키 테스트 버튼.
- `DOCS.md`, `HOWTOUSE.md` (수정) — 신규 기능.

## 의존성 / 병렬 그룹

```
T1(tier 스키마) ──▶ T2(GUI 토글) ──▶ T5(추천 표시)
                └─▶ T3(plugin 토글)
T4(하드웨어/추천 순수) ──────────────▶ T5
T6(STT 힌트) ── 독립
T7(치환 에디터) ── 독립
T8(키검증 백엔드/순수) ──▶ T9(GUI 버튼)
                       └─▶ T10(CLI+plugin 버튼)
T11(문서) ── 마지막
```
병렬 가능: {T1}, 그다음 {T2·T3·T4·T6·T7·T8} 대체로 독립(파일 경계 분리), 그다음 {T5·T9·T10}, 마지막 T11. 실행 시 파일 겹침 없는 것끼리 묶어 위임.

---

## Task 1: FieldSpec.tier + 필드 tier 태깅

**Files:** `src/obs_captions/settings_types.py`, `src/obs_captions/settings_fields.py`, `tests/test_settings_schema.py`, `tests/test_settings_fields_tier.py`(신규)

**Interfaces (Produces):** `FieldSpec.tier: Literal["simple","advanced"] = "simple"`. 헬퍼 `simple_field_keys() -> set[str]`, `advanced_field_keys() -> set[str]` (settings_fields).

- [ ] Step 1: `tests/test_settings_fields_tier.py` 작성 — (a) `FieldSpec`에 `tier` 속성이 있고 기본 `"simple"`; (b) advanced로 지정돼야 하는 대표 키(`local.vad_threshold`, `audio.samplerate`, `server.port`, `obs.hotkey.enabled`, `overlay.custom_css`)가 `tier=="advanced"`; (c) simple 대표 키(`engine`, `language`, `local.model_size`)가 `"simple"`; (d) 모든 FieldSpec의 tier가 두 값 중 하나.
- [ ] Step 2: `uv run pytest tests/test_settings_fields_tier.py -q` → 실패(속성 없음).
- [ ] Step 3: `settings_types.py` `FieldSpec`에 `tier: Literal["simple","advanced"] = "simple"` 추가(frozen dataclass, 기본값). `Widget`처럼 `Tier = Literal[...]` alias 정의, `__all__` 갱신.
- [ ] Step 4: `settings_fields.py`에서 spec의 advanced 분류(설계 A)를 각 해당 `FieldSpec(...)`에 `tier="advanced"` 명시. `simple_field_keys`/`advanced_field_keys` 헬퍼 추가. 파일 ≤350줄 확인(초과 시 분류 헬퍼를 별 함수로 유지, 데이터는 그대로).
- [ ] Step 5: `uv run pytest tests/test_settings_fields_tier.py tests/test_settings_schema.py -q` PASS, `ruff check` clean.
- [ ] Step 6: Commit (`settings_types.py settings_fields.py tests/test_settings_fields_tier.py tests/test_settings_schema.py`).

---

## Task 2: GUI 고급 설정 토글

**Files:** `src/obs_captions/gui/app.py`, `src/obs_captions/gui/sections.py`, `tests/test_gui_sections.py`, `tests/test_gui_app_smoke.py`

**Interfaces (Consumes):** `FieldSpec.tier`(T1). **Produces:** `build_app`가 "고급 설정 표시" 체크박스를 노출, off일 때 advanced 필드 숨김.

- [ ] Step 1: `tests/test_gui_sections.py`에 테스트 추가 — 고급 토글 off면 `tier=="advanced"` 필드의 `row_widgets`가 숨김(grid_remove), on이면 표시. 엔진 조건과 AND: 숨겨진 엔진의 advanced 필드는 토글 on이어도 계속 숨김.
- [ ] Step 2: 실행 → 실패.
- [ ] Step 3: `sections.py` — `build_sections`가 각 필드 표시 조건을 `(engine_ok) AND (field.tier=="simple" OR show_advanced)`로 계산하도록 `_wire_engine_visibility`를 `_apply_visibility(engine, show_advanced)`로 일반화. `show_advanced` 상태를 받는 콜백/등록부 추가.
- [ ] Step 4: `app.py` — `controls`에 `ttk.Checkbutton("고급 설정 표시", variable=show_advanced_var)` 추가, 변경 시 `_apply_visibility` 재호출. 기본 off. `AppWindow`에 `advanced_check` 핸들 노출.
- [ ] Step 5: `test_gui_app_smoke.py`에 토글 스모크 추가(fake, headless 가드). `uv run pytest tests/test_gui_sections.py tests/test_gui_app_smoke.py -q` PASS.
- [ ] Step 6: Commit.

---

## Task 3: 플러그인 고급 설정 토글 (C++)

**Files:** `native-plugin/src/plugin-settings.hpp/.cpp`, `native-plugin/src/obs-captions-properties.cpp`, `native-plugin/src/obs-captions-setting-ids.hpp`, `native-plugin/tests/plugin_settings_test.cpp`, `native-plugin/tests/run_tests.sh`

**Interfaces (Produces):** `std::vector<std::string> advanced_field_ids()` (libobs-free 순수). setting id `kShowAdvanced`.

- [ ] Step 1: `plugin_settings_test.cpp`에 테스트 추가 — `advanced_field_ids()`가 T1의 advanced 분류와 동일한 id 집합(설정 id 기준)을 반환(예: vad/audio/server 등 대응 id 포함, engine/model/api_key 미포함).
- [ ] Step 2: `bash native-plugin/tests/run_tests.sh` → 실패.
- [ ] Step 3: `plugin-settings.cpp`에 `advanced_field_ids()` 구현(libobs 미포함). `obs-captions-setting-ids.hpp`에 `kShowAdvanced` 추가.
- [ ] Step 4: `obs-captions-properties.cpp` — `obs_properties_add_bool(props, kShowAdvanced, obs_module_text("ShowAdvanced"))` 추가, `modified_callback`에서 `apply_engine_visibility`와 동일 패턴으로 advanced 속성 `obs_property_set_visible(!show ? false : true)` 처리(엔진 gating과 AND). flat 속성 유지.
- [ ] Step 5: locale(`data/locale/en-US.ini`)에 `ShowAdvanced` 라벨. `run_tests.sh` 전체 PASS(libobs-free 부분). libobs-gated glue는 Windows CI로 검증.
- [ ] Step 6: Commit.

---

## Task 4: 하드웨어 감지 + 모델 추천 (순수)

**Files:** `src/obs_captions/stt/hardware.py`(신규), `tests/test_stt_hardware.py`(신규)

**Interfaces (Produces):**
```python
@dataclass(frozen=True)
class HardwareInfo:
    cuda_available: bool
    vram_mb: int | None
    ram_mb: int | None
    cpu_count: int | None
def detect_hardware() -> HardwareInfo: ...          # IO (프로브)
def recommend_model(info: HardwareInfo) -> str: ...  # 순수 (테스트 대상)
```

- [ ] Step 1: `tests/test_stt_hardware.py` — `recommend_model`의 매핑을 순수 검증: VRAM≥8000→`"large-v3-turbo"`; 4000≤VRAM<8000→`"large-v3"`; CUDA無·RAM≥8000·cpu≥8→`"medium"`; RAM≥4000→`"small"`; 그 외→`"base"`. `vram_mb=None`이면 CUDA 유무로만 판단. 경계값 각각 테스트.
- [ ] Step 2: 실행 → 실패(모듈 없음).
- [ ] Step 3: `hardware.py` 구현. `recommend_model`은 순수 분기. `detect_hardware`: `cuda_available`은 `stt/device.py` 프로브 재사용; `vram_mb`는 `pynvml`(있으면) 또는 `nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits` 파싱, 실패 시 None; `ram_mb`는 `psutil`(있으면) 또는 플랫폼 폴백, 없으면 None; `cpu_count=os.cpu_count()`. 모든 외부 의존은 선택적 import(try/except).
- [ ] Step 4: `uv run pytest tests/test_stt_hardware.py -q` PASS, `ruff check` clean.
- [ ] Step 5: Commit.

---

## Task 5: 모델 목록 확장 + 추천 표시 (GUI/CLI)

**Files:** `src/obs_captions/settings_types.py`, `src/obs_captions/cli.py`, `src/obs_captions/gui/app.py`, `src/obs_captions/gui/sections.py`, `tests/test_cli.py`, `tests/test_gui_app_smoke.py`

**Interfaces (Consumes):** T4 `detect_hardware`/`recommend_model`. **Produces:** CLI `recommend-model`(JSON: `{recommended, hardware}`); GUI 추천 라벨 + "추천값 적용" 버튼.

- [ ] Step 1: `test_cli.py`에 `recommend-model` 테스트 — `detect_hardware`를 monkeypatch로 고정 HardwareInfo 주입, 명령이 예상 모델/감지정보 JSON을 출력.
- [ ] Step 2: 실행 → 실패.
- [ ] Step 3: `settings_types.py` `LOCAL_MODEL_SIZES`에 `"large-v3-turbo"`, `"distil-large-v3"` 추가. `cli.py`에 `recommend-model` 서브커맨드(감지→추천→JSON stdout).
- [ ] Step 4: `sections.py`/`app.py` — 로컬 모델 드롭다운 근처에 추천 라벨(`추천: <model> (감지: GPU <vram>MB / CPU)`) + "추천값 적용" 버튼(선택만 변경, 자동 저장 없음). 감지는 백그라운드 스레드(runner 마샬링 패턴), 실패 시 라벨 숨김/폴백 문구. headless 가드.
- [ ] Step 5: `uv run pytest tests/test_cli.py tests/test_gui_app_smoke.py -q` PASS.
- [ ] Step 6: Commit.

---

## Task 6: STT 사전 힌트 (initial_prompt + hotwords)

**Files:** `src/obs_captions/config.py`, `src/obs_captions/stt/local_whisper.py`, `src/obs_captions/registry.py`, `src/obs_captions/settings_fields.py`, `tests/test_stt_local_hints.py`(신규), `tests/test_config.py`

**Interfaces (Produces):** `LocalConfig.initial_prompt: str | None = None`, `LocalConfig.hotwords: str | None = None`; local 백엔드가 값 있을 때만 `model.transcribe(..., initial_prompt=, hotwords=)` 전달.

- [ ] Step 1: `tests/test_stt_local_hints.py` — fake `WhisperModel`(transcribe 호출 인자 캡처)을 주입해: `initial_prompt`/`hotwords`가 None이면 인자 미전달(또는 None), 값 있으면 그대로 전달됨을 검증. `test_config.py`에 필드 존재/기본값 테스트.
- [ ] Step 2: 실행 → 실패.
- [ ] Step 3: `config.py` `LocalConfig`에 두 필드 추가. `local_whisper.py` 생성자에서 읽어 보관, `_transcribe_with_model`의 `transcribe(...)`에 값 있을 때만 kwargs 추가(`{k:v for ...}` 빌드). `registry.py` local 분기에서 전달.
- [ ] Step 4: `settings_fields.py`에 FieldSpec 추가 — `local.initial_prompt`(멀티라인/텍스트, help 예시), `local.hotwords`(단어 목록, tier="advanced"). initial_prompt는 simple 접근(간단), 상세는 advanced 판단은 spec A 따름.
- [ ] Step 5: `uv run pytest tests/test_stt_local_hints.py tests/test_config.py -q` PASS.
- [ ] Step 6: Commit.

---

## Task 7: 후처리 치환 행 편집 위젯

**Files:** `src/obs_captions/gui/widgets.py`, `src/obs_captions/gui/sections.py`, `src/obs_captions/settings_fields.py`, `tests/test_gui_sections.py`, `tests/test_gui_widgets.py`(신규 또는 기존)

**Interfaces (Produces):** `ReplacementListEditor` 위젯 — 행마다 (들리는 말, 교정), 추가/삭제 버튼; collector가 `list[dict]`(`{"match":..,"replace":..}`) 반환, 빈 행 무시, 잘못된 값은 명확한 오류.

- [ ] Step 1: `tests/test_gui_widgets.py` — 에디터에 두 행 설정 후 collector가 `[{"match":"a","replace":"b"},...]` 반환; match 빈 행 제외; set/get 왕복.
- [ ] Step 2: 실행 → 실패.
- [ ] Step 3: `widgets.py`에 `ReplacementListEditor`(Frame 기반: 행 컨테이너 + 추가/삭제). `sections.py`에서 `text.replacements`(widget="list")를 이 에디터로 렌더하도록 분기. `settings_fields.py`의 해당 필드 라벨을 "잘못 들리는 단어 교정"으로(정규식/대소문자 옵션은 advanced 상세 유지).
- [ ] Step 4: 기존 list 파싱 경로(sections #5 결함 수정)와 호환 — 에디터 미사용 다른 list 필드는 기존 처리 유지.
- [ ] Step 5: `uv run pytest tests/test_gui_widgets.py tests/test_gui_sections.py -q` PASS.
- [ ] Step 6: Commit.

---

## Task 8: 키 검증 백엔드 (순수/로직)

**Files:** `src/obs_captions/stt/validate.py`(신규), `tests/test_stt_validate.py`(신규). 참고: 각 백엔드 모듈(read-only로 인증 방식 확인).

**Interfaces (Produces):**
```python
@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    mode: Literal["network","format","unsupported"]
    message: str
def validate_engine(engine: str, api_key: str, extra: dict | None = None,
                    *, http_get=None, timeout: float = 8.0) -> ValidationResult: ...
```
`http_get`은 주입 가능(테스트용). 네트워크 검증 가능한 백엔드는 인증 헤더로 경량 GET(모델 목록 등) → 2xx=ok, 401/403=fail. 불가 백엔드는 `mode="format"`(키 비어있음/형식) 또는 `"unsupported"`.

- [ ] Step 1: `tests/test_stt_validate.py` — `http_get` 스텁으로: 200→`ok=True, mode="network"`; 401→`ok=False, mode="network", "인증 실패"`; 네트워크 예외→`ok=False`(친화 메시지); 빈 키→`ok=False, mode="format"`; 미지원 엔진→`mode="unsupported"`. 엔진별(openai/deepgram/elevenlabs/groq/openrouter/replicate 등) 엔드포인트·헤더 매핑 테스트.
- [ ] Step 2: 실행 → 실패.
- [ ] Step 3: `validate.py` 구현 — 엔진→(검증 URL, 헤더 빌더) 매핑 테이블. `_SECRET_ENV_VARS`/백엔드 헤더 방식(탐색 결과) 재사용. `http_get` 미주입 시 `httpx.get` 사용. websocket 전용(azure 등 network 검증 애매한 것)은 우선 `format`/`unsupported`로 두고 주석에 사유. **실제 401 관측은 개발 중 수동 1회 확인 후 각 엔진을 network로 승격**(문서화).
- [ ] Step 4: `uv run pytest tests/test_stt_validate.py -q` PASS, `ruff check` clean.
- [ ] Step 5: Commit.

---

## Task 9: 키 검증 GUI 버튼

**Files:** `src/obs_captions/gui/app.py`, `src/obs_captions/gui/sections.py`, `src/obs_captions/gui/runner.py`, `tests/test_gui_app_smoke.py`

**Interfaces (Consumes):** T8 `validate_engine`. **Produces:** API 키 필드 옆 "테스트" 버튼 → 결과 메시지.

- [ ] Step 1: `test_gui_app_smoke.py` — `validate_engine`을 monkeypatch(성공/실패 반환)해 버튼 클릭 시 상태 라벨/messagebox가 성공/실패를 표시하고, 호출이 백그라운드 스레드→`root.after` 마샬링됨을 검증(직접 UI 접근 없음).
- [ ] Step 2: 실행 → 실패.
- [ ] Step 3: 현재 선택 엔진의 API 키 입력 옆 "테스트" 버튼 추가. 클릭 시 현재 입력된 키+엔진으로 `validate_engine`을 백그라운드 실행(runner 마샬링 패턴 재사용), 결과를 성공(초록)/실패(빨강)/미지원(회색) 메시지로. 진행 중 버튼 비활성.
- [ ] Step 4: `uv run pytest tests/test_gui_app_smoke.py -q` PASS.
- [ ] Step 5: Commit.

---

## Task 10: 키 검증 CLI + 플러그인 버튼

**Files:** `src/obs_captions/cli.py`, `tests/test_cli.py`, `native-plugin/src/obs-captions-properties.cpp`, `native-plugin/src/obs-captions-setting-ids.hpp`, `native-plugin/data/locale/en-US.ini`

**Interfaces (Produces):** CLI `validate-key --engine <e>`(키는 env, stdout JSON `{ok,mode,message}`); plugin `kValidateKey` 버튼.

- [ ] Step 1: `test_cli.py` — `validate-key --engine openai`가 `validate_engine`(monkeypatch)을 호출해 JSON을 stdout으로 출력(env에서 키 획득). 종료코드 0/비0 매핑.
- [ ] Step 2: 실행 → 실패.
- [ ] Step 3: `cli.py`에 `validate-key` 서브커맨드(env에서 키 읽어 `validate_engine`→JSON). plugin `obs-captions-properties.cpp`에 `obs_properties_add_button(props, kValidateKey, obs_module_text("ValidateKey"), cb)` 추가 — 콜백이 사이드카 `validate-key`를 키를 env로 넣어 1회 실행하고 결과를 상태 텍스트(info 속성)로 표시. `kValidateKey` id + locale.
- [ ] Step 4: `uv run pytest tests/test_cli.py -q` PASS. plugin libobs-gated 부분은 Windows CI.
- [ ] Step 5: Commit.

---

## Task 11: 문서 갱신

**Files:** `DOCS.md`, `HOWTOUSE.md`

- [ ] Step 1: `DOCS.md` — 고급/간단 분리, 로컬 모델 추천, STT 힌트(initial_prompt/hotwords), 치환 에디터, 키 검증 기능·CLI(`recommend-model`, `validate-key`) 문서화.
- [ ] Step 2: `HOWTOUSE.md` — 초보자 흐름 업데이트(기본 화면만으로 시작, 필요 시 고급, 키 입력+테스트, 모델 추천 적용). 전문용어 1줄 설명 유지.
- [ ] Step 3: Commit.

---

## Self-Review

- **Spec coverage:** A(T1·T2·T3) / B(T4·T5) / C(T6) / D(T7) / E(T8·T9·T10) / 문서(T11) — spec 5개 컴포넌트 전부 task로 매핑됨.
- **Placeholder:** 각 task에 파일·인터페이스 시그니처·대표 테스트·구현 방향 명시. 세부 임계값(추천 매핑)은 T4 테스트에 구체 수치로 고정. 키 검증 백엔드별 network 승격은 "수동 확인 후"로 명시(리스크 노출, 침묵 아님).
- **Type consistency:** `FieldSpec.tier`(T1)를 T2/T3/T6/T7이 소비, `HardwareInfo`/`recommend_model`(T4)을 T5가, `ValidationResult`/`validate_engine`(T8)을 T9/T10이 소비 — 시그니처 일치.
- **파일 겹침:** app.py/sections.py는 T2·T5·T9가 공유 → 이 셋은 순차(또는 동일 작업자). 나머지는 분리.

## Execution Handoff

- 실행: subagent-driven(task별 fresh subagent + 2단계 리뷰). LOGIC task(T1,T4,T6,T8,T10-CLI)는 TDD 우선, FDW task(T2,T5,T7,T9 GUI / T3,T10-plugin)는 UI. author≠verifier로 각 묶음 후 독립 code-reviewer.
- 병렬 배치: 1차 T1 → 2차 {T4,T6,T7,T8,T3}(파일 분리) → 3차 {T2→T5,T9} 및 {T10} → T11.
- 각 묶음 완료마다 전체 `uv run pytest -q` + `ruff` + 파일 길이 확인, PR은 워크스트림 단위 또는 SP1 전체 단위로.
