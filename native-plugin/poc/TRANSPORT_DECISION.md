# pipe_transport_poc transport decision

- Date: 2026-07-05
- Branch: feat/settings-gui
- Scope: native-plugin/poc only

## 플랫폼별 선택 메커니즘
- POSIX: A (blocking read()/write() 스レッド + 파이프 fd close + SIGTERM/SIGKILL).
- Windows: A/B 구현 완료 (A: anonymous/anonymous-like pipe + TerminateProcess, B: overlapped named pipe + CancelIoEx/CancelSynchronousIo). 
- Windows: 실행 환경별로 a/b/c 결과를 아래에 기록.

## POSIX 결과
| Case | PASS | Unblocked(ms) | Child exit | Reaped | TimedOut | Note |
| --- | --- | --- | --- | --- | --- | --- |
| case a | PASS | 7 | 0 | true | false | reader returned after transport close / peer death |
| case b | PASS | 6 | 0 | true | false | writer returned after fd close / peer death |
| case c | PASS | 5 | 0 | true | false | stop frame remained queued during teardown |

## Windows
- Status: PENDING (현재 Windows 빌드/실행 머신 미연결)
- case a/b/c: PENDING
