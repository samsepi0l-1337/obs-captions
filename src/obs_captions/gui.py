from __future__ import annotations

import click
import socket
import sys
import threading
import time

import uvicorn

from obs_captions.config import AppConfig
from obs_captions.packaging import resolve_overlay_dir
from obs_captions.server import Hub, create_app


def _stop_server_thread(thread: threading.Thread) -> None:
    server = getattr(thread, "server", None)
    if server is not None:
        server.should_exit = True


def _find_bound_port(server: uvicorn.Server) -> int | None:
    for serve_instance in server.servers:
        for sock in getattr(serve_instance, "sockets", ()):  # type: ignore[assignment]
            try:
                bound = sock.getsockname()
            except OSError:
                continue
            if bound is None:
                continue
            return int(bound[1])
    return None


def _can_connect(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.2):
            return True
    except OSError:
        return False


def _wait_for_server_ready(server: uvicorn.Server, thread: threading.Thread, host: str, port: int) -> int:
    deadline = time.monotonic() + 5.0
    while True:
        if server.started:
            if port == 0:
                actual_port = _find_bound_port(server)
                if actual_port is not None and _can_connect(host, actual_port):
                    return actual_port
            elif _can_connect(host, port):
                return port

        if not thread.is_alive():
            raise RuntimeError("settings 서버 스레드가 시작 직후 종료되었습니다.")

        if time.monotonic() > deadline:
            raise RuntimeError(
                "타임아웃: settings 서버가 준비되지 않았습니다."
            )

        time.sleep(0.05)


def run_server_in_thread(
    config: AppConfig,
    config_path: str | None,
) -> tuple[threading.Thread, str]:
    """Run the settings-capable app in a daemon thread and return ``(thread, base_url)``."""
    host = "127.0.0.1"
    port = int(config.server.port)

    hub = Hub()
    app = create_app(
        hub,
        overlay_dir=resolve_overlay_dir(),
        config=config,
        config_path=config_path,
    )

    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host=host,
            port=port,
            log_level="info",
        )
    )

    thread = threading.Thread(target=server.run, daemon=True)
    thread.server = server  # type: ignore[attr-defined]
    thread.start()

    actual_port = _wait_for_server_ready(server, thread, host, port)
    return thread, f"http://{host}:{actual_port}"


def launch_gui(config: AppConfig, config_path: str | None) -> None:
    _thread, base_url = run_server_in_thread(config, config_path)

    try:
        import webview
    except ImportError as exc:  # pragma: no cover - exercised by unit test + py-missing env
        _stop_server_thread(_thread)
        raise RuntimeError(
            "gui 기능을 사용하려면 pywebview가 필요합니다. `pip install .[gui]` 또는 "
            "`uv sync --extra gui` 로 설치하세요."
        ) from exc

    try:
        webview.create_window(
            "OBS Captions 설정",
            f"{base_url}/settings/",
            width=1100,
            height=820,
        )
        webview.start()
    finally:
        _stop_server_thread(_thread)


def launch_gui_cli(config: AppConfig, config_path: str | None) -> None:
    try:
        launch_gui(config, config_path)
    except Exception as exc:  # noqa: BLE001
        click.echo(f"ERROR: {exc}", err=True)
        sys.exit(1)
