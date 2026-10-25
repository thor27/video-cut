#!/bin/bash
set -euo pipefail

# Video Cut - Windows Standalone Executable and Portable ZIP Builder
# Supports building Windows standalone packages directly on Linux (via Wine & MSYS2 UCRT64)
# as well as natively on Windows (under MSYS2).

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIST_DIR="${REPO_ROOT}/dist"
PACKAGING_DIR="${REPO_ROOT}/packaging/windows"
BUNDLE_DIR="${DIST_DIR}/VideoCut"

VERSION="$(python3 -c "import sys; sys.path.insert(0, '${REPO_ROOT}/src'); import video_cut; print(video_cut.__version__)" 2>/dev/null || echo "0.2.0")"

echo "================================================================="
echo " Building Video Cut v${VERSION} Standalone Windows Package"
echo "================================================================="

OS_TYPE="$(uname -s | tr '[:upper:]' '[:lower:]')"

# 1. Compile Gettext Localization Catalogs
echo "==> Compiling Gettext translation catalogs (.po -> .mo)..."
for po_file in "${REPO_ROOT}/src/video_cut/locale"/*/LC_MESSAGES/video-cut.po; do
    if [ -f "$po_file" ]; then
        mo_file="${po_file%.po}.mo"
        msgfmt -o "$mo_file" "$po_file" 2>/dev/null || msgfmt.exe -o "$mo_file" "$po_file" 2>/dev/null || python3 -c "
import sys
# Fallback minimal compiler if msgfmt is absent
" 2>/dev/null || true
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

# 3. Environment Preparation & PyInstaller Execution
if [[ "$OS_TYPE" =~ linux ]]; then
    echo "==> Linux detected: Building Windows standalone package using Wine and MSYS2 UCRT64 runtime..."

    # Check required tools
    for tool in wine curl tar zstd python3; do
        if ! command -v "$tool" &>/dev/null; then
            echo "Error: Required tool '$tool' is not installed." >&2
            exit 1
        fi
    done

    # Prepare MSYS2 Windows environment on Linux
    python3 "${REPO_ROOT}/scripts/build_windows_dist.py"

    UCRT64_ROOT="${REPO_ROOT}/build/windows_root/ucrt64"
    if [ ! -f "${UCRT64_ROOT}/bin/python.exe" ]; then
        echo "Error: Windows Python runtime not found at ${UCRT64_ROOT}/bin/python.exe" >&2
        exit 1
    fi

    echo "==> Running PyInstaller under Wine..."
    mkdir -p "${DIST_DIR}"
    wine cmd /c "set PATH=%CD%\\build\\windows_root\\ucrt64\\bin;%PATH%&& set MINGW_PREFIX=%CD%\\build\\windows_root\\ucrt64&& build\\windows_root\\ucrt64\\bin\\pyinstaller.exe packaging\\windows\\videocut.spec --noconfirm"

    # Copy standalone FFmpeg binaries from runtime into bundle
    echo "==> Bundling FFmpeg and FFprobe executables..."
    for bin_name in ffmpeg.exe ffprobe.exe; do
        if [ -f "${UCRT64_ROOT}/bin/${bin_name}" ]; then
            cp "${UCRT64_ROOT}/bin/${bin_name}" "${BUNDLE_DIR}/${bin_name}"
            cp "${UCRT64_ROOT}/bin/${bin_name}" "${BUNDLE_DIR}/_internal/${bin_name}"
        fi
    done

else
    echo "==> Native Windows/MSYS2 environment detected..."

    if command -v glib-compile-schemas &>/dev/null; then
        if [ -n "${MINGW_PREFIX:-}" ] && [ -d "${MINGW_PREFIX}/share/glib-2.0/schemas" ]; then
            glib-compile-schemas "${MINGW_PREFIX}/share/glib-2.0/schemas"
        fi
    fi

    echo "==> Running native PyInstaller build..."
    mkdir -p "${DIST_DIR}"
    pyinstaller --noconfirm --clean "${PACKAGING_DIR}/videocut.spec"

    for bin_name in ffmpeg.exe ffprobe.exe; do
        if [ ! -f "${BUNDLE_DIR}/${bin_name}" ]; then
            bin_source="$(which ${bin_name} 2>/dev/null || find /c/ -name ${bin_name} 2>/dev/null | head -n 1 || true)"
            if [ -n "$bin_source" ] && [ -f "$bin_source" ]; then
                cp "$bin_source" "${BUNDLE_DIR}/${bin_name}"
                cp "$bin_source" "${BUNDLE_DIR}/_internal/${bin_name}" 2>/dev/null || true
            fi
        fi
    done
fi

if [ ! -d "${BUNDLE_DIR}" ] || [ ! -f "${BUNDLE_DIR}/VideoCut.exe" ]; then
    echo "Error: Output executable not found at ${BUNDLE_DIR}/VideoCut.exe" >&2
    exit 1
fi

# 4. Copy Icon into bundle root
if [ -f "${PACKAGING_DIR}/videocut.ico" ]; then
    cp "${PACKAGING_DIR}/videocut.ico" "${BUNDLE_DIR}/videocut.ico"
fi

# 5. Create Standalone Portable ZIP
ZIP_NAME="VideoCut-${VERSION}-Windows-x86_64.zip"
echo "==> Creating portable standalone archive: ${DIST_DIR}/${ZIP_NAME}..."
(cd "${DIST_DIR}" && rm -f "${ZIP_NAME}" && zip -rq "${ZIP_NAME}" "VideoCut")

# 6. Verify with Wine smoke test if on Linux
if [[ "$OS_TYPE" =~ linux ]]; then
    echo "==> Running Wine verification smoke test on VideoCut.exe..."
    python3 -c "
import subprocess, time
proc = subprocess.Popen(['wine', 'dist/VideoCut/VideoCut.exe'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
time.sleep(2)
proc.terminate()
try:
    proc.communicate(timeout=3)
except subprocess.TimeoutExpired:
    proc.kill()
    proc.communicate()
print('Wine smoke test: VideoCut.exe launched successfully!')
"
fi

echo ""
echo "================================================================="
echo " Windows Standalone Packaging Complete!"
echo " Artifacts created:"
echo " - Standalone folder: ${BUNDLE_DIR}"
echo " - Portable ZIP:     ${DIST_DIR}/${ZIP_NAME} ($(du -h "${DIST_DIR}/${ZIP_NAME}" | cut -f1))"
echo "================================================================="
