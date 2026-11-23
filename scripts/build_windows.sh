#!/bin/bash
set -euo pipefail

# Video Cut - Standalone Windows Package Builder
# Builds standalone portable .zip and Inno Setup installer (.exe) for 64-bit Windows.
# Supports --validate-only for CI packaging verification on non-Windows hosts.

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIST_DIR="${REPO_ROOT}/dist"
PACKAGING_DIR="${REPO_ROOT}/packaging/windows"
BUNDLE_DIR="${DIST_DIR}/VideoCut"

# Extract version from src/video_cut/__init__.py or default to 0.6.1
VERSION="$(python3 -c "import sys; sys.path.insert(0, '${REPO_ROOT}/src'); import video_cut; print(video_cut.__version__)" 2>/dev/null || echo "0.6.1")"
if [ "$VERSION" = "devel" ]; then
    VERSION="0.6.1"
fi

# Parse options
VALIDATE_ONLY=false
for arg in "$@"; do
    case "$arg" in
        --validate-only)
            VALIDATE_ONLY=true
            ;;
        --help|-h)
            echo "Usage: $0 [--validate-only]"
            echo ""
            echo "Options:"
            echo "  --validate-only   Validate packaging specifications and assets without building"
            exit 0
            ;;
    esac
done

if [ "$VALIDATE_ONLY" = true ]; then
    echo "==> Validating Windows packaging files and specifications..."
    python3 -m py_compile "${PACKAGING_DIR}/videocut.spec"
    echo "    [OK] Python syntax valid: videocut.spec"

    if [ -f "${PACKAGING_DIR}/videocut.iss" ]; then
        if grep -q "AppId" "${PACKAGING_DIR}/videocut.iss" && grep -q "VideoCut" "${PACKAGING_DIR}/videocut.iss"; then
            echo "    [OK] Inno Setup script present and valid: videocut.iss"
        else
            echo "    [FAIL] Inno Setup script missing essential directives" >&2
            exit 1
        fi
    else
        echo "    [FAIL] Missing videocut.iss" >&2
        exit 1
    fi

    if [ -f "${PACKAGING_DIR}/videocut.ico" ]; then
        echo "    [OK] Icon asset present: videocut.ico ($(du -h "${PACKAGING_DIR}/videocut.ico" | cut -f1))"
    else
        echo "    [FAIL] Missing videocut.ico" >&2
        exit 1
    fi

    echo "==> Windows packaging configuration validation passed successfully!"
    exit 0
fi

echo "================================================================="
echo " Building Video Cut v${VERSION} Standalone Windows Package"
echo "================================================================="

# 1. Compile Gettext Localization Catalogs
echo "==> Compiling Gettext translation catalogs (.po -> .mo)..."
for po_file in "${REPO_ROOT}/src/video_cut/locale"/*/LC_MESSAGES/video-cut.po; do
    if [ -f "$po_file" ]; then
        mo_file="${po_file%.po}.mo"
        msgfmt -o "$mo_file" "$po_file" 2>/dev/null || msgfmt.exe -o "$mo_file" "$po_file" 2>/dev/null || true
    fi
done

# 2. Ensure Windows Multi-Resolution Icon exists
if [ ! -f "${PACKAGING_DIR}/videocut.ico" ]; then
    echo "==> Generating Windows multi-resolution icon..."
    python3 -c "
from PIL import Image
img = Image.open('${REPO_ROOT}/src/video_cut/resources/icon_256.png')
img.save('${PACKAGING_DIR}/videocut.ico', format='ICO', sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
" 2>/dev/null || true
fi

# 3. Compile GLib Schemas if tool available
if command -v glib-compile-schemas &>/dev/null; then
    if [ -n "${MINGW_PREFIX:-}" ] && [ -d "${MINGW_PREFIX}/share/glib-2.0/schemas" ]; then
        echo "==> Compiling GSettings schemas..."
        glib-compile-schemas "${MINGW_PREFIX}/share/glib-2.0/schemas" || true
    fi
fi

# 4. Run PyInstaller
echo "==> Running PyInstaller build..."
mkdir -p "${DIST_DIR}"
pyinstaller --noconfirm --clean "${PACKAGING_DIR}/videocut.spec"

if [ ! -d "${BUNDLE_DIR}" ] || [ ! -f "${BUNDLE_DIR}/VideoCut.exe" ]; then
    echo "Error: Output executable not found at ${BUNDLE_DIR}/VideoCut.exe" >&2
    exit 1
fi

# 5. Ensure FFmpeg / FFprobe are bundled in bundle root & bin/
for bin_name in ffmpeg.exe ffprobe.exe; do
    if [ ! -f "${BUNDLE_DIR}/${bin_name}" ]; then
        src_bin="$(which "$bin_name" 2>/dev/null || find /c/ -name "$bin_name" 2>/dev/null | head -n 1 || true)"
        if [ -n "$src_bin" ] && [ -f "$src_bin" ]; then
            cp "$src_bin" "${BUNDLE_DIR}/${bin_name}"
            mkdir -p "${BUNDLE_DIR}/bin"
            cp "$src_bin" "${BUNDLE_DIR}/bin/${bin_name}"
        fi
    fi
done

# Copy icon to bundle root
if [ -f "${PACKAGING_DIR}/videocut.ico" ]; then
    cp "${PACKAGING_DIR}/videocut.ico" "${BUNDLE_DIR}/videocut.ico"
fi

# 6. Create Portable ZIP Archive
ZIP_NAME="VideoCut-${VERSION}-Windows-x86_64-portable.zip"
echo "==> Creating portable standalone archive: ${DIST_DIR}/${ZIP_NAME}..."
(cd "${DIST_DIR}" && rm -f "${ZIP_NAME}" && zip -rq "${ZIP_NAME}" "VideoCut")

# 7. Compile Inno Setup Installer if iscc is available
SETUP_NAME="VideoCut-${VERSION}-Windows-x86_64-Setup.exe"
if command -v iscc &>/dev/null; then
    echo "==> Compiling Inno Setup installer: ${DIST_DIR}/${SETUP_NAME}..."
    iscc "/DAppVersion=${VERSION}" "${PACKAGING_DIR}/videocut.iss"
elif command -v ISCC.exe &>/dev/null; then
    echo "==> Compiling Inno Setup installer via ISCC.exe..."
    ISCC.exe "/DAppVersion=${VERSION}" "${PACKAGING_DIR}/videocut.iss"
else
    echo "Note: Inno Setup compiler (iscc) not found; skipping installer compilation."
fi

echo ""
echo "================================================================="
echo " Windows Packaging Complete!"
echo " Artifacts:"
echo " - Standalone Directory: ${BUNDLE_DIR}"
if [ -f "${DIST_DIR}/${ZIP_NAME}" ]; then
    echo " - Portable ZIP:        ${DIST_DIR}/${ZIP_NAME} ($(du -h "${DIST_DIR}/${ZIP_NAME}" | cut -f1))"
fi
if [ -f "${DIST_DIR}/${SETUP_NAME}" ]; then
    echo " - Inno Setup Installer: ${DIST_DIR}/${SETUP_NAME} ($(du -h "${DIST_DIR}/${SETUP_NAME}" | cut -f1))"
fi
echo "================================================================="
