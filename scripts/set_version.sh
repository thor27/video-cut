#!/bin/bash
set -euo pipefail

# Video Cut - Version Management Script
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INIT_FILE="${REPO_ROOT}/src/video_cut/__init__.py"

# Function to get current version
get_current_version() {
    grep -m1 '^__version__' "${INIT_FILE}" | cut -d'"' -f2
}

CURRENT_VERSION="$(get_current_version)"

show_help() {
    cat << EOF
Video Cut Version Manager

Usage:
  ./scripts/set_version.sh [OPTIONS] [NEW_VERSION]

Options:
  --show, -s        Show current version
  --help, -h        Show this help message

Examples:
  ./scripts/set_version.sh --show
  ./scripts/set_version.sh 0.2.0
  ./scripts/set_version.sh 1.0.0-rc1

Note:
  Video Cut uses a single source of truth for versioning:
  'src/video_cut/__init__.py' (__version__).
  All packaging scripts (DEB, AppImage, Snap) and pyproject.toml
  dynamically adopt this version.
EOF
}

# No arguments or --show
if [ $# -eq 0 ] || [ "${1:-}" = "--show" ] || [ "${1:-}" = "-s" ]; then
    echo "Video Cut version: ${CURRENT_VERSION}"
    exit 0
fi

if [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then
    show_help
    exit 0
fi

NEW_VERSION="$1"

# Basic validation: ensure version is reasonable (digits, dots, optional prerelease tag)
if ! [[ "$NEW_VERSION" =~ ^[0-9]+\.[0-9]+(\.[0-9]+)?(-[a-zA-Z0-9.]+)?$ ]]; then
    echo "ERROR: Invalid version format '${NEW_VERSION}'. Expected format like 0.2.0 or 1.0.0-rc1" >&2
    exit 1
fi

if [ "$NEW_VERSION" = "$CURRENT_VERSION" ]; then
    echo "Version is already set to ${CURRENT_VERSION}."
    exit 0
fi

echo "==> Updating Video Cut version: ${CURRENT_VERSION} -> ${NEW_VERSION}"

# Update src/video_cut/__init__.py
sed -i "s/__version__ = \"${CURRENT_VERSION}\"/__version__ = \"${NEW_VERSION}\"/" "${INIT_FILE}"

# Update io.github.thor27.video-cut.metainfo.xml if present
METAINFO="${REPO_ROOT}/io.github.thor27.video-cut.metainfo.xml"
if [ -f "${METAINFO}" ]; then
    TODAY="$(date +%Y-%m-%d)"
    if grep -q "version=\"${NEW_VERSION}\"" "${METAINFO}"; then
        sed -i "s/version=\"${NEW_VERSION}\" date=\"[^\"]*\"/version=\"${NEW_VERSION}\" date=\"${TODAY}\"/" "${METAINFO}"
    else
        sed -i "/<releases>/a \    <release version=\"${NEW_VERSION}\" date=\"${TODAY}\">\n      <url type=\"details\">https://github.com/thor27/video-cut/releases/tag/v${NEW_VERSION}</url>\n      <description>\n        <p>Release v${NEW_VERSION}</p>\n      </description>\n    </release>" "${METAINFO}"
    fi
    cp "${METAINFO}" "${REPO_ROOT}/src/video_cut/resources/" 2>/dev/null || true
fi

# Verify update
UPDATED_VERSION="$(get_current_version)"
if [ "$UPDATED_VERSION" != "$NEW_VERSION" ]; then
    echo "ERROR: Failed to update version in ${INIT_FILE}" >&2
    exit 1
fi

# Refresh local venv editable installation if present
if [ -f "${REPO_ROOT}/.venv/bin/pip" ]; then
    echo "==> Updating local editable environment metadata..."
    "${REPO_ROOT}/.venv/bin/pip" install -e "${REPO_ROOT}" --no-deps -q
fi

echo "==> Successfully updated version to ${NEW_VERSION}!"
echo "    Source of truth: src/video_cut/__init__.py"
echo ""
echo "Next steps:"
echo "  1. Review changes:   git diff"
echo "  2. Run test suite:   pytest"
echo "  3. Build packages:   ./scripts/build_packages.sh"
echo "  4. Commit & tag:     git commit -am 'release: v${NEW_VERSION}' && git tag v${NEW_VERSION}"
