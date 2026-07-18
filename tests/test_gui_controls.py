"""Unit tests for the pure GUI control helpers (no Tk required)."""

from __future__ import annotations

from pathlib import Path

from obs_captions.gui import controls


def test_open_folder_command_macos():
    assert controls.open_folder_command("/tmp/x", platform="darwin") == ["open", "/tmp/x"]


def test_open_folder_command_windows():
    assert controls.open_folder_command("C:/x", platform="win32") == ["explorer", "C:/x"]


def test_open_folder_command_linux():
    assert controls.open_folder_command("/tmp/x", platform="linux") == ["xdg-open", "/tmp/x"]


def test_config_folder_uses_parent_of_config_path():
    got = controls.config_folder("some/dir/config.toml")
    assert got == Path("some/dir/config.toml").resolve().parent


def test_config_folder_defaults_to_cwd_for_in_memory():
    assert controls.config_folder(None) == Path.cwd()


def test_result_color_maps_modes():
    from obs_captions.stt.validate import ValidationResult

    assert controls.result_color(ValidationResult(True, "network", "ok")) == "green"
    assert controls.result_color(ValidationResult(False, "unsupported", "n/a")) == "gray"
    assert controls.result_color(ValidationResult(False, "network", "bad")) == "red"


def test_format_recommendation_gpu_and_cpu():
    from obs_captions.stt.hardware import HardwareInfo

    gpu = HardwareInfo(cuda_available=True, vram_mb=16000, ram_mb=32000, cpu_count=16)
    text = controls.format_recommendation("large-v3-turbo", gpu)
    assert "large-v3-turbo" in text and "16000" in text

    cpu = HardwareInfo(cuda_available=False, vram_mb=None, ram_mb=8000, cpu_count=8)
    assert "CPU" in controls.format_recommendation("medium", cpu)
