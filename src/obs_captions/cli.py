from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import click

import obs_captions.app_runner as app_runner
from obs_captions.config import load_config, redacted_config


_capture_to_backend = app_runner._capture_to_backend
_build_caption_callbacks = app_runner._build_caption_callbacks
_run_demo_backend = app_runner._run_demo_backend
_setup_export_sink = app_runner._setup_export_sink
make_capture = app_runner.make_capture


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
    """List WASAPI loopback (system-audio) devices for `[audio] source = \"loopback\"` (Windows)."""
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
def run_command(config_path: str | None, sink: str) -> None:  # pragma: no cover
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
def serve_command(config_path: str | None, demo: bool) -> None:  # pragma: no cover
    asyncio.run(_serve(config_path, demo))


@cli.command("ipc-sidecar")
@click.option("--config", "config_path", type=click.Path(exists=True, dir_okay=False), default=None)
def ipc_sidecar_command(config_path: str | None) -> None:
    """플러그인 전용, 사람이 직접 실행하는 용도 아님."""
    from obs_captions.ipc.sidecar import run_sidecar_cli

    try:
        run_sidecar_cli(config_path=config_path)
    except Exception as exc:  # noqa: BLE001
        click.echo(f"ERROR: {exc}", err=True)
        sys.exit(1)


async def _run(config_path: str | None, sink: str = "browser") -> None:
    import obs_captions.stt.registry as registry_mod

    return await app_runner._run(
        config_path,
        sink,
        load_config_fn=load_config,
        make_capture_fn=make_capture,
        create_backend_fn=registry_mod.create_backend,
    )


async def _serve(config_path: str | None, demo: bool) -> None:
    return await app_runner._serve(
        config_path,
        demo,
        load_config_fn=load_config,
        overlay_dir_fn=_overlay_dir,
    )


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
        exit_code = asyncio.run(
            _check_engine(engine, wav_path, seconds, language, config_path)
        )
    except Exception as exc:  # noqa: BLE001
        click.echo(f"ERROR: {exc}", err=True)
        sys.exit(1)
    else:
        sys.exit(exit_code)


def _overlay_dir() -> Path:  # pragma: no cover  # only called from _serve/_run which requires a live server
    # Resolve the bundled overlay assets relative to the package (dev/pip) or
    # the PyInstaller bundle (frozen) — never CWD-relative. See packaging.py.
    return app_runner._overlay_dir()


def main() -> None:
    """Console-script entry point: no args -> desktop GUI, args -> CLI."""
    if len(sys.argv) == 1:
        from obs_captions.gui.app import main as gui_main

        gui_main()
    else:
        if sys.platform == "win32":
            from obs_captions.packaging import attach_parent_console

            attach_parent_console()
        cli()


if __name__ == "__main__":
    main()
