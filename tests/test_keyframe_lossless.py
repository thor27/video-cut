"""Comprehensive tests for lossless export, keyframe probing, snapping, and help dialog."""

from dataclasses import replace
from pathlib import Path
import time

import gi
import pytest

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk

import subprocess

from video_cut.advanced_export_dialog import AdvancedExportDialog
from video_cut.keyframe_help_dialog import KeyframeHelpDialog
from video_cut.models import ExportSettings, OverlayItem, Segment
from video_cut.services.export_service import ExportService
from video_cut.services.probe_service import ProbeService
from video_cut.window import MainWindow


@pytest.fixture(scope="session")
def sample_keyframe_video(tmp_path_factory) -> Path:
    """Five second H.264 clip with keyframes every 1 second (GOP=30)."""
    p = tmp_path_factory.mktemp("keyframe_media") / "test_gop30.mp4"
    subprocess.run(
        [
            "ffmpeg", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc=size=640x360:rate=30",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
            "-t", "5.0",
            "-g", "30", "-keyint_min", "30", "-sc_threshold", "0",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            str(p),
        ],
        check=True,
    )
    return p


def test_probe_keyframe_timestamps(sample_video):
    keyframes = ProbeService.get_keyframe_timestamps(sample_video)
    assert isinstance(keyframes, list)
    assert len(keyframes) > 0
    assert keyframes[0] == 0.0
    assert all(isinstance(k, float) for k in keyframes)

    # Verify caching
    cached = ProbeService.get_keyframe_timestamps(sample_video)
    assert cached is keyframes


def test_find_nearest_and_adjacent_keyframe():
    kfs = [0.0, 1.5, 3.0, 4.5]

    # Empty list fallback
    assert ProbeService.find_nearest_keyframe([], 2.0) == 2.0
    assert ProbeService.find_adjacent_keyframe([], 2.0, 1) is None

    # Nearest
    assert ProbeService.find_nearest_keyframe(kfs, 0.2) == 0.0
    assert ProbeService.find_nearest_keyframe(kfs, 1.4) == 1.5
    assert ProbeService.find_nearest_keyframe(kfs, 2.9) == 3.0
    assert ProbeService.find_nearest_keyframe(kfs, 4.4) == 4.5
    assert ProbeService.find_nearest_keyframe(kfs, 10.0) == 4.5

    # Adjacent forwards (+1)
    assert ProbeService.find_adjacent_keyframe(kfs, 0.0, 1) == 1.5
    assert ProbeService.find_adjacent_keyframe(kfs, 1.0, 1) == 1.5
    assert ProbeService.find_adjacent_keyframe(kfs, 3.0, 1) == 4.5
    assert ProbeService.find_adjacent_keyframe(kfs, 4.5, 1) is None

    # Adjacent backwards (-1)
    assert ProbeService.find_adjacent_keyframe(kfs, 4.5, -1) == 3.0
    assert ProbeService.find_adjacent_keyframe(kfs, 3.2, -1) == 3.0
    assert ProbeService.find_adjacent_keyframe(kfs, 1.5, -1) == 0.0
    assert ProbeService.find_adjacent_keyframe(kfs, 0.0, -1) is None


def test_get_video_codec_signature(sample_video):
    sig = ProbeService.get_video_codec_signature(sample_video)
    assert sig is not None
    assert sig["video_codec"] == "h264"
    assert sig["width"] == 640
    assert sig["height"] == 360
    assert sig["pix_fmt"] == "yuv420p"
    assert sig["has_audio"] is True
    assert sig["audio_codec"] == "aac"


def test_can_lossless_export_eligibility(sample_video, sample_video2):
    service = ExportService()

    seg1 = Segment(source_path=sample_video, source_duration=5.0, start_time=0.0, end_time=2.0)
    seg2 = Segment(source_path=sample_video, source_duration=5.0, start_time=2.5, end_time=4.5)

    # 1. Clean identical video segments: Eligible
    can_lossless, reason = service.can_lossless_export([seg1, seg2])
    assert can_lossless is True
    assert reason == ""

    # 2. Empty list: Ineligible
    can_lossless, reason = service.can_lossless_export([])
    assert can_lossless is False

    # 3. Rotation applied: Ineligible
    seg_rot = replace(seg1, rotation=90)
    can_lossless, reason = service.can_lossless_export([seg_rot])
    assert can_lossless is False
    assert "transform" in reason.lower() or "rotation" in reason.lower()

    # 4. Flip applied: Ineligible
    seg_flip = replace(seg1, hflip=True)
    can_lossless, reason = service.can_lossless_export([seg_flip])
    assert can_lossless is False

    # 5. Crop applied: Ineligible
    seg_crop = replace(seg1, crop=(0.1, 0.1, 0.8, 0.8))
    can_lossless, reason = service.can_lossless_export([seg_crop])
    assert can_lossless is False
    assert "crop" in reason.lower()

    # 6. Overlay applied: Ineligible
    seg_overlay = replace(seg1, overlays=[OverlayItem(text="Watermark")])
    can_lossless, reason = service.can_lossless_export([seg_overlay])
    assert can_lossless is False
    assert "overlay" in reason.lower()

    # 7. Non-existent file: Ineligible
    seg_bad = replace(seg1, source_path=Path("/path/to/missing_video_12345.mp4"))
    can_lossless, reason = service.can_lossless_export([seg_bad])
    assert can_lossless is False


