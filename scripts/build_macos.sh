#!/bin/bash
set -euo pipefail

# Video Cut - macOS Application Bundle (.app) and Disk Image (.dmg) Builder
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIST_DIR="${REPO_ROOT}/dist"
PACKAGING_DIR="${REPO_ROOT}/packaging/macos"
BUILD_DIR="${REPO_ROOT}/build/macos"

# Extract version from src/video_cut/__init__.py or environment/git tag
if [ -z "${VERSION:-}" ]; then
    VERSION="$(grep -m1 '^__version__' "${REPO_ROOT}/src/video_cut/__init__.py" | cut -d'"' -f2)"
fi
if [ -n "${GITHUB_REF_NAME:-}" ] && [[ "${GITHUB_REF_NAME}" =~ ^v[0-9] ]]; then
    VERSION="${GITHUB_REF_NAME#v}"
elif [ "${VERSION:-}" = "devel" ] || [ -z "${VERSION:-}" ]; then
    GIT_TAG="$(git -C "${REPO_ROOT}" describe --tags --exact-match 2>/dev/null || true)"
    if [[ "${GIT_TAG}" =~ ^v[0-9] ]]; then
        VERSION="${GIT_TAG#v}"
    fi
fi
VERSION="${VERSION:-0.6.1}"
export VERSION

show_help() {
    cat << EOF
Video Cut macOS Packaging Tool (v${VERSION})

Usage:
  ./scripts/build_macos.sh [options]

Options:
  --dmg             Build macOS Disk Image (.dmg) containing VideoCut.app (default)
  --app-only        Build only VideoCut.app bundle
  --validate-only   Validate packaging configurations, scripts, and spec files
  --clean           Clean macOS build and dist artifacts
  -h, --help        Show this help message
EOF
}

DO_DMG=1
DO_VALIDATE=0
DO_CLEAN=0

while [ $# -gt 0 ]; do
    case "$1" in
        --dmg)
            DO_DMG=1
            ;;
        --app-only)
            DO_DMG=0
            ;;
        --validate-only)
            DO_VALIDATE=1
            ;;
        --clean)
            DO_CLEAN=1
            ;;
        -h|--help)
            show_help
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            show_help
            exit 1
            ;;
    esac
    shift
done

