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

# Determine build Python interpreter with PyGObject (gi)
BUILD_PY_BIN=""
for candidate in /usr/bin/python3 /bin/python3 $(command -v python3 2>/dev/null); do
    if [ -x "$candidate" ] && "$candidate" -c "import gi" 2>/dev/null; then
        BUILD_PY_BIN="$candidate"
        break
    fi
done
if [ -z "$BUILD_PY_BIN" ]; then
    BUILD_PY_BIN="$(command -v python3 2>/dev/null || echo "python3")"
fi
HOST_PY_VER="$("$BUILD_PY_BIN" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || echo "3.12")"

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

# Bundle Python 3 runtime and standard library for self-contained execution
echo "==> Bundling Python runtime (${BUILD_PY_BIN})..."
if [ -x "$BUILD_PY_BIN" ]; then
    cp -L "$BUILD_PY_BIN" "${APPDIR}/usr/bin/python3"
    chmod +x "${APPDIR}/usr/bin/python3"
fi

# Bundle standard library for the bundled Python
if [ -d "/usr/lib/python${HOST_PY_VER}" ]; then
    mkdir -p "${APPDIR}/usr/lib/python${HOST_PY_VER}"
    cp -rL /usr/lib/python${HOST_PY_VER}/* "${APPDIR}/usr/lib/python${HOST_PY_VER}/" 2>/dev/null || true
    # Remove large non-runtime modules (test suites, documentation, GUI tools)
    rm -rf "${APPDIR}/usr/lib/python${HOST_PY_VER}/test" \
           "${APPDIR}/usr/lib/python${HOST_PY_VER}/tests" \
           "${APPDIR}/usr/lib/python${HOST_PY_VER}/idlelib" \
           "${APPDIR}/usr/lib/python${HOST_PY_VER}/tkinter" \
           "${APPDIR}/usr/lib/python${HOST_PY_VER}/turtle"* 2>/dev/null || true
fi

# Bundle host dist-packages (gi, cairo, PIL) into version-specific directory for the bundled Python
for pkg in gi cairo PIL; do
    for pdir in /usr/lib/python3/dist-packages /usr/local/lib/python3/dist-packages /usr/lib/python${HOST_PY_VER}/dist-packages; do
        if [ -d "$pdir/$pkg" ] && [ ! -d "${APPDIR}/usr/lib/python${HOST_PY_VER}/dist-packages/$pkg" ]; then
            mkdir -p "${APPDIR}/usr/lib/python${HOST_PY_VER}/dist-packages"
            cp -rL "$pdir/$pkg" "${APPDIR}/usr/lib/python${HOST_PY_VER}/dist-packages/" 2>/dev/null || true
        fi
    done
done

# Bundle python3-cairo for Python 3.10 (Ubuntu 22.04 LTS catalog compatibility)
CAIRO_DEB_URL="http://archive.ubuntu.com/ubuntu/pool/main/p/pycairo/python3-cairo_1.20.1-3build1_amd64.deb"
cairo_tmp="$(mktemp -d)"
if curl -fsSL "$CAIRO_DEB_URL" -o "$cairo_tmp/cairo.deb" 2>/dev/null; then
    if dpkg-deb -x "$cairo_tmp/cairo.deb" "$cairo_tmp/extracted" 2>/dev/null; then
        mkdir -p "${APPDIR}/usr/lib/python3.10/dist-packages"
        if [ -d "$cairo_tmp/extracted/usr/lib/python3/dist-packages/cairo" ]; then
            cp -r "$cairo_tmp/extracted/usr/lib/python3/dist-packages/cairo" "${APPDIR}/usr/lib/python3.10/dist-packages/"
        fi
    fi
fi
rm -rf "$cairo_tmp"

# Also preserve host distribution PIL for a host python the pinned wheels do not cover
if [ -d "/usr/lib/python3/dist-packages/PIL" ] && [ ! -d "${APPDIR}/usr/lib/python${HOST_PY_VER}/dist-packages/PIL" ]; then
    mkdir -p "${APPDIR}/usr/lib/python${HOST_PY_VER}/dist-packages"
    cp -r /usr/lib/python3/dist-packages/PIL "${APPDIR}/usr/lib/python${HOST_PY_VER}/dist-packages/"
    cp -r /usr/lib/python3/dist-packages/Pillow*.dist-info "${APPDIR}/usr/lib/python${HOST_PY_VER}/dist-packages/" 2>/dev/null || true
fi

# Bundle GObject-Introspection typelibs into AppDir
mkdir -p "${APPDIR}/usr/lib/x86_64-linux-gnu/girepository-1.0"
mkdir -p "${APPDIR}/usr/lib/girepository-1.0"
for tlb_dir in /usr/lib/x86_64-linux-gnu/girepository-1.0 /usr/lib64/girepository-1.0 /usr/lib/girepository-1.0 /usr/local/lib/girepository-1.0; do
    if [ -d "$tlb_dir" ]; then
        for typelib in Gtk-4.0 Gdk-4.0 Gsk-4.0 Adw-1 GdkPixbuf-2.0 Pango-1.0 PangoCairo-1.0 cairo-1.0 Gio-2.0 GLib-2.0 GObject-2.0 GModule-2.0 Graphene-1.0 HarfBuzz-0.0 freetype2-2.0 Gst-1.0 GstBase-1.0 GstAudio-1.0 GstVideo-1.0 GstPbutils-1.0 GstPlay-1.0 GstTag-1.0 GstApp-1.0 GstAllocators-1.0 GstGL-1.0; do
            if [ -f "$tlb_dir/${typelib}.typelib" ]; then
                cp -f "$tlb_dir/${typelib}.typelib" "${APPDIR}/usr/lib/x86_64-linux-gnu/girepository-1.0/" 2>/dev/null || true
                cp -f "$tlb_dir/${typelib}.typelib" "${APPDIR}/usr/lib/girepository-1.0/" 2>/dev/null || true
            fi
        done
    fi
done

# Verify required typelibs are bundled
for req_tlb in Gtk-4.0 Adw-1 Gst-1.0; do
    if [ ! -f "${APPDIR}/usr/lib/x86_64-linux-gnu/girepository-1.0/${req_tlb}.typelib" ] && \
       [ ! -f "${APPDIR}/usr/lib/girepository-1.0/${req_tlb}.typelib" ]; then
        echo "Error: Required typelib ${req_tlb}.typelib is missing from AppDir!" >&2
        echo "Please install gir1.2-gtk-4.0, gir1.2-adw-1, and gir1.2-gstreamer-1.0 on the build system." >&2
        exit 1
    fi
done

# Bundle glibc loader and core runtime libraries to ensure self-contained compatibility across distros
echo "==> Bundling glibc runtime..."
mkdir -p "${APPDIR}/glibc"
for lib in ld-linux-x86-64.so.2 libc.so.6 libm.so.6 libdl.so.2 libpthread.so.0 librt.so.1 libresolv.so.2 libutil.so.1 libgcc_s.so.1; do
    for dir in /lib/x86_64-linux-gnu /usr/lib/x86_64-linux-gnu /lib64 /usr/lib64 /lib /usr/lib; do
        if [ -e "$dir/$lib" ]; then
            cp -L "$dir/$lib" "${APPDIR}/glibc/"
            break
        fi
    done
done

# Bundle GTK4, Libadwaita, Graphene, and GStreamer shared libraries into AppDir
echo "==> Bundling GTK4, Libadwaita, and GStreamer dependencies..."
mkdir -p "${APPDIR}/usr/lib/x86_64-linux-gnu"
mkdir -p "${APPDIR}/usr/lib"
for lib_pattern in "libgtk-4.so*" "libadwaita-1.so*" "libgraphene-1.0.so*" \
                   "libgstreamer-1.0.so*" "libgstbase-1.0.so*" "libgstaudio-1.0.so*" \
                   "libgstvideo-1.0.so*" "libgstpbutils-1.0.so*" "libgstapp-1.0.so*" \
                   "libgsttag-1.0.so*" "libgstallocators-1.0.so*" "libgstgl-1.0.so*"; do
    for lib_dir in /usr/lib/x86_64-linux-gnu /usr/lib64 /usr/lib; do
        if compgen -G "${lib_dir}/${lib_pattern}" > /dev/null 2>&1; then
            cp -a ${lib_dir}/${lib_pattern} "${APPDIR}/usr/lib/" 2>/dev/null || true
            break
        fi
    done
done

# Bundle essential GStreamer plugins for fallback mode
mkdir -p "${APPDIR}/usr/lib/gstreamer-1.0"
mkdir -p "${APPDIR}/usr/lib/gio/modules"
for gst_dir in /usr/lib/x86_64-linux-gnu/gstreamer-1.0 /usr/lib64/gstreamer-1.0 /usr/lib/gstreamer-1.0; do
    if [ -d "$gst_dir" ]; then
        for plugin in \
            libgstcoreelements.so libgstplayback.so libgsttypefindfunctions.so \
            libgstautodetect.so libgstgio.so libgstvideoconvertscale.so \
            libgstvideoconvert.so libgstvideoscale.so libgstvideofilter.so \
            libgstaudioconvert.so libgstaudioresample.so libgstaudiofx.so \
            libgstvolume.so libgstaudiorate.so libgstvideorate.so \
            libgstapp.so libgstgtk4.so libgstopengl.so \
            libgstpulse.so libgstpulseaudio.so libgstpipewire.so libgstalsa.so \
            libgstisomp4.so libgstmatroska.so libgstavi.so libgstflv.so \
            libgstogg.so libgstwavparse.so libgstaudioparsers.so \
            libgstvideoparsersbad.so libgstlibav.so libgstvpx.so \
            libgstvorbis.so libgstopus.so libgstopusparse.so libgsttheora.so \
            libgstmpg123.so libgsttaglib.so libgstid3demux.so libgstsubparse.so \
            libgstwaylandsink.so libgstximagesink.so libgstxvimagesink.so \
            libgstsoundtouch.so libgstscaletempo.so; do
            if [ -f "$gst_dir/$plugin" ]; then
                cp -nL "$gst_dir/$plugin" "${APPDIR}/usr/lib/gstreamer-1.0/" 2>/dev/null || true
            fi
        done
        break
    fi
done

# Bundle gst-plugin-scanner helper binary for fallback mode
for scanner in /usr/lib/x86_64-linux-gnu/gstreamer1.0/gstreamer-1.0/gst-plugin-scanner \
               /usr/lib/gstreamer-1.0/gst-plugin-scanner \
               /usr/libexec/gstreamer-1.0/gst-plugin-scanner; do
    if [ -x "$scanner" ]; then
        mkdir -p "${APPDIR}/usr/libexec/gstreamer-1.0"
        cp -L "$scanner" "${APPDIR}/usr/libexec/gstreamer-1.0/gst-plugin-scanner.real" 2>/dev/null || true
        chmod +x "${APPDIR}/usr/libexec/gstreamer-1.0/gst-plugin-scanner.real" 2>/dev/null || true
        cat << 'EOF' > "${APPDIR}/usr/libexec/gstreamer-1.0/gst-plugin-scanner"
#!/bin/sh
APPDIR="$(dirname "$(dirname "$(dirname "$(readlink -f "$0")")")")"
LIB_PATH="${APPDIR}/glibc:${APPDIR}/usr/lib:${APPDIR}/usr/lib/x86_64-linux-gnu:/usr/lib/x86_64-linux-gnu:/lib/x86_64-linux-gnu:/usr/lib64:/lib64"
if [ -f "${APPDIR}/glibc/ld-linux-x86-64.so.2" ]; then
    exec "${APPDIR}/glibc/ld-linux-x86-64.so.2" --library-path "$LIB_PATH" "${APPDIR}/usr/libexec/gstreamer-1.0/gst-plugin-scanner.real" "$@"
else
    exec "${APPDIR}/usr/libexec/gstreamer-1.0/gst-plugin-scanner.real" "$@"
fi
EOF
        chmod 755 "${APPDIR}/usr/libexec/gstreamer-1.0/gst-plugin-scanner"
        # Provide compatibility links in common GStreamer plugin directories
        mkdir -p "${APPDIR}/usr/lib/gstreamer-1.0" "${APPDIR}/usr/lib/x86_64-linux-gnu/gstreamer1.0/gstreamer-1.0"
        ln -sf "../../../libexec/gstreamer-1.0/gst-plugin-scanner" "${APPDIR}/usr/lib/x86_64-linux-gnu/gstreamer1.0/gstreamer-1.0/gst-plugin-scanner" 2>/dev/null || true
        ln -sf "../../libexec/gstreamer-1.0/gst-plugin-scanner" "${APPDIR}/usr/lib/gstreamer-1.0/gst-plugin-scanner" 2>/dev/null || true
        break
    fi
done

# Recursively resolve dependencies of all bundled .so files, excluding host GPU/driver libraries
DEPS_FILE="$(mktemp)"
ORIG_LD_LIBRARY_PATH="${LD_LIBRARY_PATH:-}"
export LD_LIBRARY_PATH="${APPDIR}/usr/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
while true; do
    NEW_COPIED=0
    find "${APPDIR}/usr/lib" "${APPDIR}/usr/bin" "${APPDIR}/usr/libexec" -maxdepth 5 -type f \( -name '*.so*' -o -name 'python3*' -o -name '*.real*' \) -print0 | xargs -0 -n1 ldd 2>/dev/null | awk '/=> \//{print $3}' | sort -u > "$DEPS_FILE"
    while read -r lib; do
        [ -z "$lib" ] && continue
        case "$lib" in "${APPDIR}"/*) continue ;; esac
        lib_name="$(basename "$lib")"
        case "$lib_name" in
            ld-linux*|libc.so*|libm.so*|libdl.so*|libpthread.so*|librt.so*|libresolv.so*|libutil.so*) continue ;;
            libnss_*|libgcc_s.so*) continue ;;
        esac
        if [ ! -e "${APPDIR}/usr/lib/${lib_name}" ]; then
            cp -nL "$lib" "${APPDIR}/usr/lib/" 2>/dev/null || true
            NEW_COPIED=$((NEW_COPIED + 1))
        fi
    done < "$DEPS_FILE"
    if [ "$NEW_COPIED" -eq 0 ]; then
        break
    fi
done
rm -f "$DEPS_FILE"
if [ -n "$ORIG_LD_LIBRARY_PATH" ]; then
    export LD_LIBRARY_PATH="$ORIG_LD_LIBRARY_PATH"
else
    unset LD_LIBRARY_PATH
fi

# Provide x86_64-linux-gnu directory compatibility symlinks
for f in "${APPDIR}/usr/lib/"*.so*; do
    if [ -f "$f" ]; then
        ln -sf "../$(basename "$f")" "${APPDIR}/usr/lib/x86_64-linux-gnu/$(basename "$f")" 2>/dev/null || true
    fi
done

# Bundle Noto Color Emoji font for self-contained emoji rendering on minimal distros
echo "==> Bundling emoji fonts..."
mkdir -p "${APPDIR}/usr/share/fonts/truetype/noto"
for font_candidate in \
    /usr/share/fonts/truetype/noto/NotoColorEmoji.ttf \
    /usr/share/fonts/google-noto-color-emoji-fonts/NotoColorEmoji.ttf \
    /usr/share/fonts/noto-color-emoji/NotoColorEmoji.ttf; do
    if [ -f "$font_candidate" ]; then
        cp -f "$font_candidate" "${APPDIR}/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf"
        chmod 644 "${APPDIR}/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf"
        echo "    Successfully bundled NotoColorEmoji.ttf (${font_candidate})"
        break
    fi
done
if [ ! -f "${APPDIR}/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf" ]; then
    echo "==> Downloading NotoColorEmoji.ttf for AppDir..."
    if curl -fsSL --connect-timeout 15 --max-time 60 -o "${APPDIR}/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf" \
        "https://raw.githubusercontent.com/googlefonts/noto-emoji/main/fonts/NotoColorEmoji.ttf" 2>/dev/null; then
        chmod 644 "${APPDIR}/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf"
        echo "    Successfully downloaded NotoColorEmoji.ttf"
    fi
fi

# Bundle and compile GSettings schemas for GTK4 and Libadwaita
echo "==> Bundling GSettings schemas..."
mkdir -p "${APPDIR}/usr/share/glib-2.0/schemas"
for schema in /usr/share/glib-2.0/schemas/*gtk4* /usr/share/glib-2.0/schemas/*adw*; do
    if [ -f "$schema" ]; then
        cp -f "$schema" "${APPDIR}/usr/share/glib-2.0/schemas/"
    fi
done
if command -v glib-compile-schemas >/dev/null 2>&1; then
    glib-compile-schemas "${APPDIR}/usr/share/glib-2.0/schemas" 2>/dev/null || true
fi

# Clean up pycache in staging
find "${APPDIR}" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
find "${APPDIR}" -type f -name "*.pyc" -delete 2>/dev/null || true

# 3. Create Binary Executable Wrapper
cat << 'EOF' > "${APPDIR}/usr/bin/video-cut"
#!/usr/bin/env python3
import os
import sys

here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Pure-python application package directory
appdir_dist = os.path.join(here, "lib", "python3", "dist-packages")
if os.path.isdir(appdir_dist) and appdir_dist not in sys.path:
    sys.path.insert(0, appdir_dist)

# Version-specific bundled C-extension directory (matching the running Python)
py_ver = f"python{sys.version_info.major}.{sys.version_info.minor}"
ver_dist = os.path.join(here, "lib", py_ver, "dist-packages")
if os.path.isdir(ver_dist) and ver_dist not in sys.path:
    sys.path.insert(0, ver_dist)

for dist_pkg in ("/usr/lib/python3/dist-packages", "/usr/local/lib/python3/dist-packages"):
    if os.path.isdir(dist_pkg) and dist_pkg not in sys.path:
        sys.path.append(dist_pkg)

from video_cut.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
EOF
chmod 755 "${APPDIR}/usr/bin/video-cut"

# Bundle ffmpeg and ffprobe into AppDir
echo "==> Bundling ffmpeg and ffprobe into AppDir..."
FFMPEG_BUNDLED=0
# Check if cached in TOOLS_DIR
if [ -x "${TOOLS_DIR}/ffmpeg" ] && [ -x "${TOOLS_DIR}/ffprobe" ]; then
    echo "    Using cached static ffmpeg and ffprobe from ${TOOLS_DIR}..."
    cp -f "${TOOLS_DIR}/ffmpeg" "${APPDIR}/usr/bin/ffmpeg"
    cp -f "${TOOLS_DIR}/ffprobe" "${APPDIR}/usr/bin/ffprobe"
    chmod +x "${APPDIR}/usr/bin/ffmpeg" "${APPDIR}/usr/bin/ffprobe"
    FFMPEG_BUNDLED=1
fi

# If not cached, attempt to download static ffmpeg release from GitHub Releases (eugeneware/ffmpeg-static)
if [ "$FFMPEG_BUNDLED" -eq 0 ]; then
    echo "==> Downloading static ffmpeg release from GitHub (eugeneware/ffmpeg-static)..."
    FFMPEG_TMP="$(mktemp -d)"
    GITHUB_FFMPEG_URL="https://github.com/eugeneware/ffmpeg-static/releases/download/b6.1.1/ffmpeg-linux-x64.gz"
    GITHUB_FFPROBE_URL="https://github.com/eugeneware/ffmpeg-static/releases/download/b6.1.1/ffprobe-linux-x64.gz"
    if curl -fsSL --connect-timeout 15 --max-time 180 "$GITHUB_FFMPEG_URL" | gzip -d > "${FFMPEG_TMP}/ffmpeg" 2>/dev/null && \
       curl -fsSL --connect-timeout 15 --max-time 180 "$GITHUB_FFPROBE_URL" | gzip -d > "${FFMPEG_TMP}/ffprobe" 2>/dev/null; then
        if [ -s "${FFMPEG_TMP}/ffmpeg" ] && [ -s "${FFMPEG_TMP}/ffprobe" ]; then
            chmod +x "${FFMPEG_TMP}/ffmpeg" "${FFMPEG_TMP}/ffprobe"
            cp -f "${FFMPEG_TMP}/ffmpeg" "${TOOLS_DIR}/ffmpeg"
            cp -f "${FFMPEG_TMP}/ffprobe" "${TOOLS_DIR}/ffprobe"
            cp -f "${FFMPEG_TMP}/ffmpeg" "${APPDIR}/usr/bin/ffmpeg"
            cp -f "${FFMPEG_TMP}/ffprobe" "${APPDIR}/usr/bin/ffprobe"
            FFMPEG_BUNDLED=1
            echo "    Successfully downloaded and bundled static ffmpeg and ffprobe from GitHub."
        fi
    fi
    rm -rf "${FFMPEG_TMP}"
fi

# Secondary fallback: John Van Sickle static tarball
if [ "$FFMPEG_BUNDLED" -eq 0 ]; then
    echo "==> Downloading static ffmpeg release from johnvansickle.com..."
    FFMPEG_STATIC_URL="https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz"
    FFMPEG_TMP="$(mktemp -d)"
    if curl -fsSL --connect-timeout 15 --max-time 120 -o "${FFMPEG_TMP}/ffmpeg.tar.xz" "${FFMPEG_STATIC_URL}" 2>/dev/null; then
        if tar -xf "${FFMPEG_TMP}/ffmpeg.tar.xz" -C "${FFMPEG_TMP}" 2>/dev/null; then
            STATIC_BIN_DIR="$(find "${FFMPEG_TMP}" -type d -name "ffmpeg-*-static" 2>/dev/null | head -n 1)"
            if [ -n "$STATIC_BIN_DIR" ] && [ -x "${STATIC_BIN_DIR}/ffmpeg" ] && [ -x "${STATIC_BIN_DIR}/ffprobe" ]; then
                cp -f "${STATIC_BIN_DIR}/ffmpeg" "${TOOLS_DIR}/ffmpeg"
                cp -f "${STATIC_BIN_DIR}/ffprobe" "${TOOLS_DIR}/ffprobe"
                cp -f "${STATIC_BIN_DIR}/ffmpeg" "${APPDIR}/usr/bin/ffmpeg"
                cp -f "${STATIC_BIN_DIR}/ffprobe" "${APPDIR}/usr/bin/ffprobe"
                chmod +x "${APPDIR}/usr/bin/ffmpeg" "${APPDIR}/usr/bin/ffprobe"
                FFMPEG_BUNDLED=1
                echo "    Successfully downloaded and bundled static ffmpeg and ffprobe from johnvansickle.com."
            fi
        fi
    fi
    rm -rf "${FFMPEG_TMP}"
fi

# Fallback: Copy host ffmpeg and ffprobe binaries (do NOT copy desktop/graphics libraries which break GTK/GdkPixbuf)
if [ "$FFMPEG_BUNDLED" -eq 0 ]; then
    if command -v ffmpeg >/dev/null 2>&1 && command -v ffprobe >/dev/null 2>&1; then
        echo "==> Bundling system ffmpeg and ffprobe as fallback..."
        HOST_FFMPEG="$(command -v ffmpeg)"
        HOST_FFPROBE="$(command -v ffprobe)"
        cp -f "$HOST_FFMPEG" "${APPDIR}/usr/bin/ffmpeg"
        cp -f "$HOST_FFPROBE" "${APPDIR}/usr/bin/ffprobe"
        chmod +x "${APPDIR}/usr/bin/ffmpeg" "${APPDIR}/usr/bin/ffprobe"
        FFMPEG_BUNDLED=1
    fi
fi

if [ "$FFMPEG_BUNDLED" -eq 0 ] || [ ! -x "${APPDIR}/usr/bin/ffmpeg" ] || [ ! -x "${APPDIR}/usr/bin/ffprobe" ]; then
    echo "Error: ffmpeg and ffprobe binaries could not be bundled into AppDir!" >&2
    echo "Please ensure internet connectivity or install ffmpeg and ffprobe on the build system." >&2
    exit 1
fi

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
