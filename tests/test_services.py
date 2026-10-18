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
    thumbs = service.get_thumbnails_for_segment(p, 1.0, 3.0)
    assert len(thumbs) > 0
    for t in thumbs:
        assert t.exists()
