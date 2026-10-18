"""Unit tests for models.py."""

import pytest
from pathlib import Path
from video_cut.models import Segment, ProjectTimeline


def test_segment_creation_and_duration():
    seg = Segment(source_path=Path("/tmp/video.mp4"), source_duration=10.0, start_time=2.0, end_time=7.5)
    assert seg.duration == 5.5
    assert seg.source_name == "video.mp4"


def test_segment_clamping():
    seg = Segment(source_path=Path("/tmp/video.mp4"), source_duration=10.0, start_time=-1.0, end_time=15.0)
    assert seg.start_time == 0.0
    assert seg.end_time == 10.0
    assert seg.duration == 10.0


def test_timeline_duration_and_mapping():
    timeline = ProjectTimeline()
    seg1 = timeline.add_video("/tmp/v1.mp4", 5.0)  # [0, 5]
    seg2 = timeline.add_video("/tmp/v2.mp4", 3.0)  # [0, 3]

    assert timeline.total_duration == 8.0

    # Test time mapping at 2.0s -> seg1 at 2.0s
    idx, seg, ts = timeline.map_global_time(2.0)
    assert idx == 0
    assert seg.id == seg1.id
    assert ts == 2.0

    # Test time mapping at 6.5s -> seg2 at 1.5s
    idx, seg, ts = timeline.map_global_time(6.5)
    assert idx == 1
    assert seg.id == seg2.id
    assert abs(ts - 1.5) < 1e-5

    # Test edge at exactly total_duration
    idx, seg, ts = timeline.map_global_time(8.0)
    assert idx == 1
    assert seg.id == seg2.id
    assert ts == 3.0


def test_timeline_split():
    timeline = ProjectTimeline()
    timeline.add_video("/tmp/v1.mp4", 10.0)

    # Split at global time 4.0s
    res = timeline.split_at(4.0)
    assert res is not None
    left, right = res

    assert len(timeline.segments) == 2
    assert left.start_time == 0.0
    assert left.end_time == 4.0
    assert left.duration == 4.0

    assert right.start_time == 4.0
    assert right.end_time == 10.0
    assert right.duration == 6.0

    assert timeline.total_duration == 10.0


def test_timeline_split_boundary_rejection():
    timeline = ProjectTimeline()
    timeline.add_video("/tmp/v1.mp4", 5.0)

    # Cut too close to start (0.01s)
    assert timeline.split_at(0.01, min_segment_duration=0.1) is None
    # Cut too close to end (4.99s)
    assert timeline.split_at(4.99, min_segment_duration=0.1) is None


def test_timeline_reorder_and_moves():
    timeline = ProjectTimeline()
    s1 = timeline.add_video("/tmp/v1.mp4", 2.0)
    s2 = timeline.add_video("/tmp/v2.mp4", 3.0)
    s3 = timeline.add_video("/tmp/v3.mp4", 4.0)

    # Move s3 left
    assert timeline.move_left(s3.id) is True
    assert [s.id for s in timeline.segments] == [s1.id, s3.id, s2.id]

    # Move s1 right
    assert timeline.move_right(s1.id) is True
    assert [s.id for s in timeline.segments] == [s3.id, s1.id, s2.id]


def test_timeline_delete():
    timeline = ProjectTimeline()
    s1 = timeline.add_video("/tmp/v1.mp4", 2.0)
    s2 = timeline.add_video("/tmp/v2.mp4", 3.0)

    assert timeline.delete_segment(s1.id) is True
    assert len(timeline.segments) == 1
    assert timeline.segments[0].id == s2.id
    assert timeline.selected_segment_id == s2.id


def test_timeline_undo_redo():
    timeline = ProjectTimeline()
    s1 = timeline.add_video("/tmp/v1.mp4", 10.0)

    # 1. Save state before split
    timeline.save_state(global_time=4.0)
    timeline.split_at(4.0)
    assert len(timeline.segments) == 2

    # 2. Undo the split
    restored_time = timeline.undo(current_global_time=4.0)
    assert restored_time == 4.0
    assert len(timeline.segments) == 1
    assert timeline.segments[0].duration == 10.0

    # 3. Redo the split
    restored_redo = timeline.redo(current_global_time=4.0)
    assert restored_redo == 4.0
    assert len(timeline.segments) == 2
    assert timeline.segments[0].duration == 4.0
    assert timeline.segments[1].duration == 6.0
