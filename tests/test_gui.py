from __future__ import annotations

import socket
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest

from obs_captions.cli import cli
from obs_captions.config import AppConfig, ServerConfig
from obs_captions.gui import launch_gui, run_server_in_thread


def _can_bind_localhost() -> bool:
    try:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
        return True
    except OSError:
        return False


def _make_webview_module() -> SimpleNamespace:
    return SimpleNamespace(
        create_window=Mock(),
        start=Mock(),
    )


def _stop_server_thread(thread: threading.Thread) -> None:
    server = getattr(thread, "server", None)
    if server is not None:
        server.should_exit = True
    thread.join(timeout=3)


def test_run_server_in_thread_starts_server_and_serves_settings(tmp_path, monkeypatch):
    if not _can_bind_localhost():
        pytest.skip("localhost socket binding not permitted in this test environment")

    monkeypatch.setenv("HOME", str(tmp_path))
    config = AppConfig(server=ServerConfig(port=0))
    thread, base_url = run_server_in_thread(config, str(tmp_path / "config.toml"))

    try:
        deadline = time.monotonic() + 5
        response_status = None
        while time.monotonic() < deadline:
            try:
                response = httpx.get(f"{base_url}/settings/", timeout=0.5)
            except (httpx.RequestError, socket.gaierror):
                time.sleep(0.1)
                continue

            response_status = response.status_code
            if response.status_code == 200:
                break
            time.sleep(0.1)

        assert response_status == 200
        assert base_url.startswith("http://127.0.0.1:")
    finally:
        _stop_server_thread(thread)


def test_launch_gui_calls_webview(monkeypatch):
    fake_webview = _make_webview_module()
    monkeypatch.setitem(sys.modules, "webview", fake_webview)

    def fake_run_server_in_thread(config, config_path):  # noqa: ARG001
        return threading.Thread(target=lambda: None, daemon=True), "http://127.0.0.1:8123"

    monkeypatch.setattr("obs_captions.gui.run_server_in_thread", fake_run_server_in_thread)
    launch_gui(AppConfig(), None)

    fake_webview.create_window.assert_called_once_with(
        "OBS Captions 설정",
        "http://127.0.0.1:8123/settings/",
        width=1100,
        height=820,
    )
    fake_webview.start.assert_called_once_with()


def test_launch_gui_import_error_if_webview_missing(monkeypatch):
    monkeypatch.setitem(sys.modules, "webview", None)
    monkeypatch.setattr(
        "obs_captions.gui.run_server_in_thread",
        lambda config, config_path: (  # noqa: ARG001
            threading.Thread(target=lambda: None),
            "http://127.0.0.1:8123",
        ),
    )

    with pytest.raises(RuntimeError, match="gui 기능을 사용하려면 pywebview가 필요합니다"):
        launch_gui(AppConfig(), None)


def test_launch_gui_cli_prints_error_on_runtime_error(monkeypatch):
    from click.testing import CliRunner

    def raise_runtime_error(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("gui 모듈을 로드하지 못했습니다")

    monkeypatch.setattr("obs_captions.gui.launch_gui", raise_runtime_error)

    runner = CliRunner()
    result = runner.invoke(cli, ["gui"])
    combined = result.output + getattr(result, "stderr", "")

    assert result.exit_code == 1
    assert "ERROR: gui 모듈을 로드하지 못했습니다" in combined
    assert "Traceback" not in combined


def test_gui_help_does_not_require_webview_import():
    from click.testing import CliRunner

    runner = CliRunner()
    result = runner.invoke(cli, ["gui", "--help"])
    assert result.exit_code == 0
