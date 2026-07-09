# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for obs-captions (Windows distributable).
#
# Build on WINDOWS:  pyinstaller obs_captions.spec   (see scripts/build_windows.ps1)
# Output: onedir bundle at dist/obs-captions/  (onedir, not onefile — far easier
#         for the large CUDA/cuDNN DLLs the GPU path needs, and faster startup).
#
# IMPORTANT: the static overlay assets are placed at "obs_captions/web" inside the
# bundle. This MUST stay in sync with obs_captions.packaging.resolve_web_dir(),
# which (when frozen) reads  Path(sys._MEIPASS) / "obs_captions" / "web".

block_cipher = None

# --- data files copied into the bundle -------------------------------------
# (source on disk, dest dir inside the bundle root)
datas = [
    ("src/obs_captions/web", "obs_captions/web"),  # overlay.{html,css,js} -> _MEIPASS/obs_captions/web
    ("config.example.toml", "."),                  # sample config next to the exe
]

# --- hidden imports --------------------------------------------------------
# These are imported LAZILY (inside functions / via the STT registry / by
# CTranslate2 & friends at runtime), so PyInstaller's static analysis misses
# them. Listing them here forces inclusion. CPU-only by default.
hiddenimports = [
    # package-internal lazy imports (cli.py / registry / sinks load these on demand)
    "obs_captions.platform_dll",
    "obs_captions.audio.devices",
    "obs_captions.audio.loopback",
    "obs_captions.pipeline",
    "obs_captions.server",
    "obs_captions.vad",
    "obs_captions.stt.registry",
    "obs_captions.stt.device",
    "obs_captions.stt.local_whisper",
    "obs_captions.stt.openai_realtime",
    "obs_captions.stt.elevenlabs_realtime",
    "obs_captions.stt.xai",
    "obs_captions.stt.openrouter",
    "obs_captions.stt.replicate",
    "obs_captions.stt.google",
    "obs_captions.stt.google_speech_v2",
    "obs_captions.stt.deepgram",
    "obs_captions.stt.assemblyai",
    "obs_captions.stt.groq",
    "obs_captions.stt.azure",
    "obs_captions.obs_sink",
    "obs_captions.obs_hotkey",
    "obs_captions.ipc",
    "obs_captions.ipc.sidecar",
    # third-party runtime deps with dynamic / C-extension imports
    "faster_whisper",
    "ctranslate2",
    "silero_vad",
    "onnxruntime",
    "sounddevice",
    "websockets",
    "simpleobsws",
    "google.cloud.speech_v2",
    # Windows + loopback only (lazy): system-audio (WASAPI) capture. Harmless to
    # list; only pulled in when actually installed (--extra loopback).
    "pyaudiowpatch",
]

# --- GPU (CUDA) build: opt-in ---------------------------------------------
# The default bundle is CPU-only (smaller, no NVIDIA deps). To build a GPU
# bundle: `uv sync --extra gpu` first, then UNCOMMENT the block below. The
# nvidia-* wheels drop their DLLs under site-packages/nvidia/*/bin; the runtime
# add_dll_directory in platform_dll.py also needs them on PATH — bundling the
# bin dirs as datas makes the frozen exe self-contained.
#
# import importlib.util
# from pathlib import Path
# hiddenimports += ["torch", "nvidia.cublas", "nvidia.cudnn", "nvidia.cuda_runtime"]
# _nv = Path(importlib.util.find_spec("nvidia").origin).parent
# for _sub in ("cublas", "cudnn", "cuda_runtime"):
#     _bin = _nv / _sub / "bin"
#     if _bin.is_dir():
#         datas.append((str(_bin), f"nvidia/{_sub}/bin"))

# --- optional: pre-bundle a Whisper model (offline first run) --------------
# By default the local engine downloads the model from HuggingFace on first run.
# To ship it inside the bundle (fully offline), download it then add as datas,
# e.g. (after `huggingface-cli download Systran/faster-whisper-small`):
# datas.append((r"C:\path\to\models\faster-whisper-small", "models/faster-whisper-small"))
# and point [local] model = "models/faster-whisper-small" at it.

a = Analysis(
    ["src/obs_captions/__main__.py"],
    pathex=["src"],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="obs-captions",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,  # CLI app — keep the console window for logs / list-devices output.
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="obs-captions",  # -> dist/obs-captions/
)
