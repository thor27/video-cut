#!/bin/bash
set -euo pipefail

# Video Cut - Debian (.deb) Package Builder
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIST_DIR="${REPO_ROOT}/dist"
BUILD_DIR="${REPO_ROOT}/build/deb"

# Extract version from src/video_cut/__init__.py
VERSION="$(grep -m1 '^__version__' "${REPO_ROOT}/src/video_cut/__init__.py" | cut -d'"' -f2)"
if [ -z "$VERSION" ]; then
    VERSION="0.1.0"
fi

ARCH="all"
PKG_NAME="video-cut"
PKG_FULLNAME="${PKG_NAME}_${VERSION}_${ARCH}"
PKG_DIR="${BUILD_DIR}/${PKG_FULLNAME}"
OUTPUT_DEB="${DIST_DIR}/${PKG_FULLNAME}.deb"

echo "==> Building Debian package for ${PKG_NAME} v${VERSION} (${ARCH})..."

# Clean build directory
rm -rf "${PKG_DIR}"
mkdir -p "${PKG_DIR}" "${DIST_DIR}"

# 1. Directory Structure
mkdir -p "${PKG_DIR}/DEBIAN"
mkdir -p "${PKG_DIR}/usr/bin"
mkdir -p "${PKG_DIR}/usr/lib/python3/dist-packages/video_cut"
mkdir -p "${PKG_DIR}/usr/share/applications"
mkdir -p "${PKG_DIR}/usr/share/icons/hicolor/scalable/apps"

for size in 16 24 32 48 64 128 256 512; do
    mkdir -p "${PKG_DIR}/usr/share/icons/hicolor/${size}x${size}/apps"
done

# 2. Copy Python Source Code and Resources
cp -r "${REPO_ROOT}/src/video_cut/"* "${PKG_DIR}/usr/lib/python3/dist-packages/video_cut/"

# Clean up pycache in package staging
find "${PKG_DIR}" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
find "${PKG_DIR}" -type f -name "*.pyc" -delete 2>/dev/null || true

# 3. Create Binary Executable Wrapper
cat << 'EOF' > "${PKG_DIR}/usr/bin/video-cut"
#!/usr/bin/env python3
import sys
from video_cut.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
EOF
chmod 755 "${PKG_DIR}/usr/bin/video-cut"

# 4. Copy Desktop Entry
cp "${REPO_ROOT}/src/video_cut/resources/io.github.thor27.video-cut.desktop" \
   "${PKG_DIR}/usr/share/applications/io.github.thor27.video-cut.desktop"
chmod 644 "${PKG_DIR}/usr/share/applications/io.github.thor27.video-cut.desktop"

# 5. Copy Icons and Pixmaps
mkdir -p "${PKG_DIR}/usr/share/pixmaps"
cp "${REPO_ROOT}/src/video_cut/resources/io.github.thor27.video-cut.svg" \
   "${PKG_DIR}/usr/share/pixmaps/io.github.thor27.video-cut.svg"
cp "${REPO_ROOT}/src/video_cut/resources/io.github.thor27.video-cut.svg" \
   "${PKG_DIR}/usr/share/pixmaps/video-cut.svg"
cp "${REPO_ROOT}/src/video_cut/resources/icon_512.png" \
   "${PKG_DIR}/usr/share/pixmaps/io.github.thor27.video-cut.png"
cp "${REPO_ROOT}/src/video_cut/resources/icon_512.png" \
   "${PKG_DIR}/usr/share/pixmaps/video-cut.png"
chmod 644 "${PKG_DIR}/usr/share/pixmaps/"*

# Hicolor scalable
mkdir -p "${PKG_DIR}/usr/share/icons/hicolor/scalable/actions"
cp "${REPO_ROOT}/src/video_cut/resources/hicolor/scalable/actions/video-split-symbolic.svg" \
   "${PKG_DIR}/usr/share/icons/hicolor/scalable/actions/video-split-symbolic.svg"
chmod 644 "${PKG_DIR}/usr/share/icons/hicolor/scalable/actions/"*
cp "${REPO_ROOT}/src/video_cut/resources/io.github.thor27.video-cut.svg" \
   "${PKG_DIR}/usr/share/icons/hicolor/scalable/apps/io.github.thor27.video-cut.svg"
