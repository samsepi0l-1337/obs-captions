# setup_obs_deps_windows.ps1 — acquire the OBS/libobs SDK on a Windows CI runner so
# that `find_package(libobs REQUIRED)` (native-plugin/CMakeLists.txt) resolves.
#
# The obs-deps prebuilt zips do NOT contain libobs — libobs' exported CMake package
# (libobsConfig.cmake / OBS::libobs) only exists after obs-studio itself is configured,
# built, and installed with its Development component. So this script mirrors the
# obs-plugintemplate recipe: download obs-deps + qt6 (3rd-party libs) + the obs-studio
# SOURCE tag, then run a nested CMake build of obs-studio (plugins/frontend OFF) to
# materialize libobs on disk.
#
# Result: a 3-entry CMAKE_PREFIX_PATH is appended to $GITHUB_ENV (when running under
# GitHub Actions) so the subsequent scripts/build_plugin_windows.ps1 step finds libobs.
#
# Versions are pinned to what obsproject/obs-plugintemplate@master/buildspec.json used
# (verified 2026-07-15). Override via env: OBS_VERSION, OBS_DEPS_VERSION.

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$ObsVersion  = if ($env:OBS_VERSION) { $env:OBS_VERSION } else { "31.1.1" }
$DepsVersion = if ($env:OBS_DEPS_VERSION) { $env:OBS_DEPS_VERSION } else { "2025-07-11" }

$DepsRoot   = Join-Path $RepoRoot ".deps"
$ObsSrcDir  = Join-Path $DepsRoot "obs-studio-$ObsVersion"
$PrebuiltDir = Join-Path $DepsRoot "obs-deps-$DepsVersion-x64"
$Qt6Dir     = Join-Path $DepsRoot "obs-deps-qt6-$DepsVersion-x64"

New-Item -ItemType Directory -Force -Path $DepsRoot | Out-Null

function Get-Archive([string]$Url, [string]$OutFile, [string]$Dest) {
    if (-not (Test-Path $OutFile)) {
        Write-Host "==> download $Url"
        Invoke-WebRequest -Uri $Url -OutFile $OutFile
    }
    Write-Host "==> extract  -> $Dest"
    Expand-Archive -Path $OutFile -DestinationPath $Dest -Force
}

# 1) Third-party prebuilt deps (ffmpeg/curl/etc.) and Qt6 — NOT libobs.
$DepsBase = "https://github.com/obsproject/obs-deps/releases/download/$DepsVersion"
Get-Archive "$DepsBase/windows-deps-$DepsVersion-x64.zip"     (Join-Path $DepsRoot "prebuilt.zip") $PrebuiltDir
Get-Archive "$DepsBase/windows-deps-qt6-$DepsVersion-x64.zip" (Join-Path $DepsRoot "qt6.zip")      $Qt6Dir

# 2) obs-studio SOURCE tag archive (extracts to .deps/obs-studio-<ver>/).
Get-Archive "https://github.com/obsproject/obs-studio/archive/refs/tags/$ObsVersion.zip" `
    (Join-Path $DepsRoot "obs-studio-src.zip") $DepsRoot

if (-not (Test-Path $ObsSrcDir)) {
    throw "obs-studio source not found at $ObsSrcDir after extraction"
}

# 3) Nested CMake build of libobs (plugins/frontend disabled), install Development
#    component into .deps so libobsConfig.cmake lands on CMAKE_PREFIX_PATH.
$ObsBuild = Join-Path $ObsSrcDir "build_x64"
$SdkPrefix = "$PrebuiltDir;$Qt6Dir"

Write-Host "==> configure obs-studio (libobs only)"
cmake -S $ObsSrcDir -B $ObsBuild -G "Visual Studio 17 2022" -A x64 `
    -DOBS_CMAKE_VERSION:STRING=3.0.0 `
    -DENABLE_PLUGINS:BOOL=OFF `
    -DENABLE_FRONTEND:BOOL=OFF `
    -DOBS_VERSION_OVERRIDE:STRING=$ObsVersion `
    "-DCMAKE_PREFIX_PATH=$SdkPrefix" `
    --fresh
if ($LASTEXITCODE -ne 0) { throw "obs-studio configure failed ($LASTEXITCODE)" }

Write-Host "==> build obs-frontend-api (pulls in libobs)"
cmake --build $ObsBuild --target obs-frontend-api --config RelWithDebInfo --parallel
if ($LASTEXITCODE -ne 0) { throw "obs-studio build failed ($LASTEXITCODE)" }

Write-Host "==> install Development component -> $DepsRoot"
cmake --install $ObsBuild --component Development --config RelWithDebInfo --prefix $DepsRoot
if ($LASTEXITCODE -ne 0) { throw "obs-studio install failed ($LASTEXITCODE)" }

# 4) Export the prefix so the plugin build (and this session) can find libobs.
$PluginPrefix = "$DepsRoot;$PrebuiltDir;$Qt6Dir"
Write-Host "==> CMAKE_PREFIX_PATH = $PluginPrefix"
if ($env:GITHUB_ENV) {
    "CMAKE_PREFIX_PATH=$PluginPrefix" | Out-File -FilePath $env:GITHUB_ENV -Encoding utf8 -Append
}
$env:CMAKE_PREFIX_PATH = $PluginPrefix

Write-Host "==> OBS SDK READY (obs $ObsVersion, deps $DepsVersion)"
