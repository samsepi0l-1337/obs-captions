from __future__ import annotations

import asyncio
import contextlib
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, NamedTuple

from obs_captions.config import load_config
from obs_captions.stt import FakeBackend
from obs_captions.stt.registry import create_backend as _default_create_backend


class VadParams(NamedTuple):
    """Resolved VAD segmenter parameters (threshold, min_silence_ms, frame_ms)."""

    threshold: float
    min_silence_ms: int
    frame_ms: int = 100


def _resolve_vad_params(config: Any) -> VadParams:
    """Map ``config`` to VAD segmenter params.

    The ``local`` engine uses the tuned ``config.local`` values; every other
    (cloud) engine uses fixed defaults, since those backends do their own VAD.
    """
    if config.engine == "local":
        return VadParams(
            threshold=config.local.vad_threshold,
            min_silence_ms=config.local.min_silence_ms,
        )
    return VadParams(threshold=0.5, min_silence_ms=500)


def make_capture(
    config: Any,
    *,
    platform: str | None = None,
    pyaudio_module: Any | None = None,
) -> Any:
    """Build the audio capture for ``config.audio.source`` ("mic" or "loopback").

    For ``source="loopback"`` (Windows only) the loopback device + its native
    sample rate are resolved and passed to MicCapture so the existing resample
    (native 48k stereo -> 16k mono) handles the conversion.
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
    """Create and start a TranscriptExportSink when export is enabled; return None otherwise."""
    if not cfg.export.enabled:
        return None
    from obs_captions.export_sink import TranscriptExportSink

    sink = TranscriptExportSink(cfg.export.path, cfg.export.format)
    sink.start()
    return sink


def _build_caption_callbacks(
    cfg: Any, state: Any, export_sink: Any | None = None
) -> tuple[Any, Any]:
    """Return (on_partial, on_final) callables that apply text transforms then notify *state*."""
    from dataclasses import replace as dc_replace

    from obs_captions.text import should_suppress, transform_text

    def _t(tr: Any) -> Any:
        return dc_replace(tr, text=transform_text(tr.text, cfg.text))

    def on_partial(tr: Any) -> None:
        t = _t(tr)
        if not t.text.strip() and cfg.text.suppress_blank:
            state.on_partial(dc_replace(t, text=""))
            return
        if should_suppress(t.text, cfg.text):
            return
        state.on_partial(t)

    def on_final(tr: Any) -> None:
        t = _t(tr)
        if should_suppress(t.text, cfg.text):
            return
        state.on_final(t)
        if export_sink is not None:
            export_sink.on_final(t)

    return on_partial, on_final


async def _serve(
    config_path: str | None,
    demo: bool,
    *,
    load_config_fn: Callable[[str | None], Any] = load_config,
    overlay_dir_fn: Callable[[], Path] | None = None,
) -> None:
    from obs_captions.pipeline import CaptionState
    from obs_captions.server import Hub, create_app, wire_caption_state

    if overlay_dir_fn is None:
        overlay_dir_fn = _overlay_dir

    config = load_config_fn(config_path)
    hub = Hub()
    state = CaptionState()
    wire_caption_state(
        state,
        hub,
        loop=asyncio.get_running_loop(),
        max_chars_per_line=config.overlay.max_chars_per_line,
    )
    app = create_app(
        hub, overlay_dir=overlay_dir_fn(), config=config, config_path=config_path
    )

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

        server_host = "127.0.0.1"
        import uvicorn

        server = uvicorn.Server(
            uvicorn.Config(
                app,
                host=server_host,
                port=config.server.port,
                log_level="info",
            )
        )
        try:
            await server.serve()
        finally:
            if demo_task is not None:
                demo_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await demo_task


async def _run(
    config_path: str | None,
    sink: str = "browser",
    *,
    load_config_fn: Callable[[str | None], Any] = load_config,
    make_capture_fn: Callable[..., Any] = make_capture,
    create_backend_fn: Callable[..., Any] = _default_create_backend,
) -> None:
    from obs_captions.pipeline import CaptionState
    from obs_captions.server import Hub, create_app, wire_caption_state

    config = load_config_fn(config_path)
    state = CaptionState()

    obs_sink = None
    use_browser = sink in ("browser", "both")
    use_obs = sink in ("obs", "both")

    if use_browser:
        hub = Hub()
        wire_caption_state(
            state,
            hub,
            loop=asyncio.get_running_loop(),
            max_chars_per_line=config.overlay.max_chars_per_line,
        )
        app = create_app(
            hub, overlay_dir=_overlay_dir(), config=config, config_path=config_path
        )
        server_host = "127.0.0.1"
        import uvicorn

        uv_server = uvicorn.Server(
            uvicorn.Config(
                app, host=server_host, port=config.server.port, log_level="info"
            )
        )

    if use_obs:  # pragma: no cover
        from obs_captions.obs_sink import ObsTextSink

        obs_sink = ObsTextSink(state=state, config=config)
        await obs_sink.start()

    with contextlib.ExitStack() as _cleanup:
        export_sink = _setup_export_sink(config)
        if export_sink is not None:
            _cleanup.callback(export_sink.stop)
        on_partial, on_final = _build_caption_callbacks(config, state, export_sink)
        backend = create_backend_fn(
            config, on_partial=on_partial, on_final=on_final
        )
        capture = make_capture_fn(config)
        from obs_captions.vad import SileroVad, UtteranceSegmenter
        vad_params = _resolve_vad_params(config)
        vad = SileroVad(threshold=vad_params.threshold)
        segmenter = UtteranceSegmenter(
            vad=vad,
            frame_ms=vad_params.frame_ms,
            min_silence_ms=vad_params.min_silence_ms,
        )

        controller = None
        hotkey_listener = None
        if config.obs.hotkey.enabled:
            from obs_captions.obs_hotkey import CaptionController, ObsHotkeyListener

            controller = CaptionController(state)
            hotkey_listener = ObsHotkeyListener(config=config, controller=controller)
            await hotkey_listener.start()

        audio_task = asyncio.create_task(
            _capture_to_backend(capture, segmenter, backend, controller)
        )

        try:
            if use_browser:
                await uv_server.serve()
            else:
                await asyncio.Event().wait()  # pragma: no cover
        finally:
            audio_task.cancel()
            with contextlib.suppress(Exception, asyncio.CancelledError):
                await audio_task
            await capture.stop()
            await backend.stop_stream()
            if hotkey_listener is not None:
                await hotkey_listener.stop()
            if obs_sink is not None:  # pragma: no cover
                await obs_sink.stop()


async def _capture_to_backend(capture, segmenter, backend, controller=None) -> None:
    await backend.start_stream()
    capture.start()
    try:
        async for pcm16 in capture.frames():
            if controller is not None and controller.paused:
                continue
            event = segmenter.process(pcm16)
            if event.is_speech:
                await backend.feed_audio(pcm16)
            if event.segment is not None:
                await backend.flush()
    finally:
        if segmenter.flush() is not None:
            await backend.flush()


async def _run_demo_backend(backend: FakeBackend) -> None:  # pragma: no cover
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


def _overlay_dir() -> Path:  # pragma: no cover
    from obs_captions.packaging import resolve_overlay_dir

    return resolve_overlay_dir()
