from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from obs_captions.stt.base import local_agreement as local_agreement  # re-export

if TYPE_CHECKING:
    from obs_captions.stt.base import Transcript


@dataclass
class CaptionSnapshot:
    committed: list[str]
    partial: str


class CaptionState:
    def __init__(
        self,
        max_lines: int = 3,
        on_change: Callable[[CaptionSnapshot], None] | None = None,
    ) -> None:
        if max_lines < 0:
            raise ValueError("max_lines must be >= 0")
        self.max_lines = max_lines
        self._subscribers: list[Callable[[CaptionSnapshot], None]] = []
        self._committed: list[str] = []
        self._partial = ""
        if on_change is not None:
            self.subscribe(on_change)

    def subscribe(
        self, callback: Callable[[CaptionSnapshot], None]
    ) -> Callable[[], None]:
        """Register a change subscriber; returns a callable that unsubscribes it."""
        self._subscribers.append(callback)

        def unsubscribe() -> None:
            try:
                self._subscribers.remove(callback)
            except ValueError:
                pass

        return unsubscribe

    def snapshot(self) -> CaptionSnapshot:
        return CaptionSnapshot(committed=list(self._committed), partial=self._partial)

    def on_partial(self, transcript: Transcript) -> None:
        self._mutate(partial=transcript.text)

    def on_final(self, transcript: Transcript) -> None:
        committed = list(self._committed)
        if not committed or committed[-1] != transcript.text:
            committed.append(transcript.text)
        self._mutate(committed=committed[-self.max_lines :] if self.max_lines else [], partial="")

    def clear(self) -> None:
        """Clear all committed lines and partial text, notifying subscribers."""
        self._mutate(committed=[], partial="")

    def _mutate(self, *, committed: list[str] | None = None, partial: str | None = None) -> None:
        before = self.snapshot()
        if committed is not None:
            self._committed = list(committed)
        if partial is not None:
            self._partial = partial
        after = self.snapshot()
        if after != before:
            for subscriber in list(self._subscribers):
                subscriber(after)
