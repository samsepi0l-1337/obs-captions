"""Concrete field list for the desktop GUI / OBS plugin settings schema.

Split out of :mod:`obs_captions.settings_schema` purely to keep both files
under the project's line-count limit. This module depends only on
:mod:`obs_captions.settings_types` (never on ``settings_schema``), so importing
either ``settings_fields`` or ``settings_schema`` first is safe — there is no
circular import. Import ``FIELDS`` from either module — ``settings_schema``
re-exports it for backward compatibility.
"""

from __future__ import annotations

from dataclasses import replace

from obs_captions.settings_types import ENGINES, LOCAL_MODEL_SIZES, FieldSpec

_GUI = frozenset({"gui"})
_BOTH = frozenset({"gui", "plugin"})

FIELDS: list[FieldSpec] = [
    FieldSpec("engine", "엔진(Engine)", "choice", "General", _BOTH, ENGINES,
              help="음성 인식에 사용할 엔진. local은 내 컴퓨터에서, 나머지는 해당 클라우드 서비스에서 처리합니다."),
    FieldSpec("language", "언어(Language)", "text", "General", _BOTH,
              help="인식할 음성의 언어 코드(예: ko, en, ja). 비우면 자동 감지합니다."),
    FieldSpec("audio.source", "입력 소스(Source)", "choice", "Audio", _GUI, ("mic", "loopback"),
              help="mic=마이크 입력, loopback=시스템(스피커) 소리 캡처. loopback은 Windows 전용입니다."),
    FieldSpec("audio.device", "장치(Device)", "text", "Audio", _GUI,
              help="사용할 오디오 장치 이름/번호. 비우면 기본 장치를 사용합니다."),
    FieldSpec("audio.samplerate", "샘플레이트(Sample Rate)", "int", "Audio", _GUI,
              help="초당 오디오 샘플 수(Hz). 보통 16000을 사용합니다."),
    FieldSpec("audio.channels", "채널 수(Channels)", "int", "Audio", _GUI,
              help="오디오 채널 수. 보통 1(모노)을 사용합니다."),
    FieldSpec("local.model_size", "모델 크기(Model Size)", "choice", "Local", _BOTH, LOCAL_MODEL_SIZES,
              help="클수록 정확하지만 느립니다. tiny=가장 빠름, large-v3=가장 정확."),
    FieldSpec("local.device", "연산 장치(Device)", "choice", "Local", _BOTH, ("auto", "cpu", "cuda"),
              help="auto=자동 선택, cpu=프로세서, cuda=NVIDIA GPU 사용."),
    FieldSpec("local.compute_type", "연산 정밀도(Compute Type)", "text", "Local", _GUI,
              help="연산 정밀도(예: int8, float16). 잘 모르면 비워 두세요(자동)."),
    FieldSpec("local.cpu_threads", "CPU 스레드(CPU Threads)", "int", "Local", _GUI,
              help="CPU 사용 시 병렬 처리 스레드 수. 0이면 자동입니다."),
    FieldSpec("local.partial_interval_ms", "중간 자막 간격(Partial Interval, ms)", "int", "Local", _GUI,
              help="말하는 중 임시 자막을 갱신하는 간격(밀리초). 작을수록 반응이 빠릅니다."),
    FieldSpec("local.max_buffer_s", "최대 버퍼(Maximum Buffer, s)", "float", "Local", _GUI,
              help="한 번에 모아 처리하는 오디오 최대 길이(초)."),
    FieldSpec("local.vad_threshold", "음성 감지 임계값(VAD Threshold)", "float", "Local", _GUI,
              help="말소리로 인정할 민감도(0~1). 높이면 작은 소리를 무시합니다."),
    FieldSpec("local.min_silence_ms", "최소 침묵 길이(Minimum Silence, ms)", "int", "Local", _GUI,
              help="이만큼 조용하면 한 문장이 끝난 것으로 봅니다(밀리초)."),
    FieldSpec("server.host", "서버 호스트(Server Host)", "text", "Output", _GUI,
              help="자막 오버레이 웹서버가 열릴 주소. 보통 127.0.0.1."),
    FieldSpec("server.port", "서버 포트(Server Port)", "int", "Output", _GUI,
              help="자막 오버레이 웹서버 포트 번호."),
    FieldSpec("overlay.font_family", "글꼴(Font Family)", "text", "Output", _GUI,
              help="자막에 사용할 글꼴 이름."),
    FieldSpec("overlay.font_size", "글자 크기(Font Size)", "int", "Output", _GUI,
              help="자막 글자 크기(픽셀)."),
    FieldSpec("overlay.font_weight", "글자 굵기(Font Weight)", "int", "Output", _GUI,
              help="글자 굵기(예: 400=보통, 700=굵게)."),
    FieldSpec("overlay.color", "글자 색(Text Color)", "text", "Output", _GUI,
              help="확정 자막 색상(예: #FFFFFF)."),
    FieldSpec("overlay.partial_color", "중간 자막 색(Partial Text Color)", "text", "Output", _GUI,
              help="말하는 중 임시 자막의 색상."),
    FieldSpec("overlay.background", "배경(Background)", "text", "Output", _GUI,
              help="자막 배경 색상(투명은 transparent)."),
    FieldSpec("overlay.outline_width", "외곽선 두께(Outline Width)", "int", "Output", _GUI,
              help="글자 외곽선 두께(픽셀)."),
    FieldSpec("overlay.outline_color", "외곽선 색(Outline Color)", "text", "Output", _GUI,
              help="글자 외곽선 색상."),
    FieldSpec("overlay.shadow", "그림자(Shadow)", "text", "Output", _GUI,
              help="글자 그림자 CSS 값."),
    FieldSpec(
        "overlay.position", "위치(Position)", "choice", "Output", _GUI, ("top", "middle", "bottom"),
        help="화면에서 자막이 표시될 세로 위치.",
    ),
    FieldSpec("overlay.align", "정렬(Alignment)", "choice", "Output", _GUI,
              ("left", "center", "right"), help="자막 가로 정렬."),
    FieldSpec("overlay.max_lines", "최대 줄 수(Maximum Lines)", "int", "Output", _GUI,
              help="화면에 동시에 보일 자막 최대 줄 수."),
    FieldSpec("overlay.line_height", "줄 높이(Line Height)", "float", "Output", _GUI,
              help="줄 간격 배율."),
    FieldSpec("overlay.padding", "안쪽 여백(Padding)", "int", "Output", _GUI,
              help="자막 상자 안쪽 여백(픽셀)."),
    FieldSpec("overlay.letter_spacing", "자간(Letter Spacing)", "int", "Output", _GUI,
              help="글자 사이 간격(픽셀)."),
    FieldSpec("overlay.fade_ms", "사라짐 시간(Fade Duration, ms)", "int", "Output", _GUI,
              help="자막이 사라질 때 걸리는 시간(밀리초)."),
    FieldSpec("overlay.uppercase", "대문자 변환(Uppercase)", "bool", "Output", _GUI,
              help="영문 자막을 모두 대문자로 표시합니다."),
    FieldSpec("overlay.custom_css", "사용자 CSS(Custom CSS)", "path", "Output", _GUI,
              help="오버레이에 추가할 사용자 정의 CSS 파일 경로."),
    FieldSpec("overlay.max_chars_per_line", "줄당 최대 글자수(Maximum Characters per Line)", "int",
              "Output", _GUI, help="한 줄에 넣을 최대 글자 수(넘으면 줄바꿈)."),
]

