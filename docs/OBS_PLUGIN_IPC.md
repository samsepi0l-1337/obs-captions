# OBS 플러그인 ↔ 파이썬 사이드카 IPC 프로토콜 설계

하이브리드(옵션 B) 아키텍처에서 **C++ 네이티브 오디오 필터 플러그인**과 **파이썬 STT 사이드카 프로세스** 사이의 통신 규약을 정의한다. 현재 트리에는 row6 필터↔bridge↔caption-output 경로가 구현되어 있으며, row7 Windows OBS SDK 빌드/로드 검증은 환경 게이트로 남아 있다.

> 상위 맥락: [OBS_PLUGIN_FEASIBILITY.md](OBS_PLUGIN_FEASIBILITY.md). 플러그인 뼈대: `native-plugin/`(특히 `src/ipc-bridge.hpp`가 이 스펙의 C++ 측 구현 자리).

---

## 1. 목표와 원칙

- **파이썬 STT 자산 재사용**: 기존 `obs_captions` 파이프라인을 사이드카에서 돌린다. 기존 `STTBackend` ABC(`src/obs_captions/stt/base.py`)가 **이미 스트리밍 인터페이스**(`feed_audio(pcm16)`, `on_partial`/`on_final`, `start_stream`/`flush`/`stop_stream`)라, IPC 어댑터는 이 인터페이스에 거의 1:1로 결선된다(§8). C++는 오디오 캡처·텍스트 표시·프로세스 관리만.
- **실시간 우선**: OBS 오디오 스레드는 **절대 블로킹하지 않는다**. bounded 링버퍼가 오디오 스레드와 파이프 I/O를 완전히 분리한다. 실시간 유지를 위해 **드롭은 두 개의 bounded 큐 두 지점에서만 drop-oldest로 허용**된다 — (1) C++ SPSC 링(§6.1), (2) 사이드카 인바운드 오디오 큐(§8.3). 이 둘 외 무제한 버퍼링 금지. writer가 느린 사이드카에 블로킹돼도 audio 스레드는 무관.
- **비밀정보 격리**: API 키를 파이프로 흘리지 않는다. 사이드카가 기존 설정(TOML + env)을 `load_config`로 **직접** 로드. 플러그인은 config 경로만 넘긴다.
- **네트워크 표면 0**: 소켓/포트 없이 **자식 프로세스 stdin/stdout** 바이너리 파이프.
- **크로스플랫폼**: Windows/macOS/Linux. 로그는 **stderr**(§6.3 오염 방지 강제).

---

## 2. 전송 계층 (transport)

플러그인이 사이드카를 자식으로 spawn: `obs-captions ipc-sidecar --config <path>`. **양 플랫폼 모두 파이프(blocking) + 전용 스레드**. 논블로킹 파이프는 불필요(§6에서 오디오 스레드는 링버퍼로 이미 분리; writer/reader는 전용 스레드라 블로킹 read/write 허용). 유일한 미해결 리스크는 **종료 시 blocking 파이프 I/O의 취소 가능성**이며, 이는 §10 row4 Windows PoC로 **증명한 뒤에만** 확정한다(아래는 기본안 + 폴백; PoC 결과가 §2를 최종 확정).

### 2.1 POSIX (mac/Linux) — 확정
| 항목 | 구현 |
|---|---|
| 프로세스 | `posix_spawn`/`fork+exec` |
| 파이프 | `pipe()` 2쌍, 자식 fd 0/1에 dup2 |
| writer/reader | 자식 stdin/stdout에 blocking `write()`/`read()` 전용 스레드(완전-프레임, §6.2) |
| 종료 취소 | 파이프 fd close → blocking I/O가 EOF/EPIPE로 반환 → join. (POSIX에서 close+상대 종료로 신뢰성 있게 언블록 — **row4 PoC가 Windows와 동일한 blocked read/write + stop-큐-대기 3케이스(a·b·c)로 맥/Linux에서 검증**.) |

### 2.2 Windows — PoC로 확정할 조건부 설계
- **기본안(A)**: 익명 파이프(`CreatePipe`) + `STARTUPINFO.hStdInput/hStdOutput`, blocking `WriteFile`/`ReadFile` 전용 스레드. 종료 취소 = 자식 핸들 close + `TerminateProcess`.
- **알려진 위험**: Windows에서 **핸들 close가 이미 blocking 중인 `ReadFile`/`WriteFile`를 항상 깨우지는 않는다.** 익명 파이프는 취소 API 지원이 제한적이라, 최악의 경우 스레드가 blocking I/O에 붙잡혀 §7.1의 join이 hang될 수 있다.
- **폴백(B, PoC가 A 실패를 보이면 채택)**: **overlapped(비동기) I/O 핸들** + `CancelIoEx`(또는 blocked 스레드를 겨냥한 `CancelSynchronousIo`)로 확실히 취소. 필요 시 익명 파이프 대신 **overlapped named pipe**(고유명, `FILE_FLAG_OVERLAPPED`)로 자식 std 핸들 구성. 이 경우도 프레이밍/스레드 모델은 동일, WriteFile/ReadFile만 overlapped + 완료 대기로 바뀜.
- **degraded 종료(정상 경로 아님)**: A/B가 취소가능 join을 보장하므로 정상 동작에서는 발생하지 않는다. A·B 모두 증명 실패면 전송은 **하드 스톱**(재설계)이지 detach로 계속 가지 않는다. 예기치 못한 런타임 극단에서만 detach + **재기동 금지·필터 비활성** degraded 상태로 전이하며 수명은 `shared_ptr`로 보호 — 상세·규칙은 §7.2.

플랫폼 공통: 취소는 "핸들 close/취소 + 자식 종료 + 유한 타임아웃 join"이라는 규약. Windows의 정확한 메커니즘(A vs B)은 PoC가 결정한다(§10 row4). "취소 가능한 join"은 row5·6 착수의 협상 불가 전제(§7.2).

---

## 3. 프레이밍 (framing)

모든 메시지는 고정 16바이트 헤더 + 가변 페이로드. 정수는 **little-endian**.

```
 offset  size  field
 0       4     magic       = "OBSC"
 4       2     version     = u16 (현재 1)
 6       2     msg_type    = u16 (§3.2)
 8       4     payload_len = u32 (payload 바이트 수, 0 가능; 상한 16 MiB)
 12      4     header_crc  = u32 (앞 12바이트의 CRC32)
 16      N     payload     = msg_type별 정의
```

### 3.1 결정적 desync 정책
- 수신기는 항상 헤더 경계에서 시작한다고 가정하고 **헤더를 통짜로만 파싱**. payload 바이트를 magic으로 스캔하지 **않는다**(오디오 payload 내 우연한 `OBSC` 오탐 원천 차단).
- 다음 중 하나라도 위반 → **즉시 세션 teardown 후 재기동**(§7): magic 불일치, `header_crc` 불일치, `version` 미지원, `payload_len > 16 MiB`, `msg_type` 미지의.
- **부분 프레임**: 헤더 16바이트 또는 선언된 `payload_len`을 다 못 채운 채 EOF/에러 → 불완전 프레임 폐기 + teardown. 정상 EOF(자식 정상 종료코드)와 에러 EOF는 종료코드로 구분.

### 3.2 메시지 타입

| type | 이름 | 방향 | 페이로드 |
|---|---|---|---|
| 0x01 | HELLO | C++→Py | 버전·config 경로·오디오 포맷·epoch |
| 0x02 | READY | Py→C++ | 엔진·언어·supports_partial·epoch echo |
| 0x03 | AUDIO | C++→Py | PCM16 프레임(16k 모노) |
| 0x04 | CAPTION_PARTIAL | Py→C++ | 잠정 자막 |
| 0x05 | CAPTION_FINAL | Py→C++ | 확정 자막 |
| 0x06 | CONTROL | C++→Py | start/stop/flush/reconfigure(+seq) |
| 0x07 | STATUS | Py→C++ | 상태·에러 또는 CONTROL ACK |
| 0x08 | HEARTBEAT | 양방향 | keep-alive(빈 페이로드) |
| 0x09 | FLUSH_DONE | Py→C++ | flush 완료(대응 seq) |

