# 설계: 데스크톱 GUI + OBS 플러그인 설정 페이지

- 작성일: 2026-07-15
- 상태: 승인 대기
- 범위: `obs-captions.exe`를 콘솔 전용에서 **GUI 앱**으로 전환(설정 편집 + 시작/중지), 그리고 OBS 네이티브 플러그인에 **LocalVocal 스타일 설정 패널**(모델 선택 + 클라우드 엔진/키) 추가.

---

## 1. 목표 / 비목표

**목표**
- 인자 없이 `obs-captions.exe` 실행 시 데스크톱 GUI 창이 뜬다(콘솔 앱 아님).
- GUI에서 STT 엔진/모델·언어·오디오·출력(sink)·오버레이·텍스트 처리·내보내기·OBS·API 키(.env)를 편집하고, 자막 파이프라인을 시작/중지하며 로그를 본다.
- OBS 네이티브 플러그인의 필터 속성을 LocalVocal 스타일로 확장: 엔진(Local Whisper + 클라우드) 선택, 모델, 언어, 프로바이더별 API 키(마스킹), 타깃 텍스트 소스, 텍스트 처리(고급).
- 기존 CLI(`run`/`serve`/`list-devices`/`check-engine`/`ipc-sidecar`)는 인자로 호출하면 그대로 동작.

**비목표**
- LocalVocal의 임베드 whisper.cpp/모델 다운로드 다이얼로그/번역 모델은 도입하지 않는다(우리는 Python 사이드카 + faster-whisper가 모델을 자동 다운로드).
- 새 STT 백엔드 추가 없음(기존 12종 표면만 노출).
- 실시간 번역 기능 없음(현재 스코프 밖).

---

## 2. 공유 설정 표면 (single source of truth)

`src/obs_captions/config.py`의 Pydantic 모델이 이미 스키마다. GUI(Python)와 플러그인(C++)이 각자 이 스키마의 **부분집합**을 렌더한다. 필드 라벨·위젯 타입·선택지·적용 컨텍스트(GUI-only / plugin-only / both)를 한 곳에 표로 정의한다:

- 신규 `src/obs_captions/settings_schema.py` — 순수 데이터(딕셔너리/데이터클래스) 목록. 각 항목: `key`(config 경로), `label`, `widget`(text/choice/int/float/bool/path/secret), `choices`, `applies_to`(gui/plugin/both), `section`(탭/그룹).
- GUI는 이 표로 폼을 자동 생성 → 필드 추가 시 한 곳만 수정.
- 플러그인은 C++라 이 표를 직접 못 읽지만, **동일한 항목 순서/그룹을 미러**한다(문서로 대응표 유지). 값 전달은 사이드카 config로 일원화하므로 스키마 진실은 여전히 Python 쪽 하나.

**적용 컨텍스트 규칙**
- 플러그인 모드에서 무의미한 설정은 노출하지 않는다: `[audio]`(OBS가 오디오 제공), `sink`/`[server]`/`[overlay]`(출력은 지정 Text 소스). → plugin `applies_to`에서 제외.
- 플러그인 노출: engine/model/language, 클라우드 키·model, 텍스트 처리(치환·필터·환각억제), 타깃 텍스트 소스, sidecar exe 경로.

---

## 3. 컴포넌트 A — 데스크톱 GUI (Tkinter)

### 3.1 진입점
- `src/obs_captions/cli.py`: 프로그램 인자가 없으면(`len(sys.argv)==1`) `obs_captions.gui.app:main()` 실행, 있으면 기존 click CLI(`cli()`).
- PyInstaller `obs_captions.spec`: `console=True` → **`console=False`(windowed)**. Windows에서 CLI 인자로 호출된 경우에만 `ctypes.windll.kernel32.AttachConsole(-1)`(ATTACH_PARENT_PROCESS)로 부모 콘솔에 stdout/stderr 재연결 → 콘솔 명령 출력 유지. 사이드카(`ipc-sidecar`)는 파이프 통신이라 콘솔 불필요.
- macOS/Linux: windowed 개념이 없어 그대로 동작(터미널 실행 시 출력 정상).

