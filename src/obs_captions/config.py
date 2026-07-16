from __future__ import annotations

import os
import re
import tomllib
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, model_validator

from obs_captions.text import ReplacementRule as ReplacementRule  # re-export


class ProviderConfig(BaseModel):
    """Per-provider options (model name, mode, etc.)."""

    model_config = ConfigDict(extra="forbid")

    model: str | None = None
    mode: str | None = None  # google: "gemini" | "speech_v2"
    # google speech_v2: regional endpoint + GCP project (chirp requires a region,
    # "global" is invalid). project_id falls back to env GOOGLE_CLOUD_PROJECT.
    location: str | None = None
    project_id: str | None = None
    # azure: service region override (falls back to env AZURE_SPEECH_REGION)
    region: str | None = None
    # openai realtime: whisper latency/accuracy tradeoff
    # (minimal|low|medium|high|xhigh); translate target language (e.g. "en").
    delay: str | None = None
    target_language: str | None = None


class AudioConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # "mic": sounddevice input (default, all platforms).
    # "loopback": WASAPI desktop/system-audio capture (Windows only; --extra loopback).
    source: Literal["mic", "loopback"] = "mic"
    device: str | None = None
    samplerate: int = 16000
    channels: int = 1


class LocalConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_size: str = "small"
    # "auto" probes for CUDA and falls back to CPU; "cpu"/"cuda" force the device.
    device: Literal["auto", "cpu", "cuda"] = "auto"
    # CTranslate2 compute type; None picks a per-device default (cuda->float16, cpu->int8).
    compute_type: str | None = None
    cpu_threads: int = 1
    partial_interval_ms: int = 500
    max_buffer_s: float = 30.0
    vad_threshold: float = 0.5
    min_silence_ms: int = 500
    # Optional decoding hints passed straight to faster-whisper's transcribe().
    initial_prompt: str | None = None
    hotwords: str | None = None


class ServerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host: str = "127.0.0.1"
    port: int = 8765


class OverlayConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    font_family: str = "Pretendard, 'Noto Sans KR', sans-serif"
    font_size: int = Field(default=48, ge=1)
    font_weight: int = Field(default=700, ge=100, le=900)
    color: str = "#ffffff"
    partial_color: str = "#aaaaaa"
    background: str = "rgba(0,0,0,0.35)"
    outline_width: int = Field(default=2, ge=0)
    outline_color: str = "#000000"
    shadow: str = "0 2px 6px rgba(0,0,0,0.6)"
    position: Literal["top", "middle", "bottom"] = "bottom"
    align: Literal["left", "center", "right"] = "center"
    max_lines: int = Field(default=3, ge=1)
    line_height: float = Field(default=1.3, gt=0)
    padding: int = Field(default=24, ge=0)
    letter_spacing: int = 0
    fade_ms: int = Field(default=200, ge=0)
    uppercase: bool = False
    custom_css: str | None = None
    # Feature 5: per-line character wrap (0 = disabled).
    # Wrapping uses codepoint count (len()), which is correct for Korean Hangul
    # (each syllable-block is one codepoint) without requiring extra dependencies.
    # Interaction with max_lines: max_lines caps committed-transcript history;
    # wrapping operates at display time and may produce more display lines than
    # max_lines — this is intentional (wrapping is display-only).
    max_chars_per_line: int = 0


