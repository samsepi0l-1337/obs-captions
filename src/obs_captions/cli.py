from __future__ import annotations

import asyncio
import contextlib
import json
import sys
from pathlib import Path
from typing import Any

import click
import uvicorn

from obs_captions.config import load_config, redacted_config
from obs_captions.stt import FakeBackend


@click.group(name="obs-captions")
def cli() -> None:
    """OBS live-caption CLI."""
    from obs_captions.platform_dll import add_cuda_dll_directories

    # Make pip-installed nvidia-* CUDA/cuDNN DLLs visible to CTranslate2 on
    # Windows. No-op off Windows. Covers both the console-script and
    # ``python -m obs_captions`` entry points (both run this group callback).
    add_cuda_dll_directories()


@cli.command("list-devices")
def list_devices() -> None:
    from obs_captions.audio.devices import list_input_devices

    for device in list_input_devices():
        click.echo(f"{device.index}\t{device.name}\t{device.channels}")


@cli.command("list-loopback-devices")
def list_loopback_devices_command() -> None:
    """List WASAPI loopback (system-audio) devices for `[audio] source = "loopback"` (Windows)."""
    from obs_captions.audio.devices import list_loopback_devices

    for device in list_loopback_devices():
        click.echo(f"{device.index}\t{device.name}\t{device.channels}")


@cli.command("run")
@click.option("--config", "config_path", type=click.Path(exists=True, dir_okay=False), default=None)
@click.option(
    "--sink",
    type=click.Choice(["browser", "obs", "both"], case_sensitive=False),
    default="browser",
    show_default=True,
    help="Output sink: browser (WS overlay server), obs (obs-websocket Text source), or both.",
)
def run_command(config_path: str | None, sink: str) -> None:  # pragma: no cover  # invokes asyncio.run with a live server
    asyncio.run(_run(config_path, sink))


@cli.command("config")
@click.option("--config", "config_path", type=click.Path(exists=True, dir_okay=False), default=None)
def config_command(config_path: str | None) -> None:
    config = load_config(config_path)
    click.echo(json.dumps(redacted_config(config), ensure_ascii=False, indent=2))


@cli.command("serve")
@click.option("--config", "config_path", type=click.Path(exists=True, dir_okay=False), default=None)
@click.option(
    "--demo", is_flag=True, help="Emit scripted fake Korean captions for browser/WS demos."
)
def serve_command(config_path: str | None, demo: bool) -> None:  # pragma: no cover  # invokes asyncio.run with a live uvicorn server
    asyncio.run(_serve(config_path, demo))


async def _serve(config_path: str | None, demo: bool) -> None:  # pragma: no cover  # requires a live uvicorn server
    from obs_captions.pipeline import CaptionState
    from obs_captions.server import Hub, create_app, wire_caption_state

    config = load_config(config_path)
    hub = Hub()
    state = CaptionState()
    wire_caption_state(state, hub, loop=asyncio.get_running_loop())
    app = create_app(hub, overlay_dir=_overlay_dir(), config=config)

    demo_task: asyncio.Task[None] | None = None
    if demo:
        backend = FakeBackend(
            language=config.language, on_partial=state.on_partial, on_final=state.on_final
        )
        demo_task = asyncio.create_task(_run_demo_backend(backend))

    server = uvicorn.Server(
        uvicorn.Config(app, host=config.server.host, port=config.server.port, log_level="info")
    )
    try:
        await server.serve()
    finally:
        if demo_task is not None:
            demo_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await demo_task


