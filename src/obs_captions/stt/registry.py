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
            device=cfg.local.device,
            compute_type=cfg.local.compute_type,
            cpu_threads=cfg.local.cpu_threads,
            partial_interval_ms=cfg.local.partial_interval_ms,
            max_buffer_s=cfg.local.max_buffer_s,
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
        from obs_captions.stt.google import build_google_backend

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

    raise ValueError(f"Unknown engine: '{engine}'")