class ObsHotkeyConfig(BaseModel):
    """Configuration for the OBS hotkey listener (default-off)."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    pause_input: str = "_CaptionPause"
    clear_input: str = "_CaptionClear"


class ObsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host: str = "localhost"
    port: int = 4455
    source_name: str = "LiveCaptions"
    hotkey: ObsHotkeyConfig = Field(default_factory=ObsHotkeyConfig)

    @property
    def obs_ws_password(self) -> str | None:
        return os.getenv("OBS_WS_PASSWORD") or None


class TextConfig(BaseModel):
    """Live-caption text transformation: replacements, word filter, and suppression."""

    model_config = ConfigDict(extra="forbid")

    replacements: list[ReplacementRule] = Field(default_factory=list)
    filter_words: list[str] = Field(default_factory=list)
    filter_mode: Literal["mask", "remove"] = "mask"
    filter_mask: str = "***"

    # Feature 4: Hallucination suppression.
    # suppress_blank=True (default ON) drops any final/partial whose text is blank
    # or whitespace-only after transform — blank finals are never wanted.
    # suppress_regex: each pattern is matched against the full stripped text
    # (re.fullmatch, case-insensitive); invalid patterns raise ValueError at load.
    # suppress_exact: case-insensitive whole-string comparison after strip.
    suppress_blank: bool = True
    suppress_regex: list[str] = Field(default_factory=list)
    suppress_exact: list[str] = Field(default_factory=list)

    # Compiled regex patterns (not serialised; rebuilt in the validator below).
    _compiled_suppress_regex: list[re.Pattern[str]] = PrivateAttr(default_factory=list)

    @model_validator(mode="after")
    def _post_validate(self) -> "TextConfig":
        """Strip blank filter_words; compile and validate suppress_regex patterns."""
        # Strip blank/whitespace-only strings from filter_words at config-load.
        # An empty string would build r'\b\b' matching every word boundary.
        self.filter_words = [w for w in self.filter_words if w.strip()]

        # Compile suppress_regex patterns eagerly so invalid patterns fail fast.
        compiled: list[re.Pattern[str]] = []
        for i, pat in enumerate(self.suppress_regex):
            try:
                compiled.append(re.compile(pat, re.IGNORECASE))
            except re.error as exc:
                raise ValueError(
                    f"Invalid suppress_regex[{i}] {pat!r}: {exc}"
                ) from exc
        self._compiled_suppress_regex = compiled
        return self


class ExportConfig(BaseModel):
    """Transcript export to subtitle files (TXT / SRT / WebVTT)."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    path: str = "captions.srt"
    format: Literal["txt", "srt", "vtt"] = "srt"  # noqa: A003


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    engine: Literal[
        "local", "openai", "elevenlabs", "google", "xai", "openrouter", "replicate",
        "assemblyai", "azure", "deepgram", "groq",
    ] = "local"
    language: str = "ko"
    audio: AudioConfig = Field(default_factory=AudioConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    overlay: OverlayConfig = Field(default_factory=OverlayConfig)
    local: LocalConfig = Field(default_factory=LocalConfig)
    providers: dict[str, ProviderConfig] = Field(default_factory=dict)
    obs: ObsConfig = Field(default_factory=ObsConfig)
    text: TextConfig = Field(default_factory=TextConfig)
    export: ExportConfig = Field(default_factory=ExportConfig)

    @property
    def openai_api_key(self) -> str | None:
        return os.getenv("OPENAI_API_KEY") or None

    @property
    def elevenlabs_api_key(self) -> str | None:
        return os.getenv("ELEVENLABS_API_KEY") or None

    @property
    def openrouter_api_key(self) -> str | None:
        return os.getenv("OPENROUTER_API_KEY") or None

    @property
    def replicate_api_token(self) -> str | None:
        return os.getenv("REPLICATE_API_TOKEN") or None

    @property
    def xai_api_key(self) -> str | None:
        return os.getenv("XAI_API_KEY") or None

    @property
    def gemini_api_key(self) -> str | None:
        return os.getenv("GEMINI_API_KEY") or None


def load_config(path: str | None) -> AppConfig:
    load_dotenv()
    if path is None:
        return AppConfig()

    with Path(path).open("rb") as config_file:
        data = tomllib.load(config_file)
    return AppConfig.model_validate(data)


def redacted_config(config: AppConfig) -> dict[str, object]:
    payload = config.model_dump(mode="json")
    payload["openai_api_key"] = "***" if config.openai_api_key else None
    payload["elevenlabs_api_key"] = "***" if config.elevenlabs_api_key else None
    payload["openrouter_api_key"] = "***" if config.openrouter_api_key else None
    payload["replicate_api_token"] = "***" if config.replicate_api_token else None
    payload["xai_api_key"] = "***" if config.xai_api_key else None
    payload["gemini_api_key"] = "***" if config.gemini_api_key else None
    # obs password is env-only; never surface it in config output
    if "obs" in payload and isinstance(payload["obs"], dict):
        payload["obs"].pop("obs_ws_password", None)
    return payload
