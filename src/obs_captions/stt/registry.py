from __future__ import annotations

import importlib
import os
from collections.abc import Callable
from typing import NamedTuple

from obs_captions.stt.base import STTBackend, Transcript


def backend_cpu_bound(config: object) -> bool:
    """Return whether the backend should receive offloaded feed_audio execution."""
    from obs_captions.config import AppConfig

    cfg: AppConfig = config  # type: ignore[assignment]
    return cfg.engine == "local"


class _KwargsEngine(NamedTuple):
    """Engine that builds ``{"api_key": ...}`` kwargs plus an optional model."""

    env_var: str
    key_attr: str | None  # cfg attribute for the key; None -> read env_var
    module: str
    cls: str
    extra_attrs: tuple[str, ...] = ()  # provider attrs forwarded when truthy


class _DefaultModelEngine(NamedTuple):
    """Engine called as ``Backend(model=..., api_key=..., **common)`` with a default model."""

    env_var: str
    module: str
    cls: str
    default_model: str


# openai/elevenlabs read the key off the cfg object; the rest read it from env.
_KWARGS_ENGINES: dict[str, _KwargsEngine] = {
    "openai": _KwargsEngine(
        "OPENAI_API_KEY", "openai_api_key",
        "obs_captions.stt.openai_realtime", "OpenAIRealtimeBackend",
        ("delay", "target_language"),
    ),
    "elevenlabs": _KwargsEngine(
        "ELEVENLABS_API_KEY", "elevenlabs_api_key",
        "obs_captions.stt.elevenlabs_realtime", "ElevenLabsRealtimeBackend",
    ),
    "xai": _KwargsEngine("XAI_API_KEY", None, "obs_captions.stt.xai", "XaiBackend"),
    "assemblyai": _KwargsEngine(
        "ASSEMBLYAI_API_KEY", None, "obs_captions.stt.assemblyai", "AssemblyAIRealtimeBackend"
    ),
    "deepgram": _KwargsEngine(
        "DEEPGRAM_API_KEY", None, "obs_captions.stt.deepgram", "DeepgramBackend"
    ),
    "groq": _KwargsEngine("GROQ_API_KEY", None, "obs_captions.stt.groq", "GroqBackend"),
}

_DEFAULT_MODEL_ENGINES: dict[str, _DefaultModelEngine] = {
    "openrouter": _DefaultModelEngine(
        "OPENROUTER_API_KEY", "obs_captions.stt.openrouter", "OpenRouterBackend",
        "openai/whisper-large-v3-turbo",
    ),
    "replicate": _DefaultModelEngine(
        "REPLICATE_API_TOKEN", "obs_captions.stt.replicate", "ReplicateBackend", "openai/whisper"
    ),
}


def _load(module: str, cls: str) -> type:
    """Lazily import ``cls`` from ``module`` (keeps backend imports out of startup)."""
    return getattr(importlib.import_module(module), cls)


def _missing_key_msg(var: str, engine: str) -> str:
    return f"{var} must be set in .env to use the {engine} engine."


def _require(value: str, message: str) -> str:
    if not value:
        raise ValueError(message)
    return value


def _build_kwargs_engine(engine: str, cfg: object, common: dict) -> STTBackend:
    spec = _KWARGS_ENGINES[engine]
    raw = getattr(cfg, spec.key_attr) if spec.key_attr else os.environ.get(spec.env_var)
    api_key = _require(raw or "", _missing_key_msg(spec.env_var, engine))
    provider_cfg = cfg.providers.get(engine)  # type: ignore[attr-defined]
    kwargs: dict[str, object] = {"api_key": api_key}
    if provider_cfg and provider_cfg.model:
        kwargs["model"] = provider_cfg.model
    for attr in spec.extra_attrs:
        value = getattr(provider_cfg, attr) if provider_cfg else None
        if value:
            kwargs[attr] = value
    return _load(spec.module, spec.cls)(**kwargs, **common)


def _build_default_model_engine(engine: str, cfg: object, common: dict) -> STTBackend:
    spec = _DEFAULT_MODEL_ENGINES[engine]
    api_key = _require(
        os.environ.get(spec.env_var) or "", _missing_key_msg(spec.env_var, engine)
    )
    provider_cfg = cfg.providers.get(engine)  # type: ignore[attr-defined]
    model = provider_cfg.model if provider_cfg else spec.default_model
    return _load(spec.module, spec.cls)(model=model, api_key=api_key, **common)


