"""Regression and stability tests for refactoring and bug fixes."""

from pathlib import Path
from unittest.mock import patch

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk

from video_cut.models import ProjectTimeline, Segment
from video_cut.player import VideoPlayerController
from video_cut.services.export_service import ExportService
from video_cut.timeline_widget import _translate_widget_coords
from video_cut.utils import clamp
from video_cut.window import MainWindow


def test_player_clear_paintable_cleanup_order(sample_video):
    """Verify player.clear() cleanly clears paintables before media file unref."""
    timeline = ProjectTimeline()
    timeline.add_video(sample_video, 5.0)

    pic = Gtk.Picture()
    ctrl = VideoPlayerController(timeline, pic, lambda t: None, lambda s: None)
    ctrl.seek_to_global_time(1.0)
    for _ in range(10):
        GLib.MainContext.default().iteration(False)
    assert pic.get_paintable() is not None

    # Clearing must not trigger g_object_unref assertion
    ctrl.clear()
    for _ in range(10):
        GLib.MainContext.default().iteration(False)
    assert pic.get_paintable() is None
    assert ctrl.current_media_file is None
    assert ctrl.current_transformed_paintable is None


def test_multi_segment_deletion_marks_dirty(sample_video):
    """Verify that deleting multiple segments in MainWindow sets is_dirty to True."""
    app = Adw.Application(application_id="io.github.thor27.video-cut.test.multidel")

    def run_test(app):
        win = MainWindow(application=app)
        try:
            win.load_video(sample_video, replace=True)
            # Create two segments
            win._on_timeline_seek(2.0)
            win.split_at_cursor()
            assert len(win.timeline.segments) == 2

            # Reset dirty flag
            win.is_dirty = False
            assert not win.is_dirty

            # Select both segments and delete
            all_ids = {s.id for s in win.timeline.segments}
            win.timeline.selected_segment_ids = all_ids
            win.delete_selected_segment()

            assert len(win.timeline.segments) == 0
            assert win.is_dirty is True
        finally:
            win.close()

    app.connect("activate", run_test)
    app.register()
    app.activate()


def test_dialog_source_timestamp_computation():
    """Verify that cursor inside non-first segment correctly translates to source timestamp."""
    timeline = ProjectTimeline()
    # Segment 1: duration 10s (0 to 10s global)
    s1 = Segment(id="s1", source_path=Path("/tmp/v1.mp4"), source_duration=20.0, start_time=5.0, end_time=15.0)
    # Segment 2: duration 15s (10 to 25s global), source start=30.0, end=45.0
    s2 = Segment(id="s2", source_path=Path("/tmp/v2.mp4"), source_duration=60.0, start_time=30.0, end_time=45.0)
    timeline.segments = [s1, s2]

    # Playhead at 14.0s global (which is 4.0s into s2)
    current_global_time = 14.0
    seg_idx = timeline.get_segment_index(s2.id)
    seg_global_start = timeline.get_segment_global_start(seg_idx)
    assert seg_global_start == 10.0

    offset = clamp(current_global_time - seg_global_start, 0.0, s2.duration)
    assert offset == 4.0
    seg_source_time = s2.start_time + offset
    assert seg_source_time == 34.0


def test_lossless_export_audio_parity_checks():
    """Verify can_lossless_export checks audio sample_rate and channels."""
    s1 = Segment(id="s1", source_path=Path("/tmp/v1.mp4"), source_duration=10.0)
    s2 = Segment(id="s2", source_path=Path("/tmp/v2.mp4"), source_duration=10.0)

    sig_base = {
        "video_codec": "h264",
        "width": 1920,
        "height": 1080,
        "pix_fmt": "yuv420p",
        "has_audio": True,
        "audio_codec": "aac",
        "sample_rate": 48000,
        "channels": 2,
    }

    # Same audio specs -> lossless allowed
    with patch("video_cut.services.probe_service.ProbeService.get_video_codec_signature") as mock_sig:
        mock_sig.side_effect = [dict(sig_base), dict(sig_base)]
        can_lossless, reason = ExportService.can_lossless_export([s1, s2])
        assert can_lossless is True
        assert reason == ""

    # Different sample rate -> lossless rejected
    sig_diff_sample = dict(sig_base)
    sig_diff_sample["sample_rate"] = 44100
    with patch("video_cut.services.probe_service.ProbeService.get_video_codec_signature") as mock_sig:
        mock_sig.side_effect = [dict(sig_base), dict(sig_diff_sample)]
        can_lossless, reason = ExportService.can_lossless_export([s1, s2])
        assert can_lossless is False
        assert "audio" in reason.lower()

    # Different channels -> lossless rejected
    sig_diff_channels = dict(sig_base)
    sig_diff_channels["channels"] = 6
    with patch("video_cut.services.probe_service.ProbeService.get_video_codec_signature") as mock_sig:
        mock_sig.side_effect = [dict(sig_base), dict(sig_diff_channels)]
        can_lossless, reason = ExportService.can_lossless_export([s1, s2])
        assert can_lossless is False
        assert "audio" in reason.lower()


def test_translate_widget_coords():
    """Verify _translate_widget_coords gracefully handles widgets and coordinates."""
    box_parent = Gtk.Box()
    box_child1 = Gtk.Box()
    box_child2 = Gtk.Box()
    box_parent.append(box_child1)
    box_parent.append(box_child2)

    # Calling before realized should return None or fallback gracefully without raising
    res = _translate_widget_coords(box_child1, box_child2, 10.0, 20.0)
    assert res is None or isinstance(res, tuple)
