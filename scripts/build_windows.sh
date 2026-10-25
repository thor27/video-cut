#!/bin/bash
set -euo pipefail

# Video Cut - Windows Standalone Executable and Installer Builder
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIST_DIR="${REPO_ROOT}/dist"
PACKAGING_DIR="${REPO_ROOT}/packaging/windows"

VERSION="$(grep -m1 '^__version__' "${REPO_ROOT}/src/video_cut/__init__.py" | cut -d'"' -f2)"
if [ -z "$VERSION" ]; then
    VERSION="0.2.0"
fi

echo "================================================================="
echo " Building Video Cut v${VERSION} Standalone Windows Package"
echo "================================================================="

# 1. Compile Gettext Localization Catalogs
echo "==> Compiling Gettext translation catalogs (.po -> .mo)..."
for po_file in "${REPO_ROOT}/src/video_cut/locale"/*/LC_MESSAGES/video-cut.po; do
    if [ -f "$po_file" ]; then
        mo_file="${po_file%.po}.mo"
        msgfmt -o "$mo_file" "$po_file" 2>/dev/null || msgfmt.exe -o "$mo_file" "$po_file" 2>/dev/null || echo "Warning: msgfmt not available, skipping $po_file"
    fi
done

# 2. Ensure Icon exists
if [ ! -f "${PACKAGING_DIR}/videocut.ico" ]; then
    echo "==> Generating Windows icon from resources..."
    python3 -c "
from PIL import Image
from pathlib import Path
img = Image.open('${REPO_ROOT}/src/video_cut/resources/icon_256.png')
img.save('${PACKAGING_DIR}/videocut.ico', format='ICO', sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
" 2>/dev/null || true
fi

# 3. Compile GSettings schemas if in MSYS2
if command -v glib-compile-schemas &>/dev/null; then
    echo "==> Compiling GLib schemas..."
    if [ -n "${MINGW_PREFIX:-}" ] && [ -d "${MINGW_PREFIX}/share/glib-2.0/schemas" ]; then
        glib-compile-schemas "${MINGW_PREFIX}/share/glib-2.0/schemas"
    fi
fi

# 4. Run PyInstaller
echo "==> Running PyInstaller build..."
mkdir -p "${DIST_DIR}"
pyinstaller --noconfirm --clean "${PACKAGING_DIR}/videocut.spec"

BUNDLE_DIR="${DIST_DIR}/VideoCut"
if [ ! -d "${BUNDLE_DIR}" ]; then
    echo "Error: PyInstaller output directory not found at ${BUNDLE_DIR}" >&2
    exit 1
fi

# 5. Ensure FFmpeg binaries are in bundle
for bin_name in ffmpeg.exe ffprobe.exe; do
    if [ ! -f "${BUNDLE_DIR}/${bin_name}" ]; then
        bin_source="$(which ${bin_name} 2>/dev/null || find /c/ -name ${bin_name} 2>/dev/null | head -n 1 || true)"
        if [ -n "$bin_source" ] && [ -f "$bin_source" ]; then
            echo "==> Copying ${bin_name} into bundle..."
            cp "$bin_source" "${BUNDLE_DIR}/${bin_name}"
        fi
    fi
done

# 6. Copy Icon into bundle
if [ -f "${PACKAGING_DIR}/videocut.ico" ]; then
    cp "${PACKAGING_DIR}/videocut.ico" "${BUNDLE_DIR}/videocut.ico"
fi

# 7. Package standalone portable ZIP
ZIP_NAME="VideoCut-${VERSION}-Windows-x86_64.zip"
echo "==> Creating portable standalone archive: ${DIST_DIR}/${ZIP_NAME}..."
(cd "${DIST_DIR}" && rm -f "${ZIP_NAME}" && zip -rq "${ZIP_NAME}" "VideoCut")

# 8. Build Inno Setup Installer if compiler is present
ISCC_BIN=""
if command -v iscc &>/dev/null; then
    ISCC_BIN="iscc"
elif command -v ISCC.exe &>/dev/null; then
    ISCC_BIN="ISCC.exe"
elif [ -f "/c/Program Files (x86)/Inno Setup 6/ISCC.exe" ]; then
    ISCC_BIN="/c/Program Files (x86)/Inno Setup 6/ISCC.exe"
elif [ -f "/c/Program Files/Inno Setup 6/ISCC.exe" ]; then
    ISCC_BIN="/c/Program Files/Inno Setup 6/ISCC.exe"
fi

if [ -n "$ISCC_BIN" ]; then
    echo "==> Compiling Inno Setup installer..."
    "$ISCC_BIN" "/DAppVersion=${VERSION}" "${PACKAGING_DIR}/videocut.iss"
    echo "==> Inno Setup installer created successfully!"
else
    echo "==> Note: Inno Setup compiler (ISCC) not found; skipping installer .exe generation."
    echo "    (The portable zip archive contains the full standalone executable and dependencies)."
fi

echo ""
echo "================================================================="
echo " Windows Packaging Complete!"
echo " Output files in ${DIST_DIR}:"
ls -lh "${DIST_DIR}"/VideoCut*.zip 2>/dev/null || true
ls -lh "${DIST_DIR}"/VideoCut*Setup.exe 2>/dev/null || true
echo "================================================================="