def make_capture(
    config: Any,
    *,
    platform: str | None = None,
    pyaudio_module: Any | None = None,
) -> Any:
    """Build the audio capture for ``config.audio.source`` ("mic" or "loopback").

    For ``source="loopback"`` (Windows only) the loopback device + its native
    sample rate are resolved and passed to MicCapture so the existing resample
    (native 48k stereo -> 16k mono) handles the conversion. ``platform`` and
    ``pyaudio_module`` are injectable for testing without Windows hardware.
    """
    from obs_captions.audio import MicCapture, resolve_device

    if config.audio.source == "loopback":
        current = platform if platform is not None else sys.platform
        if current != "win32":
            raise RuntimeError(
                "audio.source='loopback' captures Windows system audio (WASAPI) and is "
                "only supported on Windows. macOS/Linux need a virtual loopback device "
                "(out of scope); use source='mic' on this platform."
            )
        from obs_captions.audio.loopback import (
            make_loopback_stream_factory,
            resolve_loopback_device,
        )

        device = resolve_loopback_device(config.audio.device, pyaudio_module=pyaudio_module)
        factory = make_loopback_stream_factory(
            device_channels=device.channels, pyaudio_module=pyaudio_module
        )
        return MicCapture(
            device=device.index,
            samplerate=device.samplerate,
            channels=1,
            blocksize=max(1, device.samplerate // 10),
            stream_factory=factory,
        )

    return MicCapture(
        device=resolve_device(config.audio.device),
        samplerate=config.audio.samplerate,
        channels=config.audio.channels,
        blocksize=max(1, config.audio.samplerate // 10),
    )


async def _run(config_path: str | None, sink: str = "browser") -> None:  # pragma: no cover  # requires live audio capture + uvicorn server
    from obs_captions.pipeline import CaptionState
    from obs_captions.server import Hub, create_app, wire_caption_state
    from obs_captions.stt.registry import create_backend
    from obs_captions.vad import SileroVad, UtteranceSegmenter

    config = load_config(config_path)

    state = CaptionState()

    obs_sink = None

    use_browser = sink in ("browser", "both")
    use_obs = sink in ("obs", "both")

    if use_browser:
        hub = Hub()
        wire_caption_state(state, hub, loop=asyncio.get_running_loop())
        app = create_app(hub, overlay_dir=_overlay_dir(), config=config)
        uv_server = uvicorn.Server(
            uvicorn.Config(app, host=config.server.host, port=config.server.port, log_level="info")
        )

    if use_obs:
        from obs_captions.obs_sink import ObsTextSink

        obs_sink = ObsTextSink(state=state, config=config)
        await obs_sink.start()

    backend = create_backend(config, on_partial=state.on_partial, on_final=state.on_final)
    capture = make_capture(config)
    is_local = config.engine == "local"
    vad_threshold = config.local.vad_threshold if is_local else 0.5
    min_silence_ms = config.local.min_silence_ms if is_local else 500
    vad = SileroVad(threshold=vad_threshold)
    segmenter = UtteranceSegmenter(
        vad=vad,
        frame_ms=100,
        min_silence_ms=min_silence_ms,
    )
    audio_task = asyncio.create_task(_capture_to_backend(capture, segmenter, backend))

    try:
        if use_browser:
            await uv_server.serve()
        else:
            # obs-only: run until interrupted
            await asyncio.Event().wait()
    finally:
        audio_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await audio_task
        await capture.stop()
        await backend.stop_stream()
        if obs_sink is not None:
            await obs_sink.stop()


async def _capture_to_backend(capture, segmenter, backend) -> None:
    await backend.start_stream()
    capture.start()
    try:
        async for pcm16 in capture.frames():
            event = segmenter.process(pcm16)
            if event.is_speech:
                await backend.feed_audio(pcm16)
            if event.segment is not None:
                await backend.flush()
    finally:
        if segmenter.flush() is not None:
            await backend.flush()


async def _run_demo_backend(backend: FakeBackend) -> None:  # pragma: no cover  # long-running demo loop; not unit-testable
    script = [
        ["안", "안녕하세요", "안녕하세요 여러분"],
        ["오", "오늘 방송", "오늘 방송 자막 테스트입니다"],
        ["잠", "잠시 후", "잠시 후 시작합니다"],
    ]
    await backend.start_stream()
    while True:
        for phrase in script:
            for text in phrase:
                backend.emit_partial(text)
                await asyncio.sleep(0.5)
            backend.emit_final(phrase[-1])
            await asyncio.sleep(1.5)


@cli.command("check-engine")
@click.argument("engine")
@click.option(
    "--wav",
    "wav_path",
    type=click.Path(exists=True, dir_okay=False),
    default=None,
    help="Path to a WAV file to stream through the backend. Omit for connectivity-only check.",
)
@click.option(
    "--seconds",
    default=10,
    show_default=True,
    help="Maximum seconds to wait for transcripts.",
)
@click.option(
    "--language",
    default=None,
    help="Language code override (e.g. en, ko). Defaults to config value.",
)
@click.option("--config", "config_path", type=click.Path(exists=True, dir_okay=False), default=None)
def check_engine_command(
    engine: str,
    wav_path: str | None,
    seconds: int,
    language: str | None,
    config_path: str | None,
) -> None:
    """Smoke-test ENGINE: validates API key/region and optionally streams audio."""
    try:
        exit_code = asyncio.run(_check_engine(engine, wav_path, seconds, language, config_path))
    except Exception as exc:  # noqa: BLE001
        click.echo(f"ERROR: {exc}", err=True)
        sys.exit(1)
    else:
        sys.exit(exit_code)


async def _check_engine(
    engine: str,
    wav_path: str | None,
    seconds: int,
    language: str | None,
    config_path: str | None,
) -> int:
    """Return 0 on success, 1 on failure."""
    from obs_captions.stt.registry import create_backend

    config = load_config(config_path)
    # Override engine + language for this smoke run without mutating the model.
    object.__setattr__(config, "engine", engine)  # type: ignore[arg-type]
    if language is not None:
        object.__setattr__(config, "language", language)  # type: ignore[arg-type]

    finals: list[str] = []
    partials: list[str] = []

    def on_partial(t: Any) -> None:
        partials.append(t.text)
        click.echo(f"[partial] {t.text}")

    def on_final(t: Any) -> None:
        finals.append(t.text)
        click.echo(f"[final]   {t.text}")

    try:
        backend = create_backend(config, on_partial=on_partial, on_final=on_final)
    except ValueError as exc:
        click.echo(f"ERROR: {exc}", err=True)
        return 1

    await backend.start_stream()

    try:
        if wav_path is not None:
            await _stream_wav(backend, wav_path, seconds)
            await backend.flush()
            # Wait briefly for in-flight transcript events from streaming providers
            # (e.g. AssemblyAI/Deepgram) whose flush() is a no-op and whose recv
            # loop processes events asynchronously.  We poll up to the remaining
            # deadline for at least one final before stopping.
            deadline = asyncio.get_running_loop().time() + seconds
            while not finals and asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(0.05)
        else:
            click.echo(f"Connectivity check for engine '{engine}' — no WAV provided, stopping immediately.")
    finally:
        await backend.stop_stream()

    if wav_path is not None:
        n_finals = len(finals)
        n_partials = len(partials)
        if n_finals == 0:
            click.echo(
                "WARNING: 0 final(s) received after streaming WAV — "
                "provider may not have transcribed audio.",
                err=True,
            )
        click.echo(f"Done. {n_finals} final(s), {n_partials} partial(s) received.")
    else:
        click.echo(f"Engine '{engine}' started and stopped successfully.")
    return 0


async def _stream_wav(backend: Any, wav_path: str, max_seconds: int) -> None:
    """Read a WAV file and feed PCM16 chunks into the backend."""
    import wave

    chunk_samples = 1600  # 100 ms of 16 kHz mono
    deadline = asyncio.get_running_loop().time() + max_seconds

    with wave.open(wav_path, "rb") as wf:
        src_rate = wf.getframerate()
        src_channels = wf.getnchannels()
        src_width = wf.getsampwidth()
        raw = wf.readframes(wf.getnframes())

    # Normalize to 16-bit PCM
    if src_width != 2:
        import audioop  # noqa: PLC0415  # TODO: migrate to numpy when Python 3.13 is supported (audioop removed in 3.13)  # noqa: DEP001

        raw = audioop.lin2lin(raw, src_width, 2)

    # Mix down to mono if stereo
    if src_channels > 1:
        import audioop  # noqa: PLC0415  # TODO: migrate to numpy when Python 3.13 is supported (audioop removed in 3.13)  # noqa: DEP001

        raw = audioop.tomono(raw, 2, 0.5, 0.5)

    # Resample to 16000 Hz if needed
    if src_rate != 16000:
        import audioop  # noqa: PLC0415  # TODO: migrate to numpy when Python 3.13 is supported (audioop removed in 3.13)  # noqa: DEP001

        raw, _ = audioop.ratecv(raw, 2, 1, src_rate, 16000, None)

    # Feed in 100ms chunks
    offset = 0
    chunk_bytes = chunk_samples * 2
    while offset < len(raw):
        if asyncio.get_running_loop().time() >= deadline:
            click.echo(f"[check-engine] Reached {max_seconds}s limit.", err=True)
            break
        chunk = raw[offset : offset + chunk_bytes]
        await backend.feed_audio(chunk)
        offset += chunk_bytes
        await asyncio.sleep(0.1)


def _overlay_dir() -> Path:  # pragma: no cover  # only called from _serve/_run which requires a live server
    # Resolve the bundled overlay assets relative to the package (dev/pip) or
    # the PyInstaller bundle (frozen) — never CWD-relative. See packaging.py.
    from obs_captions.packaging import resolve_overlay_dir

    return resolve_overlay_dir()
