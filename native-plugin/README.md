# obs-captions-native-plugin

This repository part is GPL-2.0-or-later licensed and contains the native OBS plugin scaffold for the **hybrid option B** path.

- 라이선스: GPL-2.0-or-later (별도 `LICENSE` 참조)
- 현재 상태: row3-6 IPC 경로가 연결되었습니다. OBS 오디오 필터가 planar float을 mono로 downmix해 `IpcBridge`로 전달하고, 사이드카 CaptionEvent를 OBS 텍스트 소스에 반영합니다.
- 남은 범위: row7 OBS SDK 실빌드/런타임 검증이 필요합니다.
- 하이브리드 아키텍처: OBS 오디오 필터 플러그인에서 PCM 오디오를 수집해 **Python 사이드카**로 전달하고, Python이 STT를 수행해 캡션을 받아 텍스트 소스를 갱신합니다.

## Windows 빌드

OBS/libobs SDK 또는 OBS 빌드 prefix를 `CMAKE_PREFIX_PATH`, `OBS_STUDIO_DIR`, `OBS_BUILD_DIR` 중 하나로 지정한 뒤 저장소 루트에서 실행합니다.

```powershell
.\scripts\build_plugin_windows.ps1
```

스크립트는 `native-plugin/` CMake 프로젝트를 configure/build하고, 결과 DLL과 `data/locale` 파일을 `dist/plugin/` 아래 OBS 플러그인 레이아웃으로 복사합니다. OBS SDK 없이 configure하면 `find_package(libobs)` 단계에서 실패하는 것이 정상입니다.

- `docs/OBS_PLUGIN_FEASIBILITY.md` : 설계/타당성 배경
- `docs/OBS_PLUGIN_IPC.md` : IPC 프로토콜과 동기 teardown 계약
