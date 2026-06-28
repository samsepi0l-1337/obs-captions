# ruff: noqa: E402
import warnings

warnings.filterwarnings("ignore", message="Using `httpx` with `starlette.testclient`.*")

import asyncio
from unittest.mock import patch

import pytest
from pydantic import ValidationError
from starlette.testclient import TestClient

from obs_captions.pipeline import CaptionSnapshot, CaptionState
from obs_captions.config import AppConfig, OverlayConfig
from obs_captions.packaging import resolve_overlay_dir
from obs_captions.server.app import caption_state_to_message, create_app, wire_caption_state
from obs_captions.server.overlay_style import overlay_css_variables
from obs_captions.server.hub import Hub
from fastapi.websockets import WebSocketDisconnect


def test_caption_state_to_message_uses_ws_contract():
    assert caption_state_to_message(CaptionSnapshot(committed=["확정"], partial="진행")) == {
        "type": "caption",
        "partial": "진행",
        "committed": ["확정"],
    }


def test_websocket_client_receives_broadcast_caption_json():
    hub = Hub()
    app = create_app(hub, overlay_dir=None)

    with TestClient(app) as client:
        with client.websocket_connect("/ws") as websocket:
            assert websocket.receive_json() == {
                "type": "caption",
                "partial": "",
                "committed": [],
            }

            message = {"type": "caption", "partial": "안녕", "committed": ["첫 줄"]}
            client.portal.call(hub.broadcast, message)

            assert websocket.receive_json() == message


def test_new_websocket_client_receives_current_snapshot_immediately():
    hub = Hub()
    app = create_app(hub, overlay_dir=None)
    message = {"type": "caption", "partial": "현재", "committed": ["이전"]}

    with TestClient(app) as client:
        client.portal.call(hub.broadcast, message)
        with client.websocket_connect("/ws") as websocket:
            assert websocket.receive_json() == message


def _partial(text: str):
    from obs_captions.stt import Transcript

    return Transcript(text=text, is_final=False)


async def test_wire_caption_state_subscribes_without_clobbering_other_subscribers():
    """sink=both: wiring the browser broadcast must not clobber a second subscriber."""
    hub = Hub()
    state = CaptionState(max_lines=3)
    sink_hits: list[CaptionSnapshot] = []

    # Second subscriber, registered like ObsTextSink does.
    state.subscribe(sink_hits.append)
    wire_caption_state(state, hub, loop=asyncio.get_running_loop())

    state.on_partial(_partial("안녕"))
    # Let the threadsafe-scheduled broadcast task run.
    for _ in range(4):
        await asyncio.sleep(0)

    # OBS sink callback still fired (browser wiring did not clobber it)...
    assert sink_hits == [CaptionSnapshot(committed=[], partial="안녕")]
    # ...and the browser broadcast reached the hub.
    assert hub.last_snapshot == {"type": "caption", "partial": "안녕", "committed": []}


async def test_websocket_endpoint_calls_disconnect_exactly_once_on_normal_path():
    """GAP-B: hub.disconnect must be called EXACTLY once on the normal WebSocketDisconnect
    path. FAILS if disconnect is called zero times (finally removed) or twice
    (inner except AND finally both call it — double-call regression)."""

    disconnect_calls: list[object] = []

    class CountingHub(Hub):
        async def connect(self, ws) -> None:  # type: ignore[override]
            pass  # accept without sending snapshot

        async def disconnect(self, ws) -> None:  # type: ignore[override]
            disconnect_calls.append(ws)

    hub = CountingHub()
    app = create_app(hub, overlay_dir=None)
    endpoint = next(r.endpoint for r in app.routes if getattr(r, "path", None) == "/ws")

    class NormalDisconnectWS:
        """Simulates a client that disconnects cleanly after one receive."""

        async def receive_text(self):
            raise WebSocketDisconnect(code=1000)

    ws = NormalDisconnectWS()
    # WebSocketDisconnect is swallowed by the except clause — endpoint returns normally.
    await endpoint(ws)

    assert disconnect_calls == [ws], (
        f"Expected hub.disconnect called exactly once with ws, got: {disconnect_calls}"
    )


