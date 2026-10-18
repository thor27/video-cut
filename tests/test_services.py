"""Unit tests for probe_service and thumbnail_service."""

import pytest
from pathlib import Path
from video_cut.services.probe_service import ProbeService
from video_cut.services.thumbnail_service import ThumbnailService


def test_probe_service():
    p = Path("/tmp/test_video.mp4")
    meta = ProbeService.probe(p)
    assert meta is not None
    assert meta.width == 640
    assert meta.height == 360
    assert abs(meta.duration - 5.0) < 0.1
    assert meta.fps > 0


def test_thumbnail_service_sync_worker(tmp_path):
    service = ThumbnailService(cache_dir=tmp_path)
    p = Path("/tmp/test_video.mp4")

    received = []
    service.generate_thumbnails_async(p, 5.0, lambda path, items: received.append(items))

    # Wait for thread
    service.executor.shutdown(wait=True)

    # Check cached results
    thumbs = service.get_thumbnails_for_segment(p, 1.0, 3.0, max_thumbs=4)
    assert len(thumbs) == 4
    for t in thumbs:
        assert t is not None
        assert t.exists()


def test_thumbnail_service_timeline_positions_and_placeholders(tmp_path):
    service = ThumbnailService(cache_dir=tmp_path)
    p = Path("/tmp/test_video.mp4")

    # 1. When cache is completely empty, all requested slots are None (placeholders)
    empty_thumbs = service.get_thumbnails_for_segment(p, 0.0, 5.0, max_thumbs=5)
    assert len(empty_thumbs) == 5
    assert all(t is None for t in empty_thumbs)

    # 2. Simulate initial frame only at t=0.0
    key = service._file_key(p)
    dummy_first = tmp_path / "first.jpg"
    dummy_first.write_bytes(b"dummy")
    service._cache[key] = [(0.0, dummy_first)]

    # Slot 0 (t=0.0) matches, while slots 1..4 (>2.0s away) return None (placeholders)
    partial_thumbs = service.get_thumbnails_for_segment(p, 0.0, 5.0, max_thumbs=5)
    assert len(partial_thumbs) == 5
    assert partial_thumbs[0] == dummy_first
    assert partial_thumbs[1] is None
    assert partial_thumbs[2] is None
    assert partial_thumbs[3] is None
    assert partial_thumbs[4] is None

    # 3. Simulate fully populated uniform cache: frames at 0.0, 1.0, 2.0, 3.0, 4.0, 4.95
    frames = []
    for ts in [0.0, 1.0, 2.0, 3.0, 4.0, 4.95]:
        fp = tmp_path / f"thumb_{int(ts*100):04d}.jpg"
        fp.write_bytes(b"dummy")
        frames.append((ts, fp))
    service._cache[key] = frames

    full_thumbs = service.get_thumbnails_for_segment(p, 0.0, 5.0, max_thumbs=4)
    assert len(full_thumbs) == 4
    # Slot 0 is start (0.0) -> thumb at 0.0
    assert full_thumbs[0] == frames[0][1]
    # Slot 1 is 1/3 (~1.67) -> closest is frame at 2.0
    assert full_thumbs[1] == frames[2][1]
    # Slot 2 is 2/3 (~3.33) -> closest is frame at 3.0
    assert full_thumbs[2] == frames[3][1]
    # Slot 3 is end (~4.96) -> closest is final frame at 4.95
    assert full_thumbs[3] == frames[-1][1]

    # 4. Extract exact thumbnail async
    service.extract_exact_thumbnail_async(p, 2.5)
    service.executor.shutdown(wait=True)
    exact_match = min(service._cache[key], key=lambda x: abs(x[0] - 2.5))
    assert abs(exact_match[0] - 2.5) < 0.01
    assert exact_match[1].exists()


def test_thumbnail_service_progress_and_cache(tmp_path):
    service = ThumbnailService(cache_dir=tmp_path)
    p = Path("/tmp/test_video.mp4")

    progress_ticks = []
    received = []

    def on_prog(completed, total, path):
        progress_ticks.append((completed, total))

    service.generate_thumbnails_async(
        p,
        5.0,
        on_done=lambda path, items: received.append(items),
        on_progress=on_prog,
    )

    service.executor.shutdown(wait=True)
    service.wait_idle()

    # Dispatch queued GLib idle_add events
    from gi.repository import GLib
    ctx = GLib.MainContext.default()
    for _ in range(100):
        if not ctx.iteration(False):
            break

    # Progress ticks should have fired
    assert len(progress_ticks) > 0
    last_completed, last_total = progress_ticks[-1]
    assert last_completed == last_total

    # Verify index.json was created for instant reloads
    key = service._file_key(p)
    index_file = tmp_path / key / "index.json"
    assert index_file.exists()

    # Create a new service instance pointing to the same cache_dir to test instant disk reload
    service2 = ThumbnailService(cache_dir=tmp_path)
    reloaded = []
    service2.generate_thumbnails_async(p, 5.0, on_done=lambda path, items: reloaded.append(items))
    service2.executor.shutdown(wait=True)
    service2.wait_idle()

    assert len(service2._cache[key]) == last_total


