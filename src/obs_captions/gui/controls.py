"""Pure helpers for the desktop GUI's control row.

These are side-effect-free (or hardware-probe-only) functions split out of
:mod:`obs_captions.gui.app` to keep that module small. The Tk-touching *wiring*
(``_wire_key_test``/``_wire_model_recommendation``) stays in ``app`` so tests can
monkeypatch it there; everything here is independently unit-testable.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from obs_captions.stt import validate

if TYPE_CHECKING:
    from obs_captions.stt.hardware import HardwareInfo


def result_color(result: validate.ValidationResult) -> str:
    if result.ok:
        return "green"
    if result.mode == "unsupported":
        return "gray"
    return "red"


def current_key_widget(registry: dict[str, Any], engine: str) -> Any | None:
    for field_spec, _label, widget, _help in registry.get("field_widgets", {}).values():
        if field_spec.widget == "secret" and engine in field_spec.engines:
            return widget
    return None


def detect_recommendation() -> tuple[str, HardwareInfo]:
    """Probe hardware and return ``(recommended_model, hardware_info)`` (IO)."""
    from obs_captions.stt.hardware import detect_hardware, recommend_model

    info = detect_hardware()
    return recommend_model(info), info


def format_recommendation(model: str, info: HardwareInfo) -> str:
    detected = f"GPU {info.vram_mb}MB" if info.vram_mb is not None else "CPU"
    return f"추천: {model} (감지: {detected})"


def config_folder(config_path: str | Path | None) -> Path:
    """Folder that holds config.toml/.env — its parent, or cwd for in-memory."""
    if config_path is None:
        return Path.cwd()
    return Path(config_path).resolve().parent


def open_folder_command(folder: str, platform: str = sys.platform) -> list[str]:
    """Build the OS command that reveals ``folder`` in the file manager (pure).

    macOS ``open``, Windows ``explorer``, else ``xdg-open`` — each a plain argv
    list so the command construction is unit-testable per platform.
    """
    if platform == "darwin":
        return ["open", folder]
    if platform.startswith("win"):
        return ["explorer", folder]
    return ["xdg-open", folder]


__all__ = [
    "result_color",
    "current_key_widget",
    "detect_recommendation",
    "format_recommendation",
    "config_folder",
    "open_folder_command",
]