_PROVIDER_LABELS = {
    "openai": "OpenAI",
    "elevenlabs": "ElevenLabs",
    "google": "Google",
    "xai": "xAI",
    "deepgram": "Deepgram",
    "assemblyai": "AssemblyAI",
    "azure": "Azure",
    "openrouter": "OpenRouter",
    "replicate": "Replicate",
    "groq": "Groq",
}

FIELDS.extend(
    FieldSpec(
        f"providers.{provider}.model",
        f"{label} 모델(Model)",
        "text",
        "General",
        _BOTH,
        help=f"{label} 음성 인식에 사용할 모델 이름.",
        engines=(provider,),
    )
    for provider, label in _PROVIDER_LABELS.items()
)
FIELDS.extend(
    [
        FieldSpec(
            "providers.google.mode",
            "Google 모드(Mode)",
            "choice",
            "General",
            _BOTH,
            ("gemini", "speech_v2"),
            help="Google 처리 방식: gemini 또는 speech_v2.",
            engines=("google",),
        ),
        FieldSpec(
            "providers.google.location",
            "Google 지역(Location)",
            "text",
            "General",
            _BOTH,
            help="Google speech_v2 지역 엔드포인트(예: us-central1).",
            engines=("google",),
        ),
        FieldSpec(
            "providers.google.project_id",
            "Google 프로젝트 ID(Project ID)",
            "text",
            "General",
            _BOTH,
            help="Google Cloud 프로젝트 ID.",
            engines=("google",),
        ),
        FieldSpec(
            "providers.azure.region",
            "Azure 지역(Region)",
            "text",
            "General",
            _BOTH,
            help="Azure Speech 서비스 지역(예: koreacentral).",
            engines=("azure",),
        ),
        FieldSpec(
            "providers.openai.delay",
            "OpenAI 지연 모드(Delay)",
            "choice",
            "General",
            _BOTH,
            ("minimal", "low", "medium", "high", "xhigh"),
            help="OpenAI 실시간 인식의 정확도/지연 절충값.",
            engines=("openai",),
        ),
        FieldSpec(
            "providers.openai.target_language",
            "OpenAI 번역 대상 언어(Target Language)",
            "text",
            "General",
            _BOTH,
            help="OpenAI 실시간 번역 대상 언어 코드(예: en). 비우면 번역 안 함.",
            engines=("openai",),
        ),
        FieldSpec("obs.host", "OBS 호스트(Host)", "text", "OBS", _GUI,
                  help="OBS WebSocket 접속 주소. 보통 127.0.0.1."),
        FieldSpec("obs.port", "OBS 포트(Port)", "int", "OBS", _GUI,
                  help="OBS WebSocket 포트(기본 4455)."),
        FieldSpec("obs.source_name", "OBS 텍스트 소스(Text Source)", "text", "OBS", _GUI,
                  help="자막을 넣을 OBS 텍스트 소스 이름."),
        FieldSpec("obs.hotkey.enabled", "핫키 사용(Enable Hotkeys)", "bool", "OBS", _GUI,
                  help="OBS 핫키로 일시정지/지우기를 사용합니다."),
        FieldSpec("obs.hotkey.pause_input", "일시정지 입력(Pause Input)", "text", "OBS", _GUI,
                  help="일시정지에 사용할 OBS 입력 이름."),
        FieldSpec("obs.hotkey.clear_input", "지우기 입력(Clear Input)", "text", "OBS", _GUI,
                  help="자막 지우기에 사용할 OBS 입력 이름."),
        FieldSpec("text.replacements", "치환 규칙(Replacements)", "list", "Text", _BOTH,
                  help="특정 단어를 다른 단어로 바꾸는 규칙 목록(JSON)."),
        FieldSpec("text.filter_words", "필터 단어(Filtered Words)", "list", "Text", _BOTH,
                  help="가릴 단어 목록(JSON 배열)."),
        FieldSpec("text.filter_mode", "필터 방식(Filter Mode)", "choice", "Text", _BOTH,
                  ("mask", "remove"), help="mask=마스킹, remove=삭제."),
        FieldSpec("text.filter_mask", "필터 마스크(Filter Mask)", "text", "Text", _BOTH,
                  help="mask 방식일 때 대신 표시할 문자(예: ***)."),
        FieldSpec("text.suppress_blank", "빈 자막 억제(Suppress Blank)", "bool", "Text", _BOTH,
                  help="비어 있거나 의미 없는 자막을 표시하지 않습니다."),
        FieldSpec("text.suppress_regex", "억제 패턴(Suppression Patterns)", "list", "Text", _BOTH,
                  help="이 정규식과 일치하는 자막을 억제합니다(JSON 배열)."),
        FieldSpec("text.suppress_exact", "억제 문구(Suppressed Phrases)", "list", "Text", _BOTH,
                  help="정확히 일치하면 억제할 문구 목록(JSON 배열)."),
        FieldSpec("export.enabled", "내보내기 사용(Enable Export)", "bool", "Export", _GUI,
                  help="자막을 파일로 저장합니다."),
        FieldSpec("export.path", "내보내기 경로(Export Path)", "path", "Export", _GUI,
                  help="자막을 저장할 파일 경로."),
        FieldSpec("export.format", "내보내기 형식(Format)", "choice", "Export", _GUI,
                  ("txt", "srt", "vtt"), help="저장 형식: txt, srt(자막), vtt(웹 자막)."),
    ]
)

