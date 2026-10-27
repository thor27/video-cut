"""CLI entrypoint for Video Cut."""

import sys

from gi.repository import GLib

from .app import VideoCutApplication


def main() -> int:
    GLib.set_prgname("io.github.thor27.video-cut")
    GLib.set_application_name("Video Cut")

    initial_video = None
    args = sys.argv[1:]
    if args and not args[0].startswith("-"):
        initial_video = args[0]

    app = VideoCutApplication(initial_video=initial_video)
    return app.run([sys.argv[0]])


if __name__ == "__main__":
    sys.exit(main())
