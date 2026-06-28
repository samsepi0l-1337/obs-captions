from __future__ import annotations

import sys
import types

import pytest

from obs_captions.stt.device import _default_cuda_probe, resolve_device


def _probe_with(*types_: str):
    supported = set(types_)
    return lambda _device: supported


def test_auto_with_cuda_and_float16_picks_float16():
    assert resolve_device("auto", None, probe=_probe_with("float16", "int8")) == (
        "cuda",
        "float16",
    )


def test_auto_with_cuda_without_float16_falls_back_to_int8_float16():
    assert resolve_device("auto", None, probe=_probe_with("int8_float16", "int8")) == (
        "cuda",
        "int8_float16",
    )


def test_auto_without_cuda_falls_back_to_cpu_int8():
    assert resolve_device("auto", None, probe=_probe_with()) == ("cpu", "int8")


def test_auto_with_cuda_honors_caller_compute_type():
    assert resolve_device("auto", "bfloat16", probe=_probe_with("float16")) == (
        "cuda",
        "bfloat16",
    )


def test_auto_without_cuda_honors_caller_compute_type_on_cpu():
    assert resolve_device("auto", "int16", probe=_probe_with()) == ("cpu", "int16")


def test_explicit_cuda_defaults_to_float16_even_without_probe_confirmation():
    # Explicit request: trust the user even if the probe can't confirm CUDA.
    assert resolve_device("cuda", None, probe=_probe_with()) == ("cuda", "float16")


def test_explicit_cuda_honors_caller_compute_type():
    assert resolve_device("cuda", "int8_float16", probe=_probe_with()) == (
        "cuda",
        "int8_float16",
    )


def test_explicit_cpu_defaults_to_int8():
    assert resolve_device("cpu", None, probe=_probe_with("float16")) == ("cpu", "int8")


def test_explicit_cpu_honors_caller_compute_type():
    assert resolve_device("cpu", "int16", probe=_probe_with("float16")) == ("cpu", "int16")


def test_invalid_device_raises_value_error():
    with pytest.raises(ValueError):
        resolve_device("gpu", None, probe=_probe_with("float16"))


def test_default_probe_returns_set_type_and_never_raises():
    # On a non-CUDA host this returns set(); on a CUDA host a populated set.
    # The contract is: it returns a set and never raises.
    result = _default_cuda_probe("cuda")
    assert isinstance(result, set)


def test_default_probe_swallows_import_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "ctranslate2", None)  # forces ImportError
    assert _default_cuda_probe("cuda") == set()


def test_default_probe_swallows_runtime_error(monkeypatch):
    fake = types.ModuleType("ctranslate2")

    def boom(_device: str):
        raise RuntimeError("no cuda runtime")

    fake.get_supported_compute_types = boom  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "ctranslate2", fake)
    assert _default_cuda_probe("cuda") == set()


def test_default_probe_returns_supported_types_on_success(monkeypatch):
    fake = types.ModuleType("ctranslate2")

    def supported(device: str):
        return {"float16", "int8_float16", "int8"} if device == "cuda" else set()

    fake.get_supported_compute_types = supported  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "ctranslate2", fake)
    assert _default_cuda_probe("cuda") == {"float16", "int8_float16", "int8"}


def test_resolve_device_uses_default_probe_when_none(monkeypatch):
    # With the real default probe and ctranslate2 forced unavailable, auto must
    # deterministically resolve to CPU (the macOS / no-CUDA contract).
    monkeypatch.setitem(sys.modules, "ctranslate2", None)
    assert resolve_device("auto", None) == ("cpu", "int8")