async def test_websocket_endpoint_disconnects_on_non_disconnect_exception():
    """A non-WebSocketDisconnect error in the receive loop still calls hub.disconnect."""

    class BoomHub(Hub):
        def __init__(self) -> None:
            super().__init__()
            self.disconnected: list[object] = []

        async def connect(self, ws):  # type: ignore[override]
            pass

        async def disconnect(self, ws):  # type: ignore[override]
            self.disconnected.append(ws)

    hub = BoomHub()
    app = create_app(hub, overlay_dir=None)
    endpoint = next(r.endpoint for r in app.routes if getattr(r, "path", None) == "/ws")

    class BoomWS:
        async def receive_text(self):
            raise ValueError("boom")

    ws = BoomWS()
    with pytest.raises(ValueError, match="boom"):
        await endpoint(ws)

    assert hub.disconnected == [ws]


async def test_scheduled_broadcast_exception_is_surfaced_to_done_callback(caplog):
    """wire_caption_state's fire-and-forget broadcast must not swallow exceptions."""
    import logging

    class FailingHub(Hub):
        async def broadcast(self, message):  # type: ignore[override]
            raise RuntimeError("broadcast failed")

    hub = FailingHub()
    state = CaptionState(max_lines=3)
    wire_caption_state(state, hub, loop=asyncio.get_running_loop())

    with caplog.at_level(logging.ERROR):
        state.on_partial(_partial("안녕"))
        # Let the threadsafe schedule, the task body, and the done-callback all run.
        for _ in range(4):
            await asyncio.sleep(0)

    # Must be surfaced by our own done-callback (named logger + specific message),
    # not asyncio's GC-time "Task exception was never retrieved" default handler.
    assert any(
        r.name == "obs_captions.server.app" and "background broadcast task failed" in r.getMessage()
        for r in caplog.records
    )


async def test_scheduled_broadcast_task_retained_while_pending_then_drained():
    """wire_caption_state must hold a ref to the in-flight broadcast task (NON-EMPTY

    while pending) and drain it after completion. FAILS if retention is removed.
    """
    import obs_captions.server.app as app_module

    captured: dict[str, set] = {}
    release = asyncio.Event()
    started = asyncio.Event()

    class SlowHub(Hub):
        async def broadcast(self, message):  # type: ignore[override]
            started.set()
            await release.wait()

    hub = SlowHub()
    state = CaptionState(max_lines=3)

    real_create_task = app_module.asyncio.create_task
    created: list[asyncio.Task] = []

    def spy_create_task(coro):  # type: ignore[no-untyped-def]
        task = real_create_task(coro)
        created.append(task)
        return task

    with patch.object(app_module.asyncio, "create_task", spy_create_task):
        wire_caption_state(state, hub, loop=asyncio.get_running_loop())
        state.on_partial(_partial("안녕"))
        await asyncio.wait_for(started.wait(), timeout=1.0)
        # Callbacks are attached synchronously after create_task; the discard
        # done-callback is bound to the live `tasks` set — recover it.
        assert len(created) == 1
        task = created[0]
        for cb in task._callbacks:  # type: ignore[attr-defined]
            target = cb[0] if isinstance(cb, tuple) else cb
            owner = getattr(target, "__self__", None)
            if isinstance(owner, set):
                captured["tasks"] = owner
        # NON-EMPTY while the broadcast is in flight (retention proven).
        assert len(captured["tasks"]) == 1
        assert task in captured["tasks"]
        release.set()
        for _ in range(4):
            await asyncio.sleep(0)

    assert captured["tasks"] == set()  # drained after completion