_SECRET_ENV_VARS = {
    "openai": "OPENAI_API_KEY",
    "elevenlabs": "ELEVENLABS_API_KEY",
    "google": "GEMINI_API_KEY",
    "xai": "XAI_API_KEY",
    "deepgram": "DEEPGRAM_API_KEY",
    "assemblyai": "ASSEMBLYAI_API_KEY",
    "azure": "AZURE_SPEECH_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "replicate": "REPLICATE_API_TOKEN",
    "groq": "GROQ_API_KEY",
}

FIELDS.extend(
    FieldSpec(
        f"providers.{provider}",
        f"{_PROVIDER_LABELS[provider]} API 키(API Key)",
        "secret",
        "API Keys",
        _BOTH,
        env_var=env_var,
        help=f"{_PROVIDER_LABELS[provider]} 엔진 사용에 필요한 API 키. 화면에는 가려집니다.",
        engines=(provider,),
    )
    for provider, env_var in _SECRET_ENV_VARS.items()
)
FIELDS.append(
    FieldSpec(
        "obs.obs_ws_password",
        "OBS WebSocket 비밀번호(Password)",
        "secret",
        "API Keys",
        _BOTH,
        env_var="OBS_WS_PASSWORD",
        help="OBS WebSocket 서버 비밀번호. 화면에는 가려집니다.",
    )
)

