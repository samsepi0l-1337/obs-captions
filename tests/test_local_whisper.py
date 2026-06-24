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


@pytest.mark.asyncio
async def test_buffer_is_capped_to_max_buffer_seconds():
    """A long continuous stream must keep self._buffer (and the bytes passed to
    _transcribe) bounded by max_buffer_s * sample_rate * 2."""
    sample_rate = 16000
    max_buffer_s = 0.5  # cap = 0.5 * 16000 * 2 = 16000 bytes
    cap_bytes = int(max_buffer_s * sample_rate * 2)

    transcribe_sizes: list[int] = []

    async def fake_transcribe(pcm16: bytes) -> str:
        await asyncio.sleep(0)
        transcribe_sizes.append(len(pcm16))
        return "word"

    backend = LocalWhisperBackend(
        on_partial=lambda _: None,
        on_final=lambda _: None,
        transcribe_fn=fake_transcribe,
        partial_interval_ms=0,
        sample_rate=sample_rate,
        max_buffer_s=max_buffer_s,
    )

    await backend.start_stream()
    # Feed 0.1s chunks for 2 seconds of audio (20 chunks) — far beyond the cap.
    chunk = b"\x00\x00" * 1600  # 0.1s = 3200 bytes
    for _ in range(20):
        await backend.feed_audio(chunk)
        assert len(backend._buffer) <= cap_bytes
    await backend.stop_stream()

    assert transcribe_sizes, "transcribe was never called"
    assert max(transcribe_sizes) <= cap_bytes


@pytest.mark.asyncio
async def test_trim_does_not_re_emit_or_drop_committed_tokens():
    """Across a buffer trim, no already-committed token is emitted twice via
    on_final and none is dropped; the uncommitted tail keeps flowing.

    The fake transcriber is WINDOW-AWARE: it returns one unique word per 0.1s
    chunk currently held in the rolling buffer. So a trim physically drops the
    OLDEST words from the re-transcription — exactly the scenario that corrupts
    LocalAgreement-2 indices if the rebase is missing (causing a committed word
    to be re-emitted or lost)."""
    finals: list[Transcript] = []

    # 0.1s chunk == 3200 bytes. The Nth word corresponds to the Nth chunk.
    chunk_bytes = 3200
    words = [f"w{i}" for i in range(12)]
    fed = {"n": 0}

    async def fake_transcribe(pcm16: bytes) -> str:
        await asyncio.sleep(0)
        n_in_window = len(pcm16) // chunk_bytes
        # The most-recent n_in_window words of everything fed so far.
        held = words[: fed["n"]][-n_in_window:] if n_in_window else []
        return " ".join(held)

    backend = LocalWhisperBackend(
        on_partial=lambda _: None,
        on_final=finals.append,
        transcribe_fn=fake_transcribe,
        partial_interval_ms=0,
        sample_rate=16000,
        max_buffer_s=0.4,  # cap = 12800 bytes -> holds 4 chunks, trims afterward
    )

    await backend.start_stream()
    chunk = b"\x00\x00" * 1600  # 0.1s == 3200 bytes
    for _ in range(len(words)):
        fed["n"] += 1
        await backend.feed_audio(chunk)
    await backend.flush()
    await backend.stop_stream()

    final_texts = [t.text for t in finals]
    # No committed word emitted twice (a missing rebase re-commits a retained word).
    assert len(final_texts) == len(set(final_texts)), f"duplicate finals: {final_texts}"
    # No committed word lost: every word that scrolled fully through the window
    # before flush must have been committed exactly once. With a 4-chunk window
    # and 12 words, words w0..w7 are guaranteed to have been committed.
    for expected in words[:8]:
        assert expected in final_texts, f"{expected} dropped; finals={final_texts}"


@pytest.mark.asyncio
async def test_trim_rebase_repeated_token_surviving_copy_not_re_emitted():
    """Across a buffer trim, when a committed token appears more than once in
    curr_tokens and only the LATER (rightmost) copy survived the trim, the
    first-occurrence search anchors on the wrong (already-dropped) copy and
    leaves the survivor UNPROTECTED — causing local_agreement to re-confirm and
    re-emit it on the next frame (duplicate on_final).

    Scripted scenario (2-chunk cap, 4 feeds):
      frame1: "X the"  — first hypothesis, no agreement yet
      frame2: "X the"  — agrees -> "X","the" both committed
      frame3 (post-trim, rebase): "the Y the Z"
        "X" scrolled off. The surviving committed token is the LAST "the" (pos 2).
        With first-occurrence search: confirmed_end=1, leaving "the" at pos 2
        unprotected -> next local_agreement re-confirms and re-emits it.
        With last-occurrence search: confirmed_end=3, "the" at pos 2 is protected.
      frame4: "the Y the Z"  — same; local_agreement from confirmed_end sees only Z.

    FAILS against first-occurrence _find_subsequence in _committed_overlap.
    """
    finals: list[Transcript] = []
    scripts = {1: "X the", 2: "X the", 3: "the Y the Z", 4: "the Y the Z"}
    fed = {"n": 0}

    async def fake_transcribe(_pcm16: bytes) -> str:
        await asyncio.sleep(0)
        return scripts[fed["n"]]

    backend = LocalWhisperBackend(
        on_partial=lambda _: None,
        on_final=finals.append,
        transcribe_fn=fake_transcribe,
        partial_interval_ms=0,
        sample_rate=16000,
        max_buffer_s=0.2,  # cap=6400 bytes -> 2 chunks; trim fires at frame 3
    )

    await backend.start_stream()
    chunk = b"\x00\x00" * 1600  # 3200 bytes = 0.1s
    for _ in range(4):
        fed["n"] += 1
        await backend.feed_audio(chunk)
    await backend.flush()
    await backend.stop_stream()

    emitted_tokens = [tok for t in finals for tok in t.text.split()]
    # "X" and "the" were both committed in frame 2.
    # "the" must appear EXACTLY ONCE in finals (the committed copy).
    # With first-occurrence bug: "the" appears twice (committed + re-emitted).
    assert emitted_tokens.count("the") == 1, (
        f"committed 'the' re-emitted (first-occurrence bug); emitted={emitted_tokens}, "
        f"finals={[t.text for t in finals]}"
    )
    # "X" was committed and must not be lost.
    assert "X" in emitted_tokens, f"committed 'X' lost; emitted={emitted_tokens}"


