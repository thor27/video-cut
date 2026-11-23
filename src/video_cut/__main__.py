"""CLI entrypoint for Video Cut."""

import argparse
import os
import sys
from contextlib import contextmanager
from pathlib import Path

if sys.platform == "darwin":
    # CoreText font rendering in GTK4/GL on macOS suffers from broken weight/spacing
    # and non-monospace fallbacks; Fontconfig + FreeType renders native SF Pro crisply.
    os.environ.setdefault("PANGOCAIRO_BACKEND", "fontconfig")

from gi.repository import GLib

try:
    from . import __version__
    from .paths import APP_ID, APP_NAME
except (ImportError, ValueError):
    from video_cut import __version__
    from video_cut.paths import APP_ID, APP_NAME


def parse_args(args: list[str] | None = None) -> argparse.Namespace:
    """Parse command line arguments."""
    if args is not None and sys.platform == "darwin":
        # macOS LaunchServices may pass -psn_0_xxxxx when launching a .app bundle
        args = [a for a in args if not a.startswith("-psn_")]
    parser = argparse.ArgumentParser(
        prog="video-cut",
        description="Fast, simple video and image editor.",
    )
    parser.add_argument(
        "-v",
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "files",
        nargs="*",
        type=Path,
        help="One or more video or image files to open in the timeline.",
    )
    return parser.parse_args(args)


def _setup_macos_environment() -> None:
    """Configure runtime environment paths on macOS."""
    os.environ.setdefault("PANGOCAIRO_BACKEND", "fontconfig")

    # Ensure standard Homebrew directories are in PATH so ffmpeg/ffprobe and tools are found
    brew_paths = ["/opt/homebrew/bin", "/usr/local/bin"]
    cur_path = os.environ.get("PATH", "")
    path_parts = [p for p in cur_path.split(os.pathsep) if p]
    for bp in brew_paths:
        if bp not in path_parts and Path(bp).is_dir():
            path_parts.append(bp)
    os.environ["PATH"] = os.pathsep.join(path_parts)

    # If running frozen in a macOS app bundle, ensure writable GStreamer registry and bundled bin PATH
    if getattr(sys, "frozen", False):
        exe_path = Path(sys.executable).resolve()
        meipass = getattr(sys, "_MEIPASS", None)
        bundle_dir = Path(meipass).resolve() if meipass else exe_path.parent
        bundle_bin = bundle_dir / "bin"
        if bundle_bin.is_dir():
            b_str = str(bundle_bin)
            parts = [p for p in os.environ.get("PATH", "").split(os.pathsep) if p]
            if b_str not in parts:
                os.environ["PATH"] = os.pathsep.join([b_str, *parts])

        # Point GStreamer registry to a writable user cache dir (safe inside read-only .dmg)
        cache_dir = Path.home() / "Library" / "Caches" / APP_ID
        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
            reg_file = str(cache_dir / "gst-registry.bin")
            os.environ["GST_REGISTRY"] = reg_file
            os.environ["GST_REGISTRY_1_0"] = reg_file
        except OSError:
            pass


def _setup_windows_environment() -> None:
    """Configure runtime environment variables for GTK 4, Libadwaita, and GStreamer on Windows."""
    import os

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

    # 2. GLib Schemas
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

    # 5. GStreamer registry in user APPDATA
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
    except Exception:
        pass


def main(argv: list[str] | None = None) -> int:
    try:
        if argv is None:
            argv = sys.argv

        if sys.platform == "darwin":
            _setup_macos_environment()
        elif sys.platform == "win32":
            _setup_windows_environment()

        GLib.set_prgname(APP_ID)
        GLib.set_application_name(APP_NAME)

        parsed = parse_args(argv[1:])

        try:
            from .app import VideoCutApplication
        except (ImportError, ValueError):
            from video_cut.app import VideoCutApplication

        app = VideoCutApplication(initial_files=parsed.files)
        return app.run([argv[0]])
    except Exception as e:
        import os
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

