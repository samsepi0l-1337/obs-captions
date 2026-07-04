#!/usr/bin/env python3
"""Build the obs-captions Windows distributable (onedir) via PyInstaller.

Run from anywhere on WINDOWS:

    python scripts/build_windows.py            # CPU-only (default)
    python scripts/build_windows.py --gpu      # also `uv sync --extra gpu`

Produces ``dist/obs-captions/obs-captions.exe`` (+ ``_internal/`` deps), then
smoke-tests it with ``list-devices``. CPU-only by default; ``--gpu`` adds the
NVIDIA CUDA runtime extra (you must ALSO uncomment the GPU block in
``obs_captions.spec`` so the nvidia DLLs are bundled).

This is the cross-platform twin of ``scripts/build_windows.ps1``; the actual
PyInstaller build only works on Windows (the bundled binaries are Windows PE).
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SPEC = REPO_ROOT / "obs_captions.spec"
EXE = REPO_ROOT / "dist" / "obs-captions" / "obs-captions.exe"


def _run(cmd: list[str]) -> None:
    print(f"==> {' '.join(cmd)}", flush=True)
    subprocess.run(cmd, cwd=REPO_ROOT, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--gpu",
        action="store_true",
        help="Also sync the NVIDIA CUDA extra (remember to uncomment the GPU block in the .spec).",
    )
    args = parser.parse_args()

    if sys.platform != "win32":
        print(
            "WARNING: not on Windows — the produced bundle would target this OS, not "
            "Windows. Run this on Windows to build the distributable .exe.",
            file=sys.stderr,
        )

    extras = ["--extra", "local", "--extra", "loopback", "--extra", "gui"]
    if args.gpu:
        extras += ["--extra", "gpu"]

    _run(["uv", "sync", *extras])
    _run(["uv", "pip", "install", "pyinstaller"])
    _run(["uv", "run", "pyinstaller", "--noconfirm", str(SPEC)])

    # Smoke test the frozen exe (enumerate audio devices).
    _run([str(EXE), "list-devices"])

    print(f"\n==> BUILD OK -> {EXE.parent}  (run obs-captions.exe run --sink browser)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
