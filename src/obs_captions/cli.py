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


async def _serve(config_path: str | None, demo: bool) -> None:
    from obs_captions.pipeline import CaptionState
    from obs_captions.server import Hub, create_app, wire_caption_state

    config = load_config(config_path)
    hub = Hub()
    state = CaptionState()
    wire_caption_state(state, hub, loop=asyncio.get_running_loop())
    app = create_app(hub, overlay_dir=_overlay_dir(), config=config)

    # Finding 4/7 fix: use ExitStack so export_sink.stop() is guaranteed even if
    # FakeBackend() or asyncio.create_task() raises before the try/finally below
    # (mirrors the ExitStack pattern already used in _run).
    with contextlib.ExitStack() as _cleanup:
        export_sink = _setup_export_sink(config)
        if export_sink is not None:
            _cleanup.callback(export_sink.stop)
        on_partial, on_final = _build_caption_callbacks(config, state, export_sink)

        demo_task: asyncio.Task[None] | None = None
        if demo:
            backend = FakeBackend(
                language=config.language, on_partial=on_partial, on_final=on_final
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
            # export_sink.stop() is handled by ExitStack when the with-block exits


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


def _setup_export_sink(cfg: Any) -> Any | None:
    """Create and start a TranscriptExportSink when export is enabled; return None otherwise.

    Extracted so that both _serve and _run share identical sink-lifecycle logic and
    tests can exercise the creation + start path without a live audio/server layer.
    """
    if not cfg.export.enabled:
        return None
    from obs_captions.export_sink import TranscriptExportSink

    sink = TranscriptExportSink(cfg.export.path, cfg.export.format)
    sink.start()
    return sink


def _build_caption_callbacks(cfg: Any, state: Any, export_sink: Any | None = None) -> tuple[Any, Any]:
    """Return (on_partial, on_final) callables that apply text transforms then notify *state*.

    When *export_sink* is provided, each final transcript is also forwarded to it
    after the transform.  Both callbacks use dataclasses.replace to produce a new
    frozen Transcript rather than mutating the original.
    """
    from dataclasses import replace as dc_replace

    from obs_captions.text import transform_text

    def _t(tr: Any) -> Any:
        return dc_replace(tr, text=transform_text(tr.text, cfg.text))

    def on_partial(tr: Any) -> None:
        state.on_partial(_t(tr))

    def on_final(tr: Any) -> None:
        t = _t(tr)
        state.on_final(t)
        if export_sink is not None:
            export_sink.on_final(t)

    return on_partial, on_final


async def _run(config_path: str | None, sink: str = "browser") -> None:
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

    if use_obs:  # pragma: no cover
        from obs_captions.obs_sink import ObsTextSink

        obs_sink = ObsTextSink(state=state, config=config)
        await obs_sink.start()

    # Finding 1 fix: use ExitStack to register export_sink.stop() immediately
    # after start() so it runs even if create_backend / make_capture / SileroVad
    # raise below — prevents the export file from being left truncated and open.
    with contextlib.ExitStack() as _cleanup:
        export_sink = _setup_export_sink(config)
        if export_sink is not None:
            _cleanup.callback(export_sink.stop)
        on_partial, on_final = _build_caption_callbacks(config, state, export_sink)
        backend = create_backend(config, on_partial=on_partial, on_final=on_final)
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
                await asyncio.Event().wait()  # pragma: no cover
        finally:
            audio_task.cancel()
            # Finding 2 fix: suppress both CancelledError and regular exceptions
            # so an OSError from an audio device disconnect does not bypass the
            # remaining cleanup steps (capture.stop / backend.stop_stream).
            with contextlib.suppress(Exception, asyncio.CancelledError):
                await audio_task
            await capture.stop()
            await backend.stop_stream()
            if obs_sink is not None:  # pragma: no cover
                await obs_sink.stop()
            # export_sink.stop() is handled by ExitStack when the with-block exits


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
    from obs_captions.check_engine import _check_engine
    try:
        exit_code = asyncio.run(_check_engine(engine, wav_path, seconds, language, config_path))
    except Exception as exc:  # noqa: BLE001
        click.echo(f"ERROR: {exc}", err=True)
        sys.exit(1)
    else:
        sys.exit(exit_code)


def _overlay_dir() -> Path:  # pragma: no cover  # only called from _serve/_run which requires a live server
    # Resolve the bundled overlay assets relative to the package (dev/pip) or
    # the PyInstaller bundle (frozen) — never CWD-relative. See packaging.py.
    from obs_captions.packaging import resolve_overlay_dir

    return resolve_overlay_dir()
