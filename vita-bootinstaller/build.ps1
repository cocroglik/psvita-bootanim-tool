# Build script for PS Vita Boot Anim Installer (Windows)
# Requires: vitasdk toolchain (https://vitasdk.org/)
# Install via: https://github.com/vitasdk/vdpm
#
param([switch]$Clean)

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location $scriptDir

Write-Host "=== PS Vita Boot Anim Installer ===" -ForegroundColor Cyan

$vitasdk = $env:VITASDK
if (-not $vitasdk) {
    Write-Host "ERROR: VITASDK not set." -ForegroundColor Red
    Write-Host "Set it: `$env:VITASDK = 'C:\path\to\vitasdk'" -ForegroundColor Yellow
    exit 1
}

if ($Clean -and (Test-Path "build")) {
    Remove-Item -Recurse -Force "build"
    Write-Host "Build directory cleaned." -ForegroundColor Yellow
}

New-Item -ItemType Directory -Force -Path "build" | Out-Null
Set-Location build

$toolchain = Join-Path $vitasdk "share/vita.toolchain.cmake"
cmake .. -DCMAKE_TOOLCHAIN_FILE="$toolchain"
if (-not $?) { exit 1 }

cmake --build . --config Release
if (-not $?) { exit 1 }

# Copy assets and create VPK
New-Item -ItemType Directory -Force -Path out | Out-Null
Copy-Item "..\sce_sys\icon0.png" "out\icon0.png" -Force
Copy-Item "..\sce_sys\pic0.png" "out\pic0.png" -Force -ErrorAction SilentlyContinue
Copy-Item -Recurse "..\sce_sys\livearea" "out\sce_sys\" -Force -ErrorAction SilentlyContinue
Copy-Item "bootinstaller.self" "out\eboot.bin" -Force -ErrorAction SilentlyContinue

vita-makepkg -s -f out
if ($?) {
    Write-Host "`nVPK generado: build\out\bootinstaller.vpk" -ForegroundColor Green
    Write-Host "Instala el VPK en tu PS Vita por VitaShell." -ForegroundColor White
    Write-Host "Copia tus .rcf / .cbs a ux0:data/PSVitaBootAnim/" -ForegroundColor White
}
Set-Location $scriptDir
