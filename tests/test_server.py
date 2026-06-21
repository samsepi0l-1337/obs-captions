# ruff: noqa: E402
import warnings

warnings.filterwarnings("ignore", message="Using `httpx` with `starlette.testclient`.*")

import pytest
from pydantic import ValidationError
from starlette.testclient import TestClient

from obs_captions.pipeline import CaptionSnapshot
from obs_captions.config import AppConfig, OverlayConfig
from obs_captions.server.app import caption_state_to_message, create_app
from obs_captions.server.overlay_style import overlay_css_variables
from obs_captions.server.hub import Hub


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
    app = create_app(Hub(), overlay_dir="web/overlay", config=AppConfig())

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
        overlay_dir="web/overlay",
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
        overlay_dir="web/overlay",
        config=AppConfig(overlay=OverlayConfig(custom_css=str(custom_css))),
    )

    with TestClient(app) as client:
        response = client.get("/custom.css")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/css")
    assert response.text == ".caption { color: red; }"


def test_custom_css_route_404_when_not_configured():
    app = create_app(Hub(), overlay_dir="web/overlay", config=AppConfig())

    with TestClient(app) as client:
        response = client.get("/custom.css")

    assert response.status_code == 404


def test_overlay_config_font_weight_out_of_range_raises():
    with pytest.raises(ValidationError):
        OverlayConfig(font_weight=1000)
