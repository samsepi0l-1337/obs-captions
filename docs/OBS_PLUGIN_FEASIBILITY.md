# OBS 네이티브 플러그인화 — 실현 가능성 · 설계안

이 문서는 현재의 파이썬 기반 실시간 STT 자막 프로그램(`obs-captions`)을 **OBS에 직접 로드되는 네이티브 플러그인(.dll/.so 모듈)**으로 만들 수 있는지, 만든다면 어떻게 설계해야 하는지를 정리한다. 코드 구현 전 단계의 조사·설계 문서다.

> 조사 근거는 OBS 공식 문서(docs.obsproject.com, 32.2.0 기준 2026-07), `obsproject/obs-plugintemplate`, `royshil/obs-localvocal`, `ggml-org/whisper.cpp` 등 공개 자료. 각 절 끝에 근거를 명시한다.

---

## 1. 질문 재정의 — 지금 상태 vs "플러그인"

현재 프로젝트는 **OBS를 바깥에서 제어하는 독립 실행 프로그램(exe)**이다:
- **browser sink** — 로컬 웹 오버레이 서버를 띄우고 OBS 브라우저 소스로 캡처
- **obs sink** — obs-websocket v5(`simpleobsws`)로 OBS의 텍스트 소스를 원격 갱신

"OBS 플러그인"은 이와 다르게 **OBS 프로세스 안에 로드되는 네이티브 모듈**을 뜻한다. OBS 실행 시 `obs-plugins/64bit\*.dll`이 자동 로드되고, 별도 프로그램을 켤 필요가 없다. **이 방식은 아직 만들지 않았다.**

---

## 2. 실현 가능성 결론

**가능하다. 그리고 직접적인 선례가 이미 존재한다.**

`royshil/obs-localvocal`(GPL-2.0)은 우리 목표와 거의 동일한 실전 검증된 네이티브 OBS 플러그인이다:
- **오디오 필터**로 등록되어 오디오 소스에 얹힌다
- **whisper.cpp를 임베드**해 온디바이스 실시간 자막 + 번역
- 출력은 (1) OBS 텍스트 소스 갱신, (2) `.txt/.srt` 파일, (3) RTMP CEA-608 캡션 송출
- Windows/macOS/Linux 크로스 빌드

즉 "OBS 안에서 실시간 음성→자막"이라는 목표는 기술적으로 성립하며, 참조할 설계도가 있다. **관건은 '가능한가'가 아니라 '우리 파이썬 STT 자산(클라우드 엔진·12개 백엔드·설정 GUI·CSS 스타일링)을 얼마나 살릴 것인가'다.**

