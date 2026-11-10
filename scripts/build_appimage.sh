#!/bin/bash
set -euo pipefail

# Video Cut - AppImage Builder
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIST_DIR="${REPO_ROOT}/dist"
BUILD_DIR="${REPO_ROOT}/build"
APPDIR="${BUILD_DIR}/AppDir"
TOOLS_DIR="${BUILD_DIR}/tools"

# Extract version from src/video_cut/__init__.py or environment
if [ -z "${VERSION:-}" ]; then
    VERSION="$(grep -m1 '^__version__' "${REPO_ROOT}/src/video_cut/__init__.py" | cut -d'"' -f2)"
fi
if [ -z "$VERSION" ]; then
    VERSION="devel"
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

# Determine host python major.minor version
HOST_PY_VER="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || echo "3.12")"

# Bundle Pillow dependency for standard Python versions into version-specific directories
PILLOW_REQ_DIR="${REPO_ROOT}/packaging/appimage"
if command -v pip >/dev/null 2>&1 || python3 -m pip --version >/dev/null 2>&1; then
    PIP_CMD="$(command -v pip 2>/dev/null || echo "python3 -m pip")"
    for pyver in 3.10 3.11 3.12 3.13; do
        target_dir="${APPDIR}/usr/lib/python${pyver}/dist-packages"
        req_file="${PILLOW_REQ_DIR}/requirements-pillow.txt"
        if [ -f "${PILLOW_REQ_DIR}/requirements-pillow-py${pyver}.txt" ]; then
            req_file="${PILLOW_REQ_DIR}/requirements-pillow-py${pyver}.txt"
        fi
        wheel_tmp="$(mktemp -d)"
        $PIP_CMD download --only-binary=:all: --no-deps --require-hashes \
            --dest "$wheel_tmp" --platform manylinux_2_28_x86_64 \
            --python-version "${pyver}" -r "$req_file"
        for whl in "$wheel_tmp"/*.whl; do
            mkdir -p "$target_dir"
            unzip -q -o "$whl" -d "$target_dir"
        done
        rm -rf "$wheel_tmp"
    done
fi

# Also preserve host distribution PIL for a host python the pinned wheels do not cover
if [ -d "/usr/lib/python3/dist-packages/PIL" ] && [ ! -d "${APPDIR}/usr/lib/python${HOST_PY_VER}/dist-packages/PIL" ]; then
    mkdir -p "${APPDIR}/usr/lib/python${HOST_PY_VER}/dist-packages"
    cp -r /usr/lib/python3/dist-packages/PIL "${APPDIR}/usr/lib/python${HOST_PY_VER}/dist-packages/"
    cp -r /usr/lib/python3/dist-packages/Pillow*.dist-info "${APPDIR}/usr/lib/python${HOST_PY_VER}/dist-packages/" 2>/dev/null || true
fi

# Bundle GObject-Introspection typelibs into AppDir
mkdir -p "${APPDIR}/usr/lib/x86_64-linux-gnu/girepository-1.0"
for tlb_dir in /usr/lib/x86_64-linux-gnu/girepository-1.0 /usr/lib64/girepository-1.0 /usr/lib/girepository-1.0; do
    if [ -d "$tlb_dir" ]; then
        for typelib in Gtk-4.0 Gdk-4.0 Gsk-4.0 Adw-1 GdkPixbuf-2.0 Pango-1.0 PangoCairo-1.0 cairo-1.0 Gio-2.0 GLib-2.0 GObject-2.0 GModule-2.0 Graphene-1.0 HarfBuzz-0.0 freetype2-2.0 Gst-1.0 GstBase-1.0 GstAudio-1.0 GstVideo-1.0 GstPbutils-1.0 GstPlay-1.0 GstTag-1.0 GstApp-1.0 GstAllocators-1.0 GstGL-1.0; do
            if [ -f "$tlb_dir/${typelib}.typelib" ]; then
                cp -f "$tlb_dir/${typelib}.typelib" "${APPDIR}/usr/lib/x86_64-linux-gnu/girepository-1.0/"
            fi
        done
        break
    fi
done

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

# 4. Copy Desktop Entry, MIME definition, and AppStream MetaInfo
cp "${REPO_ROOT}/src/video_cut/resources/io.github.thor27.video-cut.desktop" \
   "${APPDIR}/io.github.thor27.video-cut.desktop"
cp "${REPO_ROOT}/src/video_cut/resources/io.github.thor27.video-cut.desktop" \
   "${APPDIR}/usr/share/applications/io.github.thor27.video-cut.desktop"
chmod 644 "${APPDIR}/io.github.thor27.video-cut.desktop" \
          "${APPDIR}/usr/share/applications/io.github.thor27.video-cut.desktop"

mkdir -p "${APPDIR}/usr/share/mime/packages"
cp "${REPO_ROOT}/src/video_cut/resources/io.github.thor27.video-cut.xml" \
   "${APPDIR}/usr/share/mime/packages/io.github.thor27.video-cut.xml"
chmod 644 "${APPDIR}/usr/share/mime/packages/io.github.thor27.video-cut.xml"

mkdir -p "${APPDIR}/usr/share/metainfo"
cp "${REPO_ROOT}/io.github.thor27.video-cut.metainfo.xml" \
   "${APPDIR}/usr/share/metainfo/io.github.thor27.video-cut.metainfo.xml"
chmod 644 "${APPDIR}/usr/share/metainfo/io.github.thor27.video-cut.metainfo.xml"

# 5. Copy Icons (at root of AppDir and in usr/share/icons)
cp "${REPO_ROOT}/src/video_cut/resources/icon_256.png" "${APPDIR}/io.github.thor27.video-cut.png"
cp "${REPO_ROOT}/src/video_cut/resources/io.github.thor27.video-cut.svg" "${APPDIR}/io.github.thor27.video-cut.svg"
ln -sf "io.github.thor27.video-cut.png" "${APPDIR}/.DirIcon"

mkdir -p "${APPDIR}/usr/share/pixmaps"
cp "${REPO_ROOT}/src/video_cut/resources/icon_512.png" "${APPDIR}/usr/share/pixmaps/video-cut.png"
cp "${REPO_ROOT}/src/video_cut/resources/icon_512.png" "${APPDIR}/usr/share/pixmaps/io.github.thor27.video-cut.png"
cp "${REPO_ROOT}/src/video_cut/resources/io.github.thor27.video-cut.svg" "${APPDIR}/usr/share/pixmaps/video-cut.svg"
cp "${REPO_ROOT}/src/video_cut/resources/io.github.thor27.video-cut.svg" "${APPDIR}/usr/share/pixmaps/io.github.thor27.video-cut.svg"
chmod 644 "${APPDIR}/usr/share/pixmaps/"*

mkdir -p "${APPDIR}/usr/share/icons/hicolor/scalable/actions"
cp "${REPO_ROOT}/src/video_cut/resources/hicolor/scalable/actions/"*.svg \
   "${APPDIR}/usr/share/icons/hicolor/scalable/actions/"
chmod 644 "${APPDIR}/usr/share/icons/hicolor/scalable/actions/"*

cp "${REPO_ROOT}/src/video_cut/resources/io.github.thor27.video-cut.svg" \
   "${APPDIR}/usr/share/icons/hicolor/scalable/apps/io.github.thor27.video-cut.svg"
cp "${REPO_ROOT}/src/video_cut/resources/io.github.thor27.video-cut.svg" \
   "${APPDIR}/usr/share/icons/hicolor/scalable/apps/video-cut.svg"

for size in 16 24 32 48 64 128 256 512; do
    if [ -f "${REPO_ROOT}/src/video_cut/resources/icon_${size}.png" ]; then
        cp "${REPO_ROOT}/src/video_cut/resources/icon_${size}.png" \
           "${APPDIR}/usr/share/icons/hicolor/${size}x${size}/apps/io.github.thor27.video-cut.png"
        cp "${REPO_ROOT}/src/video_cut/resources/icon_${size}.png" \
           "${APPDIR}/usr/share/icons/hicolor/${size}x${size}/apps/video-cut.png"
    fi
done

# 6. Copy AppRun
cp "${REPO_ROOT}/packaging/appimage/AppRun" "${APPDIR}/AppRun"
chmod 755 "${APPDIR}/AppRun"

# 7. Locate or Download appimagetool
APPIMAGETOOL_VERSION="1.9.1"
APPIMAGETOOL_SHA256="ed4ce84f0d9caff66f50bcca6ff6f35aae54ce8135408b3fa33abfc3cb384eb0"
APPIMAGETOOL=""
if command -v appimagetool >/dev/null 2>&1; then
    APPIMAGETOOL="$(command -v appimagetool)"
elif [ -x "${TOOLS_DIR}/appimagetool" ]; then
    APPIMAGETOOL="${TOOLS_DIR}/appimagetool"
else
    echo "==> Downloading appimagetool ${APPIMAGETOOL_VERSION}..."
    TOOL_URL="https://github.com/AppImage/appimagetool/releases/download/${APPIMAGETOOL_VERSION}/appimagetool-x86_64.AppImage"
    curl -fsSL -o "${TOOLS_DIR}/appimagetool.download" "${TOOL_URL}"
    echo "${APPIMAGETOOL_SHA256}  ${TOOLS_DIR}/appimagetool.download" | sha256sum --check --quiet
    mv "${TOOLS_DIR}/appimagetool.download" "${TOOLS_DIR}/appimagetool"
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
