#!/bin/bash
set -euo pipefail

# Video Cut - AppImage Builder
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIST_DIR="${REPO_ROOT}/dist"
BUILD_DIR="${REPO_ROOT}/build"
APPDIR="${BUILD_DIR}/AppDir"
TOOLS_DIR="${BUILD_DIR}/tools"

# Extract version from src/video_cut/__init__.py
VERSION="$(grep -m1 '^__version__' "${REPO_ROOT}/src/video_cut/__init__.py" | cut -d'"' -f2)"
if [ -z "$VERSION" ]; then
    VERSION="0.1.0"
fi

OUTPUT_APPIMAGE="${DIST_DIR}/VideoCut-${VERSION}-x86_64.AppImage"

echo "==> Building AppImage for Video Cut v${VERSION}..."

# Clean AppDir
rm -rf "${APPDIR}"
mkdir -p "${APPDIR}" "${DIST_DIR}" "${TOOLS_DIR}"

# 1. Directory Structure inside AppDir
mkdir -p "${APPDIR}/usr/bin"
mkdir -p "${APPDIR}/usr/lib/python3/dist-packages/video_cut"
mkdir -p "${APPDIR}/usr/share/applications"
mkdir -p "${APPDIR}/usr/share/icons/hicolor/scalable/apps"

for size in 16 24 32 48 64 128 256 512; do
    mkdir -p "${APPDIR}/usr/share/icons/hicolor/${size}x${size}/apps"
done

# 2. Copy Python Source Code and Resources
cp -r "${REPO_ROOT}/src/video_cut/"* "${APPDIR}/usr/lib/python3/dist-packages/video_cut/"

# Clean up pycache in staging
find "${APPDIR}" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
find "${APPDIR}" -type f -name "*.pyc" -delete 2>/dev/null || true

# 3. Create Binary Executable Wrapper
cat << 'EOF' > "${APPDIR}/usr/bin/video-cut"
#!/usr/bin/env python3
import sys
from video_cut.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
EOF
chmod 755 "${APPDIR}/usr/bin/video-cut"

# 4. Copy Desktop Entry (at root of AppDir and in usr/share/applications)
cp "${REPO_ROOT}/src/video_cut/resources/io.github.thomaz.videocut.desktop" \
   "${APPDIR}/io.github.thomaz.videocut.desktop"
cp "${REPO_ROOT}/src/video_cut/resources/io.github.thomaz.videocut.desktop" \
   "${APPDIR}/usr/share/applications/io.github.thomaz.videocut.desktop"
chmod 644 "${APPDIR}/io.github.thomaz.videocut.desktop" \
          "${APPDIR}/usr/share/applications/io.github.thomaz.videocut.desktop"

# 5. Copy Icons (at root of AppDir and in usr/share/icons)
cp "${REPO_ROOT}/src/video_cut/resources/icon_256.png" "${APPDIR}/io.github.thomaz.videocut.png"
cp "${REPO_ROOT}/src/video_cut/resources/io.github.thomaz.videocut.svg" "${APPDIR}/io.github.thomaz.videocut.svg"
ln -sf "io.github.thomaz.videocut.png" "${APPDIR}/.DirIcon"

mkdir -p "${APPDIR}/usr/share/pixmaps"
cp "${REPO_ROOT}/src/video_cut/resources/icon_512.png" "${APPDIR}/usr/share/pixmaps/video-cut.png"
cp "${REPO_ROOT}/src/video_cut/resources/icon_512.png" "${APPDIR}/usr/share/pixmaps/io.github.thomaz.videocut.png"
cp "${REPO_ROOT}/src/video_cut/resources/io.github.thomaz.videocut.svg" "${APPDIR}/usr/share/pixmaps/video-cut.svg"
cp "${REPO_ROOT}/src/video_cut/resources/io.github.thomaz.videocut.svg" "${APPDIR}/usr/share/pixmaps/io.github.thomaz.videocut.svg"
chmod 644 "${APPDIR}/usr/share/pixmaps/"*

cp "${REPO_ROOT}/src/video_cut/resources/io.github.thomaz.videocut.svg" \
   "${APPDIR}/usr/share/icons/hicolor/scalable/apps/io.github.thomaz.videocut.svg"
cp "${REPO_ROOT}/src/video_cut/resources/io.github.thomaz.videocut.svg" \
   "${APPDIR}/usr/share/icons/hicolor/scalable/apps/video-cut.svg"

for size in 16 24 32 48 64 128 256 512; do
    if [ -f "${REPO_ROOT}/src/video_cut/resources/icon_${size}.png" ]; then
        cp "${REPO_ROOT}/src/video_cut/resources/icon_${size}.png" \
           "${APPDIR}/usr/share/icons/hicolor/${size}x${size}/apps/io.github.thomaz.videocut.png"
        cp "${REPO_ROOT}/src/video_cut/resources/icon_${size}.png" \
           "${APPDIR}/usr/share/icons/hicolor/${size}x${size}/apps/video-cut.png"
    fi
done

# 6. Copy AppRun
cp "${REPO_ROOT}/packaging/appimage/AppRun" "${APPDIR}/AppRun"
chmod 755 "${APPDIR}/AppRun"

# 7. Locate or Download appimagetool
APPIMAGETOOL=""
if command -v appimagetool >/dev/null 2>&1; then
    APPIMAGETOOL="$(command -v appimagetool)"
elif [ -x "${TOOLS_DIR}/appimagetool" ]; then
    APPIMAGETOOL="${TOOLS_DIR}/appimagetool"
else
    echo "==> Downloading appimagetool..."
    TOOL_URL="https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage"
    curl -fsSL -o "${TOOLS_DIR}/appimagetool" "${TOOL_URL}"
    chmod +x "${TOOLS_DIR}/appimagetool"
    APPIMAGETOOL="${TOOLS_DIR}/appimagetool"
fi

# 8. Package with appimagetool
echo "==> Generating AppImage package..."
export ARCH="x86_64"

# Attempt running appimagetool directly; fall back to --appimage-extract-and-run if FUSE is unavailable
if "${APPIMAGETOOL}" "${APPDIR}" "${OUTPUT_APPIMAGE}" 2>/dev/null; then
    :
else
    "${APPIMAGETOOL}" --appimage-extract-and-run "${APPDIR}" "${OUTPUT_APPIMAGE}"
fi

chmod +x "${OUTPUT_APPIMAGE}"

echo "==> Successfully generated AppImage:"
echo "    ${OUTPUT_APPIMAGE} ($(du -h "${OUTPUT_APPIMAGE}" | cut -f1))"