def test_lossless_export_single_segment(sample_video, tmp_path):
    service = ExportService()
    dest = tmp_path / "lossless_single.mp4"
    kfs = ProbeService.get_keyframe_timestamps(sample_video)
    s_start = kfs[0]
    s_end = kfs[1] if len(kfs) > 1 else 2.0

    seg = Segment(source_path=sample_video, source_duration=5.0, start_time=s_start, end_time=s_end)
    done_result = {}

    def on_done(success: bool, err: str | None):
        done_result["success"] = success
        done_result["error"] = err

    service.export_async(
        segments=[seg],
        output_path=dest,
        on_progress=lambda f, s: None,
        on_done=on_done,
        settings=ExportSettings(container="mp4", lossless=True),
    )

    ctx = GLib.MainContext.default()
    t_end = time.time() + 10.0
    while "success" not in done_result and time.time() < t_end:
        while ctx.pending():
            ctx.iteration(False)
        time.sleep(0.02)

    assert done_result.get("success") is True, f"Lossless export failed: {done_result.get('error')}"
    assert dest.exists()
    assert dest.stat().st_size > 0
    meta = ProbeService.probe(dest)
    assert meta is not None
    assert meta.width == 640
    assert meta.height == 360


def test_lossless_export_multi_segment(sample_video, tmp_path):
    service = ExportService()
    dest = tmp_path / "lossless_multi.mp4"
    kfs = ProbeService.get_keyframe_timestamps(sample_video)
    if len(kfs) >= 3:
        seg1 = Segment(source_path=sample_video, source_duration=5.0, start_time=kfs[0], end_time=kfs[1])
        seg2 = Segment(source_path=sample_video, source_duration=5.0, start_time=kfs[1], end_time=kfs[2])
    else:
        seg1 = Segment(source_path=sample_video, source_duration=5.0, start_time=0.0, end_time=2.0)
        seg2 = Segment(source_path=sample_video, source_duration=5.0, start_time=2.0, end_time=4.0)

    done_result = {}

    def on_done(success: bool, err: str | None):
        done_result["success"] = success
        done_result["error"] = err

    service.export_async(
        segments=[seg1, seg2],
        output_path=dest,
        on_progress=lambda f, s: None,
        on_done=on_done,
        settings=ExportSettings(container="mp4", lossless=True),
    )

    ctx = GLib.MainContext.default()
    t_end = time.time() + 15.0
    while "success" not in done_result and time.time() < t_end:
        while ctx.pending():
            ctx.iteration(False)
        time.sleep(0.02)

    assert done_result.get("success") is True, f"Lossless multi-segment export failed: {done_result.get('error')}"
    assert dest.exists()
    assert dest.stat().st_size > 0


def test_advanced_export_dialog_lossless_toggle():
    win = Gtk.Window()
    exported_settings: list[ExportSettings] = []

    def on_export(settings: ExportSettings):
        exported_settings.append(settings)

    dialog = AdvancedExportDialog(
        parent=win,
        on_export=on_export,
        source_width=1920,
        source_height=1080,
        initial_lossless=False,
    )

    # Initial state: lossless is off
    assert dialog.row_lossless.get_active() is False
    assert dialog.card_lossless_info.get_visible() is False
    assert dialog.grp_video.get_sensitive() is True
    assert dialog.grp_audio.get_sensitive() is True

    # Toggle lossless on
    dialog.row_lossless.set_active(True)
    assert dialog.card_lossless_info.get_visible() is True
    assert dialog.grp_video.get_sensitive() is False
    assert dialog.grp_audio.get_sensitive() is False

    # Trigger export
    dialog._on_export_clicked()
    assert len(exported_settings) == 1
    assert exported_settings[0].lossless is True

    # Test initial_lossless=True
    dialog2 = AdvancedExportDialog(
        parent=win,
        on_export=on_export,
        initial_lossless=True,
    )
    assert dialog2.row_lossless.get_active() is True
    assert dialog2.card_lossless_info.get_visible() is True


def test_keyframe_help_dialog():
    win = Gtk.Window()
    dialog = KeyframeHelpDialog(parent=win)
    assert dialog.get_title() == "Keyframes & Lossless Cutting"
    assert dialog.get_modal() is True


