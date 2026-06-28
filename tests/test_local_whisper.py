from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
import types
import wave

import pytest

from obs_captions.stt import Transcript
from obs_captions.stt import local_whisper
from obs_captions.stt.local_whisper import LocalWhisperBackend, tokenize_text


def _install_fake_whisper_model(monkeypatch):
    """Install a fake ``faster_whisper.WhisperModel`` recording constructor kwargs.

    Real ``faster_whisper`` is not importable on hosts without a CUDA-capable
    CTranslate2 build, so inject a stand-in via ``sys.modules``.
    """
    captured: dict[str, object] = {}

    class FakeWhisperModel:
        def __init__(self, model_size, **kwargs):
            captured["model_size"] = model_size
            captured["kwargs"] = kwargs

    fake_module = types.ModuleType("faster_whisper")
    fake_module.WhisperModel = FakeWhisperModel  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "faster_whisper", fake_module)
    return captured


def test_load_model_passes_resolved_device_to_whisper_model(monkeypatch):
    captured = _install_fake_whisper_model(monkeypatch)
    seen: dict[str, object] = {}

    def fake_resolve(device, compute_type):
        seen["device"] = device
        seen["compute_type"] = compute_type
        return ("cuda", "float16")

    monkeypatch.setattr(local_whisper, "resolve_device", fake_resolve)

    backend = LocalWhisperBackend(
        on_partial=lambda _: None,
        on_final=lambda _: None,
        device="auto",
        compute_type=None,
        cpu_threads=3,
    )
    backend._load_model()

    # The backend forwards its configured (device, compute_type) to the resolver.
    assert seen == {"device": "auto", "compute_type": None}
    # The resolved values reach WhisperModel; cpu_threads is still threaded through.
    assert captured["model_size"] == "small"
    assert captured["kwargs"]["device"] == "cuda"
    assert captured["kwargs"]["compute_type"] == "float16"
    assert captured["kwargs"]["cpu_threads"] == backend.cpu_threads


def test_load_model_cpu_int8_regression_with_real_resolver(monkeypatch):
    """Zero CPU regression: real resolver + no CUDA => device=cpu, compute_type=int8."""
    captured = _install_fake_whisper_model(monkeypatch)
    monkeypatch.setitem(sys.modules, "ctranslate2", None)  # force CUDA probe to fail

    backend = LocalWhisperBackend(
        on_partial=lambda _: None,
        on_final=lambda _: None,
        cpu_threads=1,
    )  # defaults: device="auto", compute_type=None
    backend._load_model()

    assert captured["kwargs"]["device"] == "cpu"
    assert captured["kwargs"]["compute_type"] == "int8"
    assert captured["kwargs"]["cpu_threads"] == backend.cpu_threads


def test_backend_stores_device_and_compute_type():
    backend = LocalWhisperBackend(
        on_partial=lambda _: None,
        on_final=lambda _: None,
        device="cuda",
        compute_type="float16",
    )
    assert backend.device == "cuda"
    assert backend.compute_type == "float16"


def test_backend_device_defaults_are_auto_and_none():
    backend = LocalWhisperBackend(on_partial=lambda _: None, on_final=lambda _: None)
    assert backend.device == "auto"
    assert backend.compute_type is None


def test_tokenize_text_prefers_words_and_supports_unspaced_korean():
    assert tokenize_text("안녕하세요 세계") == ["안녕하세요", "세계"]
    assert tokenize_text("안녕") == ["안", "녕"]