### 3.2 모듈 구조 (`src/obs_captions/gui/`, 각 350줄 이하)
- `app.py` — 메인 창(`tk.Tk`), 상단 탭(`ttk.Notebook`), 하단 시작/중지·상태표시·로그(`ScrolledText`) 패널, 메뉴(저장/열기/종료).
- `config_io.py` — `config.toml`·`.env` 로드/저장. 로드는 기존 `load_config`(Pydantic) 재사용, 저장은 dict→TOML 직렬화(`tomli_w` 또는 표준 라이브러리 기반) + `.env` 키 기록. 파일 없으면 기본값으로 시작.
- `runner.py` — 시작/중지. `subprocess.Popen([exe, "run", "--sink", sink], stdout=PIPE, stderr=STDOUT)` 실행, 별도 스레드로 라인 읽어 로그 패널에 append(Tk `after`로 UI 스레드 반영). 중지 시 프로세스 종료(Windows `CTRL_BREAK`/terminate → kill 폴백). 개발 모드(비프리즈)에선 `python -m obs_captions run ...`.
- `sections/` — 탭별 폼 빌더. `settings_schema.py`를 읽어 위젯 생성. engine 선택 시 local/cloud 관련 필드 show/hide(콜백).
- `widgets.py` — 라벨+입력 조합, 시크릿(마스킹) 입력, 파일 선택 등 재사용 위젯.

### 3.3 탭 구성
General(engine·(local)model_size·device·language) · Audio(source·device) · Local(vad·buffer 등) · Output(sink·server port·overlay 스타일) · Text(치환·필터·환각억제) · Export(SRT/VTT/TXT) · OBS(websocket·hotkey) · API Keys(.env, 마스킹 + provider별 `check-engine` 버튼).

### 3.4 동작
- 시작 시 현재 폴더의 `config.toml`(+`.env`) 로드. 없으면 기본값.
- 편집 → **Save** 시 config.toml/.env 기록. **Start** 시(미저장 변경 있으면 먼저 저장) 서브프로세스 실행, 로그 스트리밍, 상태 "Running". **Stop** 시 종료.
- API Keys 탭의 `check-engine <engine>` 버튼 → 서브프로세스로 점검, 결과를 로그에 표시.

---

## 4. 컴포넌트 B — OBS 플러그인 설정 페이지 (LocalVocal 스타일)

### 4.1 현재 → 목표
현재 `obs_captions_filter_get_properties`는 텍스트 3개(target_text_source, config_path, sidecar_exe). 이를 LocalVocal 패턴으로 확장한다(네이티브 `obs_properties`).

### 4.2 속성 구성 (순서/그룹)
- `advanced_settings_mode` — 리스트(Simple=0 / Advanced=1), `modified_callback`으로 고급 그룹 visible 토글.
- **General 그룹**: `target_text_source`(소스 열거 드롭다운 — `obs_enum_sources`로 Text 소스 채움) · `language`(리스트) · `sidecar_exe`(경로, `OBS_PATH_FILE`).
- **Engine 그룹**: `engine`(리스트: `local`/`openai`/`deepgram`/`elevenlabs`/`google`/`xai`/`assemblyai`/`azure`/`openrouter`/`replicate`/`groq`), `modified_callback` = `engine_selection_callback`.
  - Local 전용(visible when engine==local): `local_model_size`(리스트 tiny/base/small/medium/large-v3) · `local_device`(리스트 auto/cpu/cuda).
  - Cloud 전용(visible when engine!=local, 프로바이더별): `api_key`(`OBS_TEXT_PASSWORD`) · `provider_model`(텍스트/리스트) · Azure면 `azure_region`.
  - 키 평문 저장 경고: `secret_warning`(`OBS_TEXT_INFO`) — "API 키는 OBS 씬 컬렉션 JSON에 평문 저장됩니다".
- **Text 처리 그룹**(Advanced): `suppress_blank`(bool) · `suppress_regex`(멀티라인) · `filter_words`(멀티라인) · 치환은 1차로 멀티라인 "match=replace" 라인들.

