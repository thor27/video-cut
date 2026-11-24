# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller specification for Video Cut on macOS (GTK 4 + Libadwaita + GStreamer)."""

import os
import subprocess
import sys
from pathlib import Path

block_cipher = None

# Base directories
SPEC_DIR = Path(SPECPATH).resolve()
REPO_ROOT = SPEC_DIR.parent.parent
SRC_DIR = REPO_ROOT / "src"

# Extract version from src/video_cut/__init__.py or environment
version = os.environ.get("VERSION")
if not version:
    init_py = SRC_DIR / "video_cut" / "__init__.py"
    if init_py.exists():
        for line in init_py.read_text(encoding="utf-8").splitlines():
            if line.startswith("__version__"):
                version = line.split('"')[1]
                break
if not version or version == "devel":
    version = "0.6.1"

# Discover Homebrew prefix (Apple Silicon: /opt/homebrew, Intel: /usr/local)
HOMEBREW_PREFIX = None
env_prefix = os.environ.get("HOMEBREW_PREFIX")
if env_prefix and Path(env_prefix).is_dir():
    HOMEBREW_PREFIX = Path(env_prefix)
else:
    for candidate in [Path("/opt/homebrew"), Path("/usr/local")]:
        if candidate.is_dir():
            HOMEBREW_PREFIX = candidate
            break

if not HOMEBREW_PREFIX:
    try:
        out = subprocess.check_output(["brew", "--prefix"], text=True).strip()
        if out and Path(out).is_dir():
            HOMEBREW_PREFIX = Path(out)
    except Exception:
        HOMEBREW_PREFIX = Path(sys.prefix)

print(f"==> Building VideoCut.app with Homebrew Prefix: {HOMEBREW_PREFIX}")

datas = [
    # Application assets
    (str(SRC_DIR / "video_cut" / "style.css"), "video_cut"),
    (str(SRC_DIR / "video_cut" / "resources"), "video_cut/resources"),
    (str(SRC_DIR / "video_cut" / "locale"), "video_cut/locale"),
    # Relocatable macOS Fontconfig configuration for crisp native SF Pro rendering
    (str(SPEC_DIR / "fonts.conf"), "etc/fonts"),
]

# Collect Icon Themes (Adwaita & hicolor) if installed in Homebrew
for icon_theme in ["Adwaita", "hicolor"]:
    theme_path = HOMEBREW_PREFIX / "share" / "icons" / icon_theme
    if theme_path.exists():
        datas.append((str(theme_path), f"share/icons/{icon_theme}"))

# NOTE: PyInstaller's built-in GI / GTK / GdkPixbuf / GStreamer hooks automatically
# collect and properly relocate gi_typelibs/, lib/gdk-pixbuf/, gst_plugins/,
# gio_modules/, and compiled share/glib-2.0/schemas/. Do NOT duplicate them in datas.

binaries = []

# Pre-seed Homebrew's shared libraries that Pillow's macOS PyPI wheel also bundles
# under PIL/.dylibs/ (most critically libharfbuzz.0.dylib, whose Pillow wheel copy
# is compiled without GLib support and breaks libgtk-4.1.dylib with missing
# _hb_glib_script_to_script). Seeding them here ensures PyInstaller traces all of
# their transitive Homebrew dependencies (e.g., libgraphite2).
HOMEBREW_LIB_DIR = HOMEBREW_PREFIX / "lib"
BREW_PRIORITY_LIBS = [
    "libharfbuzz.0.dylib",
    "libfreetype.6.dylib",
    "libpng16.16.dylib",
    "libtiff.6.dylib",
    "libwebp.7.dylib",
    "libwebpdemux.2.dylib",
    "libwebpmux.3.dylib",
    "libsharpyuv.0.dylib",
    "liblcms2.2.dylib",
    "liblzma.5.dylib",
    "libxcb.1.dylib",
    "libXau.6.dylib",
]
brew_priority_paths: dict[str, str] = {}
for lib_name in BREW_PRIORITY_LIBS:
    lib_path = HOMEBREW_LIB_DIR / lib_name
    if lib_path.exists():
        resolved = str(lib_path.resolve())
        binaries.append((resolved, "."))
        brew_priority_paths[lib_name] = resolved

# Collect FFmpeg and FFprobe binaries if present in Homebrew
for bin_name in ["ffmpeg", "ffprobe"]:
    bin_path = HOMEBREW_PREFIX / "bin" / bin_name
    if bin_path.exists():
        binaries.append((str(bin_path.resolve()), "bin"))

hidden_imports = [
    "gi",
    "gi.repository.Gtk",
    "gi.repository.Gdk",
    "gi.repository.Gsk",
    "gi.repository.Graphene",
    "gi.repository.Gio",
    "gi.repository.GLib",
    "gi.repository.GObject",
    "gi.repository.Pango",
    "gi.repository.PangoCairo",
    "gi.repository.Adw",
    "gi.repository.GdkPixbuf",
    "gi.repository.Gst",
    "gi.repository.GstVideo",
    "cairo",
    "PIL",
    "PIL.Image",
    "PIL.ImageDraw",
    "PIL.ImageFont",
    "PIL.IcnsImagePlugin",
    "video_cut",
    "video_cut.app",
    "video_cut.window",
    "video_cut.models",
    "video_cut.cursors",
    "video_cut.media_backend",
    "video_cut.player",
    "video_cut.timeline_widget",
    "video_cut.segment_card",
    "video_cut.crop_rotate_dialog",
    "video_cut.image_import_dialog",
    "video_cut.advanced_export_dialog",
    "video_cut.overlay_dialog",
    "video_cut.health_dialog",
    "video_cut.ffmpeg_error_dialog",
    "video_cut.keyframe_help_dialog",
    "video_cut.progress_modal",
    "video_cut.clipboard_controller",
    "video_cut.i18n",
    "video_cut.paths",
    "video_cut.utils",
    "video_cut.services.ffmpeg_service",
    "video_cut.services.export_service",
    "video_cut.services.image_service",
    "video_cut.services.probe_service",
    "video_cut.services.thumbnail_service",
    "video_cut.services.overlay_service",
    "video_cut.services.overlay_audio_player",
    "video_cut.services.project_service",
    "video_cut.services.private_dir",
    "video_cut.services.health_service",
]

