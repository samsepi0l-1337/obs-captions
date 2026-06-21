from __future__ import annotations

import pytest

from obs_captions.stt.base import Transcript
from obs_captions.stt.local_whisper import LocalWhisperBackend
from obs_captions.stt.openrouter import OpenRouterBackend
from obs_captions.stt.replicate import ReplicateBackend
from obs_captions.stt.registry import create_backend


def _noop(t: Transcript) -> None:
    pass


def _make_config(engine: str):
    """Build a minimal AppConfig with the given engine."""
    from obs_captions.config import AppConfig

    return AppConfig(engine=engine)


@pytest.mark.asyncio
async def test_local_engine_returns_local_whisper_backend(monkeypatch):
    from obs_captions.config import AppConfig

    cfg = AppConfig(engine="local")
    backend = create_backend(cfg, on_partial=_noop, on_final=_noop)
    assert isinstance(backend, LocalWhisperBackend)


@pytest.mark.asyncio
async def test_openrouter_engine_returns_openrouter_backend(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    from obs_captions.config import AppConfig

    cfg = AppConfig(engine="openrouter")
    backend = create_backend(cfg, on_partial=_noop, on_final=_noop)
    assert isinstance(backend, OpenRouterBackend)


@pytest.mark.asyncio
async def test_replicate_engine_returns_replicate_backend(monkeypatch):
    monkeypatch.setenv("REPLICATE_API_TOKEN", "test-token")
    from obs_captions.config import AppConfig

    cfg = AppConfig(engine="replicate")
    backend = create_backend(cfg, on_partial=_noop, on_final=_noop)
    assert isinstance(backend, ReplicateBackend)


@pytest.mark.asyncio
async def test_openrouter_missing_key_raises(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    from obs_captions.config import AppConfig

    cfg = AppConfig(engine="openrouter")
    with pytest.raises(ValueError, match="OPENROUTER_API_KEY"):
        create_backend(cfg, on_partial=_noop, on_final=_noop)


@pytest.mark.asyncio
async def test_replicate_missing_token_raises(monkeypatch):
    monkeypatch.delenv("REPLICATE_API_TOKEN", raising=False)
    from obs_captions.config import AppConfig

    cfg = AppConfig(engine="replicate")
    with pytest.raises(ValueError, match="REPLICATE_API_TOKEN"):
        create_backend(cfg, on_partial=_noop, on_final=_noop)


@pytest.mark.asyncio
async def test_openai_engine_returns_openai_realtime_backend(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    from obs_captions.config import AppConfig
    from obs_captions.stt.openai_realtime import OpenAIRealtimeBackend

    cfg = AppConfig(engine="openai")
    backend = create_backend(cfg, on_partial=_noop, on_final=_noop)
    assert isinstance(backend, OpenAIRealtimeBackend)


@pytest.mark.asyncio
async def test_elevenlabs_engine_returns_elevenlabs_backend(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "xi-test")
    from obs_captions.config import AppConfig
    from obs_captions.stt.elevenlabs_realtime import ElevenLabsRealtimeBackend

    cfg = AppConfig(engine="elevenlabs")
    backend = create_backend(cfg, on_partial=_noop, on_final=_noop)
    assert isinstance(backend, ElevenLabsRealtimeBackend)


@pytest.mark.asyncio
async def test_xai_engine_returns_xai_backend(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "xai-test")
    from obs_captions.config import AppConfig
    from obs_captions.stt.xai import XaiBackend

    cfg = AppConfig(engine="xai")
    backend = create_backend(cfg, on_partial=_noop, on_final=_noop)
    assert isinstance(backend, XaiBackend)


@pytest.mark.asyncio
async def test_google_engine_returns_google_backend(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-test")
    from obs_captions.config import AppConfig
    from obs_captions.stt.google import GoogleBackend

    cfg = AppConfig(engine="google")
    backend = create_backend(cfg, on_partial=_noop, on_final=_noop)
    assert isinstance(backend, GoogleBackend)


@pytest.mark.asyncio
async def test_google_engine_uses_provider_model(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-test")
    from obs_captions.config import AppConfig, ProviderConfig

    cfg = AppConfig(
        engine="google",
        providers={"google": ProviderConfig(mode="gemini", model="gemini-x")},
    )
    backend = create_backend(cfg, on_partial=_noop, on_final=_noop)
    assert backend.model == "gemini-x"  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("engine", "env_key", "match"),
    [
        ("openai", "OPENAI_API_KEY", "OPENAI_API_KEY"),
        ("elevenlabs", "ELEVENLABS_API_KEY", "ELEVENLABS_API_KEY"),
        ("xai", "XAI_API_KEY", "XAI_API_KEY"),
        ("google", "GEMINI_API_KEY", "GEMINI_API_KEY"),
    ],
)
def test_streaming_engines_missing_key_raise(monkeypatch, engine, env_key, match):
    monkeypatch.delenv(env_key, raising=False)
    from obs_captions.config import AppConfig

    cfg = AppConfig(engine=engine)
    with pytest.raises(ValueError, match=match):
        create_backend(cfg, on_partial=_noop, on_final=_noop)