def _build_local(cfg: object, common: dict) -> STTBackend:
    local = cfg.local  # type: ignore[attr-defined]
    return _load("obs_captions.stt.local_whisper", "LocalWhisperBackend")(
        model_size=local.model_size,
        device=local.device,
        compute_type=local.compute_type,
        cpu_threads=local.cpu_threads,
        partial_interval_ms=local.partial_interval_ms,
        max_buffer_s=local.max_buffer_s,
        initial_prompt=local.initial_prompt,
        hotwords=local.hotwords,
        **common,
    )


def _build_google(cfg: object, common: dict) -> STTBackend:
    build_google_backend = _load("obs_captions.stt.google", "build_google_backend")
    provider_cfg = cfg.providers.get("google")  # type: ignore[attr-defined]
    mode = provider_cfg.mode if provider_cfg and provider_cfg.mode else "gemini"
    kwargs: dict[str, object] = {"mode": mode}
    if mode == "gemini":
        api_key = os.environ.get("GEMINI_API_KEY") or ""
        if not api_key:
            raise ValueError(
                "GEMINI_API_KEY must be set in .env to use the google engine (gemini mode)."
            )
        kwargs["api_key"] = api_key
    elif mode == "speech_v2":
        project_id = (
            (provider_cfg.project_id if provider_cfg else None)
            or os.environ.get("GOOGLE_CLOUD_PROJECT")
            or ""
        )
        if not project_id:
            raise ValueError(
                "GOOGLE_CLOUD_PROJECT must be set in .env (or providers.google.project_id) "
                "to use the google engine (speech_v2 mode)."
            )
        kwargs["project_id"] = project_id
        # Pass an explicit location through verbatim -- including "" -- so the
        # ctor's regional-endpoint guard owns the rejection. Only a None/unset
        # location falls back to the backend's us-central1 default; a
        # truthiness check here would silently mask "" and bypass the guard.
        if provider_cfg and provider_cfg.location is not None:
            kwargs["location"] = provider_cfg.location
    if provider_cfg and provider_cfg.model:
        kwargs["model"] = provider_cfg.model
    return build_google_backend(**kwargs, **common)


def _build_azure(cfg: object, common: dict) -> STTBackend:
    api_key = _require(
        os.environ.get("AZURE_SPEECH_KEY") or "", _missing_key_msg("AZURE_SPEECH_KEY", "azure")
    )
    provider_cfg = cfg.providers.get("azure")  # type: ignore[attr-defined]
    # Design intent (AC 3): region is resolved as providers.azure.region OR
    # AZURE_SPEECH_REGION env var.  providers.azure.region is a valid config-file
    # override that intentionally suppresses the env-var ValueError — the user has
    # explicitly provided the region in config, so the env var is not required.
    region = (provider_cfg.region if provider_cfg else None) or os.environ.get(
        "AZURE_SPEECH_REGION"
    ) or ""
    if not region:
        raise ValueError("AZURE_SPEECH_REGION must be set in .env (or providers.azure.region) to use the azure engine.")
    return _load("obs_captions.stt.azure", "AzureBackend")(
        api_key=api_key,
        region=region,
        language=common["language"],
        on_partial=common["on_partial"],
        on_final=common["on_final"],
    )


def create_backend(
    config: object,
    *,
    on_partial: Callable[[Transcript], None],
    on_final: Callable[[Transcript], None],
) -> STTBackend:
    """Factory: map config.engine to the correct STTBackend subclass.

    Raises ValueError for unimplemented engines or missing API keys.
    """
    from obs_captions.config import AppConfig

    cfg: AppConfig = config  # type: ignore[assignment]
    engine = cfg.engine
    common = dict(
        language=cfg.language,
        on_partial=on_partial,
        on_final=on_final,
    )

    if engine == "local":
        return _build_local(cfg, common)
    if engine == "google":
        return _build_google(cfg, common)
    if engine == "azure":
        return _build_azure(cfg, common)
    if engine in _DEFAULT_MODEL_ENGINES:
        return _build_default_model_engine(engine, cfg, common)
    if engine in _KWARGS_ENGINES:
        return _build_kwargs_engine(engine, cfg, common)

    raise ValueError(f"Unknown engine: '{engine}'")