---

## 4. 페이로드 스키마

### HELLO (0x01) — C++→Py
```
u16 proto_version          // = 1
u32 epoch                  // 사이드카 세션 ID(스폰마다 +1) — §7 stale 무효화
u32 sample_rate            // = 16000
u16 channels               // = 1
u16 sample_format          // 1 = int16 (pcm16)
u32 config_path_len; utf8
```

### READY (0x02) — Py→C++
```
u16 accepted_version
u32 epoch                  // HELLO epoch echo(불일치 시 C++ 폐기)
u32 engine_name_len; utf8
u32 language_len; utf8
u8  supports_partial       // 0/1 (배치형 엔진은 0)
```

### AUDIO (0x03) — C++→Py
```
u64 timestamp_ns           // OBS 오디오 타임스탬프(단조 증가)
u32 sample_count           // 샘플 수(모노)
i16 samples[sample_count]  // 16k 모노 PCM16 — 기존 STTBackend.feed_audio(pcm16)에 직결
```
채널 다운믹스·16k 리샘플·f32→i16 변환은 **C++ 측**에서 수행(대역폭↓, `feed_audio` 포맷 정합).

### CAPTION_PARTIAL (0x04) / CAPTION_FINAL (0x05) — Py→C++
```
u32 epoch                  // 이 자막을 낳은 세션 epoch(apply-time 게이트용, §7)
u64 timestamp_ns
u64 seq                    // 사이드카 단조 증가(중복/순서 판정)
u32 text_len; utf8         // max_lines·치환·정리 적용된 표시 문자열
```
- PARTIAL=현재 라인 덮어쓰기, FINAL=확정 후 다음 문장.
- **중복 FINAL 억제**: 마지막 반영 `(timestamp,text)`와 동일 FINAL 무시.
- **순서**: `seq` 역행 프레임 무시(늦은 낡은 PARTIAL이 최신 FINAL 덮지 않게).
- **dedupe/순서 상태는 per-epoch(핵심)**: `last_seq`·마지막 `(timestamp,text)` 등 중복/역행 판정 상태는 **세션 epoch에 스코프**되며 `active_epoch` 전진(재기동) 시 **리셋**된다. 사이드카는 세션마다 `seq`를 낮은 값에서 다시 시작할 수 있으므로, 전역 last_seq를 쓰면 새 세션의 정상 캡션이 전부 거부된다 — epoch별 리셋으로 낡은 epoch의 high seq는 거부하되 **새 epoch의 low seq는 정상 수용**.

### CONTROL (0x06) — C++→Py
```
u16 command                // 1=start 2=stop 3=flush 4=reconfigure
u64 seq                    // ACK 상관용
u32 arg_len; utf8          // reconfigure 새 config 경로 등
```
**완결 계약(CONTROL 종류별 단일 신호)**: `start`/`stop`/`reconfigure`는 **STATUS(ACK, 대응 seq)** 로 완결(멱등: 같은 seq 재수신은 재실행 없이 ACK만). **`flush`의 완결 신호는 STATUS ACK가 아니라 `FLUSH_DONE(대응 seq)` 하나**다(flush는 별도 ACK를 받지 않는다 — 대기자는 FLUSH_DONE만 기다림). 이로써 각 CONTROL seq는 정확히 한 종류의 완결 신호를 가지며 대기자 무한 hang이 없다. **coalesce 시 접힌 이전 seq는 즉시 완결(superseded/커버됨)하고 대표(최신) seq만 응답 대기** → 보유 미완결 대기자는 **종류당 최대 1개(O(1))**, flood/stall에도 무한 증가 없음(§6.2·§8.3 동일).

### STATUS (0x07) / FLUSH_DONE (0x09) / HEARTBEAT (0x08)
```
STATUS:     u16 code(0=ok/ack 1=engine_init_fail 2=runtime_error 3=config_error 4=fatal
                     5=superseded 6=cancelled 7=no_session) ; u64 ack_seq ; u32 msg_len; utf8
FLUSH_DONE: u64 seq
HEARTBEAT:  (빈 페이로드)
```
STATUS/FLUSH_DONE는 상태 제어용이라 텍스트 소스를 직접 변경하지 않으므로 epoch 없이 seq 상관으로 충분(대응 CONTROL의 seq가 현재 세션 것이 아니면 무시).

---

## 5. 세션 수명 (handshake ~ shutdown)

```
1. (activate/첫 오디오)  플러그인 epoch++ 후 사이드카 spawn
2. C++ ──HELLO(epoch, config_path, 16k/mono/i16)──▶ Py
3. Py: load_config → registry로 엔진 선택 → start_stream
4. Py ──READY(epoch echo, engine, lang, supports_partial)──▶ C++  (실패 시 STATUS(error))
5. C++: epoch 일치 확인 → ──CONTROL(start,seq)──▶ ──STATUS(ack,seq)──▶ C++
6. [스트리밍]  C++ ──AUDIO···──▶ Py ──CAPTION_PARTIAL/FINAL(epoch,seq)···──▶ C++
7. (deactivate/hide)  C++ ──CONTROL(flush,seq)──▶ Py ──CAPTION_FINAL──▶ ──FLUSH_DONE(seq)──▶ C++
8. (destroy)  **`filter_destroy`가 §7.1 step0~6을 동기로 완료한 뒤 반환**(취소-먼저; teardown을 별도
             스레드로 dispatch하고 즉시 반환 **금지** — §6.4 UAF 함정). CONTROL(stop)은 종료 전제가 아니라
             §7.1 step3 내에서 writer 언블록이 확인될 때만 best-effort(ACK/grace 대기 없음).
```
- HELLO/READY 타임아웃 10s 초과 → kill 후 재기동.
- **flush 순서 보장**: C++는 **FLUSH_DONE 수신 또는 타임아웃(5s)까지 stop을 보내지 않음**(flush FINAL 유실 방지).
- 버전 미호환 → STATUS(호환 안 됨) → 자막 비활성(안전 실패).

---

## 6. 스레딩 · 백프레셔 (오디오 스레드 논블로킹 보장)

핵심: **오디오 스레드는 파이프를 절대 만지지 않는다.** 파이프 I/O는 writer/reader 전용 스레드 몫. 오디오↔writer 사이는 **고정 용량 bounded 링버퍼**. 드롭은 파이프가 아니라 링버퍼에서 일어나므로 writer 블로킹 여부와 무관.

### 6.1 오디오 스레드 (`filter_audio`, OBS 소유) — 확정된 단일 링 규율
- planar float PCM을 **고정 용량·고정 슬롯 SPSC 오버라이팅 링**(생산자=오디오 스레드, 소비자=writer)에 **논블로킹 enqueue**. 슬롯은 고정 최대 크기(예 프레임당 최대 샘플수), 프레임 길이는 §11에서 튜닝.
- **단일 규율(seqlock 슬롯 + drop-oldest)** — "lock-free냐 try-lock이냐"의 모호성 제거:
  - 각 슬롯에 **버전 카운터**(짝수=안정, 홀수=기록중). 생산자는 슬롯 write 전 version++(홀수)→데이터 복사→version++(짝수).
  - 링 가득 → 생산자가 **read 커서를 전진시켜 가장 오래된 슬롯을 재사용**(정책 = **drop-oldest**, 확정). `dropped_frames++`.
  - 소비자(writer)는 슬롯 읽기 전후 version을 확인해 **읽는 중 생산자에 덮이면(홀수/불일치) 그 슬롯을 스킵**(torn read를 원자적으로 관측·폐기, drop으로 계상). 소비자는 절대 슬롯을 블로킹 잠그지 않음.
  - 생산자·소비자 모두 **wait 없음**. 오디오 스레드는 즉시 `return audio`(비변형 통과).
