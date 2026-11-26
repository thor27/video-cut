# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller specification for Video Cut on Windows (GTK 4 + Libadwaita + GStreamer + FFmpeg)."""

import os
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

# In MSYS2, MINGW_PREFIX points to the active environment (e.g. /ucrt64 or C:/msys64/ucrt64)
mingw_prefix_env = os.environ.get("MINGW_PREFIX")
if mingw_prefix_env:
    MINGW_PREFIX = Path(mingw_prefix_env)
else:
    for candidate in [
        Path("C:/msys64/ucrt64"),
        Path("C:/msys64/mingw64"),
        Path("/ucrt64"),
        Path("/mingw64"),
    ]:
        if candidate.exists():
            MINGW_PREFIX = candidate
            break
    else:
        MINGW_PREFIX = Path(sys.prefix)

print(f"==> Building VideoCut.exe with Prefix: {MINGW_PREFIX}")

datas = [
    # Application assets
    (str(SRC_DIR / "video_cut" / "style.css"), "video_cut"),
    (str(SRC_DIR / "video_cut" / "resources"), "video_cut/resources"),
    (str(SRC_DIR / "video_cut" / "locale"), "video_cut/locale"),
]

if (SPEC_DIR / "videocut.ico").exists():
    datas.append((str(SPEC_DIR / "videocut.ico"), "."))

# Collect GSettings schemas
schemas_path = MINGW_PREFIX / "share" / "glib-2.0" / "schemas"
if schemas_path.exists():
    datas.append((str(schemas_path), "share/glib-2.0/schemas"))
    datas.append((str(schemas_path), "glib-2.0/schemas"))

# Collect GObject introspection typelibs
typelibs_path = MINGW_PREFIX / "lib" / "girepository-1.0"
if typelibs_path.exists():
    datas.append((str(typelibs_path), "lib/girepository-1.0"))
    datas.append((str(typelibs_path), "girepository-1.0"))

# Collect GdkPixbuf loaders & cache
pixbuf_path = MINGW_PREFIX / "lib" / "gdk-pixbuf-2.0"
if pixbuf_path.exists():
    datas.append((str(pixbuf_path), "lib/gdk-pixbuf-2.0"))

# Collect GStreamer plugins
gst_path = MINGW_PREFIX / "lib" / "gstreamer-1.0"
if gst_path.exists():
    datas.append((str(gst_path), "lib/gstreamer-1.0"))
    datas.append((str(gst_path), "gstreamer-1.0"))

# Collect Icon Themes
for icon_theme in ["Adwaita", "hicolor"]:
    theme_path = MINGW_PREFIX / "share" / "icons" / icon_theme
    if theme_path.exists():
        datas.append((str(theme_path), f"share/icons/{icon_theme}"))

binaries = []

# Collect FFmpeg and FFprobe binaries if present
for bin_name in ["ffmpeg.exe", "ffprobe.exe"]:
    bin_path = MINGW_PREFIX / "bin" / bin_name
    if bin_path.exists():
        binaries.append((str(bin_path), "."))
        binaries.append((str(bin_path), "bin"))

# Collect gst-plugin-scanner
gst_scanner = MINGW_PREFIX / "libexec" / "gstreamer-1.0" / "gst-plugin-scanner.exe"
if gst_scanner.exists():
    binaries.append((str(gst_scanner), "libexec/gstreamer-1.0"))

hidden_imports = [
    "socket",
    "_socket",
    "ctypes",
    "gi",
    "gi.repository.Gtk",
    "gi.repository.Gdk",
    "gi.repository.Gio",
    "gi.repository.GLib",
    "gi.repository.GObject",
    "gi.repository.Adw",
    "gi.repository.GdkPixbuf",
    "gi.repository.Gst",
    "gi.repository.GstVideo",
    "cairo",
    "PIL",
    "PIL.Image",
    "PIL.ImageDraw",
    "video_cut",
    "video_cut.app",
    "video_cut.window",
    "video_cut.models",
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
    "video_cut.services.health_service",
    "video_cut.services.project_service",
    "video_cut.services.overlay_audio_player",
    "video_cut.services.private_dir",
]

a = Analysis(
    [str(SRC_DIR / "video_cut" / "__main__.py")],
    pathex=[str(SRC_DIR)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "unittest", "pytest"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

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
    icon=str(SPEC_DIR / "videocut.ico") if (SPEC_DIR / "videocut.ico").exists() else None,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
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
