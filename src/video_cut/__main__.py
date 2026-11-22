"""CLI entrypoint for Video Cut."""

import argparse
import sys
from pathlib import Path

from gi.repository import GLib

try:
    from . import __version__
    from .paths import APP_ID, APP_NAME
except (ImportError, ValueError):
    from video_cut import __version__
    from video_cut.paths import APP_ID, APP_NAME


def parse_args(args: list[str] | None = None) -> argparse.Namespace:
    """Parse command line arguments."""
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
    """Configure GSettings, GStreamer, and system paths on macOS."""
    import os

    # Ensure standard Homebrew directories are in PATH so ffmpeg/ffprobe and tools are found
    brew_paths = ["/opt/homebrew/bin", "/usr/local/bin"]
    cur_path = os.environ.get("PATH", "")
    for bp in brew_paths:
        if bp not in cur_path.split(os.pathsep) and Path(bp).is_dir():
            cur_path = f"{bp}{os.pathsep}{cur_path}"
    os.environ["PATH"] = cur_path

    # If running frozen in a macOS app bundle, configure internal bundle paths
    if getattr(sys, "frozen", False):
        exe_path = Path(sys.executable).resolve()
        meipass = getattr(sys, "_MEIPASS", None)
        bundle_dir = Path(meipass).resolve() if meipass else exe_path.parent
        contents_dir = exe_path.parent.parent if exe_path.parent.name == "MacOS" else bundle_dir

        for s_dir in [bundle_dir / "share", contents_dir / "Resources" / "share", contents_dir / "share"]:
            if s_dir.is_dir():
                existing = os.environ.get("XDG_DATA_DIRS", "")
                os.environ["XDG_DATA_DIRS"] = f"{s_dir}:{existing}" if existing else str(s_dir)
                schemas = s_dir / "glib-2.0" / "schemas"
                if schemas.is_dir():
                    os.environ["GSETTINGS_SCHEMA_DIR"] = str(schemas)
                break

        gst_plugins = bundle_dir / "lib" / "gstreamer-1.0"
        if gst_plugins.is_dir():
            os.environ["GST_PLUGIN_SYSTEM_PATH"] = ""
            os.environ["GST_PLUGIN_PATH"] = str(gst_plugins)

        gst_scanner = bundle_dir / "libexec" / "gstreamer-1.0" / "gst-plugin-scanner"
        if gst_scanner.is_file():
            os.environ["GST_PLUGIN_SCANNER"] = str(gst_scanner)


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv

    if sys.platform == "darwin":
        _setup_macos_environment()

    GLib.set_prgname(APP_ID)
    GLib.set_application_name(APP_NAME)

    parsed = parse_args(argv[1:])

    try:
        from .app import VideoCutApplication
    except (ImportError, ValueError):
        from video_cut.app import VideoCutApplication

    app = VideoCutApplication(initial_files=parsed.files)
    return app.run([argv[0]])


if __name__ == "__main__":
    sys.exit(main())