- 이 규율은 단일 생산자·단일 소비자 전제(§9의 오디오 스레드 1 + writer 스레드 1)에서만 성립 — 다른 스레드는 링에 접근하지 않는다.
- `dropped_frames`는 writer가 주기적(1s) `blog(LOG_WARNING, "IPC: dropped %u audio frames (sidecar slow)")`. **무언 손실 금지.**

### 6.2 writer 스레드 (C++) — 방향당 단일 직렬 출력 채널 + 완전-프레임 상태기계
**불변식(핵심): 아웃바운드 파이프는 writer 스레드 하나만 write 한다.** AUDIO뿐 아니라 CONTROL·HEARTBEAT도 **직접 write 금지** — 각각 작은 **control out-queue**(락 보호 or MPSC)에 enqueue만 하고, writer가 그 큐와 오디오 링을 **한 스레드에서 프레임 경계로 인터리브**해 순차 write. 이로써 여러 스레드의 동시 write에 의한 바이트 인터리브(torn frame → §3.1 상시 desync)를 원천 차단.
- 매 반복: (우선순위) control out-queue에 대기 프레임 있으면 그것부터, 없으면 오디오 링에서 **완전한 한 프레임**을 꺼냄(오디오 드롭 결정은 링 레벨에서만, §6.1) → (오디오면) 16k 모노 리샘플 + i16 변환 → 인코딩 버퍼.
- 그 버퍼를 **전량 write**: `while (written < total) { n = write(fd, buf+written, total-written); ... }` — 부분 write면 나머지를 **계속 write**(버리지 않음). 바이트 스트림이라 프레임 중간에 버리면 수신기가 오프레이밍되므로 절대 중도 폐기 금지.
- 회복 불가 write 에러(EPIPE 등) → **세션 teardown + 재기동**(§7). 프레임 경계에서만 중단.
- writer가 느린 사이드카에 블로킹돼도 오디오 링이 오디오 스레드를 격리하므로 무해(막힌 동안 링이 overwrite-drop). CONTROL/HEARTBEAT는 control out-queue에 쌓였다가 writer가 재개되면 순차 송신.
- **control out-queue full 정책(bounded·total·O(1) pending, 종류당 전송-outstanding ≤1)**: 큐는 고정 상한 + **coalescing**, enqueue는 **논블로킹**. **핵심 규칙: 종류당 전송돼 응답 대기 중(outstanding)인 CONTROL은 최대 1개.** outstanding이 있는 동안 도착한 동종 신규 요청은 **전송하지 않고 out-queue에서 로컬 coalesce**(접힌 것은 즉시 superseded 완결, 대표 1개만 큐 보관); outstanding이 완료(응답/취소)되면 그 대표를 전송. 따라서 **사이드카는 종류당 한 번에 1개만 수신 → 받은 seq마다 1:1 완결**(사이드카가 이미 전송된 seq를 coalesce해 대기자를 stranding시키는 일이 없다). 보유 pending = (전송-outstanding 1 + 큐 대표 1)×종류 = **O(1)**:
  - HEARTBEAT → 1개로 coalesce(완결 불필요).
  - start/stop/reconfigure → 최신만 대표 pending(ACK 대기), **접힌 이전 seq는 즉시 superseded/완결**(대표가 그 의도를 커버).
  - **flush → 여러 flush를 하나로 coalesce, 대표 flush만 전송해 FLUSH_DONE 대기; 접힌 이전 flush seq는 즉시 완결**(대표 flush가 커버하므로 "이미 flush됨"으로 로컬 완결). 나중까지 보유하지 않음 → bounded.
  - 불변식: **어떤 seq도 완결 없이 사라지지 않되, 미완결 pending은 종류당 최대 1개**(전송된 대표만 응답 대기, 접힌 것은 즉시 완결). enqueue 항상 즉시 성공(coalesce 흡수).
  - **C++ waiter가 exactly-once 완결 권위(핵심)**: 전송-outstanding waiter는 (matching 응답 STATUS/FLUSH_DONE) OR (**§5 per-seq 타임아웃**) OR (**§7.1 step2 teardown 취소**) 중 **먼저 오는 것으로 정확히 1회 완결**(epoch/멱등 가드 중복 방지). 따라서 **사이드카 reject STATUS가 courtesy-drop돼도**(§8.3 out-queue full) waiter는 타임아웃/취소로 반드시 완결 — no strand. 사이드카 방출측을 무한 bounded로 만들 필요 없음.
- **lifecycle-gated admission**: teardown 래치(§7.1 step0) 이후 도착하는 새 CONTROL은 **out-queue에 들어가지 않고 즉시 cancelled/no-session으로 동기 완결**(낡은 큐 우회·hang 방지). 재기동·종료 공통.

### 6.3 reader 스레드 (C++) + stdout 오염 방지 + apply-time epoch
- 자식 stdout에서 프레임 파싱(§3). reader는 **자기 세션의 epoch로 스탬프**됨.
- **apply-time epoch 게이트(핵심, 모든 부작용에 적용)**: reader가 유발하는 **모든 상태 부작용** — CAPTION 텍스트 소스 변경뿐 아니라 **STATUS발 degraded/restart 트리거, FLUSH_DONE 처리** 등 — 을 실행 **직전** `reader_epoch == active_epoch.load(acquire)` 확인(reader 스레드는 자기 세션 epoch로 스탬프됨; CAPTION은 `frame.epoch`와도 대조). 불일치면 **거부**. 이로써 낡은 reader가 이미 파싱한 STATUS(engine_fail/fatal 등)가 재기동 후 **새 세션의 degraded/restart를 유발하지 못한다**(캡션만이 아니라 제어성 부작용까지 보호). 파이프 격리 + epoch 게이트 **이중 방어**.
- **stdout 오염 방지(사이드카 강제)**: 시작 시 (a) 원본 stdout fd를 protocol 전용으로 잡고 `sys.stdout`(텍스트)은 stderr로 리다이렉트, 바이너리·언버퍼드 사용, (b) `logging`·`warnings`·서드파티 print 전부 stderr, (c) 시작 직후 self-check로 첫 출력이 유효 프레임임을 보장(아니면 fatal). 한 바이트 오염 시 C++는 §3.1 teardown.
- **텍스트 소스 갱신**: reader가 `obs_source_update` 호출(§9 앵커). 소스 수명은 `obs_weak_source`로 관리.

### 6.4 생산자-quiesce 배리어 (링 해제 UAF 방지 — 브리지 내부 불변식, OBS 순서 가정 금지)
링/리샘플러 해제(§7.1 step6 destroy)는 **OBS가 `filter_audio`를 멈췄다는 외부 가정에 의존하지 않고**, 브리지가 소유한 배리어로 in-flight 생산자를 직접 배수한다:
- 필터 데이터에 원자 2개: `accepting_audio`(기본 true), `producer_inflight`(카운터).
- **`filter_audio` 진입부 — 증가-먼저-검사(admission counter, TOCTOU 차단)**:
  ```
  producer_inflight.fetch_add(1, seq_cst);          // 먼저 등록
  if (!accepting_audio.load(seq_cst)) {             // 그 다음 확인
      producer_inflight.fetch_sub(1, seq_cst);
      return audio;                                 // 미승인 → 링 미접근
  }
  // 링에 raw planar float enqueue만 (리샘플은 writer 소유 — filter_audio는 리샘플러 미접근)
  producer_inflight.fetch_sub(1, seq_cst);
  return audio;
  ```
  check-then-act가 아니라 **count-then-check**라, "accepting=true 읽고 멈춘 콜백"이 존재하면 이미 inflight에 반영돼 있어 destroy가 0을 관측할 수 없다. **배리어가 보호하는 객체는 링뿐**(filter_audio가 만지는 유일한 공유 객체); 리샘플러·out-queue는 **writer 소유**라 writer join(§7.1 step4) 이후 안전 해제된다.
