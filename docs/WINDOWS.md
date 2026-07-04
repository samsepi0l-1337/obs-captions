# 윈도우 exe 안내 — OBS Captions

이 문서는 윈도우에서 배포되는 `obs-captions.exe`로 **무엇을 할 수 있는지**와 **어떻게 작동하는지**를 설명한다. Python·터미널 지식이 없어도 따라올 수 있도록 쓴다.

---

## 1. 한 줄 요약

`obs-captions.exe`는 **마이크나 시스템 소리를 실시간으로 받아 자막(음성 인식 결과)을 만들고, 그 자막을 OBS 화면에 띄우는** 프로그램이다. 방송·강의·회의 화면에 실시간 한국어(또는 다국어) 자막을 올릴 때 쓴다.

---

## 2. 배포물 구조 (exe가 무엇인가)

빌드 결과물은 **onedir(폴더 통째로) 형태**다. 단일 exe 하나가 아니라 아래 폴더 전체가 프로그램이다.

```
dist\obs-captions\
├─ obs-captions.exe      ← 실행 파일 (이것을 실행)
└─ _internal\            ← 파이썬 런타임 + 의존 라이브러리 (지우면 안 됨)
```

- **파이썬을 따로 설치할 필요가 없다.** 런타임이 `_internal\`에 통째로 들어 있다.
- `obs-captions.exe`는 **콘솔(명령 프롬프트) 앱**이다. 실행하면 검은 로그 창이 뜬다 — 정상이다(음성 인식 로그·오류가 여기에 찍힌다).
- 폴더를 통째로 옮기면(예: `C:\obs-captions\`) 어디서든 동작한다. **exe만 떼어내면 실행되지 않는다.**

> 참고: 초보자용 **설정 GUI(별도 창)**도 이 exe 안에 함께 들어 있다. `obs-captions.exe gui`로 실행한다(아래 5번).

---

## 3. exe로 할 수 있는 작업 (명령어)

명령 프롬프트(cmd)나 PowerShell에서 `obs-captions.exe <명령>` 형태로 쓴다. 실제 명령은 7종(`gui`·`run`·`list-devices`·`list-loopback-devices`·`config`·`serve`·`check-engine`)이며, 아래 표에는 `run`을 sink별 변형까지 풀어 보여준다.

| 명령 | 하는 일 |
|---|---|
| `obs-captions.exe gui` | **초보자용 설정 창**을 연다(API 키·엔진·모델·줄 수·CSS를 클릭으로 설정). 가장 쉬운 시작점. |
| `obs-captions.exe run` | **실제 자막 생성 시작.** 소리 → 음성 인식 → 자막 출력. 평소 방송할 때 쓰는 핵심 명령. |
| `obs-captions.exe run --sink obs` | 자막을 **OBS 텍스트 소스**로 직접 밀어 넣는다(obs-websocket 사용). |
| `obs-captions.exe run --sink browser` | 자막을 **브라우저 오버레이 서버**로 내보낸다(OBS 브라우저 소스로 캡처). 기본값. |
| `obs-captions.exe run --sink both` | OBS 텍스트 소스 + 브라우저 오버레이 **둘 다** 출력. |
| `obs-captions.exe list-devices` | 연결된 **마이크(입력 장치) 목록**을 번호와 함께 출력. |
| `obs-captions.exe list-loopback-devices` | **시스템 소리(스피커로 나가는 소리)** 캡처용 WASAPI 루프백 장치 목록. 게임/영상 소리에 자막을 달 때 사용(윈도우 전용). |
| `obs-captions.exe config` | 지금 적용된 **설정을 확인**(API 키는 가려서 출력). |
| `obs-captions.exe serve --demo` | 실제 음성 없이 **가짜 한국어 자막**을 흘려보내 오버레이 배치·CSS를 테스트. |
| `obs-captions.exe check-engine` | 선택한 **음성 인식 엔진이 정상 준비됐는지 점검**(키·모델 확인). |

가장 흔한 사용 흐름은 **`gui`로 한 번 설정 → `run`으로 방송** 두 가지다.

---

## 4. 작동 원리 (소리가 자막이 되기까지)

```
  [소리 입력]              [음성 인식]            [자막 가공]           [출력 sink]
 마이크 또는  ──▶  STT 엔진(로컬 Whisper /  ──▶  줄 수 제한·   ──▶  ┌ browser: 웹 오버레이 서버
 시스템 루프백      OpenAI / Google 등)         글자 정리·CSS       │           → OBS 브라우저 소스로 캡처
                                                                    └ obs: obs-websocket
                                                                                → OBS 텍스트(GDI+) 소스에 직접 표시
