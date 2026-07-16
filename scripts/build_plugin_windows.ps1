[CmdletBinding()]
param(
    # When set, a missing OBS SDK is a clean no-op (exit 0) instead of an
    # error. CI uses this to skip the DLL build on runners without libobs,
    # while the release workflow omits it so a missing SDK fails hard.
    [switch]$AllowSkip
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$PrefixPaths = @()
if ($env:CMAKE_PREFIX_PATH) {
    $PrefixPaths += $env:CMAKE_PREFIX_PATH
}
if ($env:OBS_STUDIO_DIR) {
    $PrefixPaths += $env:OBS_STUDIO_DIR
}
if ($env:OBS_BUILD_DIR) {
    $PrefixPaths += $env:OBS_BUILD_DIR
}

if ($PrefixPaths.Count -eq 0) {
    $msg = "OBS SDK not configured. Set CMAKE_PREFIX_PATH, OBS_STUDIO_DIR, or OBS_BUILD_DIR to an OBS/libobs install or build prefix."
    if ($AllowSkip) {
        Write-Host "==> SKIP native plugin build: $msg"
        exit 0
    }
    Write-Error $msg
    exit 2
}

$BuildDir = Join-Path $RepoRoot "build\native-plugin-windows"
$InstallRoot = Join-Path $RepoRoot "dist\plugin"
$DllDest = Join-Path $InstallRoot "obs-plugins\64bit"
$DataDest = Join-Path $InstallRoot "data\obs-plugins\obs-captions"
$PrefixPath = ($PrefixPaths -join ";")

Write-Host "==> configure native plugin"
Write-Host "    CMAKE_PREFIX_PATH=$PrefixPath"
cmake `
    -S native-plugin `
    -B $BuildDir `
    -DCMAKE_BUILD_TYPE=RelWithDebInfo `
    "-DCMAKE_PREFIX_PATH=$PrefixPath"

Write-Host "==> build native plugin"
cmake --build $BuildDir --config RelWithDebInfo

$DllCandidates = @(
    (Join-Path $BuildDir "RelWithDebInfo\obs-captions.dll"),
    (Join-Path $BuildDir "Release\obs-captions.dll"),
    (Join-Path $BuildDir "obs-captions.dll")
)
$Dll = $DllCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $Dll) {
    throw "Build finished, but obs-captions.dll was not found under $BuildDir"
}

New-Item -ItemType Directory -Path $DllDest -Force | Out-Null
New-Item -ItemType Directory -Path $DataDest -Force | Out-Null

Write-Host "==> copy plugin dll"
Copy-Item $Dll (Join-Path $DllDest "obs-captions.dll") -Force

$DataSource = Join-Path $RepoRoot "native-plugin\data"
if (Test-Path $DataSource) {
    Write-Host "==> copy plugin data"
    Copy-Item -Path (Join-Path $DataSource "*") -Destination $DataDest -Recurse -Force
}
else {
    Write-Host "==> no plugin data directory found"
}

Write-Host "==> PLUGIN BUILD OK -> $InstallRoot"
