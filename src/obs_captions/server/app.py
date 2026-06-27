from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Response, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

from obs_captions.config import AppConfig, OverlayConfig
from obs_captions.packaging import resolve_overlay_dir
from obs_captions.pipeline import CaptionSnapshot, CaptionState
from obs_captions.server.hub import Hub
from obs_captions.server.overlay_style import overlay_css_variables
from obs_captions.text import wrap_text

logger = logging.getLogger(__name__)

# Sentinel: distinguishes "overlay_dir not provided" (resolve from the package
# via packaging.resolve_overlay_dir) from an explicit ``None`` ("do not mount
# static assets" — used by WS-only tests).
_UNSET = object()


def caption_state_to_message(snapshot: CaptionSnapshot, max_chars: int = 0) -> dict[str, Any]:
    """Convert *snapshot* to the WebSocket caption message dict.

    Feature 5: when *max_chars* > 0, committed lines are wrapped (each original
    line may expand to multiple entries in the output list) and the partial string
    is joined with newlines.  Wrapping uses codepoint count — correct for Korean
    Hangul (each syllable-block is one codepoint).
    """
    if max_chars > 0:
        committed: list[str] = []
        for line in snapshot.committed:
            committed.extend(wrap_text(line, max_chars))
        partial = "\n".join(wrap_text(snapshot.partial, max_chars)) if snapshot.partial else ""
    else:
        committed = list(snapshot.committed)
        partial = snapshot.partial
    return {
        "type": "caption",
        "partial": partial,
        "committed": committed,
    }


def wire_caption_state(
    state: CaptionState,
    hub: Hub,
    *,
    loop: asyncio.AbstractEventLoop | None = None,
    max_chars_per_line: int = 0,
) -> None:
    target_loop = loop or asyncio.get_running_loop()
    tasks: set[asyncio.Task[None]] = set()

    def on_change(snapshot: CaptionSnapshot) -> None:
        message = caption_state_to_message(snapshot, max_chars=max_chars_per_line)

        def schedule() -> None:
            task = asyncio.create_task(hub.broadcast(message))
            tasks.add(task)
            task.add_done_callback(_log_task_exception)
            task.add_done_callback(tasks.discard)

        target_loop.call_soon_threadsafe(schedule)

    state.subscribe(on_change)


def _log_task_exception(task: asyncio.Task[Any]) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error("background broadcast task failed: %s", exc, exc_info=exc)


def create_app(
    hub: Hub,
    overlay_dir: str | Path | None = _UNSET,  # type: ignore[assignment]
    config: AppConfig | OverlayConfig | None = None,
) -> FastAPI:
    app = FastAPI()
    overlay_config = _overlay_config(config)
    # Default to the package-resolved overlay dir; explicit ``None`` skips the mount.
    if overlay_dir is _UNSET:
        overlay_dir = resolve_overlay_dir()

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket) -> None:
        try:
            # connect() accepts, registers in _clients, AND sends the initial
            # snapshot. Keep it inside the try so a failure during the initial
            # send still routes to disconnect() — no permanently leaked client.
            await hub.connect(websocket)
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            await hub.disconnect(websocket)

    @app.get("/overlay-style.css")
    async def overlay_style_endpoint() -> Response:
        return Response(overlay_css_variables(overlay_config), media_type="text/css")

    @app.get("/custom.css")
    async def custom_css_endpoint() -> Response:
        if overlay_config.custom_css is None:
            raise HTTPException(status_code=404)
        custom_path = Path(overlay_config.custom_css)
        if not custom_path.is_file():
            raise HTTPException(status_code=404)
        return Response(custom_path.read_text(encoding="utf-8"), media_type="text/css")

    if overlay_dir is not None:
        overlay_path = Path(overlay_dir)
        if overlay_path.exists():
            app.mount("/", StaticFiles(directory=overlay_path, html=True), name="overlay")

    return app


def _overlay_config(config: AppConfig | OverlayConfig | None) -> OverlayConfig:
    if config is None:
        return OverlayConfig()
    if isinstance(config, AppConfig):
        return config.overlay
    return config
