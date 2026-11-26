"""CLI entrypoint for Video Cut."""

import argparse
import os
import sys
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