def test_main_window_keyframe_snap_and_undo(sample_keyframe_video):
    app = Adw.Application(application_id="io.github.thor27.video-cut.test.keyframe")
    test_error = None

    def run_test(app):
        nonlocal test_error
        win = None
        try:
            win = MainWindow(application=app)
            win.load_video(sample_keyframe_video, replace=True)

            def wait_for(condition_fn, timeout=10.0):
                ctx = GLib.MainContext.default()
                t_end = time.time() + timeout
                while not condition_fn() and time.time() < t_end:
                    while ctx.pending():
                        ctx.iteration(False)
                    time.sleep(0.02)
                assert condition_fn()

            wait_for(lambda: len(win.timeline.segments) == 1)

            # Move cursor to 1.1s (between keyframes) and split
            win.current_global_time = 1.1
            win.split_at_cursor()
            assert len(win.timeline.segments) == 2

            seg0_orig_end = win.timeline.segments[0].end_time
            seg1_orig_start = win.timeline.segments[1].start_time

            # Toggle keyframe snap
            win._on_toggle_keyframe_snap(True)
            assert win.snap_to_keyframe is True

            kfs = ProbeService.get_keyframe_timestamps(sample_keyframe_video)
            nearest_kf = ProbeService.find_nearest_keyframe(kfs, 1.1)

            # Cut should now be snapped to nearest keyframe (wait for async indexing if needed)
            wait_for(lambda: abs(win.timeline.segments[0].end_time - nearest_kf) < 1e-3)
            assert abs(win.timeline.segments[1].start_time - nearest_kf) < 1e-3

            # Undo (Ctrl+Z) should revert cut back to orig
            win.undo()
            assert abs(win.timeline.segments[0].end_time - seg0_orig_end) < 1e-3
            assert abs(win.timeline.segments[1].start_time - seg1_orig_start) < 1e-3

            # Redo restores the snapped cut
            win.redo()
            assert abs(win.timeline.segments[0].end_time - nearest_kf) < 1e-3

            # Test stepping to adjacent keyframe
            win.current_global_time = nearest_kf
            win._on_step_frame(1)
            next_kf = ProbeService.find_adjacent_keyframe(kfs, nearest_kf, 1)
            if next_kf is not None:
                assert abs(win.current_global_time - next_kf) < 1e-3

            # Test stepping back to keyframe
            win._on_step_frame(-1)
            assert abs(win.current_global_time - nearest_kf) < 1e-3

        except Exception as exc:
            test_error = exc
        finally:
            if win:
                win.close()
            app.quit()

    app.connect("activate", run_test)
    app.run(["test"])
    if test_error:
        raise test_error


def test_prefetch_keyframes_async(sample_keyframe_video):
    ProbeService.clear_cache()
    assert ProbeService.is_keyframes_cached(sample_keyframe_video) is False

    done_kfs: list[float] = []

    def on_done(kfs: list[float]):
        done_kfs.extend(kfs)

    ProbeService.prefetch_keyframes_async(sample_keyframe_video, on_done=on_done)

    ctx = GLib.MainContext.default()
    t_end = time.time() + 5.0
    while not done_kfs and time.time() < t_end:
        while ctx.pending():
            ctx.iteration(False)
        time.sleep(0.02)

    assert len(done_kfs) > 1
    assert done_kfs[0] == 0.0
    assert ProbeService.is_keyframes_cached(sample_keyframe_video) is True


def test_keyframe_snap_split_and_stepping_full(sample_keyframe_video):
    app = Adw.Application(application_id="io.github.thor27.video-cut.test.keyframe_split_step")
    test_error = None

    def run_test(app):
        nonlocal test_error
        win = None
        try:
            win = MainWindow(application=app)
            win.load_video(sample_keyframe_video, replace=True)

            def wait_for(condition_fn, timeout=10.0):
                ctx = GLib.MainContext.default()
                t_end = time.time() + timeout
                while not condition_fn() and time.time() < t_end:
                    while ctx.pending():
                        ctx.iteration(False)
                    time.sleep(0.02)
                assert condition_fn()

            wait_for(lambda: len(win.timeline.segments) == 1)

            # Enable keyframe snap
            win._on_toggle_keyframe_snap(True)
            assert win.snap_to_keyframe is True
            wait_for(lambda: ProbeService.is_keyframes_cached(sample_keyframe_video))

            # Position playhead at 2.4s and split with keyframe snap active
            win.current_global_time = 2.4
            win.split_at_cursor()

            # Split should succeed and snap to nearest internal keyframe (2.0s)
            assert len(win.timeline.segments) == 2
            assert abs(win.timeline.segments[0].end_time - 2.0) < 0.05
            assert abs(win.timeline.segments[1].start_time - 2.0) < 0.05

            # Step forward through keyframes across segments
            win.current_global_time = 0.0
            win._on_step_frame(1)
            assert abs(win.current_global_time - 1.0) < 0.05

            win._on_step_frame(1)
            assert abs(win.current_global_time - 2.0) < 0.05

            win._on_step_frame(1)
            assert abs(win.current_global_time - 3.0) < 0.05

            # Step backward through keyframes
            win._on_step_frame(-1)
            assert abs(win.current_global_time - 2.0) < 0.05

            win._on_step_frame(-1)
            assert abs(win.current_global_time - 1.0) < 0.05

        except Exception as exc:
            test_error = exc
        finally:
            if win:
                win.close()
            app.quit()

    app.connect("activate", run_test)
    app.run(["test"])
    if test_error:
        raise test_error


