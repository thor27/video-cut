#!/bin/bash
set -euo pipefail

# Video Cut - Snap Package Builder
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIST_DIR="${REPO_ROOT}/dist"
SNAP_DIR="${REPO_ROOT}/snap"

# Extract version from src/video_cut/__init__.py or environment
if [ -z "${VERSION:-}" ]; then
    VERSION="$(grep -m1 '^__version__' "${REPO_ROOT}/src/video_cut/__init__.py" | cut -d'"' -f2)"
fi
if [ -z "$VERSION" ]; then
    VERSION="devel"
fi

mkdir -p "${DIST_DIR}"

VALIDATE_ONLY=0
CLEAN=0
for arg in "${@:-}"; do
    case "$arg" in
        --validate-only)
            VALIDATE_ONLY=1
            ;;
        --clean)
            CLEAN=1
            ;;
    esac
done

echo "==> Validating Snapcraft configuration for Video Cut v${VERSION}..."

if [ ! -f "${SNAP_DIR}/snapcraft.yaml" ]; then
    echo "ERROR: snap/snapcraft.yaml not found!" >&2
    exit 1
fi

# Basic YAML validation with python
python3 -c "
import yaml
with open('${SNAP_DIR}/snapcraft.yaml', 'r') as f:
    data = yaml.safe_load(f)
assert data['name'] == 'video-cut', 'Invalid name'
assert 'apps' in data and 'video-cut' in data['apps'], 'Missing video-cut app definition'
assert 'parts' in data and 'video-cut' in data['parts'], 'Missing video-cut part definition'
print('Snapcraft YAML syntax and structure: OK')
" 2>/dev/null || python3 -c "
# Fallback without pyyaml
with open('${SNAP_DIR}/snapcraft.yaml', 'r') as f:
    content = f.read()
assert 'name: video-cut' in content
assert 'apps:' in content
assert 'parts:' in content
print('Snapcraft YAML structure: OK')
"

if [ "$VALIDATE_ONLY" -eq 1 ]; then
    echo "==> Snapcraft configuration is valid."
    exit 0
fi

# Check for snapcraft binary
if ! command -v snapcraft >/dev/null 2>&1; then
    echo "================================================================="
    echo "Notice: 'snapcraft' is not installed on this system."
    echo "To build Snap packages, install snapcraft via:"
    echo "    sudo snap install snapcraft --classic"
    echo "Then rerun this script:"
    echo "    ./scripts/build_snap.sh"
    echo "================================================================="
    exit 0
fi

cd "${REPO_ROOT}"

if [ "$CLEAN" -eq 1 ]; then
    echo "==> Cleaning previous Snapcraft build environment..."
    if [ "${DESTRUCTIVE_MODE:-0}" -eq 1 ]; then
        snapcraft clean --destructive-mode
    else
        snapcraft clean
    fi
fi

echo "==> Building Snap package with snapcraft..."

# Run snapcraft (uses LXD / Multipass container or host if destructive mode configured)
if [ "${DESTRUCTIVE_MODE:-0}" -eq 1 ]; then
    snapcraft pack --destructive-mode
else
    snapcraft pack
fi

# Move generated snap to dist
for snap_file in "${REPO_ROOT}"/*.snap; do
    if [ -f "$snap_file" ]; then
        mv "$snap_file" "${DIST_DIR}/"
        echo "==> Successfully generated Snap package:"
        echo "    ${DIST_DIR}/$(basename "$snap_file")"
    fi
done