def test_image_thumbnail_large_timeline_slots(tmp_path):
    """Ensure imported image videos with large timeline representation resolve all slots."""
    service = ThumbnailService(cache_dir=tmp_path)
    video_path = Path("/tmp/imported_image_video.mp4")
    image_path = tmp_path / "sample_image.png"
    image_path.write_bytes(b"dummy_png_data")

    # Register 3.0s image video
    service.register_image_thumbnail(video_path, image_path, duration=3.0)

    # Request 11 slots (typical for a wide timeline)
    thumbs = service.get_thumbnails_for_segment(video_path, 0.0, 3.0, max_thumbs=11)
    assert len(thumbs) == 11
    # None of the slots should be None (no stuck spinners)
    for i, t in enumerate(thumbs):
        assert t is not None, f"Slot {i} unexpectedly returned None"
        assert t == image_path


def test_short_video_timeline_slots_and_sampling(tmp_path):
    """Ensure short videos sample across duration and all timeline slots resolve when complete."""
    service = ThumbnailService(cache_dir=tmp_path)
    p = Path("/tmp/test_video.mp4")

    # Check sample times for short video (3.0s)
    # Generate thumbnails asynchronously
    received = []
    service.generate_thumbnails_async(p, 3.0, on_done=lambda path, items: received.append(items))
    service.executor.shutdown(wait=True)
    service.wait_idle()

    # Verify at least 6 samples were extracted across the 3.0s duration
    key = service._file_key(p)
    cached_items = service._cache[key]
    assert len(cached_items) >= 6

    # Verify all 11 slots on a wide timeline representation resolve to valid thumbnails
    thumbs = service.get_thumbnails_for_segment(p, 0.0, 3.0, max_thumbs=11)
    assert len(thumbs) == 11
    for i, t in enumerate(thumbs):
        assert t is not None, f"Slot {i} unexpectedly returned None"
        assert t.exists()


def test_video_larger_than_one_minute_samples_first_frame_at_10s(tmp_path):
    """Ensure videos larger than 1 minute sample the first frame from the 10s mark."""
    import subprocess
    video_path = tmp_path / "long_movie.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=duration=70:size=160x90:rate=10",
        "-c:v", "libx264", str(video_path)
    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    service = ThumbnailService(cache_dir=tmp_path / "cache")

    # Generate thumbnails for a 70s video (> 1 minute)
    received = []
    first_thumb_captured = []

    def on_first(path, thumb_path):
        first_thumb_captured.append(thumb_path)

    service.generate_thumbnails_async(
        video_path,
        duration=70.0,
        on_done=lambda path, items: received.append(items),
        on_first_thumb=on_first,
    )
    service.executor.shutdown(wait=True)
    service.wait_idle()

    # Dispatch queued GLib idle_add events
    from gi.repository import GLib
    ctx = GLib.MainContext.default()
    for _ in range(50):
        if not ctx.iteration(False):
            break

    key = service._file_key(video_path)
    cached_items = service._cache[key]
    assert len(cached_items) >= 6

    # Verify first sampled frame is at 10.0s (not 0.0s)
    first_ts, first_path = cached_items[0]
    assert abs(first_ts - 10.0) < 0.01
    assert "thumb_000_10000" in first_path.name
    assert first_path.exists()

    # If on_first_thumb fired, it should be the 10s thumbnail
    if first_thumb_captured:
        assert "thumb_000_10000" in first_thumb_captured[0].name

    # Check segment thumbnails: slot 0 for segment spanning (0.0, 70.0) should map to the 10s frame
    thumbs = service.get_thumbnails_for_segment(video_path, 0.0, 70.0, max_thumbs=8)
    assert len(thumbs) == 8
    assert thumbs[0] == first_path


def test_video_one_minute_or_less_samples_first_frame_at_zero(tmp_path):
    """Ensure videos 1 minute or less sample the first frame at 0.0s."""
    service = ThumbnailService(cache_dir=tmp_path)
    video_path = Path("/tmp/test_video.mp4")

    received = []
    service.generate_thumbnails_async(
        video_path,
        duration=60.0,
        on_done=lambda path, items: received.append(items),
    )
    service.executor.shutdown(wait=True)
    service.wait_idle()

    key = service._file_key(video_path)
    cached_items = service._cache[key]
    first_ts, first_path = cached_items[0]
    assert abs(first_ts - 0.0) < 0.01
    assert "thumb_000_0" in first_path.name




