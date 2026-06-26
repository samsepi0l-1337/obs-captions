# Path A vs Path B — 비교 분석 및 추천

공유 Caption Engine(오디오 캡처 → VAD → STT → 자막 상태머신)을 두 가지 렌더링 경로로 소비할 때의 정량·정성 비교.

- **Path A (Browser Source)**: 로컬 WebSocket 서버 → HTML 오버레이 → OBS Browser Source
- **Path B (obs-websocket Text)**: obs-websocket(v5) 클라이언트 → OBS 네이티브 Text 소스 `SetInputSettings`
- **(대안 C) OBS 내장 스크립트**: `obs-script/obs_captions_script.py` — Tools→Scripts 드롭, in-process

---

## 벤치마크 측정값 (실측)

> 실행 환경: macOS Apple Silicon (local loopback), Python 3.12, n=200 캡션 갱신.
> `uv run python scripts/benchmark.py --n 200` 로 재현 가능.

### Path A — Browser Source WS 지연 (emit → WS receive)

| 지표 | 실측값 |
|------|--------|
| 샘플 수 | 200 |
| 지연 p50 | **0.14 ms** |
| 지연 p95 | **0.20 ms** |
| 지연 max | 0.68 ms |
| 지연 min | 0.09 ms |
| 서버 프로세스 CPU% 평균 (burst) | 14.2% |
| 서버 프로세스 CPU% 최대 (burst) | 73.2% |
| RSS 평균 | 60.2 MB |
| RSS 최대 | 60.3 MB |

> 지연 정의: `CaptionState.on_partial/on_final` 호출 시각 → WebSocket 클라이언트 `recv()` 수신 시각.
> 오버헤드: Hub.broadcast → asyncio 이벤트 루프 → websockets 직렬화 → 루프백 소켓.

### Path B — obs-websocket 로컬 파이프라인 지연 (emit → SetInputSettings mock 호출)

| 지표 | 실측값 |
|------|--------|
| 디바운스 윈도우 | 120 ms |
| 캡션 emission 수 | 198 |
| SetInputSettings 호출 수 | 33 (burst 코얼레싱) |
| 로컬 파이프라인 p50 | **134.93 ms** |
| 로컬 파이프라인 p95 | **136.12 ms** |
| 로컬 파이프라인 max | 136.15 ms |

> **중요 — 실측 범위**: Path B의 위 수치는 `CaptionState` 변경 → 디바운스 대기 → `SetInputSettings` 모의 호출까지의 **로컬 파이프라인만** 측정. 실제 obs-websocket 왕복(네트워크 + OBS 처리)은 포함되지 않음.
>
> **라이브 OBS 없이 측정 불가한 항목** (라이브 OBS 필요):
> - obs-websocket 왕복 지연: LAN ~100 ms, 무선/부하 시 ~500 ms–2 s (연구/문서 기반 추정)
> - Path B 종단 지연 = 로컬 파이프라인(~135 ms) + obs-websocket 왕복(~100 ms+)
> - OBS Text 소스 렌더링 지연 (프레임 단위, 일반적으로 < 1 프레임 = ~16 ms @ 60fps)

---

## 정성 비교 매트릭스

| 항목 | 측정 방식 | Path A (Browser Source) | Path B (obs-websocket Text) | 대안 C (OBS 스크립트) |
|------|-----------|-------------------------|-----------------------------|-----------------------|
| **종단 지연** | A: 실측 / B: 부분 실측+추정 | **~0.2 ms** (emit→WS) ✅ | ~135 ms (로컬) + ~100 ms+ (OBS WS) = **~235 ms+** ⚠️ | ~0.2 ms (WS) + in-process OBS API |
| **CPU (idle)** | 아키텍처 | FastAPI+uvicorn 상시 실행 | obs-websocket 클라이언트만 | 추가 프로세스 없음 |
| **CPU (burst)** | A: 실측 / B: 추정 | 평균 14.2%, 최대 73.2% | 낮음 (디바운스로 갱신 압축) | 낮음 (in-process) |
| **RAM** | A: 실측 / B: 추정 | ~60 MB (FastAPI+uvicorn) | ~20 MB 미만 (추정) | 추가 없음 (in-process) |
| **스타일 자유도** | 아키텍처 | ✅ CSS/폰트/애니메이션 무제한 | ❌ OBS Text 소스 옵션 수준 | ❌ Text 소스 수준 |
| **committed/partial 2색** | 아키텍처 | ✅ CSS class 분리 | ❌ 단색 또는 Text 소스 2개 | ❌ 제한적 |
| **사용자 설정 단계** | 아키텍처 | 2단계: 서버 실행 → URL 입력 | 3단계: obs-websocket 활성화 → 포트/PW → `--sink obs` | 1단계: 스크립트 드롭 |
| **크로스플랫폼** | 아키텍처 | ✅ (빌드 없음) | ✅ (빌드 없음) | ✅ (OBS 내장) |
| **빌드/배포/유지보수** | 아키텍처 | ✅ HTML 정적 파일 | ✅ Python 클라이언트 | ✅ 단일 스크립트 |
| **OBS 의존성** | 아키텍처 | Browser Source (내장) | obs-websocket 서버 활성화 필요 | Tools→Scripts (내장) |
| **결정성/안정성** | 아키텍처 | CEF GPU/렌더 변동 가능 | ✅ OBS 네이티브 Text 렌더 | ✅ OBS 네이티브 |
| **OBS 씬/소스 제어** | 아키텍처 | ❌ 불가 | ✅ obs-websocket으로 가능 | ✅ in-process API |

