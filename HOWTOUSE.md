# HOWTOUSE — OBS 실시간 자막 사용 가이드

`obs-captions.exe`(사이드카 겸 GUI)와 `obs-captions.dll`(OBS 네이티브 플러그인)로 실시간 자막을 켜는 방법. Windows 기준.

> 소스 빌드/개발은 [`README.md`](./README.md), 기능 상세는 [`DOCS.md`](./DOCS.md).

---

## 1. 내려받기

릴리스 zip(`obs-captions-windows-x64.zip`)을 받아 압축을 푼다. **폴더 전체가 프로그램**이다.

```
obs-captions-windows\
├─ obs-captions\obs-captions.exe   ← 실행 파일 (GUI 겸 CLI 겸 사이드카)
│  └─ _internal\                   ← 런타임 (지우면 안 됨)
├─ obs-plugins\64bit\obs-captions.dll        ← OBS 네이티브 플러그인
├─ data\obs-plugins\obs-captions\            ← 플러그인 데이터
└─ INSTALL.txt
```

파이썬 설치 불필요. `_internal\`이 있어야 하며 exe만 떼면 실행되지 않는다.

---

## 2. 실행 방식 두 가지

- **더블클릭(인자 없이 실행) → 설정 GUI 창**이 뜬다. 콘솔 앱이 아니다.
- **명령 프롬프트에서 인자를 주면 → CLI**로 동작한다(`obs-captions.exe run` 등). 기존 스크립트/자동화 호환.

---

## 3. GUI로 사용 (권장)

`obs-captions.exe`를 더블클릭하면 탭 기반 설정 창이 열린다.

- **탭에서 설정 편집**: General(엔진·모델·언어) · Audio · Local · Output(sink·서버·오버레이) · Text · Export · OBS · API Keys.
  - **엔진/모델**: General 탭의 Engine 드롭다운(local + 클라우드 10종). `local`이면 Local 탭에서 모델 크기(tiny~large-v3)·device(auto/cpu/cuda) 조정. 클라우드면 API Keys 탭에 키 입력.
- **Save**: 현재 폴더의 `config.toml`(+ 키는 `.env`)에 저장.
- **Start**: 자막 파이프라인을 시작(내부적으로 `obs-captions run`을 자식 프로세스로 실행), 하단 로그 패널에 실시간 로그. **Stop**으로 중지.
- 출력 sink(browser/obs/both)는 Start 버튼 옆에서 선택.

자막을 OBS에 얹는 방법은 아래 5~6절(브라우저 소스 / obs-websocket / 네이티브 플러그인) 중 하나를 택한다.

---

## 4. CLI로 사용 (선택)

명령 프롬프트에서 `obs-captions.exe <명령>`:

| 명령 | 하는 일 |
|---|---|
| `run [--sink browser\|obs\|both]` | 자막 파이프라인 실행 |
| `list-devices` / `list-loopback-devices` | 마이크 / 시스템 소리 장치 목록 |
| `config` | 현재 설정 확인(키 마스킹) |
| `serve --demo` | 가짜 자막으로 오버레이 미리보기 |
| `check-engine <engine>` | 엔진·키 점검 |

---

## 5. OBS 연동 — 브라우저 / obs-websocket (사이드카 방식)

### 경로 A — Browser Source (권장)
GUI에서 sink=browser로 Start(또는 `run --sink browser`) → OBS에 **Browser** 소스 추가 → URL `http://127.0.0.1:8765/overlay.html`(포트는 `[server]`). 배경 자동 투명.

### 경로 B — obs-websocket 텍스트 소스
OBS Tools → WebSocket Server 활성화 → GUI OBS 탭(또는 `config.toml [obs]`)에 host/port/source_name, API Keys 탭에 `OBS_WS_PASSWORD` → sink=obs로 Start(또는 `run --sink obs`). 지정한 Text 소스가 실시간 갱신.

---

## 6. OBS 연동 — 네이티브 플러그인 (dll)

플러그인은 OBS **오디오 필터**다. 소스 오디오를 받아 사이드카로 자막을 만들고 지정 Text 소스에 쓴다.

1. OBS 종료 후 복사: `obs-plugins\64bit\obs-captions.dll` → `<OBS>\obs-plugins\64bit\`, `data\obs-plugins\obs-captions\` → `<OBS>\data\obs-plugins\obs-captions\`. (플러그인은 OBS 31.x용)
2. OBS 재시작 → Text(GDI+) 소스 생성(예 `LiveCaptions`).
3. 자막 대상 오디오 소스 우클릭 → **필터** → **OBS Captions (STT)** 추가.
4. 필터 속성: **Sidecar executable**(=`obs-captions.exe` 경로) · **Config path**(=`config.toml` 경로) · **Target text source**(=`LiveCaptions`).
5. 값을 넣으면 플러그인이 사이드카를 자동 실행하고 자막을 Text 소스에 표시한다.

> config.toml은 GUI(3절)로 만들어 두고, 그 경로를 플러그인 Config path에 지정하면 편하다.

---

## 7. STT 엔진 / 키

`local`(오프라인·무료, faster-whisper 자동 다운로드) 외 클라우드 엔진은 키가 필요하다: `openai`(`OPENAI_API_KEY`), `deepgram`(`DEEPGRAM_API_KEY`), `elevenlabs`, `google`(`GEMINI_API_KEY`), `xai`, `assemblyai`, `azure`(`AZURE_SPEECH_KEY`+`_REGION`), `openrouter`, `replicate`, `groq`. GUI의 API Keys 탭 또는 `.env`에 입력. 표 전체는 [README](./README.md#stt-백엔드--provider-선택).

---

## 8. 자주 겪는 문제

| 증상 | 해결 |
|---|---|
| 더블클릭해도 창이 안 뜸 | `_internal\` 폴더째 있는지 확인. 콘솔 인자 없이 실행해야 GUI |
| exe만 복사 → 실행 안 됨 | 폴더째 복사 필요 |
| (A) 브라우저 자막 없음 | Start 상태·브라우저 소스 URL·`[server] port` 확인 |
| (B) 텍스트 소스 안 바뀜 | obs-websocket 활성화·`OBS_WS_PASSWORD`·source_name 확인 |
| (플러그인) OBS에 필터 없음 | dll·data 경로·OBS 버전(31.x)·재시작 확인 |
| API 키 오류 | `check-engine <engine>` 또는 GUI API Keys 탭 확인 |
