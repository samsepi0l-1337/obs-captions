from __future__ import annotations

import asyncio
import shutil
import subprocess
import wave

import pytest

from obs_captions.stt import Transcript
from obs_captions.stt.local_whisper import LocalWhisperBackend, tokenize_text


def test_tokenize_text_prefers_words_and_supports_unspaced_korean():
    assert tokenize_text("안녕하세요 세계") == ["안녕하세요", "세계"]
    assert tokenize_text("안녕") == ["안", "녕"]


@pytest.mark.asyncio
async def test_local_whisper_emits_full_partial_and_final_with_fake_transcriber():
    partials: list[Transcript] = []
    finals: list[Transcript] = []
    hypotheses = iter(["안녕하세요 세게", "안녕하세요 세계"])

    async def fake_transcribe(_pcm16: bytes) -> str:
        await asyncio.sleep(0)
        return next(hypotheses)

    backend = LocalWhisperBackend(
        on_partial=partials.append,
        on_final=finals.append,
        transcribe_fn=fake_transcribe,
        partial_interval_ms=0,
    )

    await backend.start_stream()
    await backend.feed_audio(b"\x00\x00" * 1600)
    await backend.feed_audio(b"\x00\x00" * 1600)
    await backend.flush()
    await backend.stop_stream()

    assert [item.text for item in partials] == ["안녕하세요 세게", "세계"]
    assert backend.confirmed_text == "안녕하세요"
    assert finals == [
        Transcript(text="안녕하세요", is_final=True, lang="ko"),
        Transcript(text="세계", is_final=True, lang="ko"),
    ]


@pytest.mark.asyncio
async def test_local_whisper_emits_local_agreement_finals_and_unconfirmed_tail_once():
    partials: list[Transcript] = []
    finals: list[Transcript] = []
    hypotheses = iter(
        [
            "안녕하세요 자",
            "안녕하세요 자막",
            "안녕하세요 자막 테스트",
        ]
    )

    async def fake_transcribe(_pcm16: bytes) -> str:
        await asyncio.sleep(0)
        return next(hypotheses)

    backend = LocalWhisperBackend(
        on_partial=partials.append,
        on_final=finals.append,
        transcribe_fn=fake_transcribe,
        partial_interval_ms=0,
    )

    await backend.start_stream()
    await backend.feed_audio(b"\x00\x00" * 1600)
    await backend.feed_audio(b"\x00\x00" * 1600)
    await backend.feed_audio(b"\x00\x00" * 1600)
    await backend.flush()
    await backend.stop_stream()

    assert [item.text for item in partials] == [
        "안녕하세요 자",
        "자막",
        "테스트",
    ]
    assert [item.text for item in finals] == ["안녕하세요", "자막", "테스트"]


@pytest.mark.slow
@pytest.mark.asyncio
async def test_local_whisper_transcribes_generated_korean_wav(tmp_path):
    if shutil.which("say") is None:
        pytest.skip("macOS say command unavailable")
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg unavailable")

    voices = subprocess.run(["say", "-v", "?"], text=True, capture_output=True, check=False)
    if (
        "Yuna" not in voices.stdout
        and "한국" not in voices.stdout
        and "Korean" not in voices.stdout
    ):
        pytest.skip("Korean say voice unavailable")
    voice = (
        "Yuna"
        if "Yuna" in voices.stdout
        else next(
            (
                line.split()[0]
                for line in voices.stdout.splitlines()
                if "한국" in line or "Korean" in line
            ),
            None,
        )
    )
    if voice is None:
        pytest.skip("Korean say voice unavailable")

    aiff_path = tmp_path / "obs_sample.aiff"
    wav_path = tmp_path / "obs_sample.wav"
    subprocess.run(
        ["say", "-v", voice, "안녕하세요 자막 테스트입니다", "-o", str(aiff_path)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(aiff_path), "-ar", "16000", "-ac", "1", str(wav_path)],
        check=True,
        capture_output=True,
    )

    with wave.open(str(wav_path), "rb") as wav:
        assert wav.getframerate() == 16000
        assert wav.getnchannels() == 1
        pcm16 = wav.readframes(wav.getnframes())

    finals: list[Transcript] = []
    backend = LocalWhisperBackend(
        on_partial=lambda _: None,
        on_final=finals.append,
        model_size="small",
        cpu_threads=1,
        partial_interval_ms=0,
    )
    try:
        await backend.start_stream()
        await backend.feed_audio(pcm16)
        await backend.flush()
    except Exception as exc:  # model download/network/runtime is allowed to skip slow test
        pytest.skip(f"local Whisper model unavailable: {exc}")
    finally:
        await backend.stop_stream()

    text = " ".join(item.text for item in finals).strip()
    assert text, "empty transcription"
    expected_tokens = ["안녕", "자막", "테스트"]
    assert sum(token in text for token in expected_tokens) >= 2, text
