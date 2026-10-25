"""Benchmark tests for timeline computation, thumbnail hashing, and export filters."""

from pathlib import Path

from video_cut.models import ExportSettings, ProjectTimeline
from video_cut.services.export_service import ExportService
from video_cut.services.thumbnail_service import ThumbnailService
from video_cut.timeline_widget import compute_segment_widths


def test_benchmark_compute_segment_widths_constrained(benchmark):
    """Benchmark timeline layout width calculation under constrained width."""
    durations = [12.5, 45.0, 3.2, 120.0, 15.8, 8.4, 95.0, 60.0] * 10  # 80 segments
    from video_cut.models import Segment
    segments = [
        Segment(source_path=Path(f"/tmp/v_{i}.mp4"), source_duration=d, start_time=0.0, end_time=d)
        for i, d in enumerate(durations)
    ]
    result = benchmark(compute_segment_widths, segments, 1200, 50)
    assert len(result) == 80


def test_benchmark_compute_segment_widths_proportional(benchmark):
    """Benchmark timeline layout width calculation for multi-segment projects."""
    durations = [12.5, 45.0, 3.2, 120.0, 15.8, 8.4, 95.0, 60.0] * 10  # 80 segments
    from video_cut.models import Segment
    segments = [
        Segment(source_path=Path(f"/tmp/v_{i}.mp4"), source_duration=d, start_time=0.0, end_time=d)
        for i, d in enumerate(durations)
    ]
    result = benchmark(compute_segment_widths, segments, 5000, 50)
    assert len(result) == 80


def test_benchmark_thumbnail_cache_hash(benchmark):
    """Benchmark thumbnail cache file key generation."""
    ts = ThumbnailService()
    p = Path("/tmp/sample_video_long_path_name.mp4")
    result = benchmark(ts._file_key, p)
    assert isinstance(result, str) and len(result) == 32


def test_benchmark_get_thumbnails_for_segment(benchmark, tmp_path):
    """Benchmark binary search lookup of thumbnails across 500 cached frames."""
    ts = ThumbnailService(cache_dir=tmp_path)
    p = Path("/tmp/sample.mp4")
    key = ts._file_key(p)
    # Populate cache with 500 sorted thumbnails
    fake_items = [(float(i * 0.5), tmp_path / f"thumb_{i}.jpg") for i in range(500)]
    for _, path in fake_items:
        path.touch()
    with ts._cache_lock:
        ts._cache[key] = fake_items
        ts._completed_keys.add(key)

    result = benchmark(ts.get_thumbnails_for_segment, p, 10.0, 75.0, max_thumbs=16)
    assert len(result) == 16


def test_benchmark_export_filter_complex_generation(benchmark):
    """Benchmark complex filtergraph construction for large timeline."""
    es = ExportService()
    timeline = ProjectTimeline()
    for i in range(25):
        s = timeline.add_video(f"/tmp/v_{i}.mp4", 10.0)
        if i % 2 == 0:
            s.crop = (0.1, 0.1, 0.8, 0.8)
        if i % 3 == 0:
            s.rotation = 90
    settings = ExportSettings(container="mp4", video_codec="libx264", crf=23, preset="fast")

    cmd = benchmark(es.build_ffmpeg_command, timeline.segments, Path("/tmp/out.mp4"), settings=settings)
    assert "-filter_complex" in cmd