- **teardown destroy 분기(§7.1 step6)**: (1) `accepting_audio.store(false, seq_cst)`, (2) `producer_inflight==0` 될 때까지 **유한 타임아웃 배수 대기**, (3) 0 확인 후에만 **링** 해제(리샘플러·out-queue는 이미 writer join 후 해제됨). `store(false)`와 콜백의 `fetch_add`가 **둘 다 seq_cst**라 StoreLoad 재정렬이 닫힘 — destroy가 false를 쓴 뒤 inflight를 읽을 때, 그 전에 증가한 콜백은 반드시 카운트에 보인다.
- **타임아웃 초과**(콜백 비정상 지연) → **해제하지 않고 degraded**(shared_ptr로 상태 leak + 로그, §7.2 UAF 가드와 동일 원리) — UAF보다 경계 있는 누수.
- 이로써 "producer quiesce"는 OBS 스케줄링 가정이 아니라 **검증 가능한 브리지 불변식**이 된다.
- **`filter_destroy` 동기 완료 계약(UAF 함정 차단, 필수)**: `filter_destroy(void *data)`는 §7.1 step0~6(취소+join+quiesce+링 해제)을 **동기로 완료한 뒤에만 반환**한다 — 호출 OBS 스레드를 취소-타임아웃(~2s, row4)+join 동안 **블로킹**한다. teardown을 **별도 스레드로 dispatch하고 즉시 반환하면 금지**(반환 직후 OBS가 필터 데이터=배리어 원자+링을 free → §6.4가 막으려던 UAF 재발). 방어적으로 **정상 DESTROY 경로에서도 배리어 원자·링을 `shared_ptr`로 소유**해(§7.2 degraded뿐 아니라) 어떤 잔여 참조도 배수 완료까지 생존시킨다. UX: 필터 제거/씬 전환/종료 시 최대 ~2s 블로킹 가능(§11 기록) — 정상 사이드카는 즉시 quiesce라 실무상 순간.

---

## 7. 장애·복구 · stale 무효화

- **비정상 종료/EOF/desync**: reader 감지 → degraded 표시 → 아래 **단일·불변 teardown 순서**로 재기동(backoff initial 0.5s ×2, max 30s, jitter — 기존 ObsConfig reconnect 개념 재사용). 재기동은 HELLO부터.

### 7.1 단일·불변 teardown/재기동 순서 (이 순서만 존재 — §5도 이 순서를 따름, §6.3 게이트와 정합)
**핵심 원칙: (1) single-flight — 동시 트리거는 하나로 수렴. (2) 취소(blocking I/O 언블록)를 join보다 먼저. (3) 공유 상태 리셋은 생산자·소비자 quiesce 이후.** 그래야 wedged 사이드카에서도 hang·경합이 없다.
```
0. **single-flight + 종료 의도 래치(상태 머신)**: bridge는 라이프사이클 상태를 가진다 —
   `desired`(원자적: `RESTART` 또는 `DESTROY`; **`DESTROY`는 비가역 우선** = no_respawn 래치) +
   `teardown_owner`(CAS 소유권).
   - 트리거(reader 에러·heartbeat 타임아웃→`RESTART`, filter destroy→`DESTROY`)는 **먼저 `desired`를
     원자적으로 상향 래치**(`DESTROY`는 `RESTART`를 덮어쓰고 이후 되돌릴 수 없음). **그 다음** `teardown_owner`
     CAS 시도.
   - CAS 패자는 즉시 반환하되, **패자여도 이미 `desired`를 래치했으므로 의도는 보존**된다(특히 destroy가
     restart teardown에 CAS로 져도 `DESTROY` 래치는 남아, 소유자가 step6/7에서 이를 존중).
   → 동시 트리거가 하나의 teardown으로 수렴하면서도 **종료 의도(destroy-wins)를 잃지 않음.**
1. active_epoch++  (원자적 release) — 이 순간부터 낡은 세션의 어떤 reader-dispatch 부작용
   (캡션 apply, STATUS발 degraded/restart 등)도 §6.3 게이트에서 reader_epoch != active_epoch 로 거부.
2. 캡션 렌더 상태 리셋(표시 텍스트만: 초기화 또는 "(자막 엔진 재연결 중…)"). **링버퍼는 여기서 만지지 않음**
   (오디오 생산자·writer 소비자가 아직 살아있어 제3 액터 경합 금지 — §6.1 SPSC 불변식 유지).
   **+ 미완결 로컬 CONTROL 대기자 전부 취소 완결(재기동·종료 공통)**: 이 세션의 pending ACK/FLUSH_DONE 대기자
   (out-queue에 queued·coalesced·이미 전송돼 응답 대기 중인 모든 seq)를 **취소 신호로 동기 완결**한다. RESTART든
   **DESTROY든** 동일 적용 — 종료 경로에서 flush/reconfigure 대기자가 영구 hang하지 않는다.
3. **취소를 즉시·무조건 실행 (writer 큐·진행·stop 전달에 비의존)**: 아웃바운드 큐 drain을 **포기**하고
   **row4가 증명한 언블록 메커니즘**을 **먼저** 실행 — A: 파이프 핸들 close(→EOF/EPIPE),
   B: `CancelIoEx`/`CancelSynchronousIo` — 자식 `TerminateProcess`/SIGTERM **병행**. 이 취소가
   **가득 찬 파이프에 막힌 AUDIO write 포함** 모든 blocking read/write를 직접 반환시킨다.
   - **CONTROL(stop) 비의존**: stop은 단일 writer 큐를 거치는데 writer가 막혀 있으면 전달 불가하므로,
     teardown은 stop 송신·ACK·grace를 **기다리지 않는다.** tail flush는 §5 step7(deactivate) CONTROL(flush)에서
     이미 처리. stop은 writer 언블록 확인 시에만 best-effort, 아니면 생략.
4. 낡은 reader/writer/heartbeat 스레드 join — **3에서 언블록됐으므로 유한 시간 완료**(stop ACK·writer 진행 무의존).
   정상 경로 좀비 없음(§10 row4가 A/B로 취소가능 join 증명 전제, row5·6 하드 전제). 극단 미해제는 §7.2 degraded.
5. 자식 프로세스 reap; 3에서 미종료면 SIGKILL 후 reap.
6. **join 완료 후 `desired`를 최종 판독**(그 사이 도착한 destroy 래치까지 반영):
   - `desired == DESTROY` → **재기동하지 않음(terminal).** 리샘플러·out-queue는 writer 소유라 step4 join
     시점에 이미 해제. **링 해제** 전에만 **§6.4 생산자-quiesce 배리어** 실행(`accepting_audio=false` →
     `producer_inflight==0` 배수 대기 → 링 해제). OBS 스케줄링 가정이 아니라 브리지 배리어로 in-flight
     `filter_audio`(링 접근)를 배수하므로 UAF 없음. 배수 타임아웃 시 §6.4대로 degraded leak. 상태=INACTIVE.
   - `desired == RESTART` → 링버퍼는 **리셋하지 않고 그대로**(오디오 생산자 계속 살아 새 writer가 이어받아 소비,
     낡은 프레임 bounded라 곧 overwrite; SPSC 소비자만 교체) → step7. **단일 소비자 핸드오프 불변식**: 낡은 writer는
     step4에서 **이미 취소+join 완료**(링 read 진행 중 없음)이므로, step7의 새 writer가 **유일 소비자**로 시작 —
     두 소비자 공존·stale read cursor 불가(SPSC 불변식 유지). join(step4)이 새 writer 기동(step7)에 **선행**함이 보장.
7. (RESTART일 때만) **control out-queue를 비운다(clear)** — 낡은 세션의 미전송 CONTROL/HEARTBEAT
   (flush/stop/reconfigure 등)가 새 사이드카로 전달되지 않도록(§4의 CONTROL/STATUS/FLUSH_DONE는
   epoch 미탑재이므로, 낡은 제어 프레임은 **큐 clear로 폐기**하는 것이 stale 차단 경로). **큐 clear 시 해당
   세션의 미완결 로컬 대기자(FLUSH_DONE/ACK 대기)를 취소 신호로 즉시 해제**(무한 대기 방지 — 어차피 새 세션의
   완결은 epoch 게이트로 낡은 대기와 무관). 그 후 소유권 전이를
   **원자적으로**: `desired`가 여전히 `RESTART`임을 재확인하며 `teardown_owner` 해제하고 새 세션 진입(같은
   소유 전이 안에서). 재확인/clear 도중 destroy가 래치되면 respawn 대신 terminal(INACTIVE) — **clear-과-respawn
   사이 틈에서도 destroy가 이긴다.**
```
재기동·정상 종료(destroy·§5) 양쪽에 동일 적용. single-flight+**destroy-wins 래치**(0)·epoch 무효화(1)·취소-먼저(3)·quiesce 후 링 처리(6)·terminal 전이(6·7)로 line-order·hang·경합·respawn-after-destroy 모순 없음.

