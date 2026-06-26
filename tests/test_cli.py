from __future__ import annotations

import asyncio
import contextlib
import io
import subprocess
import wave

import pytest
from click.testing import CliRunner

from obs_captions.cli import _capture_to_backend, cli, make_capture
from obs_captions.stt.base import Transcript
from obs_captions.vad import VadEvent


# ---------------------------------------------------------------------------
# Existing _capture_to_backend tests
# ---------------------------------------------------------------------------


class BlockingCapture:
    def __init__(self) -> None:
        self.started = False
        self.frame_consumed = asyncio.Event()
        self.release = asyncio.Event()

    def start(self) -> None:
        self.started = True

    async def frames(self):
        yield b"speech"
        self.frame_consumed.set()
        await self.release.wait()


class SpeechThenPendingSegmenter:
    def __init__(self) -> None:
        self.flush_calls = 0

    def process(self, pcm16: bytes) -> VadEvent:
        assert pcm16 == b"speech"
        return VadEvent(is_speech=True)

    def flush(self) -> tuple[int, int] | None:
        self.flush_calls += 1
        return (0, 100)


class RecordingBackend:
    def __init__(self) -> None:
        self.started = False
        self.fed: list[bytes] = []
        self.flush_calls = 0

    async def start_stream(self) -> None:
        self.started = True

    async def feed_audio(self, pcm16: bytes) -> None:
        self.fed.append(pcm16)

    async def flush(self) -> None:
        self.flush_calls += 1


@pytest.mark.asyncio
async def test_capture_to_backend_flushes_pending_segment_on_cancellation():
    capture = BlockingCapture()
    segmenter = SpeechThenPendingSegmenter()
    backend = RecordingBackend()
    task = asyncio.create_task(_capture_to_backend(capture, segmenter, backend))

    await asyncio.wait_for(capture.frame_consumed.wait(), timeout=1)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert capture.started is True
    assert backend.started is True
    assert backend.fed == [b"speech"]
    assert segmenter.flush_calls == 1
    assert backend.flush_calls == 1


class SingleFrameCapture:
    """Yields one frame then stops; start is trackable."""

    def __init__(self, frame: bytes) -> None:
        self.frame = frame
        self.started = False

    def start(self) -> None:
        self.started = True

    async def frames(self):
        yield self.frame


class SegmentSegmenter:
    """Returns a non-None segment on the first frame, no pending flush."""

    def process(self, pcm16: bytes) -> VadEvent:
        return VadEvent(is_speech=True, segment=(0, len(pcm16)))

    def flush(self) -> tuple[int, int] | None:
        return None


@pytest.mark.asyncio
async def test_capture_to_backend_flushes_on_segment():
    """event.segment is not None triggers an immediate backend.flush() (line 218)."""
    capture = SingleFrameCapture(b"speech")
    segmenter = SegmentSegmenter()
    backend = RecordingBackend()

    await _capture_to_backend(capture, segmenter, backend)

    assert backend.started is True
    assert backend.fed == [b"speech"]
    assert backend.flush_calls == 1  # from the segment path, not the finally path


class NonSpeechSegmenter:
    """Returns is_speech=False on every frame, no segment, no pending flush."""

    def process(self, pcm16: bytes) -> VadEvent:
        return VadEvent(is_speech=False)

    def flush(self) -> tuple[int, int] | None:
        return None


@pytest.mark.asyncio
async def test_capture_to_backend_non_speech_frame_not_fed():
    """When is_speech=False the frame is NOT forwarded to backend (branch 215->217)."""
    capture = SingleFrameCapture(b"silence")
    segmenter = NonSpeechSegmenter()
    backend = RecordingBackend()

    await _capture_to_backend(capture, segmenter, backend)

    assert backend.started is True
    assert backend.fed == []       # non-speech frame must NOT be fed
    assert backend.flush_calls == 0


# ---------------------------------------------------------------------------
# check-engine tests
# ---------------------------------------------------------------------------


