from __future__ import annotations

import os

from obs_captions.platform_dll import add_cuda_dll_directories


def test_non_windows_is_noop():
    assert add_cuda_dll_directories(platform="darwin", site_packages=["/nope"]) == []
    assert add_cuda_dll_directories(platform="linux", site_packages=["/nope"]) == []


def _make_site_packages(tmp_path, *relative_dirs):
    root = tmp_path / "site-packages"
    for rel in relative_dirs:
        (root / rel).mkdir(parents=True, exist_ok=True)
    return root


def test_windows_registers_only_existing_nvidia_dirs(tmp_path, monkeypatch):
    root = _make_site_packages(
        tmp_path,
        "nvidia/cublas/bin",
        "nvidia/cudnn/bin",
        # nvidia/cuda_runtime/bin intentionally absent
    )
    recorded: list[str] = []
    monkeypatch.setattr(os, "add_dll_directory", recorded.append, raising=False)

    added = add_cuda_dll_directories(platform="win32", site_packages=[str(root)])

    expected = [
        str(root / "nvidia" / "cublas" / "bin"),
        str(root / "nvidia" / "cudnn" / "bin"),
    ]
    assert added == expected
    assert recorded == expected


def test_windows_with_no_nvidia_dirs_returns_empty(tmp_path, monkeypatch):
    root = _make_site_packages(tmp_path)  # empty site-packages
    monkeypatch.setattr(os, "add_dll_directory", lambda _p: None, raising=False)

    assert add_cuda_dll_directories(platform="win32", site_packages=[str(root)]) == []


def test_windows_swallows_oserror(tmp_path, monkeypatch):
    root = _make_site_packages(tmp_path, "nvidia/cublas/bin", "nvidia/cudnn/bin")

    def boom(_path: str):
        raise OSError("cannot add dll directory")

    monkeypatch.setattr(os, "add_dll_directory", boom, raising=False)

    # All registrations fail, but the helper must not raise and returns [].
    assert add_cuda_dll_directories(platform="win32", site_packages=[str(root)]) == []


def test_windows_without_add_dll_directory_attr_is_noop(tmp_path, monkeypatch):
    root = _make_site_packages(tmp_path, "nvidia/cublas/bin")
    monkeypatch.delattr(os, "add_dll_directory", raising=False)

    assert add_cuda_dll_directories(platform="win32", site_packages=[str(root)]) == []
