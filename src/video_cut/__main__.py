"""CLI entrypoint for Video Cut."""

import argparse
import sys

from gi.repository import GLib

from . import __version__
from .app import VideoCutApplication
from .paths import APP_ID, APP_NAME
from .utils import resolve_file_arg


def parse_args(args: list[str] | None = None) -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        prog="video-cut",
        description="Fast, simple video and image editor for Linux.",
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
        type=resolve_file_arg,
        help="One or more video or image files to open in the timeline.",
    )
    return parser.parse_args(args)


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv

    GLib.set_prgname(APP_ID)
    GLib.set_application_name(APP_NAME)

    parsed = parse_args(argv[1:])

    app = VideoCutApplication(initial_files=parsed.files)
    return app.run([argv[0]])


if __name__ == "__main__":
    sys.exit(main())