async def test_websocket_endpoint_evicts_client_when_initial_send_fails():
    """Defect 3: if hub.connect's initial send raises, the client must still be

    disconnected/evicted (no permanent leak). FAILS if connect runs outside the
    try/finally that calls disconnect.
    """

    class LeakyHub(Hub):
        def __init__(self) -> None:
            super().__init__()
            self.disconnected: list[object] = []

        async def connect(self, ws):  # type: ignore[override]
            self._clients.add(ws)
            raise ConnectionError("initial send failed")

        async def disconnect(self, ws):  # type: ignore[override]
            self._clients.discard(ws)
            self.disconnected.append(ws)

    hub = LeakyHub()
    app = create_app(hub, overlay_dir=None)
    endpoint = next(r.endpoint for r in app.routes if getattr(r, "path", None) == "/ws")

    class DummyWS:
        async def receive_text(self):
            return ""

    ws = DummyWS()
    with pytest.raises(ConnectionError, match="initial send failed"):
        await endpoint(ws)

    assert hub.disconnected == [ws]
    assert ws not in hub._clients


async def test_broadcast_evicts_client_raising_non_runtimeerror_without_blocking_healthy():
    """A client whose send_json raises (non-RuntimeError) is evicted and does not block others."""

    class FakeClient:
        def __init__(self, *, fail: bool) -> None:
            self.fail = fail
            self.received: list[dict] = []

        async def send_json(self, message: dict) -> None:
            if self.fail:
                raise ConnectionError("dead socket")
            self.received.append(message)

    hub = Hub()
    bad = FakeClient(fail=True)
    good = FakeClient(fail=False)
    hub._clients = {bad, good}

    await hub.broadcast({"type": "caption", "partial": "안녕", "committed": []})

    assert bad not in hub._clients
    assert good in hub._clients
    assert good.received == [{"type": "caption", "partial": "안녕", "committed": []}]


def test_overlay_css_variables_maps_config_knobs():
    css = overlay_css_variables(
        OverlayConfig(
            font_family="Pretendard",
            font_size=64,
            font_weight=900,
            color="#00ff00",
            partial_color="#444444",
            background="transparent",
            outline_width=4,
            outline_color="#111111",
            shadow="none",
            position="top",
            align="left",
            max_lines=2,
            line_height=1.5,
            padding=12,
            letter_spacing=1,
            fade_ms=150,
            uppercase=True,
        )
    )

    assert css.startswith(":root{")
    assert "--cap-font-family: Pretendard;" in css
    assert "--cap-font-size: 64px;" in css
    assert "--cap-outline-width: 4px;" in css
    assert "--cap-justify-content: flex-start;" in css
    assert "--cap-align: left;" in css
    assert "--cap-text-transform: uppercase;" in css
    assert "--cap-fade-ms: 150ms;" in css


def test_overlay_html_contains_dom_and_style_links():
    app = create_app(Hub(), overlay_dir=resolve_overlay_dir(), config=AppConfig())

    with TestClient(app) as client:
        response = client.get("/overlay.html")

    assert response.status_code == 200
    assert '<div class="caption-box">' in response.text
    assert '<span class="committed"></span>' in response.text
    assert '<span class="partial"></span>' in response.text
    assert 'href="overlay.css"' in response.text
    assert 'href="/overlay-style.css"' in response.text
    assert 'href="/custom.css"' in response.text
    html = response.text
    assert html.index("overlay.css") < html.index("/overlay-style.css") < html.index("/custom.css")


def test_overlay_style_css_route_uses_current_config():
    app = create_app(
        Hub(),
        overlay_dir=resolve_overlay_dir(),
        config=AppConfig(overlay=OverlayConfig(font_size=72)),
    )

    with TestClient(app) as client:
        response = client.get("/overlay-style.css")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/css")
    assert ":root{" in response.text
    assert "--cap-font-size: 72px;" in response.text


def test_custom_css_route_serves_configured_file(tmp_path):
    custom_css = tmp_path / "custom.css"
    custom_css.write_text(".caption { color: red; }", encoding="utf-8")
    app = create_app(
        Hub(),
        overlay_dir=resolve_overlay_dir(),
        config=AppConfig(overlay=OverlayConfig(custom_css=str(custom_css))),
    )

    with TestClient(app) as client:
        response = client.get("/custom.css")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/css")
    assert response.text == ".caption { color: red; }"