cp "${REPO_ROOT}/src/video_cut/resources/io.github.thor27.video-cut.svg" \
   "${PKG_DIR}/usr/share/icons/hicolor/scalable/apps/video-cut.svg"
chmod 644 "${PKG_DIR}/usr/share/icons/hicolor/scalable/apps/"*

# Hicolor sized PNGs
for size in 16 24 32 48 64 128 256 512; do
    if [ -f "${REPO_ROOT}/src/video_cut/resources/icon_${size}.png" ]; then
        cp "${REPO_ROOT}/src/video_cut/resources/icon_${size}.png" \
           "${PKG_DIR}/usr/share/icons/hicolor/${size}x${size}/apps/io.github.thor27.video-cut.png"
        cp "${REPO_ROOT}/src/video_cut/resources/icon_${size}.png" \
           "${PKG_DIR}/usr/share/icons/hicolor/${size}x${size}/apps/video-cut.png"
        chmod 644 "${PKG_DIR}/usr/share/icons/hicolor/${size}x${size}/apps/"*
    fi
done

# 6. Calculate Installed-Size in KB
INSTALLED_SIZE="$(du -sk "${PKG_DIR}" | cut -f1)"

# 7. Generate DEBIAN/control
cat << EOF > "${PKG_DIR}/DEBIAN/control"
Package: ${PKG_NAME}
Version: ${VERSION}
Section: video
Priority: optional
Architecture: ${ARCH}
Depends: python3 (>= 3.10), python3-gi (>= 3.42.0), gir1.2-gtk-4.0, gir1.2-adw-1, gstreamer1.0-gtk4, gstreamer1.0-plugins-base, gstreamer1.0-plugins-good, gstreamer1.0-libav, ffmpeg
Maintainer: Thomaz Reis <thor27@gmail.com>
Installed-Size: ${INSTALLED_SIZE}
Homepage: https://github.com/thor27/video-cut
Description: Easy-to-use tool to cut, merge, crop, and rotate videos
 Video Cut is an easy-to-use, fast, and lightweight video editor inspired by macOS
 QuickTime and Preview. It lets you cut and merge videos, add images with audio
 soundtracks, crop, and rotate clips on an intuitive filmstrip timeline with
 multi-format export.
EOF
chmod 644 "${PKG_DIR}/DEBIAN/control"

# 8. Generate DEBIAN/triggers
cat << 'EOF' > "${PKG_DIR}/DEBIAN/triggers"
interest-noawait /usr/share/applications
interest-noawait /usr/share/icons/hicolor
EOF
chmod 644 "${PKG_DIR}/DEBIAN/triggers"

# 9. Generate Post-Install & Post-Remove Hooks
cat << 'EOF' > "${PKG_DIR}/DEBIAN/postinst"
#!/bin/sh
set -e
if [ "$1" = "configure" ]; then
    # Clean up legacy/duplicate desktop file if present
    rm -f /usr/share/applications/video-cut.desktop 2>/dev/null || true

    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database -q /usr/share/applications || true
    fi
    if command -v update-icon-caches >/dev/null 2>&1; then
        update-icon-caches /usr/share/icons/hicolor || true
    fi
    if command -v gtk-update-icon-cache >/dev/null 2>&1; then
        gtk-update-icon-cache -q -t -f /usr/share/icons/hicolor || true
    fi
fi
exit 0
EOF
chmod 755 "${PKG_DIR}/DEBIAN/postinst"

cat << 'EOF' > "${PKG_DIR}/DEBIAN/postrm"
#!/bin/sh
set -e
if [ "$1" = "remove" ] || [ "$1" = "purge" ]; then
    rm -f /usr/share/applications/video-cut.desktop 2>/dev/null || true

    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database -q /usr/share/applications || true
    fi
    if command -v update-icon-caches >/dev/null 2>&1; then
        update-icon-caches /usr/share/icons/hicolor || true
    fi
    if command -v gtk-update-icon-cache >/dev/null 2>&1; then
        gtk-update-icon-cache -q -t -f /usr/share/icons/hicolor || true
    fi
fi
exit 0
EOF
chmod 755 "${PKG_DIR}/DEBIAN/postrm"

# 9. Build Debian Package
dpkg-deb --build --root-owner-group "${PKG_DIR}" "${OUTPUT_DEB}"

echo "==> Successfully generated Debian package:"
echo "    ${OUTPUT_DEB} ($(du -h "${OUTPUT_DEB}" | cut -f1))"
