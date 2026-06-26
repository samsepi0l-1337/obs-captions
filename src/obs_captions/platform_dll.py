from __future__ import annotations

import os
import site
import sys
from pathlib import Path

# pip-installed nvidia-* wheels drop their runtime DLLs under these subdirs.
_NVIDIA_BIN_SUBDIRS = (
    ("nvidia", "cublas", "bin"),
    ("nvidia", "cudnn", "bin"),
    ("nvidia", "cuda_runtime", "bin"),
)


def add_cuda_dll_directories(
    *,
    platform: str | None = None,
    site_packages: list[str] | None = None,
) -> list[str]:
    """Register pip-installed ``nvidia-*/bin`` dirs so CTranslate2 finds CUDA DLLs.

    Windows does not search ``site-packages`` for dependent DLLs, so the
    ``nvidia-cublas-cu12`` / ``nvidia-cudnn-cu12`` wheels installed via the
    ``gpu`` extra are invisible to CTranslate2 unless their ``bin`` dirs are
    registered with :func:`os.add_dll_directory`. Returns the directories added.

    No-op (returns ``[]``) off Windows; ``platform`` and ``site_packages`` are
    injectable for testing on any host.
    """
    current = platform if platform is not None else sys.platform
    if current != "win32" or not hasattr(os, "add_dll_directory"):
        return []
    roots = site_packages if site_packages is not None else site.getsitepackages()
    added: list[str] = []
    for root in roots:
        for parts in _NVIDIA_BIN_SUBDIRS:
            candidate = Path(root, *parts)
            if not candidate.is_dir():
                continue
            try:
                os.add_dll_directory(str(candidate))
            except OSError:
                continue
            added.append(str(candidate))
    return added
