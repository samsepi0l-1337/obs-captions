from __future__ import annotations

from collections.abc import Callable

VALID_DEVICES = ("auto", "cpu", "cuda")


def _default_cuda_probe(device: str) -> set[str]:
    """Compute types CTranslate2 supports on ``device`` (default CUDA probe).

    Returns an empty set on ANY import/runtime error so hosts without a
    CUDA-capable CTranslate2 build (e.g. macOS) deterministically fall back to
    CPU instead of crashing at startup.
    """
    try:
        import ctranslate2

        return set(ctranslate2.get_supported_compute_types(device))
    except Exception:
        return set()


def resolve_device(
    device: str,
    compute_type: str | None,
    *,
    probe: Callable[[str], set[str]] | None = None,
) -> tuple[str, str]:
    """Resolve the effective (device, compute_type) for faster-whisper / CTranslate2.

    - ``auto``: probe for CUDA. If supported, use ``cuda`` with the caller's
      ``compute_type`` (else ``float16`` when supported, else ``int8_float16``);
      otherwise fall back to ``("cpu", compute_type or "int8")``.
    - ``cuda``: trust the explicit request -> ``("cuda", compute_type or "float16")``.
    - ``cpu``: ``("cpu", compute_type or "int8")``.
    - anything else: ``ValueError``.
    """
    if device == "auto":
        supported = (probe or _default_cuda_probe)("cuda")
        if not supported:
            return "cpu", compute_type or "int8"
        if compute_type:
            return "cuda", compute_type
        return "cuda", "float16" if "float16" in supported else "int8_float16"
    if device == "cuda":
        return "cuda", compute_type or "float16"
    if device == "cpu":
        return "cpu", compute_type or "int8"
    raise ValueError(f"Invalid device {device!r}: expected one of {', '.join(VALID_DEVICES)}")
