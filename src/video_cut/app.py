"""Adw.Application definition for Video Cut."""

from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from .i18n import _
from .window import MainWindow


class VideoCutApplication(Adw.Application):
    """Main Adw.Application."""

    def __init__(self, initial_video: str | None = None) -> None:
        if not GLib.get_prgname():
            GLib.set_prgname("io.github.thor27.video-cut")
        if not GLib.get_application_name():
            GLib.set_application_name(_("Video Cut"))
        super().__init__(
            application_id="io.github.thor27.video-cut",
            flags=Gio.ApplicationFlags.HANDLES_OPEN | Gio.ApplicationFlags.NON_UNIQUE,
        )
        self.initial_video = initial_video
        self.window: MainWindow | None = None

    def do_activate(self) -> None:
        res_dir = Path(__file__).parent / "resources"
        if res_dir.exists():
            display = Gdk.Display.get_default()
            if display:
                icon_theme = Gtk.IconTheme.get_for_display(display)
                icon_theme.add_search_path(str(res_dir))

        Gtk.Window.set_default_icon_name("io.github.thor27.video-cut")

        self.set_accels_for_action("win.new_window", ["<Control>n"])
        self.set_accels_for_action("win.open", ["<Control>o"])
        self.set_accels_for_action("win.fullscreen", ["F11"])

        if not self.window:
            self.window = MainWindow(application=self)

        self.window.present()

        if self.initial_video:
            p = Path(self.initial_video)
            if p.exists():
                self.window.load_video(p)
                self.initial_video = None

    def do_open(self, files: list[Gio.File], n_files: int, hint: str) -> None:
        self.activate()
        if files and self.window:
            first = files[0]
            path_str = first.get_path()
            if path_str:
                self.window.load_video(Path(path_str))
