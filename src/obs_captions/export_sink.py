"""Transcript export sink — writes finals to TXT, SRT, or WebVTT files.

Usage::

    sink = TranscriptExportSink("captions.srt", "srt")
    sink.start()
    # ... on each final transcript:
    sink.on_final(transcript)
    sink.stop()

Timestamps use ``transcript.start_ms`` / ``transcript.end_ms`` when present;
otherwise elapsed wall-clock from ``start()`` is used.  Inject a monotonic
clock callable via *clock* for deterministic testing.

Fallback timestamp strategy (when transcript timestamps are absent):
  - ``start_ms``: the ``end_ms`` of the previous cue (0 for the first), so
    cues are contiguous rather than using a magic negative offset.
  - ``end_ms``: elapsed wall-clock milliseconds since ``start()`` was called.
  - ``end_ms`` is clamped to ``max(end_ms, start_ms)`` so cues never invert
    even when one bound comes from the transcript and the other from the clock.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import IO, Literal

from obs_captions.stt.base import Transcript


def _split_ms(ms: int) -> tuple[int, int, int, int]:
    """Decompose milliseconds into (hours, minutes, seconds, milliseconds)."""
    h = ms // 3_600_000
    m = (ms % 3_600_000) // 60_000
    s = (ms % 60_000) // 1_000
    rem = ms % 1_000
    return h, m, s, rem


def format_srt_cue(index: int, start_ms: int, end_ms: int, text: str) -> str:
    """Return one SRT subtitle block (without trailing blank line)."""
    h1, m1, s1, ms1 = _split_ms(start_ms)
    h2, m2, s2, ms2 = _split_ms(end_ms)
    return (
        f"{index}\n"
        f"{h1:02d}:{m1:02d}:{s1:02d},{ms1:03d} --> {h2:02d}:{m2:02d}:{s2:02d},{ms2:03d}\n"
        f"{text}\n"
    )


def format_vtt_cue(index: int, start_ms: int, end_ms: int, text: str) -> str:
    """Return one WebVTT cue block (without trailing blank line)."""
    h1, m1, s1, ms1 = _split_ms(start_ms)
    h2, m2, s2, ms2 = _split_ms(end_ms)
    return (
        f"{index}\n"
        f"{h1:02d}:{m1:02d}:{s1:02d}.{ms1:03d} --> {h2:02d}:{m2:02d}:{s2:02d}.{ms2:03d}\n"
        f"{text}\n"
    )


class TranscriptExportSink:
    """Incrementally write final transcripts to a subtitle/text file.

    Call ``start()`` before streaming, ``on_final()`` for each final
    transcript, and ``stop()`` to close the file.  The file is flushed
    after every write so partial runs are not lost.
    """

    def __init__(
        self,
        path: str,
        format: Literal["txt", "srt", "vtt"],  # noqa: A002
        *,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if format not in ("txt", "srt", "vtt"):
            raise ValueError(
                f"Unsupported format {format!r}; expected one of 'txt', 'srt', 'vtt'"
            )
        self._path = Path(path)
        self._format = format
        self._clock: Callable[[], float] = clock if clock is not None else time.monotonic
        self._file: IO[str] | None = None
        self._index: int = 0
        self._start_time: float = 0.0
        # Tracks the end_ms of the last written cue, used as start_ms for the
        # next cue when transcript.start_ms is absent (keeps cues contiguous).
        self._prev_end_ms: int = 0

    def start(self) -> None:
        """Open the output file and write the WebVTT header if format is vtt."""
        self._file = self._path.open("w", encoding="utf-8")
        self._start_time = self._clock()
        self._index = 0
        self._prev_end_ms = 0
        if self._format == "vtt":
            self._file.write("WEBVTT\n\n")
            self._file.flush()

    def on_final(self, transcript: Transcript) -> None:
        """Append one cue (or line) to the file and flush immediately."""
        if self._file is None:
            return
        self._index += 1
        if self._format == "txt":
            self._file.write(transcript.text + "\n")
        else:
            elapsed_ms = int((self._clock() - self._start_time) * 1000)
            # Use the previous cue's end as this cue's start when absent, so
            # cues are contiguous and do not rely on a magic negative offset.
            start_ms = (
                transcript.start_ms if transcript.start_ms is not None else self._prev_end_ms
            )
            end_ms = transcript.end_ms if transcript.end_ms is not None else elapsed_ms
            # Clamp negative start_ms (e.g. from a buggy backend supplying
            # negative offsets); ensure timestamps are always >= 0.
            start_ms = max(0, start_ms)
            # Monotonic: push start_ms forward to the end of the previous cue
            # so consecutive cues never overlap in the output file.
            start_ms = max(start_ms, self._prev_end_ms)
            # Clamp: prevent inversion when one bound comes from the transcript
            # time-base and the other from the wall-clock.
            end_ms = max(end_ms, start_ms)
            self._prev_end_ms = end_ms
            if self._format == "srt":
                self._file.write(format_srt_cue(self._index, start_ms, end_ms, transcript.text) + "\n")
            else:
                self._file.write(format_vtt_cue(self._index, start_ms, end_ms, transcript.text) + "\n")
        self._file.flush()

    def stop(self) -> None:
        """Close the output file."""
        if self._file is not None:
            self._file.close()
            self._file = None