- **중복/역순**: §4의 `(timestamp,text)` 억제 + `seq` 역행 무시로 재기동 경계 중복/역순 방지.
- **heartbeat**: 양방향 5s 주기, 2~3회 미응답 → 위 teardown 순서로 재기동.
- 모든 degraded/재기동/드롭은 OBS 로그로.

### 7.2 Windows 취소 결정 게이트와 단일 종료 모델
§7.1의 4번(스레드 join)은 blocking 파이프 I/O가 취소돼야 완료된다. Windows에서 이 취소 메커니즘은 **미확정 리스크**이므로 §10 row4 PoC로 **먼저 결정**하며, 결과에 따라 종료 모델이 하나로 확정된다:

- **A 성공**(익명 파이프 + close/TerminateProcess가 **3케이스 a·b·c**(§10 row4: blocked read, blocked full-pipe write, blocked-write+stop-큐-대기)를 유한 타임아웃 내 언블록) → 전송=A 확정. **정상 종료 모델 = join(§7.1), 좀비 없음, 재기동 안전.**
- **A 실패 → B 재검증**(overlapped I/O + `CancelIoEx`/`CancelSynchronousIo`, 필요 시 overlapped named pipe). B가 **3케이스 a·b·c**를 통과 → 전송=B 확정. **종료 모델 동일(join, 좀비 없음, 재기동 안전).**
- **A·B 모두 실패** → 네이티브 전송이 이 설계로 **성립 불가**. 이는 detach로 우회하고 계속 가는 상황이 아니라 **하드 스톱**: 전송 설계를 재검토(다른 취소 가능한 IPC)해야 하며, row5·6은 착수하지 않는다. (즉 "취소 가능한 join"은 협상 불가한 전제.)

**detach는 정상 respawn 경로가 아니다.** 위 A/B 게이트가 취소가능 join을 보장하므로 런타임 정상 동작에서 detach는 발생하지 않는다. detach는 오직 **예기치 못한 런타임 극단**(A/B로 증명됐음에도 실기기에서 스레드가 안 풀리는 경우)의 **degraded 종료 상태**로만 규정하고, 그 상태의 수명·소유·재기동 규칙을 다음으로 못박는다:
- degraded 상태 진입 시 **재기동하지 않는다**(no-respawn): 막힌 detach 스레드 위에 새 epoch/세션을 쌓지 않음. 필터를 **비활성**하고 사용자에게 오류 표기 + OBS 로그.
- **UAF 가드**: 공유 상태(링·`active_epoch`·control queue)는 **`shared_ptr`로 소유**해 detach된 스레드가 참조를 놓을 때까지 살아있게 한다(스레드 종료 시 자연 해제). no-respawn이므로 새 세션과의 경합도 없음.
- 이 degraded 경로는 row5 테스트에 **명시적 상태 전이 테스트**로 포함(진입 조건·필터 비활성·no-respawn·shared_ptr 수명).

**전송 결정 산출물(handoff)**: row4 PoC는 선택된 메커니즘을 `native-plugin/poc/TRANSPORT_DECISION.md`로 **디스크에 고정**한다. 이 파일은 **각 대상 플랫폼(맥/Linux·Windows)에서 3케이스 a·b·c 각각의 PASS/FAIL·타임아웃 수치·종료코드·로그를 기록해야 유효**하며, **a·b·c 모두 PASS가 아니면 무효**(PoC 실행 파일도 케이스 c 누락/실패 시 전체 실패로 종료). row5의 ipc-bridge는 이 파일이 존재하고 A/B 중 하나가 3케이스 전부 PASS로 확정됐음을 전제로만 착수한다.

---

## 8. 파이썬 사이드카 인터페이스 (재사용 — 기존 ABC에 결선)

신규 CLI `obs-captions ipc-sidecar --config <path>`. 기존 스트리밍 인터페이스에 결선하므로 재사용이 **증명됨**(자산 확인 완료):

### 8.1 기존 인터페이스 매핑 (`src/obs_captions/stt/base.py::STTBackend`)
| IPC | 기존 API | 비고 |
|---|---|---|
| AUDIO(pcm16) 수신 | `await backend.feed_audio(pcm16: bytes)` | AUDIO 포맷을 i16으로 정한 이유 — 직결 |
| 세션 시작 | `await backend.start_stream()` | HELLO 후 |
| CAPTION_PARTIAL | `on_partial(Transcript)` 콜백 | `Transcript.is_final==False` |
| CAPTION_FINAL | `on_final(Transcript)` 콜백 | `Transcript.is_final==True` |
| CONTROL flush | `await backend.flush()` → FLUSH_DONE | 버퍼 강제 전사 |
| CONTROL stop | `await backend.stop_stream()` | 종료 |
| 엔진 선택 | `src/obs_captions/stt/registry.py` | config의 engine |
| 텍스트 가공 | 기존 `text.py`/`obs_display.py` | max_lines·치환 |

- 신규 코드는 **프레이밍 코덱 + stdin/stdout 어댑터 + 콜백→CAPTION 프레임 브리지**뿐. STT·텍스트 로직은 그대로.

### 8.2 백엔드별 특성 (READY.supports_partial로 통지)
- 스트리밍 백엔드(`stt/google_speech_v2.py`, `stt/openai_realtime.py`, `stt/deepgram.py`, `stt/elevenlabs_realtime.py`, `stt/assemblyai.py` 등): 네이티브 부분 전사 → `supports_partial=1`.
- 로컬(`stt/local_whisper.py` + `src/obs_captions/vad.py` + `stt/streaming.py`의 local_agreement): 청크+VAD, 부분 전사 지원 → 1.
- 배치/파일 지향 엔진이 있으면(예: 일부 REST): VAD로 발화 구간 잘라 세그먼트 final만 → `supports_partial=0`, 폴백 로깅.
- 타임스탬프: `Transcript`가 시점을 보존하면 그대로, 아니면 어댑터가 AUDIO 프레임 `timestamp_ns`로 근사. **단위 변환 명시**: `Transcript.start_ms/end_ms`(ms) → CAPTION `timestamp_ns`(ns)는 어댑터가 `×1e6`로 변환.
- **reconfigure = 세션 재시작**(핵심): `STTBackend` ABC에 live reconfigure 메서드가 없으므로, CONTROL(reconfigure)는 **stop_stream → 새 config로 start_stream(새 epoch)** 으로 구현한다(§7.1 RESTART 경로 재사용). 즉 reconfigure는 별도 메커니즘이 아니라 새 config를 실은 restart이며, epoch 무효화·stale 방어가 그대로 적용된다.

