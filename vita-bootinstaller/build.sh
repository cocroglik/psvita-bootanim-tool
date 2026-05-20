#!/bin/bash
# Build script for PS Vita Boot Anim Installer
# Requires: vitasdk toolchain (https://vitasdk.org/)
#
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== PS Vita Boot Anim Installer ==="
echo ""

# Check for vitasdk
if [ -z "$VITASDK" ]; then
    echo "ERROR: VITASDK not set."
    echo "Run: export VITASDK=/usr/local/vitasdk"
    echo "Or source the toolchain: source \$VITASDK/vitasdk.sh"
    exit 1
fi

# Build
mkdir -p build
cd build
cmake .. -DCMAKE_TOOLCHAIN_FILE="$VITASDK/share/vita.toolchain.cmake"
make -j$(nproc)

# Package VPK
make install DESTDIR=./out 2>/dev/null || true
cp ../sce_sys/icon0.png out/
cp ../sce_sys/pic0.png out/ 2>/dev/null || true
cp -r ../sce_sys/livearea out/sce_sys/ 2>/dev/null || true
vita-makepkg -s -f out/

echo ""
echo "=== VPK generado: out/bootinstaller.vpk ==="
echo "Instala el VPK en tu PS Vita por VitaShell."
echo "Copia tus .rcf / .cbs a ux0:data/PSVitaBootAnim/"
