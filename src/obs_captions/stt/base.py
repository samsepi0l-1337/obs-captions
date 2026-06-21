from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class Transcript:
    text: str
    is_final: bool
    start_ms: int | None = None
    end_ms: int | None = None
    lang: str | None = None


class STTBackend(ABC):
    """Abstract streaming STT backend.

    Backends accept normalized 16 kHz PCM16 bytes via ``feed_audio`` and emit
    ``Transcript`` objects through callbacks. ``on_partial`` MUST receive the
    full current hypothesis every time, never a delta; delta-style providers are
    responsible for accumulating text before invoking the callback. ``on_final``
    emits immutable finalized transcript segments.
    """

    def __init__(
        self,
        *,
        language: str = "ko",
        sample_rate: int = 16000,
        on_partial: Callable[[Transcript], None],
        on_final: Callable[[Transcript], None],
    ) -> None:
        self.language = language
        self.sample_rate = sample_rate
        self.on_partial = on_partial
        self.on_final = on_final

    @abstractmethod
    async def start_stream(self) -> None:
        """Start any backend stream/session resources."""

    @abstractmethod
    async def feed_audio(self, pcm16: bytes) -> None:
        """Feed normalized 16 kHz PCM16 mono bytes into the stream."""

    @abstractmethod
    async def flush(self) -> None:
        """Flush buffered input and emit any available final transcript."""

    @abstractmethod
    async def stop_stream(self) -> None:
        """Stop stream/session resources."""


def local_agreement(prev_tokens: list[str], curr_tokens: list[str], n: int = 2) -> list[str]:
    """Return the agreed prefix between two token lists (LocalAgreement-2 algorithm).

    Tokens that appear in the same position in both ``prev_tokens`` and
    ``curr_tokens`` for *n* consecutive hypotheses are considered stable.
    This implementation handles n=1 (always agree) and n=2 (standard).
    """
    if n <= 1:
        return list(curr_tokens)
    if n > 2:
        return []

    agreed: list[str] = []
    for prev, curr in zip(prev_tokens, curr_tokens, strict=False):
        if prev != curr:
            break
        agreed.append(curr)
    return agreed