### 8.3 사이드카 동시성 모델 (C++ 단일-writer의 파이썬 측 대칭)
기존 백엔드는 `asyncio.run` 기반이고 콜백은 동기 `Callable`이라, 다음 모델로 **아웃바운드 프레임 write를 단일 지점에서 직렬화**한다(§6.2의 파이썬 대칭 — 다중 스레드 동시 stdout write 금지):
- **단일 asyncio 이벤트 루프**가 사이드카의 중심. STT 백엔드 구동·on_partial/on_final·flush/stop이 모두 이 루프에서 돈다.
- **blocking stdin 리더는 전용 스레드** 하나. **loop 마샬링은 프레임당 `call_soon_threadsafe`가 아니라 bounded thread-safe 구조에 직접 기록**(loop stall 시 무한 스케줄 방지): 오디오는 **고정 용량 bounded thread-safe 큐**(drop-oldest), CONTROL은 **bounded per-kind single-slot mailbox(비-coalescing·종류당 슬롯 1)** 에 리더 스레드가 직접 put하고, **loop 측 단일 drain 태스크**가 소비. C++가 종류당 전송-outstanding ≤1로 스로틀하므로 정상적으로 종류당 슬롯 1이면 충분하다; 만약 재시도 엣지 등으로 동종 slot이 이미 admitted면 **replace하지 않고**, 리더는 신규 프레임의 완결 STATUS(superseded, seq)를 **아웃바운드 STATUS out-queue(bounded)에 best-effort enqueue**한다(실제 stdout write는 loop 단일 writer만 — 직렬화 위배 없음). **완결의 권위는 사이드카 방출이 아니라 C++ waiter 측에 있다(핵심)**: 사이드카 reject STATUS는 **courtesy 통지**이고, out-queue가 (stall+flood로) 가득 차면 추가 reject STATUS를 **drop해도 무방**(리더 블록·무한 메모리 없음). 왜냐하면 각 seq의 **exactly-once 완결은 C++ 측이 보장**하기 때문 — §6.2에서 C++는 종류당 outstanding ≤1(bounded waiters)이고, 각 waiter는 (응답 STATUS/FLUSH_DONE 수신) OR (§5 per-seq 타임아웃) OR (§7.1 step2 teardown 취소) 중 **먼저 오는 것으로 정확히 1회 완결**(epoch/멱등 가드 중복방지). 따라서 사이드카 방출측을 무한 bounded로 만들 필요가 없다(adversarial flood여도 C++ waiter는 bounded·항상 완결). 오디오는 **가득 차면 가장 오래된 것 drop**(`dropped_inbound++`, stderr 로그) — C++ 링(§6.1)과 **대칭인 두 번째 drop 지점**. CONTROL 처리는 **받은 seq를 1:1로 완결**한다(§6.2에서 C++가 종류당 전송-outstanding을 ≤1로 스로틀하므로, 사이드카는 종류당 한 번에 1개만 수신 → 수신측 coalescing 불필요·이미 전송된 seq를 접어 stranding시키는 문제 없음). §4 완결 계약 준수:
  - **HEARTBEAT**: seq 없음(완결 불필요).
  - **flush**: 수신한 flush seq를 처리 후 그 seq에 **FLUSH_DONE(seq)** 1회.
  - **reconfigure**: 적용 후 그 seq에 **STATUS(ACK, seq)**.
  - **stop**: 그 seq에 **STATUS(ACK, seq)**.
  - 사이드카가 보유하는 미완결 CONTROL은 **종류당 최대 1개(O(1))** — C++ 스로틀 덕에 flood/stall에도 무한 증가 없음. (수신 coalescing은 하지 않으므로 전송-후-폐기 대기자 stranding이 원천적으로 없음.)
  - **사이드카 lifecycle-gated admission(§6.2 C++ 측과 대칭)**: 사이드카가 `stop` 수신/백엔드 종료/no-session 상태에 들어가면, 이후 stdin 리더가 파싱한 **새 CONTROL은 mailbox에 넣지 않고** 완결 `STATUS(no_session/cancelled, 대응 seq)`를 **아웃바운드 out-queue에 best-effort enqueue**(단일 writer 방출; full이면 drop 허용) — **teardown 이후 mailbox 신규 항목 0, 보유 pending 미증가.** 여기서도 **exactly-once 완결 권위는 C++ waiter 측**: 사이드카 STATUS는 courtesy이고, stdout이 STATUS 방출 전에 닫히거나(EOF) 방출이 drop돼도 그 seq는 **C++ waiter가 (응답)OR(타임아웃)OR(§7.1 step2 취소) 중 먼저 오는 것으로 정확히 1회 완결**(epoch/멱등 가드 중복방지). "pipe-close가 곧 완결"이라는 암묵 규칙은 쓰지 않고, 완결은 항상 **C++-side가 authoritative**.
- 이로써 오디오·CONTROL **모든 큐가 bounded**(§1 불변식 준수)이면서 seq 완결 계약 유지.
- **무한 버퍼링 금지(핵심)**: 백엔드/`feed_audio`가 느려도 사이드카는 stdin을 계속 읽되(데드락 방지) **오디오를 무한 축적하지 않는다** — bounded 큐 drop-oldest로 메모리·지연 상한. 즉 실시간 불변식의 drop 지점이 C++ 링 + 사이드카 큐 **두 곳** 모두에 존재.
- **모든 stdout write(CAPTION/STATUS/FLUSH_DONE/HEARTBEAT)는 루프의 단일 write 코루틴(또는 out-queue drain 태스크)에서만** 수행 → 프레임 직렬화 보장. STT 콜백은 스레드 오프로드하지 말고 out-queue에 put만.
  - **단일 writer는 논블로킹 async I/O**(`loop.connect_write_pipe`/`add_writer` 기반) — blocking `os.write`를 full 파이프에 하면 asyncio 루프 전체가 동결되므로 금지.
  - **CAPTION out-queue도 bounded**(§1 불변식): partial은 최신만 유지(overflow 시 오래된 partial drop), **final은 보존**. C++ reader가 항상 drain하므로 실질 짧지만 정책을 명시(무제한 금지).
- **CPU 바운드 백엔드 오프로드**: `local_whisper` 등 `feed_audio`가 CPU 바운드면 asyncio 루프에서 직접 돌리지 말고 **`run_in_executor`로 오프로드**(루프 점유→heartbeat stall→불필요 재기동 방지). 콜백만 out-queue에 put.
- **상호 drain 불변식(양방향 파이프-full 데드락 방지)**: C++ reader 스레드는 항상 stdout을 drain하고, 파이썬 stdin 리더 스레드는 항상 stdin을 drain한다(가득 찬 오디오 큐면 drop-oldest로 흡수, 읽기 자체는 멈추지 않음). 어느 쪽도 "쓰기 블록 때문에 읽기를 멈추지" 않는다.

### 8.4 이 서브커맨드는 플러그인 전용(도움말 명시), 사람이 직접 실행하는 용도 아님.

---

## 9. OBS API 앵커

| 지점 | 정확한 API / 규칙 |
|---|---|
| 오디오 캡처 | `filter_audio(void*, struct obs_audio_data*)`; `audio->data[c]`(planar f32, `MAX_AV_PLANES`), `audio->frames`; **`audio->channels` 없음**(채널 수는 필터 오디오 설정에서 유도). `return audio`. |
| 리샘플 | `audio_resampler_create(&dst{16000, AUDIO_FORMAT_FLOAT_PLANAR, mono}, &src)`; **writer 스레드 소유·접근**(filter_audio는 raw만 링에 넣고 리샘플 미접근), writer join 후 `audio_resampler_destroy`. f32→i16도 writer. |
| 링버퍼 | 우리 소유 SPSC(원자 head/tail). 오디오 스레드 wait 금지. |
| 텍스트 소스 | 최초 1회 `obs_get_source_by_name`→`obs_source_get_weak_source` 보관; 갱신 시 `obs_weak_source_get_source`→`obs_source_get_settings`→`obs_data_set_string(s,"text",cap)`→`obs_source_update`→`obs_data_release`→`obs_source_release`. 승격 실패(삭제/개명)는 자막 무시+로그. |
| 스레드 소유 | 오디오=OBS; **writer 스레드=아웃바운드 파이프 유일 writer**(AUDIO는 링에서, CONTROL/HEARTBEAT는 control out-queue에서 drain — §6.2); reader 스레드=인바운드 파이프 유일 reader; heartbeat/제어 로직 스레드는 파이프에 직접 write하지 않고 out-queue에 enqueue만. 종료 시 전부 join(§7.1). |