@pytest.mark.asyncio
async def test_local_whisper_emits_full_partial_and_final_with_fake_transcriber():
    partials: list[Transcript] = []
    finals: list[Transcript] = []
    # Stable transcriber: each feed advances the hypothesis; re-transcribing an
    # UNCHANGED buffer (e.g. flush's final pass) returns the same latest text, as a
    # real model would. (A finite ``iter`` would raise StopIteration on flush's
    # final re-transcription — its absence previously encoded the dropped-tail bug.)
    hypotheses = ["안녕하세요 세게", "안녕하세요 세계"]
    seen = {"i": 0}

    async def fake_transcribe(_pcm16: bytes) -> str:
        await asyncio.sleep(0)
        i = min(seen["i"], len(hypotheses) - 1)
        seen["i"] += 1
        return hypotheses[i]

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

    # No spurious empty partial: ``_emit_partial_from_buffer`` only emits a partial
    # when the unconfirmed tail is non-empty, so flush's final pass on the
    # fully-committed buffer emits nothing.
    assert [item.text for item in partials] == ["안녕하세요 세게", "세계"]
    assert finals == [
        Transcript(text="안녕하세요", is_final=True, lang="ko"),
        Transcript(text="세계", is_final=True, lang="ko"),
    ]
    # flush re-transcribes and commits the trailing token, so the confirmed text is
    # the full utterance (was "안녕하세요" before flush gained its final pass).
    assert backend.confirmed_text == "안녕하세요 세계"


@pytest.mark.asyncio
async def test_local_whisper_emits_local_agreement_finals_and_unconfirmed_tail_once():
    partials: list[Transcript] = []
    finals: list[Transcript] = []
    # Stable transcriber (see note above): re-transcribing the unchanged final
    # buffer returns the latest hypothesis instead of exhausting an iterator.
    hypotheses = [
        "안녕하세요 자",
        "안녕하세요 자막",
        "안녕하세요 자막 테스트",
    ]
    seen = {"i": 0}

    async def fake_transcribe(_pcm16: bytes) -> str:
        await asyncio.sleep(0)
        i = min(seen["i"], len(hypotheses) - 1)
        seen["i"] += 1
        return hypotheses[i]

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
    # Stable transcriber (see note above): flush's final re-transcription of the
    # unchanged buffer returns the latest hypothesis, not StopIteration.
    hypotheses = [
        "안녕하세요 자",
        "안녕하세요 자막",
        "안녕하세요 자막 테스트",
    ]
    seen = {"i": 0}

    async def fake_transcribe(_pcm16: bytes) -> str:
        await asyncio.sleep(0)
        i = min(seen["i"], len(hypotheses) - 1)
        seen["i"] += 1
        return hypotheses[i]

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


@pytest.mark.asyncio
async def test_flush_retranscribes_trailing_audio_fed_after_last_partial():
    """With partial_interval_ms > 0, audio fed AFTER the last 'due' partial but
    BEFORE flush() must still reach the finalized caption.

    Two back-to-back feeds: the first is 'due' (sets _latest_text to a prefix),
    the second arrives <interval later so feed_audio does NOT re-emit. flush()
    must re-transcribe the now-current buffer so the LATER words are finalized;
    otherwise the utterance tail (up to partial_interval_ms of speech) is dropped.

    The transcriber is window-aware: it returns a growing transcript keyed to how
    much buffer was fed ("안녕" after 1 chunk, "안녕 하세요" once 2 chunks present).
    """
    finals: list[Transcript] = []
    chunk_bytes = 3200  # 0.1s @ 16kHz PCM16

    async def fake_transcribe(pcm16: bytes) -> str:
        await asyncio.sleep(0)
        chunks_in_window = len(pcm16) // chunk_bytes
        return "안녕" if chunks_in_window <= 1 else "안녕 하세요"

    backend = LocalWhisperBackend(
        on_partial=lambda _: None,
        on_final=finals.append,
        transcribe_fn=fake_transcribe,
        partial_interval_ms=500,  # production-like; >0 gates feed_audio re-emit
        sample_rate=16000,
        max_buffer_s=30,
    )

    await backend.start_stream()
    chunk = b"\x00\x00" * 1600  # 3200 bytes == 0.1s
    await backend.feed_audio(chunk)  # due (first feed) -> emits, _latest_text="안녕"
    await backend.feed_audio(chunk)  # <500ms later -> NOT due -> no re-emit
    await backend.flush()
    await backend.stop_stream()

    final_text = " ".join(t.text for t in finals)
    # The trailing word ("하세요"), only present once the 2nd chunk is in the buffer,
    # must reach the final caption. Pre-fix, flush finalizes the stale "안녕" prefix
    # and drops it.
    assert "하세요" in final_text, (
        f"trailing audio dropped: flush finalized stale text; finals={[t.text for t in finals]}"
    )
    assert final_text == "안녕 하세요", (
        f"final caption should be the full re-transcribed utterance; got {final_text!r}"
    )


