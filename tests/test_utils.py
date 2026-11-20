"""Unit tests for utils.py."""

from pathlib import Path

from video_cut.utils import (
    get_container_ffmpeg_path,
    get_container_ffprobe_path,
    get_ffmpeg_path,
    get_ffprobe_path,
)


def test_get_ffmpeg_path_snap(monkeypatch, tmp_path: Path):
    snap_dir = tmp_path / "snap"
    snap_bin = snap_dir / "usr" / "bin" / "ffmpeg"
    snap_bin.parent.mkdir(parents=True)
    snap_bin.write_text("#!/bin/sh\n")
    snap_bin.chmod(0o755)

    monkeypatch.setenv("SNAP", str(snap_dir))
    monkeypatch.delenv("APPDIR", raising=False)
    assert get_container_ffmpeg_path() == str(snap_bin)

    monkeypatch.setattr("video_cut.utils.get_system_ffmpeg_path", lambda: None)
    assert get_ffmpeg_path() == str(snap_bin)


def test_get_ffmpeg_path_appdir(monkeypatch, tmp_path: Path):
    appdir = tmp_path / "appdir"
    appdir_bin = appdir / "usr" / "bin" / "ffmpeg"
    appdir_bin.parent.mkdir(parents=True)
    appdir_bin.write_text("#!/bin/sh\n")
    appdir_bin.chmod(0o755)

    monkeypatch.delenv("SNAP", raising=False)
    monkeypatch.setenv("APPDIR", str(appdir))
    assert get_container_ffmpeg_path() == str(appdir_bin)

    monkeypatch.setattr("video_cut.utils.get_system_ffmpeg_path", lambda: None)
    assert get_ffmpeg_path() == str(appdir_bin)


def test_get_ffprobe_path_snap(monkeypatch, tmp_path: Path):
    snap_dir = tmp_path / "snap"
    snap_bin = snap_dir / "usr" / "bin" / "ffprobe"
    snap_bin.parent.mkdir(parents=True)
    snap_bin.write_text("#!/bin/sh\n")
    snap_bin.chmod(0o755)

    monkeypatch.setenv("SNAP", str(snap_dir))
    monkeypatch.delenv("APPDIR", raising=False)
    assert get_container_ffprobe_path() == str(snap_bin)

    monkeypatch.setattr("video_cut.utils.get_system_ffprobe_path", lambda: None)
    assert get_ffprobe_path() == str(snap_bin)


def test_get_ffprobe_path_appdir(monkeypatch, tmp_path: Path):
    appdir = tmp_path / "appdir"
    appdir_bin = appdir / "usr" / "bin" / "ffprobe"
    appdir_bin.parent.mkdir(parents=True)
    appdir_bin.write_text("#!/bin/sh\n")
    appdir_bin.chmod(0o755)

    monkeypatch.delenv("SNAP", raising=False)
    monkeypatch.setenv("APPDIR", str(appdir))
    assert get_container_ffprobe_path() == str(appdir_bin)

    monkeypatch.setattr("video_cut.utils.get_system_ffprobe_path", lambda: None)
    assert get_ffprobe_path() == str(appdir_bin)


def test_get_ffmpeg_and_ffprobe_path_system_fallback(monkeypatch):
    monkeypatch.delenv("SNAP", raising=False)
    monkeypatch.delenv("APPDIR", raising=False)
    assert "ffmpeg" in get_ffmpeg_path()
    assert "ffprobe" in get_ffprobe_path()


def test_get_ffmpeg_and_ffprobe_path_macos_homebrew(monkeypatch, tmp_path: Path):
    brew_dir = tmp_path / "homebrew" / "bin"
    brew_dir.mkdir(parents=True)
    brew_ffmpeg = brew_dir / "ffmpeg"
    brew_ffmpeg.write_text("#!/bin/sh\n")
    brew_ffmpeg.chmod(0o755)

    brew_ffprobe = brew_dir / "ffprobe"
    brew_ffprobe.write_text("#!/bin/sh\n")
    brew_ffprobe.chmod(0o755)

    import sys
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.delenv("SNAP", raising=False)
    monkeypatch.delenv("APPDIR", raising=False)

    # Patch the Homebrew candidate paths to check our mock
    monkeypatch.setattr("video_cut.utils.Path", lambda p: brew_ffmpeg if "ffmpeg" in str(p) else brew_ffprobe)
    assert get_ffmpeg_path() == str(brew_ffmpeg)
    assert get_ffprobe_path() == str(brew_ffprobe)


def test_get_ffmpeg_and_ffprobe_path_frozen_bundle(monkeypatch, tmp_path: Path):
    meipass = tmp_path / "bundle"
    bin_dir = meipass / "bin"
    bin_dir.mkdir(parents=True)
    b_ffmpeg = bin_dir / "ffmpeg"
    b_ffmpeg.write_text("#!/bin/sh\n")
    b_ffmpeg.chmod(0o755)

    b_ffprobe = bin_dir / "ffprobe"
    b_ffprobe.write_text("#!/bin/sh\n")
    b_ffprobe.chmod(0o755)

    import sys
    monkeypatch.delenv("SNAP", raising=False)
    monkeypatch.delenv("APPDIR", raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(meipass), raising=False)

    assert get_ffmpeg_path() == str(b_ffmpeg)
    assert get_ffprobe_path() == str(b_ffprobe)