---

## 10. 구현 순서 · repo 앵커 · 판별 테스트

세 종류의 검증 환경:
- **파이썬(맥, libobs 불필요)**: `UV_CACHE_DIR=/private/tmp/uv_cache UV_NO_SYNC=1 uv run --no-sync pytest -q`.
- **libobs-free 순수 C++(맥에서 가능)**: framing 코덱·seqlock 링·epoch 게이트·writer 직렬화 로직은 **OBS 타입·전송(파이프)에 의존하지 않는 순수 C++**라 libobs·Windows 없이 단위 테스트 가능. 하니스 `native-plugin/tests/`. 커맨드: `cmake -S native-plugin/tests -B build/plugin-tests && cmake --build build/plugin-tests && ctest --test-dir build/plugin-tests`.
- **전송/OBS-gated(Windows)**: 실제 파이프 spawn·`filter_audio` 결선·텍스트 소스 갱신 등 전송 취소 결정(row4)이나 libobs 심볼이 필요한 부분.

**게이트 규칙(명확화)**: 순수 로직(row3)은 전송·OBS와 무관해 **선착수 가능**하다. 그러나 실제 파이프를 다루는 **ipc-bridge 결선(row5)은 `native-plugin/poc/TRANSPORT_DECISION.md`(row4가 A 또는 B PASS로 확정)가 존재해야만 착수**한다 — 취소 가능한 join이 bridge teardown의 전제이기 때문. 이 분리로 "순수 로직은 맥 검증 가능"과 "전송 결선은 게이트 후행"이 서로 모순 없이 성립한다.

