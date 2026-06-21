import pytest

from obs_captions.stt import FakeBackend, STTBackend, Transcript


def test_stt_backend_is_abstract():
    with pytest.raises(TypeError):
        STTBackend(on_partial=lambda transcript: None, on_final=lambda transcript: None)


@pytest.mark.asyncio
async def test_fake_backend_fires_callbacks_and_async_noops():
    partials: list[Transcript] = []
    finals: list[Transcript] = []
    backend = FakeBackend(on_partial=partials.append, on_final=finals.append)

    await backend.start_stream()
    await backend.feed_audio(b"pcm")
    backend.emit_partial("안녕")
    backend.emit_final("안녕하세요")
    await backend.flush()
    await backend.stop_stream()

    assert partials == [Transcript(text="안녕", is_final=False, lang="ko")]
    assert finals == [Transcript(text="안녕하세요", is_final=True, lang="ko")]


def test_fake_backend_partial_callback_receives_full_hypothesis_not_delta():
    partials: list[str] = []
    backend = FakeBackend(
        on_partial=lambda transcript: partials.append(transcript.text), on_final=lambda _: None
    )

    backend.emit_partial("안")
    backend.emit_partial("안녕")
    backend.emit_partial("안녕하세요")

    assert partials == ["안", "안녕", "안녕하세요"]
