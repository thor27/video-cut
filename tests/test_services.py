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

