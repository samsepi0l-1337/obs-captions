"""STT backends package.

Public backend classes are re-exported lazily (PEP 562 ``__getattr__``) so that
``from obs_captions.stt import LocalWhisperBackend`` keeps working while the
actual submodule import — and its heavy transitive deps (numpy via
``faster_whisper``) — is deferred until the name is first accessed. Lightweight
consumers (GUI, CLI) that only need ``stt.validate``/``stt.registry`` therefore
avoid paying that cost. ``registry.create_backend`` already imports backends
lazily inside the function, so it is unaffected.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

# name -> submodule that defines it
_EXPORTS = {
    "STTBackend": "base",
    "Transcript": "base",
    "ElevenLabsRealtimeBackend": "elevenlabs_realtime",
    "FakeBackend": "fake",
    "GoogleBackend": "google",
    "LocalWhisperBackend": "local_whisper",
    "OpenAIRealtimeBackend": "openai_realtime",
    "StreamingBackend": "streaming",
    "XaiBackend": "xai",
}

__all__ = [
    "ElevenLabsRealtimeBackend",
    "FakeBackend",
    "GoogleBackend",
    "LocalWhisperBackend",
    "OpenAIRealtimeBackend",
    "STTBackend",
    "StreamingBackend",
    "Transcript",
    "XaiBackend",
]

if TYPE_CHECKING:
    from obs_captions.stt.base import STTBackend, Transcript
    from obs_captions.stt.elevenlabs_realtime import ElevenLabsRealtimeBackend
    from obs_captions.stt.fake import FakeBackend
    from obs_captions.stt.google import GoogleBackend
    from obs_captions.stt.local_whisper import LocalWhisperBackend
    from obs_captions.stt.openai_realtime import OpenAIRealtimeBackend
    from obs_captions.stt.streaming import StreamingBackend
    from obs_captions.stt.xai import XaiBackend


def __getattr__(name: str) -> Any:
    submodule = _EXPORTS.get(name)
    if submodule is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    module = importlib.import_module(f"{__name__}.{submodule}")
    return getattr(module, name)


def __dir__() -> list[str]:
    return sorted(__all__)