def test_timeline_scrub_snaps_and_stays_on_keyframe(sample_keyframe_video):
    app = Adw.Application(application_id="io.github.thor27.video-cut.test.keyframe_scrub_snap")
    test_error = None

    def run_test(app):
        nonlocal test_error
        win = None
        try:
            win = MainWindow(application=app)
            win.load_video(sample_keyframe_video, replace=True)

            def wait_for(condition_fn, timeout=10.0):
                ctx = GLib.MainContext.default()
                t_end = time.time() + timeout
                while not condition_fn() and time.time() < t_end:
                    while ctx.pending():
                        ctx.iteration(False)
                    time.sleep(0.02)
                assert condition_fn()

            wait_for(lambda: len(win.timeline.segments) == 1)

            # Enable keyframe snap
            win._on_toggle_keyframe_snap(True)
            assert win.snap_to_keyframe is True
            wait_for(lambda: ProbeService.is_keyframes_cached(sample_keyframe_video))

            tl = win.timeline_widget
            pts = tl.get_timeline_keyframe_points()
            assert len(pts) >= 5

            # 1. Test snap_time_to_keyframe direct mapping
            assert abs(tl.snap_time_to_keyframe(0.2) - 0.0) < 1e-3
            assert abs(tl.snap_time_to_keyframe(0.4) - 0.0) < 1e-3
            assert abs(tl.snap_time_to_keyframe(0.6) - 1.0) < 1e-3
            assert abs(tl.snap_time_to_keyframe(1.2) - 1.0) < 1e-3
            assert abs(tl.snap_time_to_keyframe(1.4) - 1.0) < 1e-3
            assert abs(tl.snap_time_to_keyframe(1.6) - 2.0) < 1e-3
            assert abs(tl.snap_time_to_keyframe(2.3) - 2.0) < 1e-3
            assert abs(tl.snap_time_to_keyframe(2.7) - 3.0) < 1e-3

            # 2. Test timeline needle movement during scrubbing
            seg = win.timeline.segments[0]
            card = tl._cards[seg.id]
            w = float(card.filmstrip_box.get_width() if card.filmstrip_box.get_width() > 0 else 100)

            # Start scrub at 0.2s (ratio = 0.2 / 5.0)
            x_0_2 = (0.2 / seg.duration) * w
            tl._on_scrub_start(card, x_0_2, 10.0)
            assert abs(tl._current_time - 0.0) < 1e-3

            # Move scrub to 0.4s: vertical bar MUST stay at 0.0s!
            x_0_4 = (0.4 / seg.duration) * w
            tl._on_scrub_update(card, x_0_4, 10.0)
            assert abs(tl._current_time - 0.0) < 1e-3

            # Move scrub past halfway to 0.6s: vertical bar MUST snap to 1.0s!
            x_0_6 = (0.6 / seg.duration) * w
            tl._on_scrub_update(card, x_0_6, 10.0)
            assert abs(tl._current_time - 1.0) < 1e-3

            # Move scrub to 1.2s: vertical bar MUST stay at 1.0s!
            x_1_2 = (1.2 / seg.duration) * w
            tl._on_scrub_update(card, x_1_2, 10.0)
            assert abs(tl._current_time - 1.0) < 1e-3

            # Move scrub past halfway to 1.6s: vertical bar MUST snap to 2.0s!
            x_1_6 = (1.6 / seg.duration) * w
            tl._on_scrub_update(card, x_1_6, 10.0)
            assert abs(tl._current_time - 2.0) < 1e-3

            # Move scrub to 2.3s: vertical bar MUST stay at 2.0s!
            x_2_3 = (2.3 / seg.duration) * w
            tl._on_scrub_update(card, x_2_3, 10.0)
            assert abs(tl._current_time - 2.0) < 1e-3

            # End scrub: needle stays at 2.0s
            tl._on_scrub_end()
            assert abs(tl._current_time - 2.0) < 1e-3

        except Exception as exc:
            test_error = exc
        finally:
            if win:
                win.close()
            app.quit()

    app.connect("activate", run_test)
    app.run(["test"])
    if test_error:
        raise test_error