# Detail/tuning fields hidden behind the GUI/plugin "show advanced" toggle
# (design spec section A). Everything not listed stays "simple": engine,
# language, local.model_size, provider API keys + model, overlay position/
# font_size/color, obs.source_name, export enabled + format. Expressed as a key
# set (not inline ``tier=`` on every FieldSpec) to keep this file compact; the
# tier still lands on each FieldSpec instance via ``replace`` below.
_ADVANCED_KEYS: frozenset[str] = frozenset({
    "audio.source", "audio.device", "audio.samplerate", "audio.channels",
    "local.device", "local.compute_type", "local.cpu_threads",
    "local.partial_interval_ms", "local.max_buffer_s", "local.vad_threshold",
    "local.min_silence_ms", "server.host", "server.port", "overlay.font_family",
    "overlay.font_weight", "overlay.partial_color", "overlay.background",
    "overlay.outline_width", "overlay.outline_color", "overlay.shadow",
    "overlay.align", "overlay.max_lines", "overlay.line_height",
    "overlay.padding", "overlay.letter_spacing", "overlay.fade_ms",
    "overlay.uppercase", "overlay.custom_css", "overlay.max_chars_per_line",
    "providers.google.mode", "providers.google.location",
    "providers.google.project_id", "providers.azure.region",
    "providers.openai.delay", "providers.openai.target_language",
    "obs.host", "obs.port", "obs.obs_ws_password", "obs.hotkey.enabled",
    "obs.hotkey.pause_input", "obs.hotkey.clear_input", "text.replacements",
    "text.filter_words", "text.filter_mode", "text.filter_mask",
    "text.suppress_blank", "text.suppress_regex", "text.suppress_exact",
    "export.path",
})

FIELDS = [replace(f, tier="advanced") if f.key in _ADVANCED_KEYS else f for f in FIELDS]


def simple_field_keys() -> set[str]:
    """Keys of beginner-essential fields (``tier == "simple"``)."""
    return {f.key for f in FIELDS if f.tier == "simple"}


def advanced_field_keys() -> set[str]:
    """Keys of detail/tuning fields (``tier == "advanced"``)."""
    return {f.key for f in FIELDS if f.tier == "advanced"}


__all__ = ["FIELDS", "advanced_field_keys", "simple_field_keys"]
