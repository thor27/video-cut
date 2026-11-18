"""Unit tests for utils.py."""

from pathlib import Path

from video_cut.utils import get_ffmpeg_path, get_ffprobe_path


def test_get_ffmpeg_path_snap(monkeypatch, tmp_path: Path):
    snap_dir = tmp_path / "snap"
    snap_bin = snap_dir / "usr" / "bin" / "ffmpeg"
    snap_bin.parent.mkdir(parents=True)
    snap_bin.write_text("#!/bin/sh\n")
    snap_bin.chmod(0o755)

    monkeypatch.setenv("SNAP", str(snap_dir))
    monkeypatch.delenv("APPDIR", raising=False)
    assert get_ffmpeg_path() == str(snap_bin)


def test_get_ffmpeg_path_appdir(monkeypatch, tmp_path: Path):
    appdir = tmp_path / "appdir"
    appdir_bin = appdir / "usr" / "bin" / "ffmpeg"
    appdir_bin.parent.mkdir(parents=True)
    appdir_bin.write_text("#!/bin/sh\n")
    appdir_bin.chmod(0o755)

    monkeypatch.delenv("SNAP", raising=False)
    monkeypatch.setenv("APPDIR", str(appdir))
    assert get_ffmpeg_path() == str(appdir_bin)


def test_get_ffprobe_path_snap(monkeypatch, tmp_path: Path):
    snap_dir = tmp_path / "snap"
    snap_bin = snap_dir / "usr" / "bin" / "ffprobe"
    snap_bin.parent.mkdir(parents=True)
    snap_bin.write_text("#!/bin/sh\n")
    snap_bin.chmod(0o755)

    monkeypatch.setenv("SNAP", str(snap_dir))
    monkeypatch.delenv("APPDIR", raising=False)
    assert get_ffprobe_path() == str(snap_bin)


def test_get_ffprobe_path_appdir(monkeypatch, tmp_path: Path):
    appdir = tmp_path / "appdir"
    appdir_bin = appdir / "usr" / "bin" / "ffprobe"
    appdir_bin.parent.mkdir(parents=True)
    appdir_bin.write_text("#!/bin/sh\n")
    appdir_bin.chmod(0o755)

    monkeypatch.delenv("SNAP", raising=False)
    monkeypatch.setenv("APPDIR", str(appdir))
    assert get_ffprobe_path() == str(appdir_bin)


def test_get_ffmpeg_and_ffprobe_path_system_fallback(monkeypatch):
    monkeypatch.delenv("SNAP", raising=False)
    monkeypatch.delenv("APPDIR", raising=False)
    assert "ffmpeg" in get_ffmpeg_path()
    assert "ffprobe" in get_ffprobe_path()
