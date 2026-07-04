from __future__ import annotations

import asyncio
import logging
from pathlib import Path
import os
from typing import Any, Callable
from urllib.parse import urlparse

import secrets

from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Header,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from pydantic import ValidationError

from obs_captions.config import (
    AppConfig,
    OverlayConfig,
    redacted_config,
    save_config,
    write_env_keys,
)
from obs_captions.packaging import resolve_overlay_dir, resolve_settings_dir
from obs_captions.security import load_or_create_session_token
from obs_captions.pipeline import CaptionSnapshot, CaptionState
from obs_captions.server.hub import Hub
from obs_captions.server.overlay_style import overlay_css_variables
from obs_captions.text import wrap_text

logger = logging.getLogger(__name__)

# Sentinel: distinguishes "overlay_dir not provided" (resolve from the package
# via packaging.resolve_overlay_dir) from an explicit ``None`` ("do not mount
# static assets" — used by WS-only tests).
_UNSET = object()
_ALLOWED_API_HOSTS = {"127.0.0.1", "localhost"}


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


def _sync_model(target: BaseModel, source: BaseModel) -> None:
    for field_name in source.__class__.model_fields:
        current = getattr(target, field_name)
        value = getattr(source, field_name)
        if isinstance(current, dict) and isinstance(value, dict):
            setattr(target, field_name, value)
            continue
        if isinstance(current, BaseModel) and isinstance(value, BaseModel):
            _sync_model(current, value)
            continue
        setattr(target, field_name, value)


def _api_host(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None

    parsed = urlparse(value)
    if parsed.hostname:
        return parsed.hostname.lower()

    if value.startswith("["):
        right = value.find("]")
        if right == -1:
            return None
        return value[1:right].lower()

    if ":" in value:
        return value.rsplit(":", 1)[0].lower()

    return value.lower()


def _api_host_guard(require_token: bool) -> Callable[..., Any]:
    async def _guard(
        request: Request,
        x_obs_token: str | None = Header(default=None, alias="X-OBS-Token"),
    ) -> None:
        host = _api_host(request.headers.get("host"))
        if host not in _ALLOWED_API_HOSTS:
            raise HTTPException(status_code=403)

        origin = _api_host(request.headers.get("origin"))
        if origin is not None and origin not in _ALLOWED_API_HOSTS:
            raise HTTPException(status_code=403)

        sec_fetch_site = request.headers.get("sec-fetch-site", "").lower()
        if sec_fetch_site and sec_fetch_site not in {"same-origin", "none"}:
            raise HTTPException(status_code=403)

        if not require_token:
            return

        stored = getattr(request.app.state, "session_token", "")
        if not x_obs_token or not secrets.compare_digest(stored, x_obs_token):
            raise HTTPException(status_code=401)

    return _guard


def create_app(
    hub: Hub,
    overlay_dir: str | Path | None = _UNSET,  # type: ignore[assignment]
    config: AppConfig | OverlayConfig | None = None,
    config_path: str | Path | None = None,
) -> FastAPI:
    app = FastAPI()
    app.state.session_token = load_or_create_session_token()
    app_config = config if isinstance(config, AppConfig) else AppConfig()
    if isinstance(config, OverlayConfig):
        app_config.overlay = config
    overlay_config = _overlay_config(app_config)
    # Default to the package-resolved overlay dir; explicit ``None`` skips the mount.
    if overlay_dir is _UNSET:
        overlay_dir = resolve_overlay_dir()

    engine_info = _engine_env_mapping()
    settings_dir = resolve_settings_dir()

    @app.get("/api/config", dependencies=[Depends(_api_host_guard(require_token=True))])
    async def get_api_config() -> dict[str, object]:
        return redacted_config(app_config)

    @app.post(
        "/api/config",
        dependencies=[Depends(_api_host_guard(require_token=True))],
    )
    async def post_api_config(body: dict[str, Any]) -> dict[str, object]:
        sanitized = dict(body)
        sensitive_keys = {
            "openai_api_key",
            "elevenlabs_api_key",
            "openrouter_api_key",
            "replicate_api_token",
            "xai_api_key",
            "gemini_api_key",
        }
        allowed = set(AppConfig.model_fields)
        for field_name in list(sanitized):
            if field_name in sensitive_keys or field_name not in allowed:
                sanitized.pop(field_name, None)
        try:
            validated = AppConfig.model_validate(sanitized)
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=exc.errors()) from exc
        save_path = Path(config_path) if config_path is not None else Path("config.toml")
        save_config(validated, save_path)
        for field_name in AppConfig.model_fields:
            current = getattr(app_config, field_name)
            value = getattr(validated, field_name)
            if isinstance(current, BaseModel) and isinstance(value, BaseModel):
                _sync_model(current, value)
            else:
                setattr(app_config, field_name, value)
        return redacted_config(app_config)

    @app.post("/api/keys", dependencies=[Depends(_api_host_guard(require_token=True))])
    async def post_api_keys(body: dict[str, str]) -> dict[str, bool]:
        return write_env_keys(".env", body)

    @app.get("/api/engines", dependencies=[Depends(_api_host_guard(require_token=True))])
    async def get_api_engines() -> list[dict[str, Any]]:
        return engine_info

    @app.get("/api/keys/status", dependencies=[Depends(_api_host_guard(require_token=True))])
    async def get_api_keys_status() -> dict[str, bool]:
        key_names: set[str] = set()
        for item in engine_info:
            key_names.update(item.get("env", []))
            key_names.update(
                env_name
                for mode in item.get("modes", {}).values()
                for env_name in mode.get("env", [])
            )
        return {name: bool(os.getenv(name)) for name in sorted(key_names)}

    @app.get("/api/session", dependencies=[Depends(_api_host_guard(require_token=False))])
    async def get_api_session(request: Request) -> dict[str, str]:
        return {"token": request.app.state.session_token}

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

    @app.get("/settings", include_in_schema=False)
    async def redirect_settings() -> Response:
        return RedirectResponse("/settings/")

    settings_path = Path(settings_dir)
    if settings_path.exists():
        app.mount("/settings", StaticFiles(directory=settings_path, html=True), name="settings")

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


def _engine_env_mapping() -> list[dict[str, Any]]:
    return [
        {"engine": "local", "label": "Local", "env": [], "local": True},
        {"engine": "openai", "label": "OpenAI", "env": ["OPENAI_API_KEY"]},
        {"engine": "elevenlabs", "label": "ElevenLabs", "env": ["ELEVENLABS_API_KEY"]},
        {"engine": "xai", "label": "xAI", "env": ["XAI_API_KEY"]},
        {"engine": "openrouter", "label": "OpenRouter", "env": ["OPENROUTER_API_KEY"]},
        {"engine": "replicate", "label": "Replicate", "env": ["REPLICATE_API_TOKEN"]},
        {"engine": "assemblyai", "label": "AssemblyAI", "env": ["ASSEMBLYAI_API_KEY"]},
        {
            "engine": "google",
            "label": "Google",
            "env": ["GEMINI_API_KEY", "GOOGLE_CLOUD_PROJECT"],
            "modes": {
                "gemini": {"env": ["GEMINI_API_KEY"]},
                "speech_v2": {"env": ["GOOGLE_CLOUD_PROJECT"]},
            },
        },
        {"engine": "azure", "label": "Azure", "env": ["AZURE_SPEECH_KEY", "AZURE_SPEECH_REGION"]},
        {"engine": "deepgram", "label": "Deepgram", "env": ["DEEPGRAM_API_KEY"]},
        {"engine": "groq", "label": "Groq", "env": ["GROQ_API_KEY"]},
    ]
