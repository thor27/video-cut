"""PyInstaller runtime hook for macOS."""

import os
import sys
from pathlib import Path

if getattr(sys, "frozen", False) and sys.platform == "darwin":
    # Strip macOS LaunchServices process serial number flag if present
    sys.argv = [sys.argv[0], *[a for a in sys.argv[1:] if not a.startswith("-psn_")]]

    exe_path = Path(sys.executable).resolve()
    meipass = getattr(sys, "_MEIPASS", None)
    bundle_dir = Path(meipass).resolve() if meipass else exe_path.parent
    contents_dir = exe_path.parent.parent if exe_path.parent.name == "MacOS" else bundle_dir

    # Use Fontconfig + FreeType backend for crisp native macOS SF Pro rendering in GTK4
    os.environ.setdefault("PANGOCAIRO_BACKEND", "fontconfig")

    # Point Fontconfig to the bundled relocatable macOS fonts.conf
    for fc_cand in [
        bundle_dir / "etc" / "fonts" / "fonts.conf",
        contents_dir / "Resources" / "etc" / "fonts" / "fonts.conf",
    ]:
        if fc_cand.is_file():
            os.environ["FONTCONFIG_FILE"] = str(fc_cand)
            os.environ["FONTCONFIG_PATH"] = str(fc_cand.parent)
            break

    # Ensure XDG_DATA_DIRS includes bundled share (icons, compiled GSettings schemas)
    for s_cand in [
        bundle_dir / "share",
        contents_dir / "Resources" / "share",
    ]:
        if s_cand.is_dir():
            existing = os.environ.get("XDG_DATA_DIRS", "")
            parts = [p for p in existing.split(os.pathsep) if p]
            if str(s_cand) not in parts:
                os.environ["XDG_DATA_DIRS"] = os.pathsep.join([str(s_cand), *parts])
            schemas = s_cand / "glib-2.0" / "schemas"
            if (schemas / "gschemas.compiled").is_file():
                os.environ["GSETTINGS_SCHEMA_DIR"] = str(schemas)
            break

    # Configure GStreamer to use only PyInstaller's relocated gst_plugins and a writable
    # per-user registry cache (critical when launched directly from a read-only .dmg)
    os.environ["GST_REGISTRY_FORK"] = "no"
    os.environ["GST_PLUGIN_SYSTEM_PATH"] = ""
    os.environ["GST_PLUGIN_SYSTEM_PATH_1_0"] = ""

    gst_plugins = bundle_dir / "gst_plugins"
    if gst_plugins.is_dir():
        gst_paths = os.pathsep.join([str(bundle_dir), str(gst_plugins)])
        os.environ["GST_PLUGIN_PATH"] = gst_paths
        os.environ["GST_PLUGIN_PATH_1_0"] = gst_paths

    cache_dir = Path.home() / "Library" / "Caches" / "io.github.thor27.video-cut"
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        reg_file = str(cache_dir / "gst-registry.bin")
        os.environ["GST_REGISTRY"] = reg_file
        os.environ["GST_REGISTRY_1_0"] = reg_file
    except OSError:
        pass

    # Prepend bundled bin (ffmpeg/ffprobe) and keep Homebrew paths as fallback at the end
    cur_parts = [p for p in os.environ.get("PATH", "").split(os.pathsep) if p]
    front_paths = [str(bundle_dir / "bin"), str(contents_dir / "MacOS")]
    back_paths = ["/opt/homebrew/bin", "/usr/local/bin"]

    new_parts: list[str] = []
    for p in front_paths:
        if Path(p).is_dir() and p not in new_parts:
            new_parts.append(p)
    for p in cur_parts:
        if p not in new_parts:
            new_parts.append(p)
    for p in back_paths:
        if Path(p).is_dir() and p not in new_parts:
            new_parts.append(p)
    os.environ["PATH"] = os.pathsep.join(new_parts)