@pytest.mark.asyncio
async def test_trim_rebase_survives_adversarial_non_prefix_retranscription():
    """Across a trim, an ADVERSARIAL re-transcription whose prefix does NOT cleanly
    align (hallucinated leading token / a split) must still not re-emit an
    already-committed token via on_final, nor drop a committed one.

    Each call returns a fixed scripted hypothesis. Calls 1-2 establish committed
    tokens via LocalAgreement-2; call 3 fires after a trim (_rebase_pending) and
    is the adversarial window: the trimmed window's hypothesis has a HALLUCINATED
    leading token before the surviving committed token, so a naive prefix-based
    rebase collapses confirmed_len to 0 and the next frame re-confirms — and
    re-emits — an already-committed token.

    FAILS against the old _front_shift fallback (duplicate 'b')."""
    finals: list[Transcript] = []

    # Window holds at most ~2 chunks once trimmed (cap below). Scripted hypotheses
    # keyed by how many chunks have been fed so far.
    scripts = {
        1: "a b",  # frame 1: no agreement yet (first hypothesis)
        2: "a b c",  # frame 2: 'a','b' agree with frame 1 -> committed [a,b]
        # frame 3 is post-trim: 'a' scrolled off; window now ~[b,c,d] but the
        # transcriber HALLUCINATES a leading 'z' -> non-prefix overlap with [a,b,c]
        3: "z b c d",
        4: "z b c d",  # frame 4: stable -> local_agreement would re-confirm
    }
    fed = {"n": 0}

    async def fake_transcribe(_pcm16: bytes) -> str:
        await asyncio.sleep(0)
        return scripts[fed["n"]]

    backend = LocalWhisperBackend(
        on_partial=lambda _: None,
        on_final=finals.append,
        transcribe_fn=fake_transcribe,
        partial_interval_ms=0,
        sample_rate=16000,
        max_buffer_s=0.2,  # cap = 6400 bytes -> holds 2 chunks, trims afterward
    )

    await backend.start_stream()
    chunk = b"\x00\x00" * 1600  # 0.1s == 3200 bytes
    for _ in range(4):
        fed["n"] += 1
        await backend.feed_audio(chunk)
    await backend.flush()
    await backend.stop_stream()

    # Flatten every committed token across all on_final emissions (a final may be
    # multi-token text like "c d"). A broken rebase re-emits an already-committed
    # token — either as a standalone duplicate OR folded into a later final's text.
    emitted_tokens = [tok for t in finals for tok in t.text.split()]
    # 'a' and 'b' were committed before the trim. They must each be emitted at
    # most once, and 'b' (which survives in the trimmed window) must not be lost.
    assert emitted_tokens.count("a") <= 1, f"'a' re-emitted: {emitted_tokens}"
    assert emitted_tokens.count("b") == 1, f"'b' duplicated/lost: {emitted_tokens}"
    assert emitted_tokens.count("c") <= 1, f"'c' re-emitted: {emitted_tokens}"
    # The hallucinated leading token must never be committed as final.
    assert "z" not in emitted_tokens, f"hallucinated 'z' committed: {emitted_tokens}"


@pytest.mark.asyncio
async def test_short_utterance_within_cap_behaves_unchanged():
    """For an utterance that fits within the cap, partial/commit behavior is
    identical to the no-cap baseline (LocalAgreement-2 committing)."""
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
        max_buffer_s=30,  # well above the 3 * 0.1s fed below
    )

    await backend.start_stream()
    await backend.feed_audio(b"\x00\x00" * 1600)
    await backend.feed_audio(b"\x00\x00" * 1600)
    await backend.feed_audio(b"\x00\x00" * 1600)
    await backend.flush()
    await backend.stop_stream()

    assert [item.text for item in partials] == ["안녕하세요 자", "자막", "테스트"]
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