a = Analysis(
    [str(SRC_DIR / "video_cut" / "__main__.py")],
    pathex=[str(SRC_DIR)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[str(SPEC_DIR / "rthook_macos.py")],
    excludes=["tkinter", "unittest", "pytest"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# Prevent Pillow's wheel dylibs (PIL/.dylibs/*) from shadowing Homebrew's full dylibs
# at top-level Contents/Frameworks/ (e.g., libharfbuzz.0.dylib without GLib symbols).
# PyInstaller places Pillow's dylibs in a.binaries AND places top-level SYMLINK entries
# (e.g., ('libharfbuzz.0.dylib', 'PIL/.dylibs/libharfbuzz.0.dylib', 'SYMLINK')) in a.datas.
# Drop both and ensure Homebrew's real BINARY is placed at top-level.
# Exclude GTK 3 GStreamer plugin (libgstgtk.dylib) and GTK 3 runtime libraries.
# Loading libgstgtk.dylib during GStreamer plugin scanning pulls libgtk-3.0.dylib and
# libgdk-3.0.dylib into our GTK 4 process, causing Objective-C class collisions
# (gdkCoreCursor / GtkApplicationQuartzDelegate) and GLib GtkWidget type conflicts.
GTK3_CONFLICT_PREFIXES = (
    "libgstgtk.",
    "libgtk-3.",
    "libgdk-3.",
    "libatk-1.0.",
)

filtered_binaries = []
present_top_level: set[str] = set()
for dest, src, kind in a.binaries:
    bname = os.path.basename(dest)
    if bname.startswith(GTK3_CONFLICT_PREFIXES):
        print(f"    [exclude] Dropping GTK3-conflicting binary: {dest}")
        continue
    if bname in brew_priority_paths:
        if dest.startswith("PIL/") or (kind == "SYMLINK" and "PIL/" in str(src)):
            print(f"    [dedup] Replacing Pillow wheel binary with Homebrew dylib: {dest} ({kind})")
            continue
        if dest == bname and kind == "BINARY":
            present_top_level.add(bname)
    filtered_binaries.append((dest, src, kind))

for bname, brew_src in brew_priority_paths.items():
    if bname not in present_top_level:
        filtered_binaries.append((bname, brew_src, "BINARY"))
a.binaries = filtered_binaries

filtered_datas = []
for dest, src, kind in a.datas:
    bname = os.path.basename(dest)
    if bname.startswith(GTK3_CONFLICT_PREFIXES):
        continue
    if kind == "SYMLINK" and bname in brew_priority_paths and "PIL/" in str(src):
        print(f"    [dedup] Dropping conflicting Pillow symlink from a.datas: {dest} -> {src}")
        continue
    filtered_datas.append((dest, src, kind))
a.datas = filtered_datas

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="VideoCut",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=str(SPEC_DIR / "entitlements.plist"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="VideoCut",
)

app = BUNDLE(
    coll,
    name="VideoCut.app",
    icon=str(SPEC_DIR / "videocut.icns") if (SPEC_DIR / "videocut.icns").exists() else None,
    bundle_identifier="io.github.thor27.video-cut",
    info_plist={
        "CFBundleName": "VideoCut",
        "CFBundleDisplayName": "Video Cut",
        "CFBundleIdentifier": "io.github.thor27.video-cut",
        "CFBundleVersion": version,
        "CFBundleShortVersionString": version,
        "CFBundlePackageType": "APPL",
        "CFBundleSignature": "????",
        "CFBundleDocumentTypes": [
            {
                "CFBundleTypeName": "Video or Image File",
                "CFBundleTypeRole": "Viewer",
                "LSHandlerRank": "Alternate",
                "LSItemContentTypes": [
                    "public.movie",
                    "public.video",
                    "public.image",
                ],
            },
            {
                "CFBundleTypeName": "Video Cut Project",
                "CFBundleTypeRole": "Editor",
                "CFBundleTypeExtensions": ["vcut"],
                "CFBundleTypeIconFile": "videocut.icns",
                "LSHandlerRank": "Owner",
                "LSItemContentTypes": ["io.github.thor27.video-cut.project"],
            },
        ],
        "UTExportedTypeDeclarations": [
            {
                "UTTypeIdentifier": "io.github.thor27.video-cut.project",
                "UTTypeDescription": "Video Cut Project File",
                "UTTypeConformsTo": ["public.data", "public.json"],
                "UTTypeTagSpecification": {
                    "public.filename-extension": ["vcut"],
                    "public.mime-type": ["application/x-videocut"],
                },
            }
        ],
        "NSHighResolutionCapable": True,
        "NSRequiresAquaSystemAppearance": False,
        "NSHumanReadableCopyright": "Copyright © 2026 Thomaz Reis. GPL-3.0 Licensed.",
    },
)
