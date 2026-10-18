"""Unit tests for export_service.py."""

import pytest
from pathlib import Path
from video_cut.models import Segment
from video_cut.services.export_service import ExportService


def test_build_ffmpeg_command_structure():
    service = ExportService()
    seg1 = Segment(source_path=Path("/tmp/test_video.mp4"), source_duration=5.0, start_time=1.0, end_time=3.0)
    seg2 = Segment(source_path=Path("/tmp/test_video2.mp4"), source_duration=3.0, start_time=0.5, end_time=2.5)

    cmd = service.build_ffmpeg_command([seg1, seg2], "/tmp/out.mp4", target_width=640, target_height=360)

    cmd_str = " ".join(cmd)
    assert "ffmpeg -y" in cmd_str
    assert "-i /tmp/test_video.mp4" in cmd_str
    assert "-i /tmp/test_video2.mp4" in cmd_str
    assert "trim=start=1.000:end=3.000" in cmd_str
    assert "trim=start=0.500:end=2.500" in cmd_str
    assert "concat=n=2:v=1:a=1" in cmd_str
    assert "/tmp/out.mp4" in cmd_str


def test_build_ffmpeg_command_single_video_multiple_segments():
    service = ExportService()
    p = Path("/tmp/test_video.mp4")
    seg1 = Segment(source_path=p, source_duration=5.0, start_time=0.0, end_time=2.0)
    seg2 = Segment(source_path=p, source_duration=5.0, start_time=3.0, end_time=5.0)

    cmd = service.build_ffmpeg_command([seg1, seg2], "/tmp/out2.mp4", target_width=640, target_height=360)

    # Single input file should only appear once in -i flags
    inputs = [arg for i, arg in enumerate(cmd) if cmd[i - 1] == "-i"]
    assert len(inputs) == 1
    assert inputs[0] == str(p)


def test_build_ffmpeg_command_fast_seek():
    service = ExportService()
    p = Path("/tmp/test_video.mp4")
    seg1 = Segment(source_path=p, source_duration=60.0, start_time=10.0, end_time=15.0)

    cmd = service.build_ffmpeg_command([seg1], "/tmp/out_fast.mp4", target_width=640, target_height=360, fast_seek=True)

    cmd_str = " ".join(cmd)
    assert "-ss 10.000 -i /tmp/test_video.mp4" in cmd_str
    assert "trim=start=0.000:end=5.000" in cmd_str

