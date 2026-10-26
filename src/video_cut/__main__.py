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

    # 6. Initialize Windows Sockets (Winsock) early
    try:
        import ctypes
        wsa_data = ctypes.create_string_buffer(512)
        ctypes.windll.ws2_32.WSAStartup(0x0202, wsa_data)
    except Exception:
        pass

    # 7. Protect against PyGObject [WinError 10093] during signal helper socketpair setup
    try:
        from contextlib import contextmanager

        import gi._ossighelper

        orig_wakeup = getattr(gi._ossighelper, "wakeup_on_signal", None)
        if orig_wakeup is not None:
            @contextmanager
            def _safe_wakeup():
                try:
                    with orig_wakeup():
                        yield
                except OSError:
                    yield

            gi._ossighelper.wakeup_on_signal = _safe_wakeup

        dummy_loop = getattr(gi._ossighelper, "DummyEventLoop", None)
        if dummy_loop is not None:
            @classmethod
            @contextmanager
            def _safe_running(cls, quit_func):
                try:
                    with gi._ossighelper.wakeup_on_signal():
                        yield
                except Exception:
                    yield

            dummy_loop.running = _safe_running
    except Exception:
        pass


_init_windows_environment()

from gi.repository import GLib  # noqa: E402

try:
    from .app import VideoCutApplication  # noqa: E402
except ImportError:
    from video_cut.app import VideoCutApplication  # noqa: E402


def main() -> int:
    try:
        GLib.set_prgname("io.github.thomaz.videocut")
        GLib.set_application_name("Video Cut")

        initial_video = None
        args = sys.argv[1:]
        if args and not args[0].startswith("-"):
            initial_video = args[0]

        app = VideoCutApplication(initial_video=initial_video)
        return app.run([sys.argv[0]])
    except Exception as e:
        import traceback
        tb_str = traceback.format_exc()
        try:
            appdata = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(Path.home())
            log_p = Path(appdata) / "VideoCut" / "crash.log"
            log_p.parent.mkdir(parents=True, exist_ok=True)
            log_p.write_text(tb_str, encoding="utf-8")
        except Exception:
            pass
        if sys.platform == "win32":
            try:
                import ctypes
                ctypes.windll.user32.MessageBoxW(
                    0,
                    f"Video Cut encountered an error:\n\n{e}\n\nCheck crash.log for details.",
                    "Video Cut - Startup Error",
                    0x10,
                )
            except Exception:
                pass
        sys.stderr.write(tb_str)
        return 1


if __name__ == "__main__":
    sys.exit(main())
