# obs-captions-native-plugin

This repository part is GPL-2.0-or-later licensed and contains the native OBS plugin scaffold for the **hybrid option B** path.

- 라이선스: GPL-2.0-or-later (별도 `LICENSE` 참조)
- 현재 상태: 뼈대. 오디오 캡처 훅 + 텍스트 갱신 헬퍼는 존재하지만 STT/IPC는 미구현입니다.
- 하이브리드 아키텍처: OBS 오디오 필터 플러그인에서 PCM 오디오를 수집해 **Python 사이드카**로 전달하고, Python이 STT를 수행해 캡션을 받아 텍스트 소스를 갱신합니다.

## 빌드 예정

`obs-plugintemplate`를 기반으로 Windows( VS2022 ) 환경에서 빌드를 수행합니다.
`native-plugin/obs-plugintemplate`를 기준으로 `native-plugin/src/`를 붙여 올리고, CMake와 패키징 단계는 해당 템플릿 경로에서 완결합니다.

- `docs/OBS_PLUGIN_FEASIBILITY.md` : 설계/타당성 배경
