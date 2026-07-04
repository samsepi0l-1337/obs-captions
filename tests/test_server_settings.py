import os

from starlette.testclient import TestClient

from obs_captions.config import AppConfig, OverlayConfig, load_config
from obs_captions.server.app import create_app
from obs_captions.server.hub import Hub


def _build_client(monkeypatch, tmp_path, *, config: AppConfig | None = None, config_path=None):
    monkeypatch.setenv("HOME", str(tmp_path))
    app = create_app(
        Hub(),
        overlay_dir=None,
        config=AppConfig() if config is None else config,
        config_path=config_path,
    )
    return TestClient(app, base_url="http://127.0.0.1:8765")


def _build_real_client(monkeypatch, tmp_path, *, config: AppConfig | None = None, config_path=None):
    monkeypatch.setenv("HOME", str(tmp_path))
    app = create_app(
        Hub(),
        config=AppConfig() if config is None else config,
        config_path=config_path,
    )
    return TestClient(app, base_url="http://127.0.0.1:8765")


def _session_token(client: TestClient) -> str:
    response = client.get("/api/session")
    assert response.status_code == 200
    token = response.json()["token"]
    assert isinstance(token, str)
    return token


def test_get_api_session_bootstrap_returns_token(monkeypatch, tmp_path):
    with _build_client(monkeypatch, tmp_path) as client:
        token = _session_token(client)
        response = client.post(
            "/api/config",
            json={"engine": "openai"},
            headers={"X-OBS-Token": token},
        )

    assert response.status_code == 200


def test_get_api_config_redacts_api_keys(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "openai-secret")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "eleven-secret")

    with _build_client(monkeypatch, tmp_path) as client:
        response = client.get("/api/config", headers={"X-OBS-Token": _session_token(client)})

    assert response.status_code == 200
    payload = response.json()
    assert payload["openai_api_key"] == "***"
    assert payload["elevenlabs_api_key"] == "***"
    assert "openai-secret" not in response.text
    assert "eleven-secret" not in response.text


