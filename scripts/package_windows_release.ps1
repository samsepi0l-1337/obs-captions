$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$ExeSource = Join-Path $RepoRoot "dist\obs-captions"
$ReleaseRoot = Join-Path $RepoRoot "dist\release"
$PackageRoot = Join-Path $ReleaseRoot "obs-captions-windows"
$ExeDest = Join-Path $PackageRoot "obs-captions"
$PluginDllDestDir = Join-Path $PackageRoot "obs-plugins\64bit"
$PluginDataDest = Join-Path $PackageRoot "data\obs-plugins\obs-captions"
$ZipPath = Join-Path $ReleaseRoot "obs-captions-windows-x64.zip"

if (-not (Test-Path $ExeSource)) {
    throw "Missing PyInstaller onedir output: $ExeSource"
}

if (Test-Path $PackageRoot) {
    Remove-Item $PackageRoot -Recurse -Force
}
New-Item -ItemType Directory -Path $ExeDest -Force | Out-Null
New-Item -ItemType Directory -Path $PluginDllDestDir -Force | Out-Null
New-Item -ItemType Directory -Path $PluginDataDest -Force | Out-Null

Write-Host "==> copy executable bundle"
Copy-Item -Path (Join-Path $ExeSource "*") -Destination $ExeDest -Recurse -Force

$PluginDllCandidates = @(
    (Join-Path $RepoRoot "dist\plugin\obs-plugins\64bit\obs-captions.dll"),
    (Join-Path $RepoRoot "dist\plugin\obs-captions.dll"),
    (Join-Path $RepoRoot "native-plugin\build\RelWithDebInfo\obs-captions.dll"),
    (Join-Path $RepoRoot "native-plugin\build\Release\obs-captions.dll"),
    (Join-Path $RepoRoot "build\native-plugin-windows\RelWithDebInfo\obs-captions.dll"),
    (Join-Path $RepoRoot "build\native-plugin-windows\Release\obs-captions.dll")
)
$PluginDll = $PluginDllCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($PluginDll) {
    Write-Host "==> copy plugin dll: $PluginDll"
    Copy-Item $PluginDll (Join-Path $PluginDllDestDir "obs-captions.dll") -Force
}
else {
    Write-Host "==> plugin dll not found; packaging executable-only release"
}

$PluginDataCandidates = @(
    (Join-Path $RepoRoot "dist\plugin\data\obs-plugins\obs-captions"),
    (Join-Path $RepoRoot "native-plugin\data")
)
$PluginData = $PluginDataCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($PluginData) {
    Write-Host "==> copy plugin data: $PluginData"
    Copy-Item -Path (Join-Path $PluginData "*") -Destination $PluginDataDest -Recurse -Force
}
else {
    Write-Host "==> plugin data not found; leaving data directory empty"
}

$InstallText = @"
obs-captions Windows install
============================

Python app / sidecar executable
-------------------------------
Copy the obs-captions/ folder anywhere on the target machine.

Path A/B usage
--------------
Run obs-captions\obs-captions.exe from that folder.

Typical commands:
  obs-captions\obs-captions.exe list-devices
  obs-captions\obs-captions.exe run --sink browser

Native OBS plugin
-----------------
If this package includes obs-plugins\64bit\obs-captions.dll, copy it to:
  <OBS install>\obs-plugins\64bit\obs-captions.dll

If this package includes data\obs-plugins\obs-captions\, copy that folder to:
  <OBS install>\data\obs-plugins\obs-captions\

The native plugin talks to the sidecar executable. Configure the plugin's sidecar
executable path to the copied obs-captions\obs-captions.exe location.

Notes
-----
The native plugin DLL is only included when it was built with OBS/libobs
dependencies available. The executable-only package is still usable for Path A
browser-source and Path B obs-websocket workflows.
"@
$InstallText | Set-Content -Path (Join-Path $PackageRoot "INSTALL.txt") -Encoding ASCII

if (Test-Path $ZipPath) {
    Remove-Item $ZipPath -Force
}
Write-Host "==> create zip: $ZipPath"
Compress-Archive -Path $PackageRoot -DestinationPath $ZipPath -Force

Write-Host "==> PACKAGE OK -> $ZipPath"