```

1. **소리 입력** — `[audio] source`가 `mic`(마이크)면 마이크를, `loopback`(윈도우 전용)이면 스피커로 나가는 시스템 소리를 캡처한다.
2. **음성 인식(STT)** — 오디오를 엔진에 넘겨 텍스트로 바꾼다. 엔진은 로컬 Whisper(오프라인, 인터넷 불필요)부터 클라우드(OpenAI·Google 등, API 키 필요)까지 여러 종류 중 하나를 고른다.
3. **자막 가공** — `max_lines`(화면에 유지할 줄 수), 한 줄 글자 수, 글꼴·크기·색 같은 CSS 스타일을 적용한다.
4. **출력(sink)** — 위 표의 `--sink` 값에 따라 브라우저 오버레이 / OBS 텍스트 소스 / 둘 다로 내보낸다.

### 두 가지 OBS 연동 방식 차이

- **browser sink** — exe가 로컬 웹 서버(예: `http://127.0.0.1:포트`)를 띄운다. OBS에 **브라우저 소스**를 추가하고 그 주소를 넣으면, 자막이 투명 배경 위에 렌더링돼 화면에 얹힌다. CSS로 자유롭게 꾸밀 수 있다.
- **obs sink** — **obs-websocket**으로 OBS에 직접 접속해, 미리 만들어 둔 **텍스트(GDI+) 소스**의 내용을 실시간으로 바꿔 쓴다. 브라우저 소스 없이 OBS 네이티브 텍스트로 표시된다.

두 방식 모두 자막은 **본인 PC 안에서만** 처리된다. 웹 서버는 외부가 아닌 로컬(127.0.0.1)에만 바인딩된다.

---

## 5. 초보자용 설정 창 (`gui`)

터미널이 낯설다면 이것부터 쓴다.

```
obs-captions.exe gui
```

네이티브 창(윈도우에서는 WebView2 기반)이 뜨고, 탭으로 나뉜 설정을 **클릭·입력**으로 바꾼다.

- **[엔진 / 키]** — 음성 인식 엔진과 LLM 모델 선택, 프로바이더별 **API 키** 입력.
- **[자막 스타일]** — 글꼴, 글자 크기, 굵기, 색, **표시할 줄 수(max_lines)** 등 CSS 값. 미리보기로 바로 확인.
- **[오디오]** — 마이크 / 시스템 루프백 입력 선택.
- **[OBS]** — obs-websocket 접속 정보(주소·비밀번호), 텍스트 소스 이름.

설정한 값은 로컬 설정 파일(TOML)에 안전하게 저장되고(파일 권한 0600), API 키는 설정 파일이 아닌 별도 환경 파일에 보관돼 노출을 줄인다. 저장 후 `obs-captions.exe run`으로 방송을 시작하면 된다.

> **WebView2 런타임**이 필요하다. 최신 윈도우 10/11에는 기본 포함돼 있으나, 창이 안 뜨면 Microsoft "Edge WebView2 Runtime"을 한 번 설치하면 된다.

---

## 6. 처음부터 끝까지 (권장 순서)

1. `dist\obs-captions\` 폴더를 원하는 위치(예: `C:\obs-captions\`)에 복사한다.
2. `obs-captions.exe list-devices`로 마이크 번호를 확인한다(선택).
3. `obs-captions.exe gui`로 엔진·키·스타일·오디오·OBS를 설정한다.
4. `obs-captions.exe check-engine`으로 엔진 준비 상태를 점검한다.
5. `obs-captions.exe serve --demo`로 자막 위치·CSS를 미리 맞춘다(선택).
6. OBS에서 브라우저 소스(browser sink) 또는 텍스트 소스(obs sink)를 준비한다.
7. `obs-captions.exe run --sink obs`(또는 `browser`/`both`)로 실제 자막 방송을 시작한다.

---

## 7. 빌드 방법 (개발자용)

윈도우 PowerShell에서 리포지토리 루트에서 실행한다.

```powershell
.\scripts\build_windows.ps1
```

- 내부적으로 `uv sync --extra local --extra loopback --extra gui`로 런타임 의존성(로컬 엔진·루프백·GUI)을 맞추고, `pyinstaller obs_captions.spec`로 **onedir 번들**을 만든다.
- 결과: `dist\obs-captions\obs-captions.exe` (+ `_internal\`).
- 빌드 마지막에 `obs-captions.exe list-devices` 스모크 테스트로 오디오 스택이 정상 로드되는지 확인한다.
- **GPU(NVIDIA CUDA) 빌드**는 옵트인이다: 스크립트의 `--extra gpu`와 `.spec`의 GPU 블록 주석을 해제한다.
- 첫 실행 시 로컬 Whisper 모델을 HuggingFace에서 자동 내려받는다. **완전 오프라인 배포**를 원하면 모델을 미리 받아 `.spec` datas에 넣고 `[local] model`을 그 경로로 지정한다.

---

## 8. 자주 겪는 문제

| 증상 | 원인 / 해결 |
|---|---|
| exe만 복사했더니 실행 안 됨 | `_internal\` 폴더가 있어야 한다. **폴더째** 복사할 것. |
| 검은 콘솔 창이 계속 떠 있음 | 정상이다. 로그·오류 출력용 창이며, 닫으면 프로그램도 종료된다. |
| `gui` 창이 안 뜸 | WebView2 런타임 미설치. Microsoft Edge WebView2 Runtime 설치 후 재시도. |
| 자막이 OBS에 안 보임 | browser sink면 OBS 브라우저 소스 주소 확인, obs sink면 obs-websocket 접속 정보·텍스트 소스 이름 확인. |
| 시스템 소리에 자막을 달고 싶음 | `list-loopback-devices`로 장치 확인 후 `[audio] source = "loopback"` 설정(윈도우 전용). |
| API 키 오류 | `check-engine`으로 점검. `gui`의 [엔진/키] 탭에서 해당 프로바이더 키 재입력. |
