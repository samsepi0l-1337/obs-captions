from __future__ import annotations

from unittest.mock import patch

import pytest

from obs_captions.stt.base import Transcript
from obs_captions.stt.local_whisper import LocalWhisperBackend
from obs_captions.stt.openrouter import OpenRouterBackend
from obs_captions.stt.replicate import ReplicateBackend
from obs_captions.stt.registry import backend_cpu_bound, create_backend


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


@pytest.mark.parametrize(
    ("engine", "expected"),
    [
        ("local", True),
        ("openai", False),
        ("deepgram", False),
    ],
)
def test_backend_cpu_bound_matches_local_cpu_backend_rule(engine: str, expected: bool):
    """backend_cpu_bound should return True only for local engine."""
    from obs_captions.config import AppConfig

    cfg = AppConfig(engine=engine)
    assert backend_cpu_bound(cfg) is expected


@pytest.mark.asyncio
async def test_local_backend_defaults_device_auto_and_compute_type_none():
    from obs_captions.config import AppConfig

    cfg = AppConfig(engine="local")
    backend = create_backend(cfg, on_partial=_noop, on_final=_noop)
    assert isinstance(backend, LocalWhisperBackend)
    assert backend.device == "auto"
    assert backend.compute_type is None


@pytest.mark.asyncio
async def test_local_backend_receives_device_and_compute_type_from_config():
    from obs_captions.config import AppConfig, LocalConfig

    cfg = AppConfig(engine="local", local=LocalConfig(device="cuda", compute_type="float16"))
    backend = create_backend(cfg, on_partial=_noop, on_final=_noop)
    assert isinstance(backend, LocalWhisperBackend)
    assert backend.device == "cuda"
    assert backend.compute_type == "float16"


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


# ---------------------------------------------------------------------------
# AssemblyAI
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_assemblyai_engine_returns_correct_backend(monkeypatch):
    monkeypatch.setenv("ASSEMBLYAI_API_KEY", "aai-test")
    from obs_captions.config import AppConfig
    from obs_captions.stt.assemblyai import AssemblyAIRealtimeBackend

    cfg = AppConfig(engine="assemblyai")
    backend = create_backend(cfg, on_partial=_noop, on_final=_noop)
    assert isinstance(backend, AssemblyAIRealtimeBackend)
    assert backend.language == cfg.language


@pytest.mark.asyncio
async def test_assemblyai_engine_uses_provider_model(monkeypatch):
    monkeypatch.setenv("ASSEMBLYAI_API_KEY", "aai-test")
    from obs_captions.config import AppConfig, ProviderConfig
    from obs_captions.stt.assemblyai import AssemblyAIRealtimeBackend

    cfg = AppConfig(
        engine="assemblyai",
        providers={"assemblyai": ProviderConfig(model="universal-streaming-multilingual")},
    )
    backend = create_backend(cfg, on_partial=_noop, on_final=_noop)
    assert isinstance(backend, AssemblyAIRealtimeBackend)
    assert backend.model == "universal-streaming-multilingual"


@pytest.mark.asyncio
async def test_assemblyai_missing_key_raises(monkeypatch):
    monkeypatch.delenv("ASSEMBLYAI_API_KEY", raising=False)
    from obs_captions.config import AppConfig

    cfg = AppConfig(engine="assemblyai")
    with pytest.raises(ValueError, match="ASSEMBLYAI_API_KEY"):
        create_backend(cfg, on_partial=_noop, on_final=_noop)


@pytest.mark.asyncio
async def test_assemblyai_registry_forwards_api_key(monkeypatch):
    """Registry must explicitly pass api_key= to AssemblyAIRealtimeBackend constructor."""
    monkeypatch.setenv("ASSEMBLYAI_API_KEY", "aai-forwarded")
    from obs_captions.config import AppConfig

    cfg = AppConfig(engine="assemblyai")
    with patch("obs_captions.stt.assemblyai.AssemblyAIRealtimeBackend.__init__", return_value=None) as mock_init:
        create_backend(cfg, on_partial=_noop, on_final=_noop)
    call_kwargs = mock_init.call_args.kwargs
    assert call_kwargs.get("api_key") == "aai-forwarded", (
        "Registry must forward api_key= explicitly; backend fallback to env var is not sufficient"
    )


# ---------------------------------------------------------------------------
# Deepgram
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deepgram_engine_returns_correct_backend(monkeypatch):
    monkeypatch.setenv("DEEPGRAM_API_KEY", "dg-test")
    from obs_captions.config import AppConfig
    from obs_captions.stt.deepgram import DeepgramBackend

    cfg = AppConfig(engine="deepgram")
    backend = create_backend(cfg, on_partial=_noop, on_final=_noop)
    assert isinstance(backend, DeepgramBackend)
    assert backend.language == cfg.language


@pytest.mark.asyncio
async def test_deepgram_engine_uses_provider_model(monkeypatch):
    monkeypatch.setenv("DEEPGRAM_API_KEY", "dg-test")
    from obs_captions.config import AppConfig, ProviderConfig
    from obs_captions.stt.deepgram import DeepgramBackend

    cfg = AppConfig(
        engine="deepgram",
        providers={"deepgram": ProviderConfig(model="nova-2")},
    )
    backend = create_backend(cfg, on_partial=_noop, on_final=_noop)
    assert isinstance(backend, DeepgramBackend)
    assert backend.model == "nova-2"


