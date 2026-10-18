"""Test to ensure playback works flawlessly after splitting clips."""

import time
from pathlib import Path
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib

from video_cut.window import MainWindow


def test_playback_after_split():
    app = Adw.Application(application_id="io.github.thomaz.videocut.testsplitplay")

    def run_test(app):
        win = None
        try:
            win = MainWindow(application=app)
            win.load_video(Path("/tmp/test_video.mp4"))

            assert len(win.timeline.segments) == 1
            assert abs(win.timeline.total_duration - 5.0) < 0.1

            # Seek to 2.0s
            win.current_global_time = 2.0
            win.player.seek_to_global_time(2.0)

            # Split at cursor
            win.split_at_cursor()
            assert len(win.timeline.segments) == 2

            # Start playback after split!
            win.player.play()
            assert win.player.is_playing is True

            # Advance playback ticks
            start_t = win.player.current_global_time
            ctx = GLib.MainContext.default()

            wait_start = time.time()
            while time.time() - wait_start < 0.3:
                while ctx.pending():
                    ctx.iteration(False)
                time.sleep(0.02)

            # Time must have advanced beyond start_t
            assert win.player.current_global_time > start_t

            # Pause playback
            win.player.pause()
            assert win.player.is_playing is False
        finally:
            if win:
                if hasattr(win, "player") and win.player:
                    win.player.clear()
                win.close()
            app.quit()

    app.connect("activate", run_test)
    app.run(["test"])
