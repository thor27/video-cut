#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

echo "==> Looking up version from main branch..."

# Try fetching latest origin/main if remote exists and is accessible
if git remote get-url origin >/dev/null 2>&1; then
    git fetch origin main 2>/dev/null || true
fi

TARGET_VERSION=""
if git rev-parse --verify origin/main >/dev/null 2>&1; then
    TARGET_VERSION="$(git show origin/main:src/video_cut/__init__.py 2>/dev/null | grep -oP '__version__\s*=\s*"\K[^"]+' || true)"
fi

if [ -z "${TARGET_VERSION}" ] && git rev-parse --verify main >/dev/null 2>&1; then
    TARGET_VERSION="$(git show main:src/video_cut/__init__.py 2>/dev/null | grep -oP '__version__\s*=\s*"\K[^"]+' || true)"
fi

# Fallback: allow manual argument override
if [ -n "${1:-}" ]; then
    TARGET_VERSION="$1"
fi

if [ -z "${TARGET_VERSION}" ]; then
    echo "ERROR: Could not retrieve version from main branch and no version was provided." >&2
    echo "Usage: ./update_version.sh [VERSION]" >&2
    exit 1
fi

echo "==> Target version from main: ${TARGET_VERSION}"

INDEX_FILE="${SCRIPT_DIR}/index.html"
if [ ! -f "${INDEX_FILE}" ]; then
    echo "ERROR: index.html not found at ${INDEX_FILE}" >&2
    exit 1
fi

# Extract current version from index.html data-version attribute
CURRENT_VERSION="$(grep -oP 'data-version="\K[^"]+' "${INDEX_FILE}" | head -n 1 || true)"
if [ -z "${CURRENT_VERSION}" ]; then
    CURRENT_VERSION="$(grep -oP 'VideoCut-\K[0-9]+\.[0-9]+\.[0-9]+' "${INDEX_FILE}" | head -n 1 || true)"
fi

echo "==> Current version in index.html: ${CURRENT_VERSION:-unknown}"

if [ "${CURRENT_VERSION}" = "${TARGET_VERSION}" ]; then
    echo "==> index.html is already up to date with version ${TARGET_VERSION}."
    exit 0
fi

echo "==> Updating index.html from ${CURRENT_VERSION} to ${TARGET_VERSION}..."

sed -i "s/VideoCut-${CURRENT_VERSION}-x86_64\.AppImage/VideoCut-${TARGET_VERSION}-x86_64.AppImage/g" "${INDEX_FILE}"
sed -i "s/VideoCut-${CURRENT_VERSION}\.flatpak/VideoCut-${TARGET_VERSION}.flatpak/g" "${INDEX_FILE}"
sed -i "s/\/download\/v${CURRENT_VERSION}\//\/download\/v${TARGET_VERSION}\//g" "${INDEX_FILE}"
sed -i "s/data-version=\"${CURRENT_VERSION}\"/data-version=\"${TARGET_VERSION}\"/g" "${INDEX_FILE}"
sed -i "s/\"softwareVersion\": \"${CURRENT_VERSION}\"/\"softwareVersion\": \"${TARGET_VERSION}\"/g" "${INDEX_FILE}"
sed -i "s/v${CURRENT_VERSION}/v${TARGET_VERSION}/g" "${INDEX_FILE}"

LLMS_FILE="${SCRIPT_DIR}/llms.txt"
if [ -f "${LLMS_FILE}" ]; then
    echo "==> Updating llms.txt to ${TARGET_VERSION}..."
    sed -i "s/VideoCut-${CURRENT_VERSION}-x86_64\.AppImage/VideoCut-${TARGET_VERSION}-x86_64.AppImage/g" "${LLMS_FILE}"
fi

SITEMAP_FILE="${SCRIPT_DIR}/sitemap.xml"
if [ -f "${SITEMAP_FILE}" ]; then
    TODAY="$(date +%Y-%m-%d)"
    echo "==> Updating sitemap.xml lastmod to ${TODAY}..."
    sed -i -E "s|<lastmod>[0-9]{4}-[0-9]{2}-[0-9]{2}</lastmod>|<lastmod>${TODAY}</lastmod>|g" "${SITEMAP_FILE}"
fi

echo "==> Successfully updated site files to v${TARGET_VERSION}!"
echo ""
echo "Next steps:"
echo "  1. Review changes: git diff"
echo "  2. Commit:        git commit -am 'chore: update site to v${TARGET_VERSION}'"
echo "  3. Deploy:        git push origin gh-pages"
