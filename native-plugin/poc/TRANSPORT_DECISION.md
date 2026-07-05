# pipe_transport_poc transport decision

- Date: 2026-07-05
- Branch: feat/settings-gui
- Scope: native-plugin/poc only

## 플랫폼별 선택 메커니즘 (확정)
- POSIX: **A** (blocking read()/write() 스레드 + 파이프 fd close + SIGTERM/SIGKILL).
- Windows: **A** (CreatePipe 익명 파이프 + CloseHandle + TerminateProcess) — A만으로 3케이스 전부 취소 성공, 폴백 B 불필요.
- 폴백 B (overlapped named pipe + CancelIoEx) 코드는 구현·준비돼 있으나 A가 통과해 미사용.

## 게이트 판정 (§10 row5 전제)
- **PASS: POSIX + Windows 양 플랫폼 모두 a·b·c 전부 PASS** → row5(ipc-bridge) 착수 가능.
- 검증 환경: POSIX=macOS(clang++, ASan/UBSan clean, 취소 제거 변형 hang→FAIL로 판별력 확인). Windows=WinLibs MinGW-w64 g++ 16.1(`-static -Wall -Wextra`), Tailscale SSH 원격 빌드·실행.

## POSIX 결과
| Case | PASS | Unblocked(ms) | Child exit | Reaped | TimedOut | Note |
| --- | --- | --- | --- | --- | --- | --- |
| case a | PASS | 6 | 0 | true | false | reader returned after transport close / peer death |
| case b | PASS | 6 | 0 | true | false | writer returned after fd close / peer death |
| case c | PASS | 6 | 0 | true | false | stop frame remained queued during teardown |

## Windows 결과 (WinLibs MinGW-w64 g++ 16.1, Tailscale SSH)
- Status: PASS (mechanism A)
| Case | PASS | Unblocked(ms) | Child exit | Reaped | TimedOut | Note |
| --- | --- | --- | --- | --- | --- | --- |
| case a | PASS | 15 | 1 | true | false | reader blocked in ReadFile |
| case b | PASS | 15 | 1 | true | false | writer blocked in WriteFile |
| case c | PASS | 15 | 1 | true | false | stop frame remained queued during teardown |