> 근거: [royshil/obs-localvocal](https://github.com/royshil/obs-localvocal), [obsproject/obs-plugintemplate](https://github.com/obsproject/obs-plugintemplate)

---

## 3. 반드시 알아야 할 3가지 제약

### 제약 1 — 플러그인 언어는 C/C++ (파이썬 불가)
OBS 네이티브 플러그인은 **C/C++ + CMake**로 작성한다. 공식 `obs-plugintemplate`가 표준 스캐폴딩(Win: VS 2022 / macOS: XCode 16 / Linux: Ninja, GitHub Actions 3플랫폼 CI 포함).
→ 현재 파이썬 코드를 **그대로** 플러그인으로 만들 수는 없다.

### 제약 2 — OBS 내장 파이썬 스크립팅으로는 안 된다
OBS에는 Tools > Scripts로 파이썬 스크립트를 돌리는 기능이 있으나:
- **소스/필터 등록은 Lua 전용** — 파이썬으로는 커스텀 필터를 만들 수 없다("Script Sources (Lua Only)")
- `obs_add_raw_audio_callback()` 같은 raw 오디오 콜백이 파이썬 API에 공식 노출되지 않음(ctypes FFI 편법은 유지보수·버전 호환 리스크)
- 실제 용도는 프론트엔드 자동화/UI 제어 위주
→ "OBS 내장 파이썬 스크립팅으로 재사용" 경로는 **사실상 막혀 있다.**

### 제약 3 — faster-whisper는 네이티브 임베드 불가, whisper.cpp만 가능
- **faster-whisper**는 CTranslate2의 파이썬 래퍼로 파이썬 런타임 의존이 강해 C/C++ 플러그인에 직접 링크 불가
- **whisper.cpp**(C/C++, ggml, GGUF, 스트리밍 예제 제공)가 네이티브 임베드의 사실상 유일한 실용적 선택
- 클라우드 엔진(OpenAI/Google STT)은 C++에서 HTTP로 호출 가능하나 우리 파이썬 구현을 재사용하려면 별도 경로 필요
→ "완전 네이티브"를 택하면 STT 백엔드를 whisper.cpp로 **교체**해야 하고, 파이썬 엔진들을 살리려면 **하이브리드(IPC)**가 필요하다.

> 근거: [OBS Scripting docs](https://docs.obsproject.com/scripting), [whisper.cpp](https://github.com/ggml-org/whisper.cpp), [faster-whisper](https://github.com/SYSTRAN/faster-whisper)

---

## 4. 오디오를 OBS 안에서 받는 표준 경로

플러그인이 실시간 오디오를 받는 표준은 **`OBS_SOURCE_TYPE_FILTER` + `filter_audio` 콜백**이다:
- 필터는 독립 존재 불가 — 반드시 대상 오디오 소스(마이크/데스크톱 오디오)에 얹힌다
- 필터 체인 맨 앞 필터만 원본(raw) PCM을 받는다
- 대안: 코어 API `obs_add_raw_audio_callback()`으로 소스 종속 없이 믹스된 오디오를 전역 가로채기(더 저수준)

자막을 화면에 올리는 표준 경로 두 갈래:
1. **화면 오버레이** — 내장 `text_ft2_source`(FreeType2 텍스트 소스)를 주기적으로 `update`(localvocal 방식)
2. **방송 CC 송출** — `obs_output` 캡션 API(libcaption 기반 EIA-608/708)로 RTMP 스트림에 임베드

> 근거: [Source API Reference](https://docs.obsproject.com/reference-sources), [text-freetype2.c](https://github.com/obsproject/obs-studio/blob/master/plugins/text-freetype2/text-freetype2.c), [libcaption](https://github.com/obsproject/obs-studio/tree/master/deps/libcaption)

---

## 5. 설계 옵션 비교

| | **옵션 A — 완전 네이티브 (whisper.cpp)** | **옵션 B — 하이브리드 (C++ 필터 + 파이썬 STT IPC)** |
|---|---|---|
| 구조 | C++ 필터가 whisper.cpp를 직접 임베드 | C++ 필터가 PCM을 IPC로 파이썬 STT 프로세스에 전달, 텍스트 회신 |
| 별도 프로세스 | **없음** (단일 .dll) | 파이썬 STT 프로세스 필요(플러그인이 자동 기동/관리) |
| 파이썬 자산 재사용 | ✕ (STT 전면 C++ 재작성) | ○ (faster-whisper·클라우드 엔진·텍스트 가공 로직 재사용) |
| 클라우드 엔진(OpenAI/Google) | C++로 재구현 필요 | 기존 파이썬 그대로 |
| 지연(latency) | 최저 | IPC + 프로세스 오버헤드 추가 |
| 구현 난이도 | 높음(C++ + whisper.cpp + GPU 백엔드) | 중간(얇은 C++ 셸 + IPC 프로토콜) |
| 사실상 정체 | **obs-localvocal 재구현/포크에 가까움** | 우리 프로젝트의 차별점(다중 엔진·GUI)을 살리는 유일한 길 |
| 패키징 | .dll 단독 | .dll + 파이썬 런타임 동봉 |

### 전략적 판단
- **옵션 A를 택할 거라면 obs-localvocal을 새로 만들 이유가 약하다.** localvocal이 이미 whisper.cpp 온디바이스 자막의 90%를 GPL-2.0으로 제공한다. 정말 필요하면 **localvocal을 포크/기여**하는 편이 그린필드보다 합리적이다.
- **우리 프로젝트의 고유 가치**(클라우드+로컬 12개 엔진 선택, 초보자 설정 GUI, CSS 스타일링)는 전부 **파이썬 자산**이다. 이 가치를 플러그인에서 살리려면 **옵션 B(하이브리드)**가 사실상 유일한 길이다.

---

## 6. 권장 아키텍처 (옵션 B — 하이브리드)

```
┌───────────────────────── OBS 프로세스 ─────────────────────────┐
│  마이크/데스크톱 오디오 소스                                     │
│        │                                                        │
│        ▼   filter_audio 콜백 (PCM)                              │
│  [obs-captions 오디오 필터  ← 우리가 만드는 얇은 C++ .dll]       │
│        │  ①PCM을 IPC로 송신          ▲ ③자막 텍스트 수신        │
│        │                             │                          │
│        ▼                             │  ④text_ft2_source update │
│  (named pipe / local socket)   ──────┘         ▼                │
│                                          OBS 텍스트 소스(화면)   │
└────────────────│───────────────────────────────────────────────┘
                 │ ②
                 ▼
   [파이썬 STT 사이드카 프로세스  ← 기존 코드 재사용]
     · faster-whisper / OpenAI / Google 등 엔진 선택
     · 줄 수(max_lines)·글자 정리·치환 규칙
     · 설정 GUI(pywebview)로 구성한 TOML/키 그대로 사용
```

**컴포넌트 분담**
- **C++ 플러그인(신규, 얇게)**: `obs_source_info`(FILTER) 등록 · `filter_audio`에서 PCM 캡처 · IPC 클라이언트 · 텍스트 소스 갱신 · OBS 속성 UI(엔진/줄수/스타일을 OBS 설정창에 노출) · 사이드카 프로세스 생명주기 관리(기동/종료/헬스체크)
- **파이썬 사이드카(기존 재사용)**: STT 파이프라인 · 다중 엔진 · 텍스트 가공. 현재 `run` 파이프라인을 IPC 서버 모드로 감싸면 대부분 재활용
- **IPC 프로토콜**: 로컬 전용(외부 미노출). PCM 프레임 → 텍스트(부분/최종) 스트림. Windows named pipe 또는 로컬 소켓 + 길이-프리픽스 프레이밍

**설계 시 반영할 오버헤드**: IPC 왕복 지연, 사이드카 크래시 복구, 파이썬 런타임 동봉으로 인한 배포 용량 증가.

---

## 7. 빌드 · 배포 스택

- **스캐폴딩**: `obsproject/obs-plugintemplate`를 그대로 사용(CMake, C/C++, 3플랫폼 GitHub Actions CI)
- **빌드**: Windows VS 2022 / macOS XCode 16 / Linux Ninja
- **설치 경로(Windows)**: `C:\Program Files\obs-studio\obs-plugins\64bit\obs-captions.dll` + `data\obs-plugins\obs-captions\`
- **ABI**: OBS 메이저 버전 간 재빌드가 관례 — 타깃 OBS 버전 고정 및 CI에서 대응 버전 빌드
- **라이선스 주의**: obs-plugintemplate와 obs-localvocal 모두 **GPL-2.0**. libobs에 링크하는 플러그인은 GPL 계열이 되며, localvocal 코드를 참조/차용하면 GPL-2.0 준수가 필수. 현재 프로젝트 라이선스와의 정합성을 먼저 확인해야 한다.

> 근거: [obs-plugintemplate](https://github.com/obsproject/obs-plugintemplate), [Plugins Guide](https://obsproject.com/kb/plugins-guide)

---

## 8. 작업량 · 리스크 요약

| 항목 | 옵션 A | 옵션 B |
|---|---|---|
| 신규 C++ 코드량 | 큼(STT 백엔드 포함) | 중간(필터+IPC 셸) |
| 파이썬 재사용 | 없음 | 높음 |
| 주요 리스크 | whisper.cpp GPU 백엔드/모델 관리, 사실상 localvocal 중복 | IPC 지연·프로세스 관리, 파이썬 동봉 패키징 |
| 라이선스 | GPL-2.0 | GPL-2.0(플러그인 셸), 파이썬부는 분리 프로세스라 경계 검토 필요 |
| 크로스플랫폼 | 템플릿 CI로 확보 | 동일 + 사이드카 파이썬 배포 별도 |

---

## 9. 단계별 로드맵 (옵션 B 채택 시)

1. **선례 정독** — `royshil/obs-localvocal` 소스에서 `obs_source_info` 필터 등록부 + `filter_audio` 콜백 패턴을 explore로 열람해 구조 확정
2. **라이선스 결정** — GPL-2.0 수용 여부 확정(전체 프로젝트 영향)
3. **스캐폴딩** — obs-plugintemplate로 빈 오디오 필터 플러그인 생성, Windows 로드까지 확인(로그에 등록 확인)
4. **PCM 캡처 PoC** — `filter_audio`에서 PCM을 파일/로그로 덤프해 정상 수신 검증
5. **IPC 프로토콜** — PCM↔텍스트 프레이밍 정의, 파이썬 사이드카를 IPC 서버 모드로 래핑
6. **텍스트 소스 갱신** — 수신 텍스트로 text_ft2_source 업데이트, 화면 표시 확인
7. **속성 UI** — 엔진/줄수/스타일을 OBS 필터 속성창에 노출(기존 GUI 개념 이식)
8. **사이드카 생명주기** — 플러그인이 파이썬 프로세스 기동/종료/재시작 관리
9. **패키징·CI** — 3플랫폼 빌드 + 파이썬 런타임 동봉, 설치 테스트

---

## 10. 지금 필요한 결정

이 문서는 **조사·설계까지**이며 구현은 시작하지 않았다. 다음 중 방향을 정하면 그에 맞춰 다음 단계를 진행한다:

1. **옵션 B(하이브리드)로 실제 착수** — 우리 파이썬 자산을 살리는 방향. 로드맵 1~3단계(선례 정독 + 라이선스 결정 + 빈 플러그인 스캐폴딩)부터 시작.
2. **옵션 A / localvocal 포크 검토** — 완전 온디바이스가 최우선이고 클라우드 엔진을 포기할 수 있다면, 신규 개발보다 localvocal 포크·기여가 합리적. 그 비교 조사를 먼저 수행.
3. **현행 유지** — obs-websocket 외부 연동으로 충분하다고 판단되면, 문서에 "네이티브 플러그인 아님"을 명시하고 종료.

> **핵심 요지**: 네이티브 플러그인화는 **기술적으로 가능하고 선례도 있으나**, faster-whisper·클라우드 엔진을 살리려면 C++ 필터 + 파이썬 사이드카 **하이브리드**가 되며, 완전 네이티브를 원하면 whisper.cpp로 갈아타야 하고 그건 사실상 obs-localvocal과 겹친다. **어느 쪽도 지금 완료된 상태는 아니다.**