@pytest.mark.asyncio
async def test_flush_consumes_pending_rebase_from_trim_after_last_partial():
    """When a buffer trim sets _rebase_pending=True AND trailing audio arrives
    after the last 'due' partial, flush() must finalize from the REBASED current
    window (consuming the rebase), not from stale pre-rebase state — recovering
    the trailing word, with no committed token lost or duplicated.

    Scenario (3-chunk cap, partial_interval_ms=500): feed w0..w4 each forced 'due'
    (reset _last_partial_at) so LocalAgreement-2 commits w0,w1,w2 as the rolling
    window slides; then feed a final trailing chunk (w5) that is NOT due — it only
    buffers and trims, setting _rebase_pending without transcribing. flush() must
    re-transcribe the trimmed window [w3,w4,w5], consume the rebase, and finalize
    w5. Against the old flush (no final re-transcription) w5 is dropped and the
    rebase is bypassed — i.e. this is RED before the fix.

    The transcriber is window-aware (returns the most-recent chunk's worth of words
    held in the rolling buffer), so trims physically drop the oldest words.
    """
    finals: list[Transcript] = []
    chunk_bytes = 3200
    words = ["w0", "w1", "w2", "w3", "w4", "w5"]
    fed = {"n": 0}

    async def fake_transcribe(pcm16: bytes) -> str:
        await asyncio.sleep(0)
        n_in_window = len(pcm16) // chunk_bytes
        held = words[: fed["n"]][-n_in_window:] if n_in_window else []
        return " ".join(held)

    backend = LocalWhisperBackend(
        on_partial=lambda _: None,
        on_final=finals.append,
        transcribe_fn=fake_transcribe,
        partial_interval_ms=500,
        sample_rate=16000,
        max_buffer_s=0.3,  # cap = 9600 bytes -> 3 chunks; trims as the window slides
    )

    await backend.start_stream()
    chunk = b"\x00\x00" * 1600  # 3200 bytes == 0.1s
    # Feed w0..w4, each forced 'due' so every chunk is transcribed and the sliding
    # 3-chunk window commits w0, w1, w2 via LocalAgreement-2 (with rebases on trim).
    for _ in range(len(words) - 1):
        fed["n"] += 1
        backend._last_partial_at = 0.0  # force this feed to be 'due'
        await backend.feed_audio(chunk)
    # Final trailing chunk (w5) arrives <500ms after the last partial -> NOT due:
    # it buffers + trims (sets _rebase_pending) but is never transcribed pre-flush.
    fed["n"] += 1
    await backend.feed_audio(chunk)
    assert backend._rebase_pending, "scenario precondition: trim should flag a rebase"
    await backend.flush()
    await backend.stop_stream()

    emitted = [tok for t in finals for tok in t.text.split()]
    # No committed token duplicated across the rebase/flush.
    assert len(emitted) == len(set(emitted)), f"duplicate finals: {emitted}"
    # The trailing word, only present in the post-trim window, must be finalized
    # (old flush finalizes from stale state and drops it).
    assert "w5" in emitted, f"trailing word in rebased window dropped: {emitted}"
    # No committed token lost: every word that passed through the window is
    # finalized exactly once.
    assert set(emitted) == set(words), f"committed token lost/spurious: {emitted}"