### 4.3 플러그인 → 사이드카 전달
- 플러그인은 위 속성값을 읽어 **사이드카가 소비할 설정을 생성**한다. 두 방식 중:
  - (기본) 필터가 임시 `config.toml`을 생성/갱신(플러그인 config dir, `obs_module_config_path`)하고 `ipc-sidecar --config <생성경로>` 실행.
  - API 키는 config 파일에 쓰지 않고 **자식 프로세스 환경변수**로 주입(`spawn.env`에 `OPENAI_API_KEY=...`) → 사이드카가 기존 방식대로 env에서 읽음. (키는 OBS 속성에도 저장되지만 config 파일 디스크에는 남기지 않음.)
- 설정 변경(`obs_captions_filter_update`) 시 config 재생성 + 브리지 재시작(기존 restart 로직 재사용).

### 4.4 IpcBridge.Config 확장
- 현재 `cfg.spawn.argv = {exe, "ipc-sidecar", "--config", path}`. 여기에 `cfg.spawn.env`(키=값 목록) 주입 경로 추가(`ipc-transport` spawn 시 환경 합성). 이미 env 지원이 없으면 추가.

### 4.5 locale
- `data/locale/en-US.ini`에 신규 키 라벨 추가(engine, model, language, api_key, advanced 등). ko-KR도 선택적으로.

---

## 5. 데이터 플로우

```
[A] GUI:  사용자 편집 → config.toml/.env 저장 → Start → subprocess `obs-captions run --sink` → 로그 스트림
[B] 플러그인: OBS 속성 편집 → 필터가 config 생성 + env 키 주입 → `obs-captions ipc-sidecar --config` 스폰
             → 소스 오디오(16kHz) IPC 전송 → 사이드카 STT → 캡션 이벤트 → 타깃 Text 소스 갱신
```

---

## 6. 에러 처리
- GUI: 잘못된 값은 저장 전 검증(Pydantic 재사용). 서브프로세스 비정상 종료 시 상태 "Stopped(exit N)" + 로그 노출. exe 경로/포트 충돌 등은 로그로.
- 플러그인: engine=local인데 클라우드 키 없음 등은 사이드카가 기존 `check`/로그로 처리. sidecar_exe 비었으면 오디오 통과(현행 유지) + 경고 로그.
- 키 미설정 클라우드 엔진 선택 시 사이드카가 명확한 오류 로그(기존 동작).

---

## 7. 테스트
- A(Python): `config_io` 라운드트립(load→edit→save→reload 동치), `settings_schema` 완전성(모든 config 필드 커버), `runner`의 subprocess 조립/종료(모킹). Tkinter 위젯 자체는 헤드리스 스모크(생성/파괴)만.
- B(C++): 속성 빌드가 크래시 없이 구성되는지, engine 콜백의 visible 토글 로직(순수 함수로 분리해 유닛 테스트), config 생성기(속성→toml 문자열) 유닛 테스트. 기존 네이티브 테스트 스위트 유지.
- 회귀: 인자 있는 CLI 경로가 기존과 동일 동작(기존 `test_cli.py` 유지/확장).

---

## 8. 분해 / 단계 (독립 서브프로젝트)

1. **P0 — 공유 스키마**: `settings_schema.py` + `config_io.py`(load/save/.env). 유닛 테스트.
2. **P1 — 데스크톱 GUI**: `gui/` 모듈 + cli 진입 분기 + spec `console=False`/AttachConsole. Start/Stop 서브프로세스.
3. **P2 — 플러그인 설정 페이지**: obs_properties 확장 + engine 콜백 + config 생성기 + env 주입 + locale. libobs 필요(Windows CI 빌드 경로 존재).

각 단계는 자체 계획/구현/검증 사이클. P0가 A·B의 공통 기반.

---

## 9. 열린 리스크
- windowed PyInstaller에서 CLI 출력(AttachConsole)은 Windows에서만 검증 가능 → Windows CI 스모크로 확인.
- 플러그인 config 생성/키 env 주입은 실제 OBS 로드 환경에서만 완전 검증(OBS SDK 게이트). 유닛 레벨(순수 함수)로 최대한 커버.
- API 키 OBS 평문 저장은 사용자 승인된 트레이드오프(경고 라벨 표기).
