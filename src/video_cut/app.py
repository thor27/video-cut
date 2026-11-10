"""Adw.Application definition for Video Cut."""

from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib

from .i18n import _
from .paths import APP_ID, APP_NAME, load_application_css, register_resource_icons
from .window import MainWindow


class VideoCutApplication(Adw.Application):
    """Main Adw.Application."""

    def __init__(
        self,
        initial_files: list[Path | str] | None = None,
        initial_video: str | None = None,
    ) -> None:
        if not GLib.get_prgname():
            GLib.set_prgname(APP_ID)
        if not GLib.get_application_name():
            GLib.set_application_name(_(APP_NAME))
        super().__init__(
            application_id=APP_ID,
            flags=Gio.ApplicationFlags.HANDLES_OPEN | Gio.ApplicationFlags.NON_UNIQUE,
        )

        files: list[Path] = []
        if initial_files:
            files.extend(Path(f) for f in initial_files)
        elif initial_video:
            files.append(Path(initial_video))

        self.initial_files: list[Path] = files
        self.window: MainWindow | None = None

    def do_activate(self) -> None:
        display = Gdk.Display.get_default()
        register_resource_icons(display)
        load_application_css(display)

        self.set_accels_for_action("win.new_window", ["<Control>n"])
        self.set_accels_for_action("win.open", ["<Control>o"])
        self.set_accels_for_action("win.open_project", ["<Control><Shift>o"])
        self.set_accels_for_action("win.save_project", ["<Control>s"])
        self.set_accels_for_action("win.save_project_as", ["<Control><Shift>s"])
        self.set_accels_for_action("win.fullscreen", ["F11"])

        if not self.window:
            self.window = MainWindow(application=self)

        self.window.present()

        if self.initial_files and self.window:
            self.window.import_media_files(self.initial_files, replace=True)
            self.initial_files = []

    def do_open(self, files: list[Gio.File], n_files: int, hint: str) -> None:
        self.activate()
        if files and self.window:
            paths: list[Path] = []
            for gfile in files:
                p = gfile.get_path()
                if p:
                    paths.append(Path(p))
            if paths:
                self.window.import_media_files(paths, replace=self.window.timeline.is_empty)