---

## 핵심 트레이드오프 요약

### Path A가 유리한 이유
1. **지연 압도적 우위**: WS 루프백 지연 p50=0.14 ms — Path B 로컬 파이프라인(135 ms)보다 1000× 빠름. 실시간 자막에서 결정적.
2. **스타일 완전 자유**: 폰트, 색상, 애니메이션, committed/partial 2색 구분 — CSS 한 줄로 변경.
3. **설정 간단**: OBS에서 URL 붙여넣기만. obs-websocket 활성화·포트·비밀번호 설정 불필요.
4. **커스텀 CSS 지원**: `[overlay] custom_css` 경로(예: 작업 폴더의 `custom.css`)로 완전 오버라이드.

### Path B가 유리한 이유
1. **네이티브 렌더링**: CEF(Browser Source)를 거치지 않아 GPU 변동 없음. OBS 네이티브 Text 소스 직접 제어.
2. **브라우저 프로세스 없음**: RSS ~20 MB 미만(추정) vs Path A ~60 MB. 저사양 시스템에서 의미 있을 수 있음.
3. **OBS 씬/소스 통합**: obs-websocket으로 자막 외 소스/씬 제어도 가능.
4. **디바운스 코얼레싱**: 200개 emission → 33회 SetInputSettings 호출 — OBS 부하 최소화.

### 대안 C (OBS 스크립트)가 유리한 이유
1. **설치 최소화**: OBS Tools→Scripts에 단일 파일 드롭. 외부 포트·서버 불필요.
2. **in-process**: OBS 내부 API 직접 접근, 추가 네트워크 왕복 없음.
3. **단점**: OBS 스크립트 스레딩 제약, 재시작 시 번거로움, 스타일 제한.

---

## 추천

### 일반 사용자 / 저지연 / 스타일 중시 → **Path A (Browser Source) 권장**

```bash
uv run python -m obs_captions run --sink browser
# OBS → Sources → Browser → URL: http://127.0.0.1:8765/overlay.html
```

- 지연 p50=0.14 ms로 실시간 자막에 최적
- 스타일을 `config.toml [overlay]` 노브 + `custom.css`로 코드 없이 커스터마이즈
- 설정 단계 최소(URL 입력만)

### 브라우저 없음 / 네이티브 렌더 선호 / OBS 통합 → **Path B (obs-websocket Text) 선택 가능**

```bash
uv run python -m obs_captions run --sink obs
# OBS → Tools → WebSocket Server Settings → 활성화, config.toml [obs] 설정
```

- 단 총 지연 = 로컬 파이프라인(~135 ms) + obs-websocket 왕복(~100 ms 이상) = **~235 ms+** 예상
- 저지연보다 네이티브 렌더·무브라우저가 우선인 경우에 적합

### 설치 최소화 / 외부 서버 없이 → **대안 C (OBS 스크립트)**

```
OBS → Tools → Scripts → obs-script/obs_captions_script.py 추가
```

- 별도 포트·서버 불필요. in-process OBS API. 스타일은 Text 소스 수준.

---

## 측정 재현

```bash
# psutil 설치 후 실행
uv sync --extra bench
uv run python scripts/benchmark.py --n 200

# 라이브 OBS 연동 시 Path B 종단 지연 실측 필요:
# OBS → obs-websocket 활성화 → uv run python -m obs_captions run --sink obs
# (라이브 OBS 없이는 로컬 파이프라인 부분만 측정 가능)
```