| # | 산출물(deliverable) | 환경 | 전제조건 | 신규/수정 파일 | 판별 테스트(red/green) |
|---|---|---|---|---|---|
| 1 | 프레이밍 코덱(파이썬) | py(맥) | 없음 | `src/obs_captions/ipc/framing.py`; `tests/test_ipc_framing.py` | 라운드트립(각 type); **STATUS code 0~7(0=ok/ack…5=superseded 6=cancelled 7=no_session) 인코딩/디코딩 왕복 정확**; header_crc 위반→teardown; `payload_len>16MiB`→거부; **payload 내 임베드 `OBSC`가 재동기 오탐 안 함**; 부분프레임/EOF 폐기; version 미스매치 |
| 2 | 사이드카 CLI | py(맥) | row1 | `src/obs_captions/cli.py` + `src/obs_captions/ipc/sidecar.py`; `tests/test_ipc_sidecar.py` | fake 오디오→자막 방출; **stdout 오염 self-check**(라이브러리 print가 stderr로); flush→FLUSH_DONE; reconfigure ACK 멱등(같은 seq 재수신); 배치엔진 세그먼트 폴백; **stalled backend backpressure**(feed_audio가 멈춰도 stdin 계속 읽되 bounded 큐 depth·메모리 상한 유지, drop-oldest·dropped_inbound 로그 — 무한 축적 없음, §8.3); **사이드카 CONTROL 완결(비-coalescing single-slot·단일 writer 경유)**(입력 계약: 종류당 ≤1 outstanding으로 스로틀된 전송 CONTROL만 주입 — C++ 스로틀은 row3가 검증; 여기선 **파싱된 각 CONTROL seq가 정확히 1회 완결**: admit되어 나중 ACK/FLUSH_DONE, 또는 collision/lifecycle-gate면 완결 STATUS를 **아웃바운드 out-queue에 등록해 단일 writer가 방출**(리더 직접 stdout write 금지). **stalled-loop 재현**: 이벤트 루프 정지 중 duplicate/post-teardown CONTROL flood 파싱 → 사이드카 reject STATUS는 bounded out-queue에 best-effort 등록(리더 동시 stdout write 없음)·**full이면 courtesy-drop(리더 블록·무한 메모리 없음)**. 사이드카 방출측은 bounded 유지만 검증하고, **exactly-once 완결은 C++-side(row3/row5)가 담당**(응답 OR 타임아웃 OR teardown 취소) — 사이드카 STATUS 유실에도 waiter no-strand. row2는 "리더 blocking·무한 메모리 없음 + admitted seq는 단일 writer로 방출"만 단언); **사이드카 lifecycle-gated admission**(사이드카 teardown/stop/no-session 시작 후 도착한 start/stop/flush/reconfigure는 **mailbox 미진입**(size 0 유지)·**정확한 STATUS 코드(6=cancelled/7=no_session)로 seq당 1회 완결**·pending 미증가, 종료 중 새 세션 상태 미생성); **stalled-loop ingress bound**(이벤트 루프 정지 중 stdin으로 오디오/CONTROL flood → 리더가 bounded thread-safe 구조(drop-oldest/coalesce)에만 쌓아 pre-loop 메모리 상한 유지, call_soon 무한 축적 없음) |
| 3 | **C++ 순수 코덱/로직**(전송·OBS 무관) | libobs-free 순수 C++(맥) | **없음**(선착수 가능) | `native-plugin/src/{framing,ring,epoch_gate,out_queue,quiesce}.{hpp,cpp}`(순수 원자/로직, OBS/파이프 심볼 미포함); `native-plugin/tests/{framing_test,ring_test,writer_serialize_test,epoch_gate_test,quiesce_test}.cpp` + `native-plugin/tests/CMakeLists.txt` | **링 overflow=생산자 논블로킹**; **seqlock torn-read 무결성**(읽는 중 overwrite→version 불일치 슬롯 스킵); **drop-oldest 정책**; **단일-writer 직렬화**(AUDIO+CONTROL/HEARTBEAT 동시 enqueue해도 프레임 인터리브 없음); **완전-프레임 인코딩/부분-write 재개**; **epoch 게이트**(active_epoch++ 후 낡은 이벤트 거부); 중복 FINAL 억제; `seq` 역행 무시; **epoch-스코프 dedupe/순서**(active_epoch 전진 시 last_seq·중복상태 리셋 → **낡은 epoch high seq 거부하되 새 epoch low seq 수용**, 전역 last_seq면 실패); **control out_queue O(1) bounded 완결 + 전송-outstanding ≤1**(start/stop/flush/reconfigure 무한 flood + stalled writer에도 **종류당 전송-outstanding ≤1**·outstanding 완료 전 동종 미전송·큐 대표 1개만 보관 → 보유 pending 종류당 O(1); 전송 전 접힌 seq는 즉시 superseded 완결, **전송된 seq는 사이드카가 1:1 완결(전송-후 coalesce로 stranding 없음)**, 어떤 seq도 완결 없이 사라지지 않음); **C++ waiter exactly-once 권위**(각 outstanding waiter가 응답 STATUS/FLUSH_DONE OR per-seq 타임아웃 OR teardown 취소 중 먼저 오는 것으로 **정확히 1회 완결** — **사이드카 STATUS 유실/지연/EOF를 주입해도 no-strand·no-double**, epoch/멱등 가드); **lifecycle-gated admission**(teardown 래치 후 새 CONTROL은 큐 미진입·즉시 cancelled/no-session 완결); **§6.4 quiesce admission(count-then-check)**: 결정적 스케줄로 콜백이 `producer_inflight++` 직후·`accepting_audio` 검사 전(및 링 접근 전) 멈춘 상태에서 quiesce가 `accepting=false`+`inflight` 판독→**0을 관측하지 못하고 대기**, 콜백 재개·decrement 후에만 배수 완료(해제 상당). seq_cst StoreLoad 경합 재현 |
| 4 | **전송 PoC (게이트) — Windows + POSIX 양 플랫폼** | 전송-gated(맥/Linux 로컬 + Windows/Tailscale SSH) | row3 권장 | `native-plugin/poc/pipe_transport_poc.cpp`(크로스플랫폼); `native-plugin/poc/CMakeLists.txt` | 파이프 spawn + blocking read/write 왕복. **취소 독립 케이스 3개를 각 플랫폼에서 실행**: (a) reader가 read에 blocked, (b) writer가 가득 찬 파이프 write에 blocked인 상태에서 종료, (c) **writer가 AUDIO write에 blocked이고 CONTROL(stop)이 아웃바운드 큐에 대기 중인 상태**에서 teardown → 취소가 stop 전달·grace·writer 진행에 **의존하지 않고** 실행되어 join이 타임아웃 내 완료(§7.1 step3 재현). 취소가 **유한 타임아웃(2s) 내 언블록+join**하면 PASS. POSIX = fd close(→EOF/EPIPE)로 언블록(맥/Linux 로컬에서 검증). Windows = A(close+TerminateProcess) 검증, **타임아웃 초과=A 실패→폴백 B(overlapped+CancelIoEx/named pipe) 재검증. A·B 모두 실패=하드 스톱(전송 재설계).** 자식 좀비 reap. **산출물**: `native-plugin/poc/TRANSPORT_DECISION.md`(플랫폼별 선택 메커니즘, 타임아웃 수치, 종료코드, 로그). 실행: 맥 `cmake --build build/poc && ./build/poc/pipe_transport_poc`; Windows Tailscale SSH `cmake --build build/poc && .\build\poc\pipe_transport_poc.exe`. |
| 5 | **C++ ipc-bridge(전송 결선)** | 전송/OBS-gated | **(둘 다) row3 PASS(framing/ring/epoch/out_queue 순수 인터페이스 확정·ctest 통과) + `native-plugin/poc/TRANSPORT_DECISION.md`가 A 또는 B로 3케이스 a·b·c 전부 PASS 기록**(row4). ipc-bridge는 row3 인터페이스를 **소비만** 하고 그 로직을 재구현/중복/stub 금지. 두 전제 충족 전 ipc-bridge 파일 생성 금지 | `native-plugin/src/ipc-bridge.{hpp,cpp}`(spawn·파이프 I/O·writer/reader/heartbeat 스레드·backoff·teardown — row3의 framing/ring/epoch/out_queue를 링크해 사용) | 실제 spawn 왕복; heartbeat 상실→재기동; **재기동 후 낡은 epoch 캡션 apply 거부**(런타임 재현); **재기동 후 낡은 STATUS(engine_fail/fatal)가 새 세션 degraded/restart 미유발**(모든 reader-dispatch 부작용 epoch 게이트, §6.3); **취소-먼저-join teardown이 wedged 사이드카에서 hang 없음**(§7.1); **동시 teardown 트리거 single-flight 수렴 + destroy-wins 라이프사이클**(§7.1 step0/6/7): (i) 중복 실행 없음, (ii) **restart-then-destroy**·**destroy-then-restart**·**clear/respawn 도중 destroy 도착** 모든 인터리브에서 **destroy가 이겨 respawn 안 함**, (iii) destroy 후 **잔여 자식 프로세스·스레드·세션 없음** 단언, (iv) **filter destroy 시 생산자 quiesce(오디오 스레드 정지) 후에만 링 해제**, (v) **terminal destroy 중 pending flush/reconfigure 로컬 대기자가 취소로 완결**(§7.1 step2, 무한 hang 없음); **재기동 시 링 미리셋 + control out-queue clear**(낡은 queued CONTROL flush/stop/reconfigure/HEARTBEAT가 새 세션 미영향, §7.1 step7); **단일 소비자 핸드오프**(낡은 writer가 mid-read/blocked인 상태에서 teardown 취소+join 완료 후에만 새 writer가 유일 SPSC 소비자로 시작 — 두 소비자 공존·stale cursor 없음, 결정적 스케줄로 재현); degraded 상태 전이(A/B 증명됐음에도 극단 실패 시 no-respawn+필터 비활성+shared_ptr 수명); FLUSH_DONE 타임아웃 순서 |
| 6 | 필터 결선 + 자막 apply | OBS-gated(Windows); 단 epoch 거부·quiesce 배리어 로직은 순수부로 분리 검증 | row5 | `native-plugin/src/obs-captions-filter.cpp`; 자막 apply 헬퍼는 기존 `native-plugin/src/caption-output.cpp`(§9 weak-source 경계) | `filter_audio`→(§6.4 배리어)→링→writer, reader→apply(§9). **§9 실패경로 매트릭스**: (1) 소스 present→`obs_source_update` 성공, (2) 소스명 없음→무 mutation+로그, (3) 개명/삭제→weak-source 승격 실패→자막 무시+로그, (4) 콜백 중 소스 삭제→UAF 없이 처리, (5) stale epoch→update 전 거부(순수부). **§6.4 생산자-quiesce 적대적 통합 테스트**: (6) 실제 `filter_audio`에서 (6a) 콜백이 `inflight++` 직후 검사 전 멈춘 인터리빙, (6b) 콜백이 링 enqueue 임계구역 안인 인터리빙(리샘플은 writer라 무관) 각각에서 destroy step6 진입 → 배리어가 `inflight==0` 배수까지 대기, **링 해제 후 생산자 접근 0**(잘못된 로직=UAF 검출, 올바른 로직=대기 또는 타임아웃 degraded leak). 순수 admission 로직은 row3 `quiesce_test`가 이미 커버하고, row6은 실 `filter_audio` 결선에서 재확인. **(6c) async-teardown 오구현 검출**: teardown을 별도 스레드로 dispatch하고 `filter_destroy`가 즉시 반환하도록 만든 잘못된 구현에서 종료 시 UAF(배리어 원자/링 free 후 접근)가 **검출**되어야 함(올바른 동기 구현은 무결). 결정적 스케줄 배리어. |
| 7 | Windows 빌드/로드 | OBS-gated(Windows) | row6 + obs-plugintemplate 이식 | 이식 후 `.dll` | Tailscale SSH, `.dll`→`obs-plugins/64bit`, OBS 필터 로드 확인 |

> 상태: row6은 현재 트리에 구현되어 있다(`obs-captions-filter` ↔ `ipc-bridge` ↔ `caption-output`). row7은 Windows OBS SDK/libobs가 있는 환경에서 `scripts/build_plugin_windows.ps1`로 DLL 빌드와 OBS 로드 검증이 필요하며, SDK가 없는 CI에서는 DLL 산출을 건너뛴다.

---

## 11. 열린 결정 사항

- **프레임 길이/VAD 청킹**: AUDIO 프레임(40ms 기본) vs latency↔정확도.
- **부분 전사 빈도**: PARTIAL 최소 방출 간격(깜빡임 방지).
- **다중 필터 인스턴스**: 초기엔 **필터당 1 사이드카**(격리 단순), 자원 문제 시 멀티플렉스 검토. **주의(소스 충돌)**: 두 필터 인스턴스가 `obs_get_source_by_name`로 **같은 텍스트 소스명**을 겨냥하면 서로 덮어쓴다 → 인스턴스별로 다른 대상 텍스트 소스를 쓰도록 UI에서 강제하거나, 중복 대상 감지 시 경고 로깅.
- **동봉 파이썬 런타임**: 기존 `obs_captions.spec` PyInstaller onedir 산출물 재활용.
- **`filter_destroy` 동기 블로킹 UX**: §6.4 UAF 방지를 위해 `filter_destroy`는 quiesce+join까지 동기 완료(정상 사이드카는 즉시, 최악 취소-타임아웃 ~2s). OBS가 이 콜백을 호출하는 스레드에서 다중초 블로킹이 필터 제거/씬 전환/앱 종료 지연으로 수용 가능한지 확인 필요 — 필요 시 타임아웃 단축 또는 진행 표기.