@pytest.mark.asyncio
async def test_deepgram_missing_key_raises(monkeypatch):
    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
    from obs_captions.config import AppConfig

    cfg = AppConfig(engine="deepgram")
    with pytest.raises(ValueError, match="DEEPGRAM_API_KEY"):
        create_backend(cfg, on_partial=_noop, on_final=_noop)


@pytest.mark.asyncio
async def test_deepgram_registry_forwards_api_key(monkeypatch):
    """Registry must explicitly pass api_key= to DeepgramBackend constructor."""
    monkeypatch.setenv("DEEPGRAM_API_KEY", "dg-forwarded")
    from obs_captions.config import AppConfig

    cfg = AppConfig(engine="deepgram")
    with patch("obs_captions.stt.deepgram.DeepgramBackend.__init__", return_value=None) as mock_init:
        create_backend(cfg, on_partial=_noop, on_final=_noop)
    call_kwargs = mock_init.call_args.kwargs
    assert call_kwargs.get("api_key") == "dg-forwarded", (
        "Registry must forward api_key= explicitly; backend fallback to env var is not sufficient"
    )


# ---------------------------------------------------------------------------
# Groq
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_groq_engine_returns_correct_backend(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "groq-test")
    from obs_captions.config import AppConfig
    from obs_captions.stt.groq import GroqBackend

    cfg = AppConfig(engine="groq")
    backend = create_backend(cfg, on_partial=_noop, on_final=_noop)
    assert isinstance(backend, GroqBackend)
    assert backend.language == cfg.language


@pytest.mark.asyncio
async def test_groq_engine_uses_provider_model(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "groq-test")
    from obs_captions.config import AppConfig, ProviderConfig
    from obs_captions.stt.groq import GroqBackend

    cfg = AppConfig(
        engine="groq",
        providers={"groq": ProviderConfig(model="whisper-large-v3")},
    )
    backend = create_backend(cfg, on_partial=_noop, on_final=_noop)
    assert isinstance(backend, GroqBackend)
    assert backend.model == "whisper-large-v3"


