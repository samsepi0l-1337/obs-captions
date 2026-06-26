from __future__ import annotations

import asyncio
import inspect
import os
import time
from collections.abc import Awaitable, Callable
from typing import Any


from obs_captions.audio.capture import PCM16_SAMPLE_RATE, pcm16_to_float32
from obs_captions.stt.base import STTBackend, Transcript, local_agreement
from obs_captions.stt.device import resolve_device

TranscribeFn = Callable[[bytes], str | Awaitable[str]]


class LocalWhisperBackend(STTBackend):
    def __init__(
        self,
        *,
        language: str = "ko",
        sample_rate: int = PCM16_SAMPLE_RATE,
        on_partial: Callable[[Transcript], None],
        on_final: Callable[[Transcript], None],
        model_size: str = "small",
        device: str = "auto",
        compute_type: str | None = None,
        cpu_threads: int = 1,
        partial_interval_ms: int = 500,
        max_buffer_s: float = 30.0,
        transcribe_fn: TranscribeFn | None = None,
    ) -> None:
        super().__init__(
            language=language,
            sample_rate=sample_rate,
            on_partial=on_partial,
            on_final=on_final,
        )
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self.cpu_threads = _bounded_cpu_threads(cpu_threads)
        self.partial_interval_ms = partial_interval_ms
        self.max_buffer_s = max_buffer_s
        # PCM16 mono => 2 bytes per sample. Rolling-window cap in bytes.
        self._max_buffer_bytes = max(1, int(max_buffer_s * sample_rate * 2))
        self._transcribe_fn = transcribe_fn
        self._model: Any | None = None
        self._buffer = bytearray()
        self._started = False
        self._last_partial_at = 0.0
        self._previous_tokens: list[str] = []
        self._last_confirmed_len = 0
        self._latest_text = ""
        self.confirmed_text = ""
        self._rebase_pending = False

    async def start_stream(self) -> None:
        if self._started:
            return
        if self._transcribe_fn is None:
            self._model = await asyncio.to_thread(self._load_model)
            self._transcribe_fn = self._transcribe_with_model
        self._started = True

    async def feed_audio(self, pcm16: bytes) -> None:
        self._ensure_started()
        if not pcm16:
            return
        if not self._buffer:
            self.confirmed_text = ""
        self._buffer.extend(pcm16)
        self._trim_buffer_to_cap()
        now = time.monotonic()
        due = self.partial_interval_ms <= 0 or (
            now - self._last_partial_at >= self.partial_interval_ms / 1000
        )
        if due:
            await self._emit_partial_from_buffer()

    async def flush(self) -> None:
        self._ensure_started()
        if not self._buffer:
            self._reset_segment()
            return
        if not self._latest_text:
            await self._emit_partial_from_buffer()
        tokens = tokenize_text(self._latest_text)
        text = detokenize_text(tokens[self._last_confirmed_len :], source_text=self._latest_text)
        if text:
            self.on_final(Transcript(text=text, is_final=True, lang=self.language))
        self._reset_segment()

    async def stop_stream(self) -> None:
        if not self._started:
            return
        if self._buffer:
            await self.flush()
        self._started = False

    def _trim_buffer_to_cap(self) -> None:
        """Keep ``_buffer`` to the most recent ``max_buffer_s`` of audio.

        Trimming drops the OLDEST bytes, which correspond to already-committed
        tokens. So flag a rebase: the next transcription covers a shorter window,
        and re-applying the old token indices would corrupt LocalAgreement-2
        state. ``_emit_partial_from_buffer`` consumes the flag to restart the
        window cleanly (no duplicate finals, no committed text lost).
        """
        excess = len(self._buffer) - self._max_buffer_bytes
        if excess <= 0:
            return
        del self._buffer[:excess]
        self._rebase_pending = True

    async def _emit_partial_from_buffer(self) -> None:
        text = (await self._transcribe(bytes(self._buffer))).strip()
        self._last_partial_at = time.monotonic()
        if not text:
            return
        current_tokens = tokenize_text(text)
        if self._rebase_pending:
            self._rebase_after_trim(current_tokens, text)
            return
        agreed = local_agreement(self._previous_tokens, current_tokens, n=2)
        if len(agreed) > self._last_confirmed_len:
            for token in agreed[self._last_confirmed_len :]:
                self.on_final(Transcript(text=token, is_final=True, lang=self.language))
            self._last_confirmed_len = len(agreed)

        if self._last_confirmed_len:
            self.confirmed_text = detokenize_text(
                current_tokens[: self._last_confirmed_len], source_text=text
            )
        self._previous_tokens = current_tokens
        self._latest_text = text
        tail = detokenize_text(current_tokens[self._last_confirmed_len :], source_text=text)
        self.on_partial(Transcript(text=tail, is_final=False, lang=self.language))

    def _rebase_after_trim(self, current_tokens: list[str], text: str) -> None:
        """Rebase LocalAgreement-2 state onto the trimmed rolling window.

        Trimming drops the OLDEST tokens (already committed via on_final). The
        re-transcription of the shorter window normally begins with a suffix of
        the prior hypothesis, but an ADVERSARIAL re-transcription may hallucinate
        a leading token or split words so the overlap is not a clean prefix.

        The rebase recomputes the confirmed count by locating the surviving
        committed tokens inside ``current_tokens``. Invariants that hold even for
        an adversarial hypothesis:
        - no already-committed token is re-emitted (no duplicate on_final),
        - no committed token is silently lost,
        - dropped-but-uncommitted tokens are force-committed (last chance),
        - the still-pending tail stays uncommitted so partials keep flowing.
        """
        self._rebase_pending = False
        prev_tokens = self._previous_tokens
        prev_text = self._latest_text
        confirmed_len = self._last_confirmed_len
        # Locate the retained overlap GEOMETRICALLY: ``prev_tokens[-length:]``
        # reappears at ``current_tokens[offset : offset+length]``. Everything in
        # prev before ``overlap_start`` scrolled off when the oldest audio was
        # trimmed. Anchoring on position (not on searching for a token value)
        # keeps the survivor accounting correct for REPEATED tokens, which a
        # value search mis-anchors onto a stale copy (dropping/duplicating a
        # committed token). A nonzero ``offset`` tolerates an adversarial
        # hallucinated leading token.
        length, offset = _retained_overlap(prev_tokens, current_tokens)
        overlap_start = len(prev_tokens) - length if length else len(prev_tokens)
        # Surviving committed tokens are those at prev indices
        # [overlap_start, confirmed_len), remapped into current_tokens; the new
        # confirmed boundary sits just past them so local_agreement never
        # re-emits a committed token (no duplicate on_final).
        survivors = min(max(0, confirmed_len - overlap_start), length)
        new_confirmed_len = offset + survivors if survivors else (offset if length else 0)
        # Uncommitted prev tokens that scrolled off (indices [confirmed_len,
        # overlap_start)) lost their audio for good — force-commit them now (last
        # chance) so none is dropped. Selecting by INDEX RANGE (not by value
        # membership in the surviving tail) is what keeps repeated tokens from
        # being wrongly skipped or double-committed.
        for token in prev_tokens[confirmed_len:overlap_start]:
            self.on_final(
                Transcript(
                    text=detokenize_text([token], source_text=prev_text),
                    is_final=True,
                    lang=self.language,
                )
            )
        self._last_confirmed_len = new_confirmed_len
        self._previous_tokens = current_tokens
        self._latest_text = text
        if new_confirmed_len:
            self.confirmed_text = detokenize_text(
                current_tokens[:new_confirmed_len], source_text=text
            )
        else:
            # Full-window rebase confirmed nothing: the pre-trim confirmed_text no
            # longer maps to this window. Clear it so it matches the rebased state.
            self.confirmed_text = ""
        tail = detokenize_text(current_tokens[new_confirmed_len:], source_text=text)
        self.on_partial(Transcript(text=tail, is_final=False, lang=self.language))

    async def _transcribe(self, pcm16: bytes) -> str:
        if self._transcribe_fn is None:
            raise RuntimeError("LocalWhisperBackend has no transcriber")
        value = self._transcribe_fn(pcm16)
        if inspect.isawaitable(value):
            return str(await value)
        return str(value)

    def _load_model(self) -> Any:
        from faster_whisper import WhisperModel

        device, compute_type = resolve_device(self.device, self.compute_type)
        return WhisperModel(
            self.model_size,
            device=device,
            compute_type=compute_type,
            cpu_threads=self.cpu_threads,
        )

    def _transcribe_with_model(self, pcm16: bytes) -> str:
        if self._model is None:
            raise RuntimeError("Whisper model is not loaded")
        audio = pcm16_to_float32(pcm16)
        segments, _info = self._model.transcribe(
            audio,
            language=self.language,
            vad_filter=False,
            beam_size=1,
        )
        return "".join(segment.text for segment in segments).strip()

    def _ensure_started(self) -> None:
        if not self._started:
            raise RuntimeError("LocalWhisperBackend.start_stream() must be called first")

    def _reset_segment(self) -> None:
        self._buffer.clear()
        self._previous_tokens = []
        self._last_confirmed_len = 0
        self._latest_text = ""
        self._last_partial_at = 0.0
        self._rebase_pending = False


