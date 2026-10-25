"""CLI entrypoint for Video Cut."""

import os
import sys
from pathlib import Path


def _init_windows_environment() -> None:
    """Configure runtime environment variables for GTK 4, Libadwaita, and GStreamer on Windows."""
    if sys.platform != "win32":
        return

    if os.environ.get("GDK_BACKEND") in ("wayland", "x11", None):
        os.environ["GDK_BACKEND"] = "win32"
    if "WAYLAND_DISPLAY" in os.environ:
        del os.environ["WAYLAND_DISPLAY"]

    if getattr(sys, "frozen", False):
        base_dir = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    else:
        base_dir = Path(__file__).resolve().parent.parent.parent

    roots = [
        base_dir,
        Path(sys.prefix),
        Path(sys.executable).parent.parent,
        Path(sys.executable).parent,
    ]

    # 1. Typelibs path
    if "GI_TYPELIB_PATH" not in os.environ:
        for r in roots:
            for t_dir in [r / "lib" / "girepository-1.0", r / "girepository-1.0"]:
                if t_dir.is_dir():
                    os.environ["GI_TYPELIB_PATH"] = str(t_dir)
                    break
            if "GI_TYPELIB_PATH" in os.environ:
                break

    # 2. GLib Schemas (crucial for Adw / GTK themes & settings)
    if "GSETTINGS_SCHEMA_DIR" not in os.environ:
        for r in roots:
            for s_dir in [r / "share" / "glib-2.0" / "schemas", r / "glib-2.0" / "schemas"]:
                if s_dir.is_dir() and (s_dir / "gschemas.compiled").is_file():
                    os.environ["GSETTINGS_SCHEMA_DIR"] = str(s_dir)
                    break
            if "GSETTINGS_SCHEMA_DIR" in os.environ:
                break

    # 3. GdkPixbuf loaders
    if "GDK_PIXBUF_MODULE_FILE" not in os.environ:
        for r in roots:
            for p_cache in [
                r / "lib" / "gdk-pixbuf-2.0" / "2.10.0" / "loaders.cache",
                r / "gdk-pixbuf-2.0" / "loaders.cache",
            ]:
                if p_cache.is_file():
                    os.environ["GDK_PIXBUF_MODULE_FILE"] = str(p_cache)
                    break
            if "GDK_PIXBUF_MODULE_FILE" in os.environ:
                break

    # 4. GStreamer plugins
    if "GST_PLUGIN_PATH" not in os.environ:
        for r in roots:
            for gst_dir in [r / "lib" / "gstreamer-1.0", r / "gstreamer-1.0"]:
                if gst_dir.is_dir():
                    os.environ["GST_PLUGIN_PATH"] = str(gst_dir)
                    os.environ["GST_PLUGIN_SYSTEM_PATH"] = str(gst_dir)
                    break
            if "GST_PLUGIN_PATH" in os.environ:
                break

    # 5. GStreamer registry in user APPDATA to avoid read-only or temp collisions
    appdata = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(Path.home())
    registry_dir = Path(appdata) / "VideoCut"
    try:
        registry_dir.mkdir(parents=True, exist_ok=True)
        os.environ["GST_REGISTRY"] = str(registry_dir / "gstreamer_registry.bin")
    except OSError:
        pass


_init_windows_environment()

from gi.repository import GLib  # noqa: E402

try:
    from .app import VideoCutApplication  # noqa: E402
except ImportError:
    from video_cut.app import VideoCutApplication  # noqa: E402


def main() -> int:
    GLib.set_prgname("io.github.thomaz.videocut")
    GLib.set_application_name("Video Cut")

    initial_video = None
    args = sys.argv[1:]
    if args and not args[0].startswith("-"):
        initial_video = args[0]

    app = VideoCutApplication(initial_video=initial_video)
    return app.run([sys.argv[0]])


if __name__ == "__main__":
    sys.exit(main())
