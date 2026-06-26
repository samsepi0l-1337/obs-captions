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
    from obs_captions import cli as cli_mod

    _instance = None

    def fake_create_backend(config, *, on_partial, on_final):
        nonlocal _instance
        _instance = FakeConnectivityBackend(on_partial=on_partial, on_final=on_final)
        return _instance

    monkeypatch.setattr(reg_mod, "create_backend", fake_create_backend)

    async def boom(backend, wav_path, max_seconds):
        raise OSError("corrupt wav")

    monkeypatch.setattr(cli_mod, "_stream_wav", boom)

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