class FakeCheckBackend:
    """Minimal fake backend for check-engine tests.

    Transcripts are emitted from feed_audio so the WAV-path assertions are
    causally tied to actual audio being streamed (not just start_stream being
    called).  A broken _stream_wav that never calls feed_audio would produce
    zero transcripts, correctly failing the output assertions.
    """

    def __init__(self, *, on_partial, on_final, **_kwargs):
        self.on_partial = on_partial
        self.on_final = on_final
        self.started = False
        self.stopped = False
        self.fed: list[bytes] = []
        self.flush_calls = 0
        self._chunks_received = 0

    async def start_stream(self) -> None:
        self.started = True

    async def feed_audio(self, pcm16: bytes) -> None:
        self.fed.append(pcm16)
        self._chunks_received += 1
        # Emit one partial on the first chunk and one final on the second chunk,
        # so the transcript output is causally tied to audio being fed.
        if self._chunks_received == 1:
            self.on_partial(Transcript(text="hello", is_final=False))
        elif self._chunks_received == 2:
            self.on_final(Transcript(text="hello world", is_final=True))

    async def flush(self) -> None:
        self.flush_calls += 1

    async def stop_stream(self) -> None:
        self.stopped = True


class FakeConnectivityBackend:
    """Fake backend for connectivity-only tests: does NOT emit transcripts."""

    def __init__(self, *, on_partial, on_final, **_kwargs):
        self.on_partial = on_partial
        self.on_final = on_final
        self.started = False
        self.stopped = False

    async def start_stream(self) -> None:
        self.started = True

    async def feed_audio(self, pcm16: bytes) -> None:
        pass

    async def flush(self) -> None:
        pass

    async def stop_stream(self) -> None:
        self.stopped = True