def test_custom_css_route_404_when_not_configured():
    app = create_app(Hub(), overlay_dir=resolve_overlay_dir(), config=AppConfig())

    with TestClient(app) as client:
        response = client.get("/custom.css")

    assert response.status_code == 404


def test_overlay_config_font_weight_out_of_range_raises():
    with pytest.raises(ValidationError):
        OverlayConfig(font_weight=1000)


# ---------------------------------------------------------------------------
# Gap #2 — create_app() _UNSET branch and overlay_dir=None branch
# ---------------------------------------------------------------------------


def test_create_app_default_overlay_dir_mounts_overlay_and_serves_html(monkeypatch, tmp_path):
    """create_app() with no overlay_dir arg resolves from the package and mounts the
    static overlay; GET /overlay.html must return 200 with the real overlay content.

    This exercises the _UNSET sentinel branch: overlay_dir is omitted so
    create_app() calls resolve_overlay_dir() internally.

    Crucially, the process is chdir'd into a TEMP dir that has NO web/ subdir.
    A 200 here proves the resolution is __file__-based, NOT cwd-based.
    """
    # Change cwd to a temp dir with no web/ dir — cwd-relative resolution would fail.
    assert not (tmp_path / "web").exists(), "tmp_path must have no web/ dir"
    monkeypatch.chdir(tmp_path)

    app = create_app(Hub())  # overlay_dir omitted → _UNSET → resolved from package

    with TestClient(app) as client:
        response = client.get("/overlay.html")

    assert response.status_code == 200, (
        f"Expected 200 from /overlay.html when overlay_dir is auto-resolved from a "
        f"cwd with no web/ dir; got {response.status_code}. "
        "Resolution must be __file__-based (packaging.resolve_overlay_dir), not cwd-based."
    )
    # Confirm it's the real overlay HTML (not an empty stub).
    assert '<div class="caption-box">' in response.text


def test_log_task_exception_returns_silently_for_cancelled_task():
    """Line 76: _log_task_exception returns early and logs nothing for cancelled tasks."""
    import contextlib
    from obs_captions.server.app import _log_task_exception

    async def noop() -> None:
        await asyncio.sleep(10)

    loop = asyncio.new_event_loop()
    try:
        task = loop.create_task(noop())
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            loop.run_until_complete(task)
        # Must not raise and must not attempt task.exception() (which would raise).
        _log_task_exception(task)
    finally:
        loop.close()


def test_custom_css_route_404_when_configured_but_file_missing():
    """Line 117: custom.css returns 404 when path is configured but the file doesn't exist."""
    app = create_app(
        Hub(),
        overlay_dir=None,
        config=AppConfig(overlay=OverlayConfig(custom_css="/nonexistent/path/custom.css")),
    )

    with TestClient(app) as client:
        response = client.get("/custom.css")

    assert response.status_code == 404


def test_create_app_with_overlay_config_directly():
    """Line 133: _overlay_config passes OverlayConfig through unchanged."""
    overlay_cfg = OverlayConfig(font_size=60)
    app = create_app(Hub(), overlay_dir=None, config=overlay_cfg)

    with TestClient(app) as client:
        response = client.get("/overlay-style.css")

    assert response.status_code == 200
    assert "--cap-font-size: 60px;" in response.text


def test_create_app_overlay_dir_none_skips_mount_but_ws_works():
    """create_app(overlay_dir=None) must skip the static mount entirely.

    The WebSocket endpoint must still be wired; /overlay.html must return 404
    (no static files mounted), not crash.
    """
    hub = Hub()
    app = create_app(hub, overlay_dir=None)

    with TestClient(app) as client:
        # Static overlay is NOT mounted — no 200 for overlay.html.
        response = client.get("/overlay.html")
        assert response.status_code == 404, (
            f"Expected 404 for /overlay.html when overlay_dir=None; got {response.status_code}"
        )

        # WebSocket endpoint is still present and functional.
        with client.websocket_connect("/ws") as ws:
            initial = ws.receive_json()
    assert initial == {"type": "caption", "partial": "", "committed": []}
