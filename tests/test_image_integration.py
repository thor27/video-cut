"""Integration tests for image loading, segment manipulation, and export concatenation."""

import subprocess
import time
from pathlib import Path

import gi
import pytest

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk

from video_cut.models import ProjectTimeline
from video_cut.services.export_service import ExportService
from video_cut.services.image_service import ImageService
from video_cut.services.probe_service import ProbeService
from video_cut.window import MainWindow


@pytest.fixture
def sample_image(tmp_path):
    img_path = tmp_path / "photo_banner.png"
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi",
            "-i", "color=c=magenta:s=640x360:d=1",
            "-frames:v", "1",
            str(img_path)
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return img_path


def test_load_image_initial_and_add(sample_image):
    app = Adw.Application(application_id="io.github.thomaz.videocut.test.image")
    test_error = None

    def run_test(app):
        nonlocal test_error
        win = None
        try:
            win = MainWindow(application=app)
            assert win.player_stack.get_visible_child_name() == "empty"

            def wait_for(condition_fn, timeout=10.0):
                ctx = GLib.MainContext.default()
                t_end = time.time() + timeout
                while not condition_fn() and time.time() < t_end:
                    while ctx.pending():
                        ctx.iteration(False)
                    time.sleep(0.02)
                assert condition_fn()

            # 1. Load image as initial media
            win.load_video(sample_image, replace=True)
            wait_for(lambda: len(win.timeline.segments) == 1)
            assert win.player_stack.get_visible_child_name() == "player"

            seg = win.timeline.segments[0]
            assert abs(seg.duration - 1.0) < 0.05
            assert abs(win.timeline.total_duration - 1.0) < 0.05
            assert seg.display_name == sample_image.name
            assert seg.source_name == sample_image.name
            assert seg.stem == sample_image.stem
            assert seg.thumbnails and (seg.thumbnails == [sample_image] or seg.thumbnails[0].exists())
            assert win.window_title.get_title() == sample_image.name

            # Verify export initial filename uses original image stem instead of hex hash
            saved_initial_name = None

            def mock_save(dlg_self, parent, cancellable, callback):
                nonlocal saved_initial_name
                saved_initial_name = dlg_self.get_initial_name()

            orig_save = Gtk.FileDialog.save
            try:
                Gtk.FileDialog.save = mock_save
                win._export_video_dialog()
                assert saved_initial_name == f"{sample_image.stem}_edited.mp4"
            finally:
                Gtk.FileDialog.save = orig_save

            # 2. Add video after the image
            v1 = Path("/tmp/test_video.mp4")
            if v1.exists():
                win.load_video(v1, replace=False)
                assert len(win.timeline.segments) == 2
                assert abs(win.timeline.total_duration - 6.0) < 0.1

                # 3. Add another image
                win.load_video(sample_image, replace=False)
                wait_for(lambda: len(win.timeline.segments) == 3)
                assert abs(win.timeline.total_duration - 7.0) < 0.1
                assert win.timeline.segments[2].display_name == sample_image.name
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



def test_export_concatenation_with_image(tmp_path, sample_image):
    v1 = Path("/tmp/test_video.mp4")
    if not v1.exists():
        pytest.skip("Test video /tmp/test_video.mp4 not found")

    cache_dir = tmp_path / "img_cache"
    img_video = ImageService.convert_image_to_video(sample_image, cache_dir=cache_dir, duration=1.0)
    assert img_video.exists()

    timeline = ProjectTimeline()
    # Segment 1: Video 5s (cut to 2s: 1.0s - 3.0s)
    s1 = timeline.add_video(v1, 5.0)
    s1.start_time = 1.0
    s1.end_time = 3.0

    # Segment 2: Image converted video 1s (full 1.0s)
    s2 = timeline.add_video(img_video, 1.0, display_name=sample_image.name)
    s2.start_time = 0.0
    s2.end_time = 1.0

    assert abs(timeline.total_duration - 3.0) < 0.05

    output_file = tmp_path / "exported_video_with_image.mp4"
    export_service = ExportService()

    done = False
    results = {}

    def on_done(success: bool, error: str | None):
        nonlocal done
        results["success"] = success
        results["error"] = error
        done = True

    export_service.export_async(
        segments=timeline.segments,
        output_path=output_file,
        on_progress=lambda f, c: None,
        on_done=on_done,
    )

    ctx = GLib.MainContext.default()
    timeout = time.time() + 30
    while not done and time.time() < timeout:
        while ctx.pending():
            ctx.iteration(False)
        time.sleep(0.02)

    while ctx.pending():
        ctx.iteration(False)

    assert results.get("success") is True, f"Export failed: {results.get('error')}"
    assert output_file.exists()
    assert output_file.stat().st_size > 0

    meta = ProbeService.probe(output_file)
    assert meta is not None
    assert abs(meta.duration - 3.0) < 0.5


def test_load_image_with_soundtrack_and_duration(tmp_path, sample_image):
    audio_file = tmp_path / "soundtrack.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=10", str(audio_file)],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    app = Adw.Application(application_id="io.github.thomaz.videocut.test.image.soundtrack")
    test_error = None

    def run_test(app):
        nonlocal test_error
        win = None
        try:
            win = MainWindow(application=app)

            def wait_for(condition_fn, timeout=10.0):
                ctx = GLib.MainContext.default()
                t_end = time.time() + timeout
                while not condition_fn() and time.time() < t_end:
                    while ctx.pending():
                        ctx.iteration(False)
                    time.sleep(0.02)
                assert condition_fn()

            # Load image with 4.5s duration and audio slice starting at 2.0s
            win.load_video(
                sample_image,
                replace=True,
                duration=4.5,
                audio_path=audio_file,
                audio_start_time=2.0,
            )
            wait_for(lambda: len(win.timeline.segments) == 1)
            seg = win.timeline.segments[0]
            assert abs(seg.duration - 4.5) < 0.1
            assert abs(win.timeline.total_duration - 4.5) < 0.1
            assert seg.display_name == sample_image.name

            # Verify probe on the generated segment file
            meta = ProbeService.probe(seg.source_path)
            assert meta is not None
            assert abs(meta.duration - 4.5) < 0.2
            aud_dur = ProbeService.probe_duration(seg.source_path)
            assert aud_dur is not None
            assert abs(aud_dur - 4.5) < 0.2
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


