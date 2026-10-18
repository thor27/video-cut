#!/bin/bash
set -euo pipefail

# Video Cut - Master Package Builder
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIST_DIR="${REPO_ROOT}/dist"
SCRIPTS_DIR="${REPO_ROOT}/scripts"

# Extract version from pyproject.toml
VERSION="$(grep -m1 '^version' "${REPO_ROOT}/pyproject.toml" | cut -d'"' -f2)"
if [ -z "$VERSION" ]; then
    VERSION="0.1.0"
fi

show_help() {
    cat << EOF
Video Cut Packaging Tool (v${VERSION})

Usage:
  ./scripts/build_packages.sh [options]

Options:
  --all         Build all distribution packages (DEB, AppImage, Snap)
  --deb         Build only Debian (.deb) package
  --appimage    Build only AppImage (.AppImage) package
  --snap        Build only Snap (.snap) package
  --clean       Clean build/ and dist/ directories
  -h, --help    Show this help message

If no option is provided, DEB and AppImage packages will be built by default,
and Snapcraft configuration will be validated.
EOF
}

DO_DEB=0
DO_APPIMAGE=0
DO_SNAP=0
DO_CLEAN=0

if [ $# -eq 0 ]; then
    DO_DEB=1
    DO_APPIMAGE=1
    DO_SNAP=1
else
    while [ $# -gt 0 ]; do
        case "$1" in
            --all)
                DO_DEB=1
                DO_APPIMAGE=1
                DO_SNAP=1
                ;;
            --deb)
                DO_DEB=1
                ;;
            --appimage)
                DO_APPIMAGE=1
                ;;
            --snap)
                DO_SNAP=1
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
fi

if [ "$DO_CLEAN" -eq 1 ]; then
    echo "==> Cleaning build and dist directories..."
    rm -rf "${REPO_ROOT}/build" "${REPO_ROOT}/dist"
    echo "==> Clean complete."
    if [ "$DO_DEB" -eq 0 ] && [ "$DO_APPIMAGE" -eq 0 ] && [ "$DO_SNAP" -eq 0 ]; then
        exit 0
    fi
fi

mkdir -p "${DIST_DIR}"

echo "================================================================="
echo " Building Video Cut v${VERSION} Packages"
echo "================================================================="

# 1. Debian Package
if [ "$DO_DEB" -eq 1 ]; then
    echo ""
    "${SCRIPTS_DIR}/build_deb.sh"
fi

# 2. AppImage Package
if [ "$DO_APPIMAGE" -eq 1 ]; then
    echo ""
    "${SCRIPTS_DIR}/build_appimage.sh"
fi

# 3. Snap Package
if [ "$DO_SNAP" -eq 1 ]; then
    echo ""
    "${SCRIPTS_DIR}/build_snap.sh"
fi

echo ""
echo "================================================================="
echo " Packaging Summary"
echo "================================================================="
echo "Output Directory: ${DIST_DIR}"
echo ""

if ls "${DIST_DIR}"/* 1>/dev/null 2>&1; then
    printf "%-38s %-10s %-20s\n" "Package File" "Size" "SHA-256 (first 12)"
    printf "%-38s %-10s %-20s\n" "--------------------------------------" "----------" "--------------------"
    for f in "${DIST_DIR}"/*; do
        if [ -f "$f" ]; then
            fname="$(basename "$f")"
            fsize="$(du -h "$f" | cut -f1)"
            fhash="$(sha256sum "$f" | cut -c1-12)"
            printf "%-38s %-10s %-20s\n" "$fname" "$fsize" "$fhash"
        fi
    done
else
    echo "No packages were generated."
fi
echo "================================================================="
