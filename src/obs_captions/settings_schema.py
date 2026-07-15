"""Presentation metadata shared by the desktop GUI and OBS plugin."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Widget = Literal["text", "choice", "int", "float", "bool", "path", "secret", "list"]


@dataclass(frozen=True)
class FieldSpec:
    """Describe how one application setting is presented."""

    key: str
    label: str
    widget: Widget
    section: str
    applies_to: frozenset[str]
    choices: tuple[str, ...] = ()
    env_var: str | None = None


ENGINES: tuple[str, ...] = (
    "local",
    "openai",
    "elevenlabs",
    "google",
    "xai",
    "deepgram",
    "assemblyai",
    "azure",
    "openrouter",
    "replicate",
    "groq",
)
LOCAL_MODEL_SIZES: tuple[str, ...] = ("tiny", "base", "small", "medium", "large-v3")

_GUI = frozenset({"gui"})
_BOTH = frozenset({"gui", "plugin"})

FIELDS: list[FieldSpec] = [
    FieldSpec("engine", "Engine", "choice", "General", _BOTH, ENGINES),
    FieldSpec("language", "Language", "text", "General", _BOTH),
    FieldSpec("audio.source", "Source", "choice", "Audio", _GUI, ("mic", "loopback")),
    FieldSpec("audio.device", "Device", "text", "Audio", _GUI),
    FieldSpec("audio.samplerate", "Sample Rate", "int", "Audio", _GUI),
    FieldSpec("audio.channels", "Channels", "int", "Audio", _GUI),
    FieldSpec("local.model_size", "Model Size", "choice", "Local", _BOTH, LOCAL_MODEL_SIZES),
    FieldSpec("local.device", "Device", "choice", "Local", _BOTH, ("auto", "cpu", "cuda")),
    FieldSpec("local.compute_type", "Compute Type", "text", "Local", _GUI),
    FieldSpec("local.cpu_threads", "CPU Threads", "int", "Local", _GUI),
    FieldSpec("local.partial_interval_ms", "Partial Interval (ms)", "int", "Local", _GUI),
    FieldSpec("local.max_buffer_s", "Maximum Buffer (s)", "float", "Local", _GUI),
    FieldSpec("local.vad_threshold", "VAD Threshold", "float", "Local", _GUI),
    FieldSpec("local.min_silence_ms", "Minimum Silence (ms)", "int", "Local", _GUI),
    FieldSpec("server.host", "Server Host", "text", "Output", _GUI),
    FieldSpec("server.port", "Server Port", "int", "Output", _GUI),
    FieldSpec("overlay.font_family", "Font Family", "text", "Output", _GUI),
    FieldSpec("overlay.font_size", "Font Size", "int", "Output", _GUI),
    FieldSpec("overlay.font_weight", "Font Weight", "int", "Output", _GUI),
    FieldSpec("overlay.color", "Text Color", "text", "Output", _GUI),
    FieldSpec("overlay.partial_color", "Partial Text Color", "text", "Output", _GUI),
    FieldSpec("overlay.background", "Background", "text", "Output", _GUI),
    FieldSpec("overlay.outline_width", "Outline Width", "int", "Output", _GUI),
    FieldSpec("overlay.outline_color", "Outline Color", "text", "Output", _GUI),
    FieldSpec("overlay.shadow", "Shadow", "text", "Output", _GUI),
    FieldSpec(
        "overlay.position", "Position", "choice", "Output", _GUI, ("top", "middle", "bottom")
    ),
    FieldSpec("overlay.align", "Alignment", "choice", "Output", _GUI, ("left", "center", "right")),
    FieldSpec("overlay.max_lines", "Maximum Lines", "int", "Output", _GUI),
    FieldSpec("overlay.line_height", "Line Height", "float", "Output", _GUI),
    FieldSpec("overlay.padding", "Padding", "int", "Output", _GUI),
    FieldSpec("overlay.letter_spacing", "Letter Spacing", "int", "Output", _GUI),
    FieldSpec("overlay.fade_ms", "Fade Duration (ms)", "int", "Output", _GUI),
    FieldSpec("overlay.uppercase", "Uppercase", "bool", "Output", _GUI),
    FieldSpec("overlay.custom_css", "Custom CSS", "path", "Output", _GUI),
    FieldSpec("overlay.max_chars_per_line", "Maximum Characters per Line", "int", "Output", _GUI),
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
    FieldSpec(f"providers.{provider}.model", f"{label} Model", "text", "General", _BOTH)
    for provider, label in _PROVIDER_LABELS.items()
)
FIELDS.extend(
    [
        FieldSpec(
            "providers.google.mode",
            "Google Mode",
            "choice",
            "General",
            _BOTH,
            ("gemini", "speech_v2"),
        ),
        FieldSpec("providers.google.location", "Google Location", "text", "General", _BOTH),
        FieldSpec("providers.google.project_id", "Google Project ID", "text", "General", _BOTH),
        FieldSpec("providers.azure.region", "Azure Region", "text", "General", _BOTH),
        FieldSpec(
            "providers.openai.delay",
            "OpenAI Delay",
            "choice",
            "General",
            _BOTH,
            ("minimal", "low", "medium", "high", "xhigh"),
        ),
        FieldSpec(
            "providers.openai.target_language",
            "OpenAI Target Language",
            "text",
            "General",
            _BOTH,
        ),
        FieldSpec("obs.host", "OBS Host", "text", "OBS", _GUI),
        FieldSpec("obs.port", "OBS Port", "int", "OBS", _GUI),
        FieldSpec("obs.source_name", "OBS Text Source", "text", "OBS", _GUI),
        FieldSpec("obs.hotkey.enabled", "Enable Hotkeys", "bool", "OBS", _GUI),
        FieldSpec("obs.hotkey.pause_input", "Pause Input", "text", "OBS", _GUI),
        FieldSpec("obs.hotkey.clear_input", "Clear Input", "text", "OBS", _GUI),
        FieldSpec("text.replacements", "Replacements", "list", "Text", _BOTH),
        FieldSpec("text.filter_words", "Filtered Words", "list", "Text", _BOTH),
        FieldSpec("text.filter_mode", "Filter Mode", "choice", "Text", _BOTH, ("mask", "remove")),
        FieldSpec("text.filter_mask", "Filter Mask", "text", "Text", _BOTH),
        FieldSpec("text.suppress_blank", "Suppress Blank", "bool", "Text", _BOTH),
        FieldSpec("text.suppress_regex", "Suppression Patterns", "list", "Text", _BOTH),
        FieldSpec("text.suppress_exact", "Suppressed Phrases", "list", "Text", _BOTH),
        FieldSpec("export.enabled", "Enable Export", "bool", "Export", _GUI),
        FieldSpec("export.path", "Export Path", "path", "Export", _GUI),
        FieldSpec("export.format", "Export Format", "choice", "Export", _GUI, ("txt", "srt", "vtt")),
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
        f"{_PROVIDER_LABELS[provider]} API Key",
        "secret",
        "API Keys",
        _BOTH,
        env_var=env_var,
    )
    for provider, env_var in _SECRET_ENV_VARS.items()
)
FIELDS.append(
    FieldSpec(
        "obs.obs_ws_password",
        "OBS WebSocket Password",
        "secret",
        "API Keys",
        _BOTH,
        env_var="OBS_WS_PASSWORD",
    )
)

__all__ = ["ENGINES", "FIELDS", "LOCAL_MODEL_SIZES", "FieldSpec"]