def test_post_api_config_saves_and_returns_redacted(monkeypatch, tmp_path):
    with _build_client(monkeypatch, tmp_path, config_path=tmp_path / "settings.toml") as client:
        body = AppConfig(
            engine="openai",
            language="en",
            audio={"samplerate": 22050},
            overlay={"font_size": 60},
        ).model_dump(mode="python")

        response = client.post(
            "/api/config",
            json=body,
            headers={"X-OBS-Token": _session_token(client)},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["engine"] == "openai"
    loaded = load_config(str(tmp_path / "settings.toml"))
    assert loaded.engine == "openai"
    assert loaded.audio.samplerate == 22050
    assert loaded.overlay.font_size == 60


def test_post_api_config_rejects_invalid_body(monkeypatch, tmp_path):
    with _build_client(monkeypatch, tmp_path) as client:
        response = client.post(
            "/api/config",
            json={"engine": "not-an-engine"},
            headers={"X-OBS-Token": _session_token(client)},
        )

    assert response.status_code == 422


def test_post_api_config_roundtrip_works_with_redacted_response(monkeypatch, tmp_path):
    with _build_real_client(monkeypatch, tmp_path, config_path=tmp_path / "settings.toml") as client:
        headers = {"X-OBS-Token": _session_token(client)}
        baseline = client.get("/api/config", headers=headers)
        assert baseline.status_code == 200
        payload = baseline.json()
        payload["overlay"]["font_size"] = 99
        response = client.post(
            "/api/config",
            json=payload,
            headers=headers,
        )
        assert response.status_code == 200
        follow = client.get("/api/config", headers=headers)

    assert response.json()["overlay"]["font_size"] == 99
    assert follow.status_code == 200
    assert follow.json()["overlay"]["font_size"] == 99


def test_post_api_config_updates_live_overlay_preview(monkeypatch, tmp_path):
    with _build_client(
        monkeypatch,
        tmp_path,
        config=AppConfig(overlay=OverlayConfig(font_size=72)),
        config_path=tmp_path / "settings.toml",
    ) as client:
        response = client.post(
            "/api/config",
            json=AppConfig(overlay={"font_size": 99}).model_dump(mode="python"),
            headers={"X-OBS-Token": _session_token(client)},
        )
        assert response.status_code == 200
        css_response = client.get("/overlay-style.css")

    assert css_response.status_code == 200
    assert "--cap-font-size: 99px;" in css_response.text


def test_post_api_keys_writes_env_and_reinjects(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "before")
    with _build_client(monkeypatch, tmp_path) as client:
        response = client.post(
            "/api/keys",
            json={"OPENAI_API_KEY": "sk-test", "BOGUS_KEY": "x"},
            headers={"X-OBS-Token": _session_token(client)},
        )

    assert response.status_code == 200
    assert response.json() == {"OPENAI_API_KEY": True, "BOGUS_KEY": False}
    env_path = tmp_path / ".env"
    assert env_path.exists()
    env_text = env_path.read_text(encoding="utf-8")
    assert "OPENAI_API_KEY" in env_text
    assert os.environ["OPENAI_API_KEY"] == "sk-test"

    assert env_text is not None


def test_get_api_engines_covers_all_supported_engines(monkeypatch, tmp_path):
    with _build_client(monkeypatch, tmp_path) as client:
        response = client.get(
            "/api/engines",
            headers={"X-OBS-Token": _session_token(client)},
        )

    assert response.status_code == 200
    payload = response.json()
    engines = {item["engine"] for item in payload}
    expected = set(AppConfig.model_fields["engine"].annotation.__args__)  # type: ignore[attr-defined]
    assert engines == expected

    local_entry = next(item for item in payload if item["engine"] == "local")
    assert local_entry["local"] is True
    assert local_entry["env"] == []

    google_entry = next(item for item in payload if item["engine"] == "google")
    env_names = set(google_entry.get("env", []))
    for mode_payload in google_entry.get("modes", {}).values():
        env_names.update(mode_payload.get("env", []))
    assert {"GEMINI_API_KEY", "GOOGLE_CLOUD_PROJECT"} <= env_names

    azure_entry = next(item for item in payload if item["engine"] == "azure")
    azure_env = set(azure_entry.get("env", []))
    assert {"AZURE_SPEECH_KEY", "AZURE_SPEECH_REGION"} <= azure_env


def test_post_api_config_rejects_missing_token(monkeypatch, tmp_path):
    with _build_client(monkeypatch, tmp_path) as client:
        response = client.post(
            "/api/config",
            json={"engine": "openai"},
        )

    assert response.status_code == 401


def test_post_api_config_rejects_invalid_host(monkeypatch, tmp_path):
    with _build_client(monkeypatch, tmp_path) as client:
        response = client.post(
            "/api/config",
            json={"engine": "openai"},
            headers={
                "Host": "malicious",
                "X-OBS-Token": _session_token(client),
            },
        )

    assert response.status_code == 403


def test_post_api_config_rejects_invalid_origin(monkeypatch, tmp_path):
    with _build_client(monkeypatch, tmp_path) as client:
        response = client.post(
            "/api/config",
            json={"engine": "openai"},
            headers={
                "Origin": "https://example.com",
                "X-OBS-Token": _session_token(client),
            },
        )

    assert response.status_code == 403


def test_post_api_config_accepts_valid_token_and_origin(monkeypatch, tmp_path):
    with _build_client(monkeypatch, tmp_path) as client:
        response = client.post(
            "/api/config",
            json={"engine": "openai"},
            headers={
                "Origin": "http://127.0.0.1:8765",
                "X-OBS-Token": _session_token(client),
            },
        )

    assert response.status_code == 200


def test_get_api_session_rejects_cross_site_sec_fetch(monkeypatch, tmp_path):
    with _build_client(monkeypatch, tmp_path) as client:
        response = client.get(
            "/api/session",
            headers={"Sec-Fetch-Site": "cross-site"},
        )

    assert response.status_code == 403


def test_get_api_session_accepts_same_origin_sec_fetch(monkeypatch, tmp_path):
    with _build_client(monkeypatch, tmp_path) as client:
        response = client.get(
            "/api/session",
            headers={"Sec-Fetch-Site": "same-origin"},
        )

    assert response.status_code == 200


def test_get_settings_page_returns_index_html(monkeypatch, tmp_path):
    with _build_client(monkeypatch, tmp_path) as client:
        response = client.get("/settings")

    assert response.status_code == 200
    assert response.text.startswith("<!doctype html>")


def test_get_settings_assets_return_files(monkeypatch, tmp_path):
    with _build_client(monkeypatch, tmp_path) as client:
        css_response = client.get("/settings/settings.css")
        js_response = client.get("/settings/settings.js")

    assert css_response.status_code == 200
    assert js_response.status_code == 200
    assert "font-size" in css_response.text
    assert "(() => {" in js_response.text


def test_get_settings_page_without_slash_redirects(monkeypatch, tmp_path):
    with _build_real_client(monkeypatch, tmp_path) as client:
        response = client.get("/settings")

    assert response.status_code == 200
    assert response.url.path == "/settings/"
    assert response.text.startswith("<!doctype html>")


def test_get_api_keys_status_needs_token(monkeypatch, tmp_path):
    with _build_real_client(monkeypatch, tmp_path) as client:
        response = client.get("/api/keys/status")

    assert response.status_code == 401


def test_get_api_keys_status_reports_environment_presence(monkeypatch, tmp_path):
    monkeypatch.setenv("ASSEMBLYAI_API_KEY", "assembly-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-secret")

    with _build_real_client(monkeypatch, tmp_path) as client:
        response = client.get(
            "/api/keys/status",
            headers={"X-OBS-Token": _session_token(client)},
        )

    payload = response.json()
    expected_keys = {
        "OPENAI_API_KEY",
        "ELEVENLABS_API_KEY",
        "XAI_API_KEY",
        "OPENROUTER_API_KEY",
        "REPLICATE_API_TOKEN",
        "ASSEMBLYAI_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_CLOUD_PROJECT",
        "AZURE_SPEECH_KEY",
        "AZURE_SPEECH_REGION",
        "DEEPGRAM_API_KEY",
        "GROQ_API_KEY",
    }

    assert response.status_code == 200
    assert set(payload) == expected_keys
    assert payload["ASSEMBLYAI_API_KEY"] is True
    assert payload["OPENAI_API_KEY"] is True
    assert payload["ELEVENLABS_API_KEY"] is False
    assert "assembly-secret" not in response.text
    assert "openai-secret" not in response.text
