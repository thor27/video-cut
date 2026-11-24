"""Tests for CLI entrypoint and argument parsing."""

from pathlib import Path

from video_cut.__main__ import parse_args


def test_cli_parse_empty():
    args = parse_args([])
    assert args.files == []


def test_cli_parse_single_file():
    args = parse_args(["/tmp/video.mp4"])
    assert args.files == [Path("/tmp/video.mp4")]


def test_cli_parse_multiple_files():
    args = parse_args(["/tmp/video1.mp4", "/tmp/photo.jpg", "/tmp/video2.mkv"])
    assert args.files == [
        Path("/tmp/video1.mp4"),
        Path("/tmp/photo.jpg"),
        Path("/tmp/video2.mkv"),
    ]


def test_cli_parse_ignores_macos_psn_arg(monkeypatch):
    import sys

    monkeypatch.setattr(sys, "platform", "darwin")
    args = parse_args(["-psn_0_123456"])
    assert args.files == []
