"""Integration test for full end-to-end export on test video files."""

import time
from pathlib import Path

import gi

gi.require_version("GLib", "2.0")
from gi.repository import GLib

from video_cut.models import ProjectTimeline
from video_cut.services.export_service import ExportService
from video_cut.services.probe_service import ProbeService


def test_end_to_end_export(tmp_path):
    v1 = Path("/tmp/test_video.mp4")
    v2 = Path("/tmp/test_video2.mp4")
    assert v1.exists() and v2.exists()

    timeline = ProjectTimeline()
    # Segment 1: v1 [1.0s, 3.0s] (2.0s)
    s1 = timeline.add_video(v1, 5.0)
    s1.start_time = 1.0
    s1.end_time = 3.0

    # Segment 2: v2 [0.5s, 2.5s] (2.0s)
    s2 = timeline.add_video(v2, 3.0)
    s2.start_time = 0.5
    s2.end_time = 2.5

    # Segment 3: v1 [3.5s, 4.5s] (1.0s)
    s3 = timeline.add_video(v1, 5.0)
    s3.start_time = 3.5
    s3.end_time = 4.5

    assert timeline.total_duration == 5.0

    output_file = tmp_path / "combined_output.mp4"
    export_service = ExportService()

    done = False
    results = {}

    def on_progress(fraction: float, cur_s: float):
        results["last_progress"] = (fraction, cur_s)

    def on_done(success: bool, error: str | None):
        nonlocal done
        results["success"] = success
        results["error"] = error
        done = True

    export_service.export_async(
        segments=timeline.segments,
        output_path=output_file,
        on_progress=on_progress,
        on_done=on_done,
    )

    # Pump GLib main loop until done or timeout (10s)
    ctx = GLib.MainContext.default()
    start_wait = time.time()
    while not done and (time.time() - start_wait < 10.0):
        while ctx.pending():
            ctx.iteration(False)
        time.sleep(0.02)

    # Final pump
    while ctx.pending():
        ctx.iteration(False)

    assert done, "Export timed out"
    assert results.get("success") is True, f"Export failed: {results.get('error')}"
    assert output_file.exists()

    # Probe the exported file
    meta = ProbeService.probe(output_file)
    assert meta is not None
    assert abs(meta.duration - 5.0) < 0.2
    assert meta.width > 0 and meta.height > 0
