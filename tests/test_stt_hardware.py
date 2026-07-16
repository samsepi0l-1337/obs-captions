from __future__ import annotations

import subprocess

import pytest

from obs_captions.stt.hardware import (
    HardwareInfo,
    _nvidia_smi_vram_mb,
    detect_hardware,
    recommend_model,
)


def _info(
    *,
    cuda: bool = False,
    vram: int | None = None,
    ram: int | None = None,
    cpu: int | None = None,
) -> HardwareInfo:
    return HardwareInfo(cuda_available=cuda, vram_mb=vram, ram_mb=ram, cpu_count=cpu)


# --- recommend_model: VRAM tiers (vram_mb not None) ---


def test_vram_at_8000_picks_large_v3_turbo():
    assert recommend_model(_info(cuda=True, vram=8000)) == "large-v3-turbo"


def test_vram_above_8000_picks_large_v3_turbo():
    assert recommend_model(_info(cuda=True, vram=24000)) == "large-v3-turbo"


def test_vram_at_4000_picks_large_v3():
    assert recommend_model(_info(cuda=True, vram=4000)) == "large-v3"


def test_vram_just_below_8000_picks_large_v3():
    assert recommend_model(_info(cuda=True, vram=7999)) == "large-v3"


def test_vram_just_below_4000_falls_through_to_ram_rules():
    # vram known but tiny; cuda present so "medium" (CUDA無) cannot apply.
    assert recommend_model(_info(cuda=True, vram=3999, ram=16000, cpu=16)) == "small"


def test_vram_below_4000_no_ram_picks_base():
    assert recommend_model(_info(cuda=True, vram=2000, ram=None, cpu=4)) == "base"


# --- recommend_model: vram_mb is None -> decide by CUDA presence ---


def test_vram_none_with_cuda_picks_large_v3():
    assert recommend_model(_info(cuda=True, vram=None, ram=16000, cpu=16)) == "large-v3"


def test_vram_none_no_cuda_ram_8000_cpu_8_picks_medium():
    assert recommend_model(_info(cuda=False, vram=None, ram=8000, cpu=8)) == "medium"


def test_vram_none_no_cuda_ram_below_8000_picks_small():
    assert recommend_model(_info(cuda=False, vram=None, ram=7999, cpu=8)) == "small"


def test_vram_none_no_cuda_cpu_below_8_picks_small():
    assert recommend_model(_info(cuda=False, vram=None, ram=16000, cpu=7)) == "small"


def test_vram_none_no_cuda_ram_at_4000_picks_small():
    assert recommend_model(_info(cuda=False, vram=None, ram=4000, cpu=4)) == "small"


def test_vram_none_no_cuda_ram_below_4000_picks_base():
    assert recommend_model(_info(cuda=False, vram=None, ram=3999, cpu=4)) == "base"


def test_vram_none_no_cuda_ram_none_picks_base():
    assert recommend_model(_info(cuda=False, vram=None, ram=None, cpu=None)) == "base"


def test_medium_requires_both_ram_and_cpu_high():
    # RAM high but cpu None -> not medium, falls to small (ram>=4000).
    assert recommend_model(_info(cuda=False, vram=None, ram=16000, cpu=None)) == "small"


def test_all_none_no_cuda_picks_base():
    assert recommend_model(_info()) == "base"


# --- _nvidia_smi_vram_mb parsing ---


def test_nvidia_smi_vram_parses_first_line(monkeypatch):
    def fake_run(*_args, **_kwargs):
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="8192\n4096\n")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert _nvidia_smi_vram_mb() == 8192


def test_nvidia_smi_vram_returns_none_on_failure(monkeypatch):
    def boom(*_args, **_kwargs):
        raise FileNotFoundError("nvidia-smi not found")

    monkeypatch.setattr(subprocess, "run", boom)
    assert _nvidia_smi_vram_mb() is None


def test_nvidia_smi_vram_returns_none_on_nonzero(monkeypatch):
    def fake_run(*_args, **_kwargs):
        return subprocess.CompletedProcess(args=[], returncode=1, stdout="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert _nvidia_smi_vram_mb() is None


def test_nvidia_smi_vram_returns_none_on_unparsable(monkeypatch):
    def fake_run(*_args, **_kwargs):
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="N/A\n")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert _nvidia_smi_vram_mb() is None


# --- detect_hardware: smoke (env-dependent, never raises) ---


def test_detect_hardware_returns_hardware_info_without_raising():
    info = detect_hardware()
    assert isinstance(info, HardwareInfo)
    assert isinstance(info.cuda_available, bool)
    assert info.vram_mb is None or isinstance(info.vram_mb, int)
    assert info.ram_mb is None or isinstance(info.ram_mb, int)
    assert info.cpu_count is None or isinstance(info.cpu_count, int)


def test_detect_hardware_survives_all_probes_failing(monkeypatch):
    import obs_captions.stt.hardware as hw

    monkeypatch.setattr(hw, "_default_cuda_probe", lambda _d: (_ for _ in ()).throw(RuntimeError()))
    monkeypatch.setattr(hw, "_detect_vram_mb", lambda: (_ for _ in ()).throw(RuntimeError()))
    monkeypatch.setattr(hw, "_detect_ram_mb", lambda: (_ for _ in ()).throw(RuntimeError()))
    info = detect_hardware()
    assert isinstance(info, HardwareInfo)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
