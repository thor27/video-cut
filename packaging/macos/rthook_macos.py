"""PyInstaller runtime hook for macOS."""

import os
import sys
from pathlib import Path

if getattr(sys, "frozen", False) and sys.platform == "darwin":
    exe_path = Path(sys.executable).resolve()
    # In a .app bundle, sys.executable is Contents/MacOS/VideoCut
    # Contents directory is exe_path.parent.parent
    meipass = getattr(sys, "_MEIPASS", None)
    bundle_dir = Path(meipass).resolve() if meipass else exe_path.parent
    contents_dir = exe_path.parent.parent if exe_path.parent.name == "MacOS" else bundle_dir

    # Share and GSettings schemas
    for s_cand in [
        bundle_dir / "share",
        contents_dir / "Resources" / "share",
        contents_dir / "share",
    ]:
        if s_cand.is_dir():
            existing = os.environ.get("XDG_DATA_DIRS", "")
            os.environ["XDG_DATA_DIRS"] = f"{s_cand}:{existing}" if existing else str(s_cand)
            schemas = s_cand / "glib-2.0" / "schemas"
            if schemas.is_dir():
                os.environ["GSETTINGS_SCHEMA_DIR"] = str(schemas)
            break

    # Pre-load core GTK4, Libadwaita, and GLib libraries so GObject introspection
    # resolves dynamic interface types (e.g. Adw.Window / Gtk.Accessible) cleanly.
    import ctypes

    lib_candidates = [
        "libglib-2.0.0.dylib",
        "libgobject-2.0.0.dylib",
        "libgirepository-1.0.1.dylib",
        "libgtk-4.1.dylib",
        "libadwaita-1.0.dylib",
    ]
    search_dirs = [
        bundle_dir,
        bundle_dir / "lib",
        contents_dir / "Frameworks",
        contents_dir / "MacOS",
        Path("/opt/homebrew/lib"),
        Path("/usr/local/lib"),
    ]
    for lib_name in lib_candidates:
        for s_dir in search_dirs:
            p = Path(s_dir) / lib_name
            if p.is_file():
                try:
                    ctypes.CDLL(str(p), mode=ctypes.RTLD_GLOBAL)
                    break
                except Exception:
                    pass

    # Typelibs
    typelib_candidates = [
        bundle_dir / "lib" / "girepository-1.0",
        contents_dir / "Frameworks" / "girepository-1.0",
        Path("/opt/homebrew/lib/girepository-1.0"),
        Path("/usr/local/lib/girepository-1.0"),
    ]
    valid_typelibs = [str(t) for t in typelib_candidates if t.is_dir()]
    existing_gi = os.environ.get("GI_TYPELIB_PATH", "")
    if existing_gi:
        valid_typelibs.append(existing_gi)
    if valid_typelibs:
        os.environ["GI_TYPELIB_PATH"] = ":".join(valid_typelibs)

    # Dynamic library search paths
    cur_dyld = os.environ.get("DYLD_FALLBACK_LIBRARY_PATH", "")
    valid_lib_dirs = [str(d) for d in search_dirs if Path(d).is_dir()]
    if cur_dyld:
        valid_lib_dirs.append(cur_dyld)
    if valid_lib_dirs:
        os.environ["DYLD_FALLBACK_LIBRARY_PATH"] = ":".join(valid_lib_dirs)

    # GStreamer plugins
    for gst_cand in [
        bundle_dir / "lib" / "gstreamer-1.0",
        contents_dir / "Frameworks" / "gstreamer-1.0",
    ]:
        if gst_cand.is_dir():
            os.environ["GST_PLUGIN_SYSTEM_PATH"] = ""
            os.environ["GST_PLUGIN_PATH"] = str(gst_cand)
            break

    # GStreamer scanner
    for scan_cand in [
        bundle_dir / "libexec" / "gstreamer-1.0" / "gst-plugin-scanner",
        contents_dir / "Helpers" / "gst-plugin-scanner",
    ]:
        if scan_cand.is_file():
            os.environ["GST_PLUGIN_SCANNER"] = str(scan_cand)
            break

    # Add bundle bin and Homebrew to PATH for ffmpeg and ffprobe
    path_entries = [
        str(bundle_dir / "bin"),
        str(contents_dir / "MacOS"),
        "/opt/homebrew/bin",
        "/usr/local/bin",
    ]
    cur_path = os.environ.get("PATH", "")
    for p in path_entries:
        if p not in cur_path.split(os.pathsep):
            cur_path = f"{p}{os.pathsep}{cur_path}"
    os.environ["PATH"] = cur_path
