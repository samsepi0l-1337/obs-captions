"""Hardware detection and pure model recommendation for the local STT backend.

``recommend_model`` is a pure function over :class:`HardwareInfo` and is the
tested contract. ``detect_hardware`` performs best-effort IO probes; every
external dependency is optional and every probe failure is absorbed into
``None`` so detection never raises.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass

from .device import _default_cuda_probe


@dataclass(frozen=True)
class HardwareInfo:
    cuda_available: bool
    vram_mb: int | None
    ram_mb: int | None
    cpu_count: int | None


def recommend_model(info: HardwareInfo) -> str:
    """Recommend a faster-whisper model size for the detected hardware (pure).

    Priority:
    - Known VRAM: >=8000 -> ``large-v3-turbo``; 4000..7999 -> ``large-v3``;
      below 4000 falls through to the RAM/CPU rules.
    - Unknown VRAM with CUDA present -> ``large-v3``.
    - No CUDA, RAM>=8000 and cpu>=8 -> ``medium``.
    - RAM>=4000 -> ``small``.
    - Otherwise -> ``base``.
    """
    vram = info.vram_mb
    if vram is not None:
        if vram >= 8000:
            return "large-v3-turbo"
        if vram >= 4000:
            return "large-v3"
    elif info.cuda_available:
        return "large-v3"

    ram = info.ram_mb
    cpu = info.cpu_count
    if (
        not info.cuda_available
        and ram is not None
        and ram >= 8000
        and cpu is not None
        and cpu >= 8
    ):
        return "medium"
    if ram is not None and ram >= 4000:
        return "small"
    return "base"


def _safe(fn, default):
    """Call ``fn`` returning ``default`` on any error, so probes never leak."""
    try:
        return fn()
    except Exception:
        return default


def detect_hardware() -> HardwareInfo:
    """Probe the host for CUDA/VRAM/RAM/CPU. Never raises; failures -> ``None``."""
    return HardwareInfo(
        cuda_available=_safe(_detect_cuda_available, False),
        vram_mb=_safe(_detect_vram_mb, None),
        ram_mb=_safe(_detect_ram_mb, None),
        cpu_count=_safe(os.cpu_count, None),
    )


def _detect_cuda_available() -> bool:
    try:
        return bool(_default_cuda_probe("cuda"))
    except Exception:
        return False


def _detect_vram_mb() -> int | None:
    return _pynvml_vram_mb() or _nvidia_smi_vram_mb()


def _pynvml_vram_mb() -> int | None:
    try:
        import pynvml

        pynvml.nvmlInit()
        try:
            handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            total_bytes = pynvml.nvmlDeviceGetMemoryInfo(handle).total
        finally:
            pynvml.nvmlShutdown()
        return int(total_bytes) // (1024 * 1024)
    except Exception:
        return None


def _nvidia_smi_vram_mb() -> int | None:
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return None
        first = result.stdout.strip().splitlines()[0].strip()
        return int(first)
    except Exception:
        return None


def _detect_ram_mb() -> int | None:
    return _psutil_ram_mb() or _os_ram_mb()


def _psutil_ram_mb() -> int | None:
    try:
        import psutil

        return int(psutil.virtual_memory().total) // (1024 * 1024)
    except Exception:
        return None


def _os_ram_mb() -> int | None:
    try:
        page_size = os.sysconf("SC_PAGE_SIZE")
        pages = os.sysconf("SC_PHYS_PAGES")
        return int(page_size) * int(pages) // (1024 * 1024)
    except Exception:
        return None