def _retained_overlap(prev_tokens: list[str], curr_tokens: list[str]) -> tuple[int, int]:
    """Geometry of the slid window: ``(length, offset)`` of the retained block.

    A trim drops the OLDEST tokens, so a SUFFIX of ``prev_tokens`` reappears
    inside ``curr_tokens``. We return the LONGEST such suffix length ``length``
    and the curr index ``offset`` where it reappears, so
    ``prev_tokens[-length:] == curr_tokens[offset : offset + length]``.

    The longest overlap wins (minimal slide — a trim drops only a small oldest
    slice). Ties on ``length`` break to the LARGEST ``offset`` (rightmost
    placement): with repeated tokens the surviving copy is the most recent one,
    so anchoring rightmost keeps the survivor accounting from latching onto a
    stale earlier repeat. Returns ``(0, 0)`` when no suffix reappears.
    """
    max_overlap = min(len(prev_tokens), len(curr_tokens))
    for length in range(max_overlap, 0, -1):
        suffix = prev_tokens[-length:]
        offset = -1
        for start in range(len(curr_tokens) - length + 1):
            if curr_tokens[start : start + length] == suffix:
                offset = start
        if offset >= 0:
            return length, offset
    return 0, 0


def tokenize_text(text: str) -> list[str]:
    stripped = text.strip()
    if not stripped:
        return []
    words = stripped.split()
    if len(words) > 1:
        return words
    return list(stripped)


def detokenize_text(tokens: list[str], *, source_text: str) -> str:
    if " " in source_text:
        return " ".join(tokens)
    return "".join(tokens)


def _bounded_cpu_threads(requested: int) -> int:
    available = os.cpu_count() or 1
    return max(1, min(int(requested), available, 4))
