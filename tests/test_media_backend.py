"""Tests for the GStreamer media playback backend (GstMediaFile)."""

import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib

from video_cut.media_backend import GstMediaFile, create_media_file


def _pump(seconds: float = 0.2) -> None:
    ctx = GLib.MainContext.default()
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        while ctx.pending():
            ctx.iteration(False)
        time.sleep(0.01)


def test_gst_media_file_prepare_seek_play_pause(sample_video):
    """Verify GstMediaFile loads, prepares, seeks, plays, pauses, and closes cleanly."""
    app = Adw.Application(application_id="io.github.thor27.video-cut.testgstmedia")

    def run_test(app):
        mf = None
        try:
            mf = GstMediaFile.new_for_filename(sample_video)
            prepared_events = []
            mf.connect("notify::prepared", lambda obj, _: prepared_events.append(obj.is_prepared()))

            # Wait for preroll
            deadline = time.monotonic() + 3.0
            ctx = GLib.MainContext.default()
            while time.monotonic() < deadline and not mf.is_prepared():
                while ctx.pending():
                    ctx.iteration(False)
                time.sleep(0.01)

            assert mf.is_prepared() is True
            assert True in prepared_events
            assert mf.get_error() is None
            assert mf.get_duration() >= 4_500_000

            # Seek to 2.0s
            mf.seek(2_000_000)
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline and mf.get_property("seeking"):
                while ctx.pending():
                    ctx.iteration(False)
                time.sleep(0.01)

            assert mf.get_property("seeking") is False
            assert abs(mf.get_timestamp() / 1_000_000.0 - 2.0) < 0.35

            # Play briefly and verify position advances
            t_before = mf.get_timestamp()
            mf.play()
            assert mf.get_playing() is True
            _pump(0.35)
            assert mf.get_timestamp() > t_before

            # Pause
            mf.pause()
            assert mf.get_playing() is False
        finally:
            if mf is not None:
                mf.close()
            app.quit()

    app.connect("activate", run_test)
    app.run(["test"])


def test_gst_media_file_missing_path(tmp_path):
    """Verify GstMediaFile handles nonexistent files without crashing or hanging."""
    missing = tmp_path / "does_not_exist.mp4"
    mf = GstMediaFile.new_for_filename(missing)
    try:
        assert mf.is_prepared() is False
        assert mf.get_error() is not None
    finally:
        mf.close()


def test_create_media_file_returns_working_backend(sample_video):
    """Verify create_media_file returns a usable paintable media object."""
    mf = create_media_file(sample_video)
    try:
        assert type(mf).__name__ != "GtkNoMediaFile"
        assert hasattr(mf, "play")
        assert hasattr(mf, "pause")
        assert hasattr(mf, "seek")
        assert hasattr(mf, "is_prepared")
    finally:
        if hasattr(mf, "close"):
            mf.close()