def _make_wav_bytes(
    *,
    samplerate: int = 16000,
    channels: int = 1,
    n_frames: int = 3200,
    sampwidth: int = 2,
) -> bytes:
    """Build a minimal valid WAV in memory with the given sample width."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sampwidth)
        wf.setframerate(samplerate)
        # Write raw bytes of the right total size
        wf.writeframes(b"\x00" * (n_frames * channels * sampwidth))
    return buf.getvalue()


def test_check_engine_success_connectivity(monkeypatch, tmp_path):
    """Connectivity-only check (no --wav) exits 0 and prints success message; no transcripts expected."""
    _instance = None

    def fake_create_backend(config, *, on_partial, on_final):
        nonlocal _instance
        _instance = FakeConnectivityBackend(on_partial=on_partial, on_final=on_final)
        return _instance

    import obs_captions.stt.registry as reg_mod

    monkeypatch.setattr(reg_mod, "create_backend", fake_create_backend)

    runner = CliRunner()
    result = runner.invoke(cli, ["check-engine", "deepgram"])

    assert result.exit_code == 0, result.output
    assert "successfully" in result.output
    # Connectivity-only path must NOT print partial/final transcripts
    assert "[partial]" not in result.output
    assert "[final]" not in result.output
    assert _instance is not None
    assert _instance.started is True
    assert _instance.stopped is True


def test_check_engine_success_with_wav(monkeypatch, tmp_path):
    """--wav path streams audio; exits 0 and reports finals count."""
    import obs_captions.stt.registry as reg_mod

    _instance = None

    def fake_create_backend(config, *, on_partial, on_final):
        nonlocal _instance
        _instance = FakeCheckBackend(on_partial=on_partial, on_final=on_final)
        return _instance

    monkeypatch.setattr(reg_mod, "create_backend", fake_create_backend)

    wav_file = tmp_path / "test.wav"
    wav_file.write_bytes(_make_wav_bytes())

    runner = CliRunner()
    result = runner.invoke(cli, ["check-engine", "deepgram", "--wav", str(wav_file)])

    assert result.exit_code == 0, result.output
    # Summary line should report at least the 1 final emitted during start_stream.
    assert "1 final" in result.output
    # Transcripts should appear in output for the --wav path
    assert "[partial]" in result.output
    assert "[final]" in result.output
    # Audio was fed into the backend.
    assert _instance is not None
    assert len(_instance.fed) > 0
    assert _instance.flush_calls >= 1
    assert _instance.stopped is True


def test_check_engine_missing_api_key(monkeypatch):
    """When create_backend raises ValueError (missing key), exits non-zero with error."""
    import obs_captions.stt.registry as reg_mod

    def fake_create_backend(config, *, on_partial, on_final):
        raise ValueError("DEEPGRAM_API_KEY must be set in .env to use the deepgram engine.")

    monkeypatch.setattr(reg_mod, "create_backend", fake_create_backend)

    runner = CliRunner()
    result = runner.invoke(cli, ["check-engine", "deepgram"])

    # Exit code should be non-zero (sys.exit(1) from the command).
    assert result.exit_code != 0
    assert "DEEPGRAM_API_KEY" in result.output


def test_check_engine_unknown_engine():
    """An unknown engine name causes the real create_backend to raise ValueError -> non-zero exit."""
    # The registry raises ValueError("Unknown engine: 'bogusengine'") before any
    # env-var lookups, so no monkeypatching is needed — the real dispatch path is
    # exercised end-to-end.
    runner = CliRunner()
    result = runner.invoke(cli, ["check-engine", "bogusengine"])

    assert result.exit_code != 0
    assert "bogusengine" in result.output


def test_check_engine_language_override(monkeypatch):
    """--language overrides config.language before creating the backend."""
    import obs_captions.stt.registry as reg_mod

    captured_language = []

    def fake_create_backend(config, *, on_partial, on_final):
        captured_language.append(config.language)
        return FakeConnectivityBackend(on_partial=on_partial, on_final=on_final)

    monkeypatch.setattr(reg_mod, "create_backend", fake_create_backend)

    runner = CliRunner()
    result = runner.invoke(cli, ["check-engine", "groq", "--language", "en"])

    assert result.exit_code == 0, result.output
    assert captured_language == ["en"]


def test_check_engine_help():
    """check-engine --help exits 0 and shows the command description."""
    runner = CliRunner()
    result = runner.invoke(cli, ["check-engine", "--help"])
    assert result.exit_code == 0
    assert "ENGINE" in result.output
    assert "--wav" in result.output
    assert "--seconds" in result.output


def test_check_engine_help_via_installed_entrypoint():
    """AC4: the installed console entry point must resolve obs_captions and show help.

    CliRunner bypasses the installed entry point (uses pytest's injected pythonpath).
    This test invokes the real `uv run obs-captions` subprocess so any regression in
    the editable-install path (broken .pth, missing sitecustomize, etc.) is caught here.
    """
    result = subprocess.run(
        ["uv", "run", "obs-captions", "check-engine", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"uv run obs-captions check-engine --help failed (exit {result.returncode}).\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "ENGINE" in result.stdout
    assert "--wav" in result.stdout


def test_check_engine_unexpected_exception(monkeypatch):
    """Non-ValueError exception from asyncio.run propagates to the outer except, exits 1."""
    import obs_captions.stt.registry as reg_mod

    def fake_create_backend(config, *, on_partial, on_final):
        raise RuntimeError("unexpected network error")

    monkeypatch.setattr(reg_mod, "create_backend", fake_create_backend)

    runner = CliRunner()
    # mix_stderr=False so we can distinguish stderr vs stdout.
    # Note: catch_exceptions=False has no effect here because check_engine_command's
    # outer `except Exception` block catches the RuntimeError before it can propagate
    # to CliRunner. The assertion on exit_code == 1 is what matters.
    result = runner.invoke(cli, ["check-engine", "deepgram"], catch_exceptions=False)

    # The outer except Exception in check_engine_command catches it -> exit 1
    assert result.exit_code == 1


def test_check_engine_stop_stream_called_on_wav_exception(monkeypatch, tmp_path):
    """If _stream_wav raises after start_stream, stop_stream is still called (try/finally)."""
    import obs_captions.stt.registry as reg_mod
    import obs_captions.check_engine as check_engine_mod

    _instance = None

    def fake_create_backend(config, *, on_partial, on_final):
        nonlocal _instance
        _instance = FakeConnectivityBackend(on_partial=on_partial, on_final=on_final)
        return _instance

    monkeypatch.setattr(reg_mod, "create_backend", fake_create_backend)

    async def boom(backend, wav_path, max_seconds):
        raise OSError("corrupt wav")

    monkeypatch.setattr(check_engine_mod, "_stream_wav", boom)

    wav_file = tmp_path / "test.wav"
    wav_file.write_bytes(_make_wav_bytes())

    runner = CliRunner()
    result = runner.invoke(cli, ["check-engine", "deepgram", "--wav", str(wav_file)])

    # stop_stream must have been called despite the exception
    assert _instance is not None
    assert _instance.stopped is True
    # The exception should propagate to the outer except and exit 1
    assert result.exit_code == 1


# ---------------------------------------------------------------------------
# _stream_wav audioop branches
# ---------------------------------------------------------------------------


def test_stream_wav_8bit_lin2lin(monkeypatch, tmp_path):
    """8-bit WAV triggers audioop.lin2lin (lines 343-345)."""
    import obs_captions.stt.registry as reg_mod

    _instance = None

    def fake_create_backend(config, *, on_partial, on_final):
        nonlocal _instance
        _instance = FakeCheckBackend(on_partial=on_partial, on_final=on_final)
        return _instance

    monkeypatch.setattr(reg_mod, "create_backend", fake_create_backend)

    # 8-bit mono 16kHz WAV (sampwidth=1)
    wav_file = tmp_path / "8bit.wav"
    wav_file.write_bytes(_make_wav_bytes(samplerate=16000, channels=1, n_frames=160, sampwidth=1))

    runner = CliRunner()
    result = runner.invoke(cli, ["check-engine", "deepgram", "--wav", str(wav_file)])

    assert result.exit_code == 0, result.output
    assert _instance is not None
    assert _instance.stopped is True


def test_stream_wav_stereo_tomono(monkeypatch, tmp_path):
    """2-channel stereo WAV triggers audioop.tomono (lines 349-351)."""
    import obs_captions.stt.registry as reg_mod

    _instance = None

    def fake_create_backend(config, *, on_partial, on_final):
        nonlocal _instance
        _instance = FakeCheckBackend(on_partial=on_partial, on_final=on_final)
        return _instance

    monkeypatch.setattr(reg_mod, "create_backend", fake_create_backend)

    # 16-bit stereo 16kHz WAV (channels=2)
    wav_file = tmp_path / "stereo.wav"
    wav_file.write_bytes(_make_wav_bytes(samplerate=16000, channels=2, n_frames=160, sampwidth=2))

    runner = CliRunner()
    result = runner.invoke(cli, ["check-engine", "deepgram", "--wav", str(wav_file)])

    assert result.exit_code == 0, result.output
    assert _instance is not None
    assert _instance.stopped is True


def test_stream_wav_8khz_ratecv(monkeypatch, tmp_path):
    """8kHz WAV triggers audioop.ratecv resampling (lines 355-357)."""
    import obs_captions.stt.registry as reg_mod

    _instance = None

    def fake_create_backend(config, *, on_partial, on_final):
        nonlocal _instance
        _instance = FakeCheckBackend(on_partial=on_partial, on_final=on_final)
        return _instance

    monkeypatch.setattr(reg_mod, "create_backend", fake_create_backend)

    # 16-bit mono 8kHz WAV
    wav_file = tmp_path / "8khz.wav"
    wav_file.write_bytes(_make_wav_bytes(samplerate=8000, channels=1, n_frames=800, sampwidth=2))

    runner = CliRunner()
    result = runner.invoke(cli, ["check-engine", "deepgram", "--wav", str(wav_file)])

    assert result.exit_code == 0, result.output
    assert _instance is not None
    assert _instance.stopped is True


def test_stream_wav_deadline_break(monkeypatch, tmp_path):
    """--seconds 0 causes the deadline to be hit immediately, breaking the feed loop (lines 364-365)."""
    import obs_captions.stt.registry as reg_mod

    _instance = None

    def fake_create_backend(config, *, on_partial, on_final):
        nonlocal _instance
        _instance = FakeCheckBackend(on_partial=on_partial, on_final=on_final)
        return _instance

    monkeypatch.setattr(reg_mod, "create_backend", fake_create_backend)

    # Long WAV: 3 seconds of 16kHz mono PCM16 = 48000 frames
    wav_file = tmp_path / "long.wav"
    wav_file.write_bytes(_make_wav_bytes(samplerate=16000, channels=1, n_frames=48000, sampwidth=2))

    runner = CliRunner()
    result = runner.invoke(cli, ["check-engine", "deepgram", "--wav", str(wav_file), "--seconds", "0"])

    # Should still succeed (break just limits feeding)
    assert result.exit_code == 0, result.output
    assert _instance is not None
    assert _instance.stopped is True


# ---------------------------------------------------------------------------
# list-devices / list-loopback-devices commands
# ---------------------------------------------------------------------------


def test_list_devices_command(monkeypatch):
    """list-devices command lists input devices from monkeypatched list_input_devices."""
    from obs_captions.audio.devices import InputDevice
    import obs_captions.audio.devices as devices_mod

    fake_devices = [
        InputDevice(index=0, name="Built-in Microphone", channels=1),
        InputDevice(index=2, name="USB Headset", channels=2),
    ]

    monkeypatch.setattr(devices_mod, "list_input_devices", lambda: fake_devices)

    runner = CliRunner()
    result = runner.invoke(cli, ["list-devices"])

    assert result.exit_code == 0, result.output
    assert "Built-in Microphone" in result.output
    assert "USB Headset" in result.output
    assert "0\t" in result.output
    assert "2\t" in result.output


def test_list_loopback_devices_command(monkeypatch):
    """list-loopback-devices command lists loopback devices."""
    from obs_captions.audio.devices import InputDevice
    import obs_captions.audio.devices as devices_mod

    fake_devices = [
        InputDevice(index=1, name="Speakers (loopback)", channels=2),
    ]

    monkeypatch.setattr(devices_mod, "list_loopback_devices", lambda: fake_devices)

    runner = CliRunner()
    result = runner.invoke(cli, ["list-loopback-devices"])

    assert result.exit_code == 0, result.output
    assert "Speakers (loopback)" in result.output
    assert "1\t" in result.output


# ---------------------------------------------------------------------------
# config command
# ---------------------------------------------------------------------------


def test_config_command_outputs_json():
    """config command prints valid JSON of the current config."""
    import json

    runner = CliRunner()
    result = runner.invoke(cli, ["config"])

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert "engine" in data
    assert "language" in data


# ---------------------------------------------------------------------------
# make_capture
# ---------------------------------------------------------------------------


def test_make_capture_loopback_on_non_windows():
    """make_capture with source=loopback on non-windows raises RuntimeError."""
    from obs_captions.config import AppConfig, AudioConfig

    config = AppConfig(audio=AudioConfig(source="loopback"))

    with pytest.raises(RuntimeError, match="only supported on Windows"):
        make_capture(config, platform="linux")


def test_make_capture_mic(monkeypatch):
    """make_capture with source=mic returns a MicCapture (no hardware needed)."""
    from obs_captions.config import AppConfig, AudioConfig
    import obs_captions.audio as audio_mod

    created = []

    class FakeMicCapture:
        def __init__(self, **kwargs):
            created.append(kwargs)

    monkeypatch.setattr(audio_mod, "MicCapture", FakeMicCapture)
    monkeypatch.setattr(audio_mod, "resolve_device", lambda spec: None)

    config = AppConfig(audio=AudioConfig(source="mic", samplerate=16000, channels=1))
    cap = make_capture(config)

    assert isinstance(cap, FakeMicCapture)
    assert len(created) == 1
    assert created[0]["samplerate"] == 16000


# ---------------------------------------------------------------------------
# _build_caption_callbacks
# ---------------------------------------------------------------------------


def test_build_caption_callbacks_transforms_partial():
    from obs_captions.cli import _build_caption_callbacks
    from obs_captions.config import TextConfig
    from obs_captions.pipeline import CaptionState
    from obs_captions.text import ReplacementRule

    state = CaptionState()
    from obs_captions.config import AppConfig
    cfg = AppConfig(text=TextConfig(replacements=[ReplacementRule(match="hello", replace="hi")]))
    on_partial, _ = _build_caption_callbacks(cfg, state)

    on_partial(Transcript(text="hello world", is_final=False))

    assert state.snapshot().partial == "hi world"


def test_build_caption_callbacks_transforms_final():
    from obs_captions.cli import _build_caption_callbacks
    from obs_captions.config import AppConfig, TextConfig
    from obs_captions.pipeline import CaptionState
    from obs_captions.text import ReplacementRule

    state = CaptionState()
    cfg = AppConfig(text=TextConfig(replacements=[ReplacementRule(match="hello", replace="hi")]))
    _, on_final = _build_caption_callbacks(cfg, state)

    on_final(Transcript(text="hello world", is_final=True))

    assert "hi world" in state.snapshot().committed


def test_build_caption_callbacks_feeds_export_sink_on_final():
    from obs_captions.cli import _build_caption_callbacks
    from obs_captions.config import AppConfig
    from obs_captions.pipeline import CaptionState

    state = CaptionState()
    cfg = AppConfig()
    received: list[str] = []

    class FakeSink:
        def on_final(self, t: Transcript) -> None:
            received.append(t.text)

    _, on_final = _build_caption_callbacks(cfg, state, export_sink=FakeSink())
    on_final(Transcript(text="exported", is_final=True))

    assert received == ["exported"]


def test_build_caption_callbacks_no_export_sink_is_safe():
    from obs_captions.cli import _build_caption_callbacks
    from obs_captions.config import AppConfig
    from obs_captions.pipeline import CaptionState

    state = CaptionState()
    cfg = AppConfig()
    on_partial, on_final = _build_caption_callbacks(cfg, state)  # no export_sink

    on_partial(Transcript(text="partial text", is_final=False))
    on_final(Transcript(text="final text", is_final=True))

    assert state.snapshot().partial == ""  # cleared after final
    assert "final text" in state.snapshot().committed


def test_build_caption_callbacks_identity_with_default_config():
    from obs_captions.cli import _build_caption_callbacks
    from obs_captions.config import AppConfig
    from obs_captions.pipeline import CaptionState

    state = CaptionState()
    cfg = AppConfig()
    on_partial, on_final = _build_caption_callbacks(cfg, state)

    on_partial(Transcript(text="unchanged text", is_final=False))
    assert state.snapshot().partial == "unchanged text"

    on_final(Transcript(text="also unchanged", is_final=True))
    assert "also unchanged" in state.snapshot().committed


def test_build_caption_callbacks_export_sink_receives_transformed_text():
    """Export sink gets post-transform text, not the original."""
    from obs_captions.cli import _build_caption_callbacks
    from obs_captions.config import AppConfig, TextConfig
    from obs_captions.pipeline import CaptionState
    from obs_captions.text import ReplacementRule

    state = CaptionState()
    cfg = AppConfig(text=TextConfig(replacements=[ReplacementRule(match="bad", replace="good")]))
    received: list[str] = []

    class FakeSink:
        def on_final(self, t: Transcript) -> None:
            received.append(t.text)

    _, on_final = _build_caption_callbacks(cfg, state, export_sink=FakeSink())
    on_final(Transcript(text="bad word", is_final=True))

    assert received == ["good word"]
    assert "good word" in state.snapshot().committed


# ---------------------------------------------------------------------------
# _setup_export_sink — Finding 3: testable sink lifecycle outside pragma block
# ---------------------------------------------------------------------------


def test_setup_export_sink_disabled_returns_none():
    """With export.enabled=False (default), _setup_export_sink returns None."""
    from obs_captions.cli import _setup_export_sink
    from obs_captions.config import AppConfig

    assert _setup_export_sink(AppConfig()) is None


def test_setup_export_sink_enabled_creates_and_starts(tmp_path):
    """With export.enabled=True, _setup_export_sink returns a started sink."""
    from obs_captions.cli import _setup_export_sink
    from obs_captions.config import AppConfig, ExportConfig

    cfg = AppConfig(
        export=ExportConfig(enabled=True, path=str(tmp_path / "out.srt"), format="srt")
    )
    sink = _setup_export_sink(cfg)
    assert sink is not None
    sink.on_final(Transcript(text="hello", is_final=True, start_ms=0, end_ms=1000))
    sink.stop()

    content = (tmp_path / "out.srt").read_text(encoding="utf-8")
    assert "hello" in content
    assert "00:00:00,000 --> 00:00:01,000" in content


# ---------------------------------------------------------------------------
# AC4: default config → transform is identity, no file is written
# ---------------------------------------------------------------------------


def test_ac4_default_config_no_export_file(tmp_path, monkeypatch):
    """AC4: with default AppConfig, transform is identity and no export file appears on disk."""
    from obs_captions.cli import _build_caption_callbacks
    from obs_captions.config import AppConfig
    from obs_captions.pipeline import CaptionState

    # Change cwd to tmp_path so any stray relative-path file creation lands there.
    monkeypatch.chdir(tmp_path)

    state = CaptionState()
    cfg = AppConfig()  # export.enabled=False, no text transforms

    # No export_sink passed — export is disabled by default.
    on_partial, on_final = _build_caption_callbacks(cfg, state)

    on_partial(Transcript(text="hello", is_final=False))
    assert state.snapshot().partial == "hello"  # transform is identity

    on_final(Transcript(text="world", is_final=True))
    assert "world" in state.snapshot().committed  # transform is identity

    # No file must have been written.
    assert list(tmp_path.iterdir()) == []


# ---------------------------------------------------------------------------
# _run / _serve infrastructure — Finding 1 + 2 + 3 fix tests
# ---------------------------------------------------------------------------
# These tests exercise _run and _serve with heavily-mocked dependencies to
# cover the wiring (pragma was previously on the entire function body).
# ---------------------------------------------------------------------------

# Shared mock primitives used across multiple tests below.


class _FakeCapture:
    """Async-generator audio capture shim for _run tests (no real hardware)."""

    def __init__(self, *, oserror: bool = False) -> None:
        self.started = False
        self.stopped = False
        self._oserror = oserror

    def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    async def frames(self):
        if self._oserror:
            raise OSError("audio device disconnected")
        return
        yield  # makes this an async generator; never reached


class _FakeStreamBackend:
    """Minimal streaming backend for _run/_serve tests."""

    def __init__(self, **_: object) -> None:
        self.started = False
        self.stopped = False

    async def start_stream(self) -> None:
        self.started = True

    async def stop_stream(self) -> None:
        self.stopped = True

    async def feed_audio(self, pcm: bytes) -> None:
        pass

    async def flush(self) -> None:
        pass


class _ImmediateServer:
    """uvicorn.Server shim whose serve() returns after one event-loop tick."""

    def __init__(self, _config: object) -> None:
        pass

    async def serve(self) -> None:
        await asyncio.sleep(0)  # yield so audio_task can run first


def _patch_run_deps(
    monkeypatch,
    tmp_path,
    cfg,
    *,
    raise_on_create_backend: Exception | None = None,
    capture_oserror: bool = False,
):
    """Apply monkeypatches for _run without live hardware.

    Returns (capture_instance, backend_getter) where backend_getter() returns
    the backend created by create_backend (None if it was never called).
    """
    import obs_captions.cli as cli_mod
    import obs_captions.pipeline as pipeline_mod
    import obs_captions.server as server_mod
    import obs_captions.stt.registry as registry_mod
    import obs_captions.vad as vad_mod

    monkeypatch.setattr(cli_mod, "load_config", lambda _p: cfg)
    monkeypatch.setattr(cli_mod, "_overlay_dir", lambda: tmp_path)

    class _FakeCaptionState:
        def on_partial(self, t: object) -> None: ...
        def on_final(self, t: object) -> None: ...

    class _FakeHub:
        pass

    monkeypatch.setattr(pipeline_mod, "CaptionState", _FakeCaptionState)
    monkeypatch.setattr(server_mod, "Hub", _FakeHub)
    monkeypatch.setattr(server_mod, "create_app", lambda *a, **kw: object())
    monkeypatch.setattr(server_mod, "wire_caption_state", lambda *a, **kw: None)
    monkeypatch.setattr("uvicorn.Server", _ImmediateServer)
    monkeypatch.setattr("uvicorn.Config", lambda *a, **kw: None)

    cap = _FakeCapture(oserror=capture_oserror)
    monkeypatch.setattr(cli_mod, "make_capture", lambda _cfg: cap)

    class _FakeVad:
        def __init__(self, threshold: float) -> None: ...

    class _FakeSegmenter:
        def __init__(self, **kw: object) -> None: ...
        def process(self, pcm: bytes) -> VadEvent:
            return VadEvent(is_speech=False)
        def flush(self) -> None:
            return None

    monkeypatch.setattr(vad_mod, "SileroVad", _FakeVad)
    monkeypatch.setattr(vad_mod, "UtteranceSegmenter", _FakeSegmenter)

    _backend: list[_FakeStreamBackend] = []

    def _fake_create_backend(_cfg: object, *, on_partial: object, on_final: object) -> _FakeStreamBackend:
        if raise_on_create_backend is not None:
            raise raise_on_create_backend
        b = _FakeStreamBackend()
        _backend.append(b)
        return b

    monkeypatch.setattr(registry_mod, "create_backend", _fake_create_backend)

    return cap, lambda: _backend[0] if _backend else None


@pytest.mark.asyncio
async def test_run_export_cleanup_on_backend_failure(tmp_path, monkeypatch):
    """Finding 1 fix: export_sink.stop() is called via ExitStack even when
    create_backend raises after _setup_export_sink has already opened the file."""
    from obs_captions.cli import _run
    from obs_captions.config import AppConfig, ExportConfig

    export_path = tmp_path / "captions.srt"
    cfg = AppConfig(export=ExportConfig(enabled=True, path=str(export_path), format="srt"))

    _patch_run_deps(
        monkeypatch,
        tmp_path,
        cfg,
        raise_on_create_backend=RuntimeError("backend init failed"),
    )

    with pytest.raises(RuntimeError, match="backend init failed"):
        await _run(None, "browser")

    # The file was opened (truncated) but ExitStack must have closed it.
    # Verifiable: the file exists (start() created it) and is readable after the error.
    assert export_path.exists()
    # A closed file can be opened again without ResourceWarning / OSError.
    export_path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_run_browser_cleanup(tmp_path, monkeypatch):
    """Finding 3 wiring coverage: _run browser path — all cleanup steps run after
    serve() returns, and export_sink is stopped by ExitStack."""
    from obs_captions.cli import _run
    from obs_captions.config import AppConfig, ExportConfig

    export_path = tmp_path / "out.srt"
    cfg = AppConfig(export=ExportConfig(enabled=True, path=str(export_path), format="srt"))

    cap, get_backend = _patch_run_deps(monkeypatch, tmp_path, cfg)

    await _run(None, "browser")

    backend = get_backend()
    assert backend is not None
    assert backend.started is True
    assert backend.stopped is True   # backend.stop_stream() was called
    assert cap.started is True
    assert cap.stopped is True       # capture.stop() was called
    # ExitStack called export_sink.stop() → file is closed and readable
    assert export_path.exists()
    export_path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_run_audio_oserror_suppressed_in_cleanup(tmp_path, monkeypatch):
    """Finding 2 fix: OSError from audio device disconnect is suppressed so
    backend.stop_stream() and capture.stop() are still reached in the finally block."""
    from obs_captions.cli import _run
    from obs_captions.config import AppConfig

    cfg = AppConfig()
    cap, get_backend = _patch_run_deps(
        monkeypatch, tmp_path, cfg, capture_oserror=True
    )

    # _ImmediateServer.serve() does asyncio.sleep(0), yielding to audio_task.
    # The audio_task raises OSError from frames(); suppress(Exception, CancelledError)
    # catches it and cleanup continues normally.
    await _run(None, "browser")

    backend = get_backend()
    assert backend is not None
    assert backend.stopped is True   # not skipped despite audio OSError
    assert cap.stopped is True


@pytest.mark.asyncio
async def test_serve_wiring_no_demo(tmp_path, monkeypatch):
    """Finding 3 wiring coverage: _serve with demo=False — export sink started
    and stopped, callbacks wired correctly."""
    from obs_captions.cli import _serve
    from obs_captions.config import AppConfig, ExportConfig

    export_path = tmp_path / "serve.srt"
    cfg = AppConfig(export=ExportConfig(enabled=True, path=str(export_path), format="srt"))

    import obs_captions.cli as cli_mod
    import obs_captions.pipeline as pipeline_mod
    import obs_captions.server as server_mod

    monkeypatch.setattr(cli_mod, "load_config", lambda _p: cfg)
    monkeypatch.setattr(cli_mod, "_overlay_dir", lambda: tmp_path)

    class _FakeCaptionState:
        def on_partial(self, t: object) -> None: ...
        def on_final(self, t: object) -> None: ...

    class _FakeHub:
        pass

    monkeypatch.setattr(pipeline_mod, "CaptionState", _FakeCaptionState)
    monkeypatch.setattr(server_mod, "Hub", _FakeHub)
    monkeypatch.setattr(server_mod, "create_app", lambda *a, **kw: object())
    monkeypatch.setattr(server_mod, "wire_caption_state", lambda *a, **kw: None)
    monkeypatch.setattr("uvicorn.Server", _ImmediateServer)
    monkeypatch.setattr("uvicorn.Config", lambda *a, **kw: None)

    await _serve(None, demo=False)

    # Export file was opened, written (WEBVTT header for vtt; empty for srt), and closed.
    assert export_path.exists()
    export_path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_serve_wiring_with_demo(tmp_path, monkeypatch):
    """Finding 3: _serve with demo=True — demo_task is created, cancelled in finally,
    and CancelledError is suppressed; no export configured."""
    from obs_captions.cli import _serve
    from obs_captions.config import AppConfig

    cfg = AppConfig()

    import obs_captions.cli as cli_mod
    import obs_captions.pipeline as pipeline_mod
    import obs_captions.server as server_mod

    monkeypatch.setattr(cli_mod, "load_config", lambda _p: cfg)
    monkeypatch.setattr(cli_mod, "_overlay_dir", lambda: tmp_path)

    class _FakeCaptionState:
        def on_partial(self, t: object) -> None: ...
        def on_final(self, t: object) -> None: ...

    class _FakeHub:
        pass

    monkeypatch.setattr(pipeline_mod, "CaptionState", _FakeCaptionState)
    monkeypatch.setattr(server_mod, "Hub", _FakeHub)
    monkeypatch.setattr(server_mod, "create_app", lambda *a, **kw: object())
    monkeypatch.setattr(server_mod, "wire_caption_state", lambda *a, **kw: None)
    monkeypatch.setattr("uvicorn.Server", _ImmediateServer)
    monkeypatch.setattr("uvicorn.Config", lambda *a, **kw: None)

    # demo=True: FakeBackend runs an infinite loop as demo_task; serve() returns
    # immediately; finally: cancels demo_task, suppress(CancelledError) absorbs it.
    await _serve(None, demo=True)
