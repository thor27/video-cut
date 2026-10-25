"""CLI entrypoint for Video Cut."""

import os
import sys
from pathlib import Path


def _init_windows_environment() -> None:
    """Configure runtime environment variables for GTK 4, Libadwaita, and GStreamer on Windows."""
    if sys.platform != "win32":
        return

    if getattr(sys, "frozen", False):
        base_dir = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    else:
        base_dir = Path(__file__).resolve().parent.parent.parent

    # 1. Typelibs path
    for t_dir in [
        base_dir / "lib" / "girepository-1.0",
        base_dir / "girepository-1.0",
    ]:
        if t_dir.is_dir():
            os.environ["GI_TYPELIB_PATH"] = str(t_dir)
            break

    # 2. GLib Schemas (crucial for Adw / GTK themes & settings)
    for s_dir in [
        base_dir / "share" / "glib-2.0" / "schemas",
        base_dir / "glib-2.0" / "schemas",
    ]:
        if s_dir.is_dir():
            os.environ["GSETTINGS_SCHEMA_DIR"] = str(s_dir)
            break

    # 3. GdkPixbuf loaders
    for p_cache in [
        base_dir / "lib" / "gdk-pixbuf-2.0" / "2.10.0" / "loaders.cache",
        base_dir / "gdk-pixbuf-2.0" / "loaders.cache",
    ]:
        if p_cache.is_file():
            os.environ["GDK_PIXBUF_MODULE_FILE"] = str(p_cache)
            break

    # 4. GStreamer plugins
    for gst_dir in [
        base_dir / "lib" / "gstreamer-1.0",
        base_dir / "gstreamer-1.0",
    ]:
        if gst_dir.is_dir():
            os.environ["GST_PLUGIN_PATH"] = str(gst_dir)
            os.environ["GST_PLUGIN_SYSTEM_PATH"] = str(gst_dir)
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

from .app import VideoCutApplication  # noqa: E402


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
