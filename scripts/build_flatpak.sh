#!/bin/bash
set -euo pipefail

# Video Cut - Flatpak Package Builder
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIST_DIR="${REPO_ROOT}/dist"
BUILD_DIR="${REPO_ROOT}/build/flatpak"
REPO_DIR="${REPO_ROOT}/build/flatpak-repo"
STATE_DIR="${REPO_ROOT}/build/flatpak-state"
MANIFEST="${REPO_ROOT}/io.github.thor27.video-cut.yaml"
METAINFO="${REPO_ROOT}/io.github.thor27.video-cut.metainfo.xml"
DESKTOP_FILE="${REPO_ROOT}/src/video_cut/resources/io.github.thor27.video-cut.desktop"

# Extract version from src/video_cut/__init__.py
VERSION="$(grep -m1 '^__version__' "${REPO_ROOT}/src/video_cut/__init__.py" | cut -d'"' -f2)"
if [ -z "$VERSION" ]; then
    VERSION="0.1.0"
fi

APP_ID="io.github.thor27.video-cut"
OUTPUT_BUNDLE="${DIST_DIR}/VideoCut-${VERSION}.flatpak"

VALIDATE_ONLY=0
INSTALL_LOCAL=0
CLEAN_BUILD=0

show_help() {
    cat << 'HELP'
Video Cut Flatpak Builder

Usage:
  ./scripts/build_flatpak.sh [options]

Options:
  --validate-only   Validate Flatpak manifest, desktop file, and AppStream metainfo
  --install-local   Build and install directly into user's Flatpak environment
  --clean           Clean previous Flatpak build directories before building
  -h, --help        Show this help message
HELP
}

while [ $# -gt 0 ]; do
    case "$1" in
        --validate-only)
            VALIDATE_ONLY=1
            ;;
        --install-local)
            INSTALL_LOCAL=1
            ;;
        --clean)
            CLEAN_BUILD=1
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

echo "================================================================="
echo " Validating Flatpak Configuration for Video Cut v${VERSION}"
echo "================================================================="

# 1. Validate AppStream MetaInfo
if command -v appstreamcli >/dev/null 2>&1; then
    echo "==> Validating AppStream metadata (${METAINFO})..."
    appstreamcli validate --explain "${METAINFO}"
else
    echo "==> Notice: 'appstreamcli' not found, skipping AppStream validation."
fi

# 2. Validate Desktop Entry
if command -v desktop-file-validate >/dev/null 2>&1; then
    echo "==> Validating desktop entry (${DESKTOP_FILE})..."
    desktop-file-validate "${DESKTOP_FILE}"
else
    echo "==> Notice: 'desktop-file-validate' not found, skipping desktop entry validation."
fi

# 3. Validate Manifest Syntax
if [ ! -f "${MANIFEST}" ]; then
    echo "ERROR: Flatpak manifest not found at ${MANIFEST}!" >&2
    exit 1
fi

if [ -f "${REPO_ROOT}/.venv/bin/python" ]; then
    "${REPO_ROOT}/.venv/bin/python" -c "
import yaml
with open('${MANIFEST}', 'r') as f:
    data = yaml.safe_load(f)
assert data['app-id'] == '${APP_ID}'
assert data['runtime'] == 'org.gnome.Platform'
print('==> Flatpak manifest syntax and structure: OK')
"
fi

if [ "$VALIDATE_ONLY" -eq 1 ]; then
    echo "================================================================="
    echo "✔ Validation complete: All Flatpak configurations are valid!"
    echo "================================================================="
    exit 0
fi

# 4. Check for builder tool: flatpak-builder or org.flatpak.Builder
BUILDER_CMD=""
if command -v flatpak-builder >/dev/null 2>&1; then
    BUILDER_CMD="flatpak-builder"
elif flatpak info org.flatpak.Builder >/dev/null 2>&1; then
    BUILDER_CMD="flatpak run org.flatpak.Builder --disable-rofiles-fuse"
fi

if [ -z "$BUILDER_CMD" ]; then
    echo "================================================================="
    echo "Notice: 'flatpak-builder' is not currently installed."
    echo "To build Flatpaks locally, you can install flatpak-builder via:"
    echo "    sudo apt install flatpak-builder"
    echo "Or run in user-space via Flatpak:"
    echo "    flatpak install --user flathub org.flatpak.Builder"
    echo ""
    echo "Note for Flathub publication:"
    echo "Flathub builds apps on remote build infrastructure when you submit"
    echo "the manifest (io.github.thor27.video-cut.yaml). Local building is"
    echo "only required for local testing."
    echo "================================================================="
    exit 0
fi

# 5. Clean build directories if requested
if [ "$CLEAN_BUILD" -eq 1 ]; then
    echo "==> Cleaning Flatpak build directories..."
    rm -rf "${BUILD_DIR}" "${REPO_DIR}" "${STATE_DIR}"
fi

mkdir -p "${DIST_DIR}" "${BUILD_DIR}" "${REPO_DIR}" "${STATE_DIR}"

echo "================================================================="
echo " Building Flatpak for ${APP_ID} v${VERSION}..."
echo "================================================================="

EXTRA_ARGS=""
if [ "$INSTALL_LOCAL" -eq 1 ]; then
    EXTRA_ARGS="--user --install"
fi

# Run builder
$BUILDER_CMD \
    --force-clean \
    --repo="${REPO_DIR}" \
    --state-dir="${STATE_DIR}" \
    ${EXTRA_ARGS} \
    "${BUILD_DIR}" \
    "${MANIFEST}"

# Export single-file bundle
echo "==> Creating single-file Flatpak bundle: ${OUTPUT_BUNDLE}..."
flatpak build-bundle "${REPO_DIR}" "${OUTPUT_BUNDLE}" "${APP_ID}"

echo "================================================================="
echo "✔ Flatpak build complete!"
echo "  Bundle: ${OUTPUT_BUNDLE} ($(du -h "${OUTPUT_BUNDLE}" | cut -f1))"
echo "  To test run locally: flatpak install --user ${OUTPUT_BUNDLE}"
echo "================================================================="