@pytest.mark.asyncio
async def test_flush_empty_retranscription_with_pending_rebase_keeps_committed_tokens():
    """INVARIANT (B): when flush()'s final re-transcription returns EMPTY while a
    buffer trim left ``_rebase_pending=True``, committed/surviving tokens must NOT
    be silently dropped.

    ``_emit_partial_from_buffer`` early-returns on empty text BEFORE consuming the
    pending rebase, so the force-commit of uncommitted-but-scrolled-off tokens in
    ``_rebase_after_trim`` is skipped — violating LocalAgreement-2's "no committed
    token lost" invariant. This is the RED case.

    Scenario (2-chunk cap, partial_interval_ms=500, window-aware transcriber):
    feed w0..w3 each forced 'due' so the sliding 2-chunk window commits the early
    words via LocalAgreement-2 (rebasing on each trim); then feed a final trailing
    chunk (w4) NOT due so it only buffers + trims (sets ``_rebase_pending``) without
    transcribing. The flush re-transcription is forced to return "" (e.g. a final
    silent/garbage window the model rejects). The words that scrolled through the
    window before flush must still each be finalized exactly once.
    """
    finals: list[Transcript] = []
    chunk_bytes = 3200
    words = ["w0", "w1", "w2", "w3", "w4"]
    fed = {"n": 0}
    flush_pass = {"empty": False}

    async def fake_transcribe(pcm16: bytes) -> str:
        await asyncio.sleep(0)
        if flush_pass["empty"]:
            return ""  # final flush window: model yields nothing
        n_in_window = len(pcm16) // chunk_bytes
        held = words[: fed["n"]][-n_in_window:] if n_in_window else []
        return " ".join(held)

    backend = LocalWhisperBackend(
        on_partial=lambda _: None,
        on_final=finals.append,
        transcribe_fn=fake_transcribe,
        partial_interval_ms=500,
        sample_rate=16000,
        max_buffer_s=0.2,  # cap = 6400 bytes -> 2 chunks; trims as the window slides
    )

    await backend.start_stream()
    chunk = b"\x00\x00" * 1600  # 3200 bytes == 0.1s
    for _ in range(len(words) - 1):  # feed w0..w3, each forced 'due'
        fed["n"] += 1
        backend._last_partial_at = 0.0
        await backend.feed_audio(chunk)
    # Final trailing chunk (w4): NOT due -> buffers + trims, flags rebase, no transcribe.
    fed["n"] += 1
    await backend.feed_audio(chunk)
    assert backend._rebase_pending, "scenario precondition: trim should flag a rebase"
    flush_pass["empty"] = True  # the flush re-transcription returns empty
    await backend.flush()
    await backend.stop_stream()

    emitted = [tok for t in finals for tok in t.text.split()]
    # No committed token duplicated across the rebase/flush.
    assert len(emitted) == len(set(emitted)), f"duplicate finals: {emitted}"
    # No committed token lost: every word that fully scrolled through the 2-chunk
    # window before flush must have been force-committed. With a 2-chunk window and
    # 5 words, w0..w2 are guaranteed to have scrolled off (committed) before flush.
    for expected in ["w0", "w1", "w2"]:
        assert expected in emitted, f"committed {expected} dropped on empty flush: {emitted}"


