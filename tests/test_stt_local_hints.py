from __future__ import annotations

from typing import Any

from obs_captions.stt.local_whisper import LocalWhisperBackend


class _FakeSegment:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeModel:
    """Captures the kwargs passed to ``transcribe`` for assertion."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def transcribe(self, audio: Any, **kwargs: Any) -> tuple[list[_FakeSegment], object]:
        self.calls.append(kwargs)
        return ([_FakeSegment("hi")], object())


def _make_backend(**extra: Any) -> LocalWhisperBackend:
    return LocalWhisperBackend(
        on_partial=lambda _t: None,
        on_final=lambda _t: None,
        **extra,
    )


def test_hints_absent_from_transcribe_when_none() -> None:
    backend = _make_backend()
    backend._model = _FakeModel()
    backend._transcribe_with_model(b"\x00\x00" * 100)
    kwargs = backend._model.calls[0]
    assert "initial_prompt" not in kwargs
    assert "hotwords" not in kwargs


def test_hints_passed_to_transcribe_when_set() -> None:
    backend = _make_backend(initial_prompt="게임 방송입니다", hotwords="닉네임 길드")
    backend._model = _FakeModel()
    backend._transcribe_with_model(b"\x00\x00" * 100)
    kwargs = backend._model.calls[0]
    assert kwargs["initial_prompt"] == "게임 방송입니다"
    assert kwargs["hotwords"] == "닉네임 길드"


def test_empty_string_hint_is_not_passed() -> None:
    backend = _make_backend(initial_prompt="", hotwords="")
    backend._model = _FakeModel()
    backend._transcribe_with_model(b"\x00\x00" * 100)
    kwargs = backend._model.calls[0]
    assert "initial_prompt" not in kwargs
    assert "hotwords" not in kwargs


def test_transcribe_result_is_stripped() -> None:
    backend = _make_backend()
    backend._model = _FakeModel()
    assert backend._transcribe_with_model(b"\x00\x00" * 100) == "hi"
