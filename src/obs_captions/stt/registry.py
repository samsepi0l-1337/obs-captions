from __future__ import annotations

import os
from collections.abc import Callable

from obs_captions.stt.base import STTBackend, Transcript


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
        from obs_captions.stt.local_whisper import LocalWhisperBackend

        return LocalWhisperBackend(
            model_size=cfg.local.model_size,
            cpu_threads=cfg.local.cpu_threads,
            partial_interval_ms=cfg.local.partial_interval_ms,
            **common,
        )

    if engine == "openrouter":
        from obs_captions.stt.openrouter import OpenRouterBackend

        api_key = os.environ.get("OPENROUTER_API_KEY") or ""
        if not api_key:
            raise ValueError("OPENROUTER_API_KEY must be set in .env to use the openrouter engine.")
        provider_cfg = cfg.providers.get("openrouter")
        model = provider_cfg.model if provider_cfg else "openai/whisper-large-v3-turbo"
        return OpenRouterBackend(model=model, api_key=api_key, **common)

    if engine == "replicate":
        from obs_captions.stt.replicate import ReplicateBackend

        api_key = os.environ.get("REPLICATE_API_TOKEN") or ""
        if not api_key:
            raise ValueError("REPLICATE_API_TOKEN must be set in .env to use the replicate engine.")
        provider_cfg = cfg.providers.get("replicate")
        model = provider_cfg.model if provider_cfg else "openai/whisper"
        return ReplicateBackend(model=model, api_key=api_key, **common)

    if engine == "openai":
        from obs_captions.stt.openai_realtime import OpenAIRealtimeBackend

        api_key = cfg.openai_api_key or ""
        if not api_key:
            raise ValueError("OPENAI_API_KEY must be set in .env to use the openai engine.")
        provider_cfg = cfg.providers.get("openai")
        kwargs: dict[str, object] = {"api_key": api_key}
        if provider_cfg and provider_cfg.model:
            kwargs["model"] = provider_cfg.model
        return OpenAIRealtimeBackend(**kwargs, **common)

    if engine == "elevenlabs":
        from obs_captions.stt.elevenlabs_realtime import ElevenLabsRealtimeBackend

        api_key = cfg.elevenlabs_api_key or ""
        if not api_key:
            raise ValueError("ELEVENLABS_API_KEY must be set in .env to use the elevenlabs engine.")
        provider_cfg = cfg.providers.get("elevenlabs")
        kwargs = {"api_key": api_key}
        if provider_cfg and provider_cfg.model:
            kwargs["model"] = provider_cfg.model
        return ElevenLabsRealtimeBackend(**kwargs, **common)

    if engine == "xai":
        from obs_captions.stt.xai import XaiBackend

        api_key = os.environ.get("XAI_API_KEY") or ""
        if not api_key:
            raise ValueError("XAI_API_KEY must be set in .env to use the xai engine.")
        provider_cfg = cfg.providers.get("xai")
        kwargs = {"api_key": api_key}
        if provider_cfg and provider_cfg.model:
            kwargs["model"] = provider_cfg.model
        return XaiBackend(**kwargs, **common)

    if engine == "google":
        from obs_captions.stt.google import GoogleBackend

        provider_cfg = cfg.providers.get("google")
        mode = provider_cfg.mode if provider_cfg and provider_cfg.mode else "gemini"
        kwargs = {"mode": mode}
        if mode == "gemini":
            api_key = os.environ.get("GEMINI_API_KEY") or ""
            if not api_key:
                raise ValueError(
                    "GEMINI_API_KEY must be set in .env to use the google engine (gemini mode)."
                )
            kwargs["api_key"] = api_key
        if provider_cfg and provider_cfg.model:
            kwargs["model"] = provider_cfg.model
        return GoogleBackend(**kwargs, **common)

    raise ValueError(f"Unknown engine: '{engine}'")