@pytest.mark.asyncio
async def test_groq_missing_key_raises(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    from obs_captions.config import AppConfig

    cfg = AppConfig(engine="groq")
    with pytest.raises(ValueError, match="GROQ_API_KEY"):
        create_backend(cfg, on_partial=_noop, on_final=_noop)


@pytest.mark.asyncio
async def test_groq_registry_forwards_api_key(monkeypatch):
    """Registry must explicitly pass api_key= to GroqBackend constructor."""
    monkeypatch.setenv("GROQ_API_KEY", "groq-forwarded")
    from obs_captions.config import AppConfig

    cfg = AppConfig(engine="groq")
    with patch("obs_captions.stt.groq.GroqBackend.__init__", return_value=None) as mock_init:
        create_backend(cfg, on_partial=_noop, on_final=_noop)
    call_kwargs = mock_init.call_args.kwargs
    assert call_kwargs.get("api_key") == "groq-forwarded", (
        "Registry must forward api_key= explicitly; backend fallback to env var is not sufficient"
    )


# ---------------------------------------------------------------------------
# Azure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_azure_engine_returns_correct_backend(monkeypatch):
    monkeypatch.setenv("AZURE_SPEECH_KEY", "az-key")
    monkeypatch.setenv("AZURE_SPEECH_REGION", "eastus")
    from obs_captions.config import AppConfig
    from obs_captions.stt.azure import AzureBackend

    cfg = AppConfig(engine="azure")
    backend = create_backend(cfg, on_partial=_noop, on_final=_noop)
    assert isinstance(backend, AzureBackend)
    assert backend.language == cfg.language


@pytest.mark.asyncio
async def test_azure_missing_speech_key_raises(monkeypatch):
    monkeypatch.delenv("AZURE_SPEECH_KEY", raising=False)
    monkeypatch.setenv("AZURE_SPEECH_REGION", "eastus")
    from obs_captions.config import AppConfig

    cfg = AppConfig(engine="azure")
    with pytest.raises(ValueError, match="AZURE_SPEECH_KEY"):
        create_backend(cfg, on_partial=_noop, on_final=_noop)


@pytest.mark.asyncio
async def test_azure_missing_region_raises(monkeypatch):
    monkeypatch.setenv("AZURE_SPEECH_KEY", "az-key")
    monkeypatch.delenv("AZURE_SPEECH_REGION", raising=False)
    from obs_captions.config import AppConfig

    cfg = AppConfig(engine="azure")
    with pytest.raises(ValueError, match="AZURE_SPEECH_REGION"):
        create_backend(cfg, on_partial=_noop, on_final=_noop)


@pytest.mark.asyncio
async def test_azure_region_from_provider_config(monkeypatch):
    monkeypatch.setenv("AZURE_SPEECH_KEY", "az-key")
    monkeypatch.delenv("AZURE_SPEECH_REGION", raising=False)
    from obs_captions.config import AppConfig, ProviderConfig
    from obs_captions.stt.azure import AzureBackend

    cfg = AppConfig(engine="azure", providers={"azure": ProviderConfig(region="westus")})
    backend = create_backend(cfg, on_partial=_noop, on_final=_noop)
    assert isinstance(backend, AzureBackend)
    assert backend._region == "westus"


@pytest.mark.asyncio
async def test_azure_model_kwarg_never_forwarded(monkeypatch):
    """model= must never be passed to AzureBackend (it has no **kwargs and no model param)."""
    monkeypatch.setenv("AZURE_SPEECH_KEY", "az-key")
    monkeypatch.setenv("AZURE_SPEECH_REGION", "eastus")
    from obs_captions.config import AppConfig, ProviderConfig
    from obs_captions.stt.azure import AzureBackend

    cfg = AppConfig(
        engine="azure",
        providers={"azure": ProviderConfig(model="should-be-ignored", region="eastus")},
    )
    # Must not raise TypeError (which would happen if model= were forwarded to AzureBackend)
    backend = create_backend(cfg, on_partial=_noop, on_final=_noop)
    assert isinstance(backend, AzureBackend)


# ---------------------------------------------------------------------------
# Provider model override branches (openai / elevenlabs / xai)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_openai_engine_uses_provider_model(monkeypatch):
    """Registry must forward providers.openai.model to OpenAIRealtimeBackend."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    from obs_captions.config import AppConfig, ProviderConfig
    from obs_captions.stt.openai_realtime import OpenAIRealtimeBackend

    cfg = AppConfig(
        engine="openai",
        providers={"openai": ProviderConfig(model="gpt-realtime-translate", target_language="en")},
    )
    backend = create_backend(cfg, on_partial=_noop, on_final=_noop)
    assert isinstance(backend, OpenAIRealtimeBackend)
    assert backend.model == "gpt-realtime-translate"  # type: ignore[attr-defined]
    assert backend.target_language == "en"  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_openai_engine_forwards_delay(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    from obs_captions.config import AppConfig, ProviderConfig
    from obs_captions.stt.openai_realtime import OpenAIRealtimeBackend

    cfg = AppConfig(
        engine="openai",
        providers={"openai": ProviderConfig(model="gpt-realtime-whisper", delay="high")},
    )
    backend = create_backend(cfg, on_partial=_noop, on_final=_noop)
    assert isinstance(backend, OpenAIRealtimeBackend)
    assert backend.delay == "high"  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_elevenlabs_engine_uses_provider_model(monkeypatch):
    """Registry must forward providers.elevenlabs.model to ElevenLabsRealtimeBackend."""
    monkeypatch.setenv("ELEVENLABS_API_KEY", "xi-test")
    from obs_captions.config import AppConfig, ProviderConfig
    from obs_captions.stt.elevenlabs_realtime import ElevenLabsRealtimeBackend

    cfg = AppConfig(
        engine="elevenlabs",
        providers={"elevenlabs": ProviderConfig(model="scribe_v1_experimental")},
    )
    backend = create_backend(cfg, on_partial=_noop, on_final=_noop)
    assert isinstance(backend, ElevenLabsRealtimeBackend)
    assert backend.model == "scribe_v1_experimental"  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_xai_engine_uses_provider_model(monkeypatch):
    """Registry must forward providers.xai.model to XaiBackend."""
    monkeypatch.setenv("XAI_API_KEY", "xai-test")
    from obs_captions.config import AppConfig, ProviderConfig
    from obs_captions.stt.xai import XaiBackend

    cfg = AppConfig(
        engine="xai",
        providers={"xai": ProviderConfig(model="grok-2-latest")},
    )
    backend = create_backend(cfg, on_partial=_noop, on_final=_noop)
    assert isinstance(backend, XaiBackend)
    assert backend.model == "grok-2-latest"  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Google speech_v2 missing project_id raise (registry.py line 118)
# ---------------------------------------------------------------------------


def test_google_speech_v2_missing_project_id_raises(monkeypatch):
    """google engine in speech_v2 mode must raise ValueError when project_id is absent."""
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    from obs_captions.config import AppConfig, ProviderConfig

    cfg = AppConfig(
        engine="google",
        providers={"google": ProviderConfig(mode="speech_v2")},
    )
    with pytest.raises(ValueError, match="GOOGLE_CLOUD_PROJECT"):
        create_backend(cfg, on_partial=_noop, on_final=_noop)


# ---------------------------------------------------------------------------
# Unknown engine raise (registry.py line 193)
# ---------------------------------------------------------------------------


def test_unknown_engine_raises_value_error():
    """create_backend must raise ValueError for an unrecognised engine name."""
    from unittest.mock import MagicMock

    # AppConfig uses a Literal type that rejects unknown values at construction.
    # Bypass Pydantic validation with a mock so we can reach the registry's final raise.
    cfg = MagicMock()
    cfg.engine = "does-not-exist"
    with pytest.raises(ValueError, match="Unknown engine"):
        create_backend(cfg, on_partial=_noop, on_final=_noop)
