"""Test to ensure playback works flawlessly after splitting clips."""

import time
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk

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


def test_reordered_segments_playback_no_seek_spam():
    """Verify that playing split and reordered segments across EOF does not spam seek or get stuck in ended state."""
    from video_cut.models import ProjectTimeline, Segment
    from video_cut.player import VideoPlayerController

    app = Adw.Application(application_id="io.github.thomaz.videocut.testreorderplay")

    def run_test(app):
        pic = Gtk.Picture()
        player = None
        try:
            timeline = ProjectTimeline()
            # Seg 0 is [3.5, 5.0] (1.5s duration, ends at EOF of 5s test_video)
            # Seg 1 is [0.0, 3.5] (3.5s duration)
            seg0 = Segment(Path("/tmp/test_video.mp4"), 5.0, 3.5, 5.0)
            seg1 = Segment(Path("/tmp/test_video.mp4"), 5.0, 0.0, 3.5)
            timeline.segments = [seg0, seg1]

            player = VideoPlayerController(
                timeline,
                pic,
                on_time_update=lambda t: None,
                on_playback_state_changed=lambda s: None,
            )

            player.seek_to_global_time(0.0)
            ctx = GLib.MainContext.default()
            wait_start = time.time()
            while time.time() - wait_start < 1.0 and not (
                player.current_media_file and player.current_media_file.is_prepared()
            ):
                ctx.iteration(True)

            seeks = []
            orig_seek = player._seek_media_file

            def tracked_seek(source_ts, force=False):
                seeks.append((time.monotonic(), source_ts, force))
                return orig_seek(source_ts, force=force)

            player._seek_media_file = tracked_seek

            player.play()
            assert player.is_playing is True

            # Run playback across the 1.5s boundary into seg1
            t0 = time.time()
            while time.time() - t0 < 1.9:
                while ctx.pending():
                    ctx.iteration(False)
                time.sleep(0.02)

            # In the old code, seeks would be spammed 30+ times.
            # In the new code, seeks must be <= 3 (initial setup + single boundary transition).
            assert len(seeks) <= 3, f"Seek was spammed {len(seeks)} times!"

            # Timeline must have crossed into seg1 (> 1.5s)
            assert player.current_global_time > 1.5
            assert player.current_segment_id == seg1.id

            # Player must still be playing and not deadlocked at EOF
            assert player.is_playing is True
            assert player.current_media_file is not None
            assert player.current_media_file.get_ended() is False

            # Test unpause does not issue redundant seek
            player.pause()
            assert player.is_playing is False
            seeks_before_unpause = len(seeks)

            player.play()
            assert player.is_playing is True
            # Unpausing at current position should not issue a seek
            assert len(seeks) == seeks_before_unpause

            player.pause()
        finally:
            if player:
                player.clear()
            app.quit()

    app.connect("activate", run_test)
    app.run(["test"])