@pytest.mark.asyncio
async def test_flush_empty_retranscription_does_not_emit_stale_final():
    """CASE (A): after a real partial sets ``_latest_text``, if flush()'s final
    re-transcription returns EMPTY, ``flush()`` must not emit a SPURIOUS final from
    the now-stale ``_latest_text``.

    Semantics chosen: an empty final window means the rolling buffer holds no
    recognizable speech *at finalize time* — the previously-emitted partial was a
    transient hypothesis the model has since retracted, so nothing residual should
    be force-finalized. Tokens that were genuinely confirmed earlier (via
    LocalAgreement-2 / rebase force-commit) were already emitted as finals at that
    time; the only thing the stale path adds is the UNCONFIRMED tail, which an
    empty re-transcription has explicitly withdrawn.

    Here a single short utterance fits within the cap (no trim, no rebase). One
    forced-due feed sets ``_latest_text="hello world"`` with nothing yet confirmed
    (first frame -> LocalAgreement-2 commits nothing). The flush re-transcription
    returns "" -> flush must emit NO final (pre-fix it finalizes the stale
    "hello world").
    """
    finals: list[Transcript] = []
    calls = {"n": 0}

    async def fake_transcribe(_pcm16: bytes) -> str:
        await asyncio.sleep(0)
        calls["n"] += 1
        # First (the single due partial) yields a hypothesis; the flush pass is empty.
        return "hello world" if calls["n"] == 1 else ""

    backend = LocalWhisperBackend(
        on_partial=lambda _: None,
        on_final=finals.append,
        transcribe_fn=fake_transcribe,
        partial_interval_ms=0,
        sample_rate=16000,
        max_buffer_s=30,  # fits the single chunk; no trim/rebase
    )

    await backend.start_stream()
    await backend.feed_audio(b"\x00\x00" * 1600)  # due -> _latest_text="hello world", confirmed=0
    await backend.flush()
    await backend.stop_stream()

    # Nothing was ever confirmed; an empty final window retracts the unconfirmed
    # hypothesis, so no final should be emitted at all.
    assert finals == [], f"spurious stale final emitted on empty flush: {[t.text for t in finals]}"


@pytest.mark.asyncio
async def test_rebase_after_trim_does_not_emit_empty_partial_when_all_committed():
    """When a post-trim REBASE leaves every surviving token committed (the rebased
    tail is empty), ``_rebase_after_trim`` must NOT emit a spurious empty
    ``on_partial("")`` — mirroring the empty-tail guard already on the normal
    ``_emit_partial_from_buffer`` path. Both partial-emission sites must be uniform.

    Scenario (2-chunk cap, partial_interval_ms=500, scripted transcriber):
      feed1 (due): "a b"  -> first hypothesis, nothing agreed yet
      feed2 (due): "a b"  -> LocalAgreement-2 commits "a","b" (confirmed_len=2)
      feed3 (NOT due): the trailing chunk only buffers + trims (sets
                       ``_rebase_pending``), it is not transcribed pre-flush.
      flush -> re-transcribes the trimmed window as "b": the sole surviving token
               is already committed, so the rebase confirms it and the tail is "".
               Pre-fix, the rebase path emits a spurious on_partial("") here.

    RED before the 1-line ``if tail:`` guard on the rebase emission.
    """
    partials: list[Transcript] = []
    finals: list[Transcript] = []
    scripts = {1: "a b", 2: "a b", 3: "b"}
    fed = {"n": 0}

    async def fake_transcribe(_pcm16: bytes) -> str:
        await asyncio.sleep(0)
        return scripts[fed["n"]]

    backend = LocalWhisperBackend(
        on_partial=partials.append,
        on_final=finals.append,
        transcribe_fn=fake_transcribe,
        partial_interval_ms=500,
        sample_rate=16000,
        max_buffer_s=0.2,  # cap = 6400 bytes -> 2 chunks; trims on the 3rd feed
    )

    await backend.start_stream()
    chunk = b"\x00\x00" * 1600  # 3200 bytes == 0.1s
    # feed1, feed2: forced 'due' so each is transcribed and LocalAgreement-2 commits.
    for _ in range(2):
        fed["n"] += 1
        backend._last_partial_at = 0.0  # force this feed to be 'due'
        await backend.feed_audio(chunk)
    # feed3: trailing chunk arrives <interval later -> NOT due; it buffers + trims
    # (flags the rebase) but is not transcribed until flush.
    fed["n"] += 1
    await backend.feed_audio(chunk)
    assert backend._rebase_pending, "scenario precondition: trim should flag a rebase"
    await backend.flush()
    await backend.stop_stream()

    # The committed tokens still finalize exactly once.
    assert [t.text for t in finals] == ["a", "b"]
    # The rebase whose surviving token is already committed (empty tail) must emit
    # NO partial at all — no spurious empty on_partial("").
    partial_texts = [p.text for p in partials]
    assert "" not in partial_texts, f"spurious empty partial from rebase: {partial_texts}"
    assert all(p.text for p in partials), f"empty partial emitted: {partial_texts}"


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
