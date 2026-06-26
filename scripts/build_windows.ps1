# build_windows.ps1 — build the obs-captions Windows distributable (onedir).
#
# Run from the repo root in PowerShell on WINDOWS:
#     .\scripts\build_windows.ps1
#
# Produces:  dist\obs-captions\obs-captions.exe   (+ its _internal\ deps)
# CPU-only by default. GPU is opt-in (see the commented step below + the .spec).

$ErrorActionPreference = "Stop"

# 1) Resolve repo root (this script's parent dir) and move there so the relative
#    paths in obs_captions.spec (src/obs_captions/web, config.example.toml) resolve.
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot
Write-Host "==> repo root: $RepoRoot"

# 2) Sync runtime deps (CPU local engine + loopback). Uses uv (project-pinned).
#    Add  --extra gpu  here for an NVIDIA CUDA build (and uncomment the GPU block
#    in obs_captions.spec so the nvidia DLLs get bundled).
Write-Host "==> uv sync (local + loopback extras)"
uv sync --extra local --extra loopback
# GPU (opt-in):  uv sync --extra local --extra gpu --extra loopback

# 3) Make sure PyInstaller is available in the env.
Write-Host "==> ensuring pyinstaller is installed"
uv pip install pyinstaller

# 4) Build the bundle from the spec (onedir, hidden imports + datas defined there).
Write-Host "==> pyinstaller obs_captions.spec"
uv run pyinstaller --noconfirm obs_captions.spec

# 5) Smoke test: the frozen exe must at least enumerate audio devices (exercises
#    sounddevice + the CLI group callback / CUDA DLL registration path).
$Exe = Join-Path $RepoRoot "dist\obs-captions\obs-captions.exe"
Write-Host "==> smoke test: $Exe list-devices"
& $Exe list-devices
if ($LASTEXITCODE -ne 0) {
    throw "smoke test failed (exit $LASTEXITCODE): $Exe list-devices"
}

Write-Host ""
Write-Host "==> BUILD OK -> dist\obs-captions\  (run obs-captions.exe run --sink browser)"

# --- optional: pre-bundle a Whisper model for OFFLINE first run --------------
# By default the local engine downloads the model from HuggingFace on first run.
# To ship it offline: download, then add it to obs_captions.spec datas and set
# [local] model to the bundled path. Example:
#   uv run huggingface-cli download Systran/faster-whisper-small `
#       --local-dir models\faster-whisper-small
#   # then add to the .spec datas + set [local] model = "models/faster-whisper-small"