if [ "$DO_CLEAN" -eq 1 ]; then
    echo "==> Cleaning macOS build artifacts..."
    rm -rf "${BUILD_DIR}" "${DIST_DIR}/VideoCut.app" "${DIST_DIR}"/*.dmg "${DIST_DIR}"/*.dmg.sha256
    echo "==> Clean complete."
    if [ "$DO_VALIDATE" -eq 0 ] && [ "$DO_DMG" -eq 0 ]; then
        exit 0
    fi
fi

validate_packaging() {
    echo "==> Validating macOS packaging files and specifications..."
    
    # 1. Validate Python spec and runtime hook syntax
    python3 -m py_compile "${PACKAGING_DIR}/rthook_macos.py"
    python3 -m py_compile "${PACKAGING_DIR}/videocut.spec"
    echo "    [OK] Python syntax valid: rthook_macos.py & videocut.spec"

    # 2. Validate Entitlements Plist XML
    if command -v plutil 1>/dev/null 2>&1; then
        plutil -lint "${PACKAGING_DIR}/entitlements.plist"
    else
        python3 -c "import xml.etree.ElementTree as ET; ET.parse('${PACKAGING_DIR}/entitlements.plist')"
    fi
    echo "    [OK] XML valid: entitlements.plist"

    # 3. Validate Icon (.icns)
    if [ ! -f "${PACKAGING_DIR}/videocut.icns" ] || [ ! -s "${PACKAGING_DIR}/videocut.icns" ]; then
        echo "Error: ${PACKAGING_DIR}/videocut.icns is missing or empty." >&2
        exit 1
    fi
    echo "    [OK] Icon asset present: videocut.icns ($(du -h "${PACKAGING_DIR}/videocut.icns" | cut -f1))"
    echo "==> macOS packaging configuration validation passed successfully!"
}

if [ "$DO_VALIDATE" -eq 1 ]; then
    validate_packaging
    exit 0
fi

# If running on non-macOS host without Darwin kernel:
if [ "$(uname -s)" != "Darwin" ]; then
    echo "Notice: macOS application bundles (.app and .dmg) must be compiled on a macOS system (e.g. GitHub Actions macos-latest runner)."
    echo "Running configuration validation instead..."
    validate_packaging
    exit 0
fi

# Ensure PyInstaller is installed
if ! command -v pyinstaller 1>/dev/null 2>&1; then
    echo "Error: pyinstaller is not installed. Run 'pip install pyinstaller' first." >&2
    exit 1
fi

mkdir -p "${DIST_DIR}" "${BUILD_DIR}"
ARCH="$(uname -m)"

echo "================================================================="
echo " Building Video Cut v${VERSION} for macOS (${ARCH})"
echo "================================================================="

# 1. Compile GSettings schemas if Homebrew glib-2.0 schemas exist
HOMEBREW_PREFIX="${HOMEBREW_PREFIX:-$(brew --prefix 2>/dev/null || echo "/opt/homebrew")}"
if [ -d "${HOMEBREW_PREFIX}/share/glib-2.0/schemas" ] && command -v glib-compile-schemas 1>/dev/null 2>&1; then
    echo "==> Ensuring GSettings schemas are compiled..."
    glib-compile-schemas "${HOMEBREW_PREFIX}/share/glib-2.0/schemas" || true
fi

# 2. Run PyInstaller
echo "==> Compiling VideoCut.app with PyInstaller..."
pyinstaller \
    --noconfirm \
    --clean \
    --distpath "${DIST_DIR}" \
    --workpath "${BUILD_DIR}" \
    "${PACKAGING_DIR}/videocut.spec"

APP_PATH="${DIST_DIR}/VideoCut.app"
if [ ! -d "${APP_PATH}" ]; then
    echo "Error: Expected bundle not found at ${APP_PATH}" >&2
    exit 1
fi

# 3. Ensure executable permissions and configure library rpaths
echo "==> Setting executable permissions..."
chmod +x "${APP_PATH}/Contents/MacOS/VideoCut"
find "${APP_PATH}/Contents" -type f \( -name "*.dylib" -o -name "*.so" -o -perm +111 \) -exec chmod +x {} + 2>/dev/null || true

if command -v install_name_tool 1>/dev/null 2>&1; then
    echo "==> Configuring rpaths on VideoCut executable..."
    install_name_tool -add_rpath "/opt/homebrew/lib" "${APP_PATH}/Contents/MacOS/VideoCut" 2>/dev/null || true
    install_name_tool -add_rpath "/usr/local/lib" "${APP_PATH}/Contents/MacOS/VideoCut" 2>/dev/null || true
    install_name_tool -add_rpath "@executable_path/../Frameworks" "${APP_PATH}/Contents/MacOS/VideoCut" 2>/dev/null || true
fi

# 4. Ad-hoc codesign for local execution and Apple Silicon gatekeeper compliance
if command -v codesign 1>/dev/null 2>&1; then
    echo "==> Applying ad-hoc code signature..."
    codesign --force --deep --sign - "${APP_PATH}" || echo "Warning: codesign failed, continuing."
fi

echo "==> VideoCut.app bundle created successfully: ${APP_PATH}"

# 5. Build Disk Image (.dmg)
if [ "$DO_DMG" -eq 1 ]; then
    DMG_NAME="VideoCut-${VERSION}-macOS-${ARCH}.dmg"
    DMG_PATH="${DIST_DIR}/${DMG_NAME}"
    DMG_STAGING="${BUILD_DIR}/dmg_staging"

    echo ""
    echo "==> Creating macOS Disk Image: ${DMG_PATH}..."
    rm -rf "${DMG_STAGING}" "${DMG_PATH}"
    mkdir -p "${DMG_STAGING}"

    cp -R "${APP_PATH}" "${DMG_STAGING}/"
    ln -s /Applications "${DMG_STAGING}/Applications"

    if command -v create-dmg 1>/dev/null 2>&1; then
        echo "==> Using create-dmg tool..."
        create-dmg \
            --volname "Video Cut" \
            --window-pos 200 120 \
            --window-size 600 400 \
            --icon-size 100 \
            --icon "VideoCut.app" 160 180 \
            --hide-extension "VideoCut.app" \
            --app-drop-link 440 180 \
            "${DMG_PATH}" \
            "${DMG_STAGING}" || {
                echo "create-dmg had a non-fatal warning, ensuring dmg exists..."
                [ -f "${DMG_PATH}" ] || hdiutil create -volname "Video Cut" -srcfolder "${DMG_STAGING}" -ov -format UDZO "${DMG_PATH}"
            }
    else
        echo "==> Creating DMG using native hdiutil..."
        hdiutil create -volname "Video Cut" -srcfolder "${DMG_STAGING}" -ov -format UDZO "${DMG_PATH}"
    fi

    # Generate SHA-256
    if command -v shasum 1>/dev/null 2>&1; then
        (cd "${DIST_DIR}" && shasum -a 256 "${DMG_NAME}" > "${DMG_NAME}.sha256")
    elif command -v sha256sum 1>/dev/null 2>&1; then
        (cd "${DIST_DIR}" && sha256sum "${DMG_NAME}" > "${DMG_NAME}.sha256")
    fi

    echo "==> Successfully created macOS Disk Image:"
    echo "    ${DMG_PATH} ($(du -h "${DMG_PATH}" | cut -f1))"
fi

echo "================================================================="
echo " macOS Build Finished Successfully"
echo "================================================================="
