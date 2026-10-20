"""Tests for advanced export functionality, dynamic capabilities, dialog, and ffmpeg command generation."""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, Gio

from video_cut.models import Segment, ExportSettings
from video_cut.services.export_service import ExportService, FFmpegCapabilities
from video_cut.advanced_export_dialog import AdvancedExportDialog


def test_ffmpeg_capabilities_detection():
    encoders = FFmpegCapabilities.get_encoders()
    assert isinstance(encoders, set)
    assert len(encoders) > 0
    assert "libx264" in encoders

    muxers = FFmpegCapabilities.get_muxers()
    assert isinstance(muxers, set)
    assert len(muxers) > 0
    assert "mp4" in muxers


def test_ffmpeg_capabilities_containers():
    containers = FFmpegCapabilities.get_available_containers()
    keys = [c[0] for c in containers]
    assert "mp4" in keys


def test_ffmpeg_capabilities_compatible_codecs():
    # WebM should only allow VP9 / AV1 and Opus
    webm_v = [c[0] for c in FFmpegCapabilities.get_compatible_video_codecs("webm")]
    assert "libx264" not in webm_v
    assert any(c in webm_v for c in ("libvpx-vp9", "libsvtav1", "libaom-av1"))

    webm_a = [c[0] for c in FFmpegCapabilities.get_compatible_audio_codecs("webm")]
    assert "aac" not in webm_a
    assert "libopus" in webm_a

    # MP4 should allow H.264 and AAC
    mp4_v = [c[0] for c in FFmpegCapabilities.get_compatible_video_codecs("mp4")]
    assert "libx264" in mp4_v
    mp4_a = [c[0] for c in FFmpegCapabilities.get_compatible_audio_codecs("mp4")]
    assert "aac" in mp4_a


def test_build_ffmpeg_command_advanced_settings():
    service = ExportService()
    seg = Segment(source_path=Path("/tmp/clip.mp4"), source_duration=10.0, start_time=1.0, end_time=5.0)

    # 1. Custom resolution, fps, x264, crf 18, preset slow
    settings = ExportSettings(
        container="mp4",
        video_codec="libx264",
        crf=18,
        preset="slow",
        target_width=1280,
        target_height=720,
        fps=30.0,
        audio_codec="aac",
        audio_bitrate="256k",
    )
    cmd = service.build_ffmpeg_command([seg], "/tmp/out.mp4", settings=settings)
    cmd_str = " ".join(cmd)

    assert "scale=1280:720" in cmd_str
    assert "fps=30.000" in cmd_str
    assert "-c:v libx264" in cmd_str
    assert "-crf 18" in cmd_str
    assert "-preset slow" in cmd_str
    assert "-c:a aac" in cmd_str
    assert "-b:a 256k" in cmd_str

    # 2. WebM with VP9
    settings_vp9 = ExportSettings(
        container="webm",
        video_codec="libvpx-vp9",
        crf=26,
        preset="veryfast",
        audio_codec="libopus",
        audio_bitrate="128k",
    )
    cmd_vp9 = service.build_ffmpeg_command([seg], "/tmp/out.webm", settings=settings_vp9)
    cmd_vp9_str = " ".join(cmd_vp9)
    assert "-c:v libvpx-vp9" in cmd_vp9_str
    assert "-crf 26" in cmd_vp9_str
    assert "-c:a libopus" in cmd_vp9_str
    assert "-b:a 128k" in cmd_vp9_str
    assert "-cpu-used 5" in cmd_vp9_str

    # 3. QuickTime MOV with ProRes and FLAC
    settings_prores = ExportSettings(
        container="mov",
        video_codec="prores_ks",
        audio_codec="flac",
    )
    cmd_prores = service.build_ffmpeg_command([seg], "/tmp/out.mov", settings=settings_prores)
    cmd_prores_str = " ".join(cmd_prores)
    assert "-c:v prores_ks" in cmd_prores_str
    assert "-profile:v 3" in cmd_prores_str
    assert "-pix_fmt yuv422p10le" in cmd_prores_str
    assert "-c:a flac" in cmd_prores_str
    assert "-b:a" not in cmd_prores_str  # FLAC does not use bitrate

    # 4. SVT-AV1
    settings_av1 = ExportSettings(
        container="mp4",
        video_codec="libsvtav1",
        crf=21,
        preset="fast",
        audio_codec="aac",
    )
    cmd_av1 = service.build_ffmpeg_command([seg], "/tmp/out.mp4", settings=settings_av1)
    cmd_av1_str = " ".join(cmd_av1)
    assert "-c:v libsvtav1" in cmd_av1_str
    assert "-preset 8" in cmd_av1_str


def test_advanced_export_dialog_ui():
    dummy_parent = Gtk.Window()
    result_settings = None

    def on_export(s: ExportSettings):
        nonlocal result_settings
        result_settings = s

    dlg = AdvancedExportDialog(
        parent=dummy_parent,
        on_export=on_export,
        source_width=1920,
        source_height=1080,
        source_fps=29.97,
    )

    # Check that rows exist
    assert hasattr(dlg, "row_container")
    assert hasattr(dlg, "row_resolution")
    assert hasattr(dlg, "row_fps")
    assert hasattr(dlg, "row_video_codec")
    assert hasattr(dlg, "row_quality")
    assert hasattr(dlg, "row_preset")
    assert hasattr(dlg, "row_audio_codec")
    assert hasattr(dlg, "row_audio_bitrate")

    # Select resolution index 1 (4K UHD)
    dlg.row_resolution.set_selected(1)
    # Select fps index 2 (30 fps)
    dlg.row_fps.set_selected(2)
    # Select quality index 1 (High Quality CRF 18)
    dlg.row_quality.set_selected(1)

    dlg._on_export_clicked()

    assert result_settings is not None
    assert result_settings.target_width == 3840
    assert result_settings.target_height == 2160
    assert result_settings.fps == 30.0
    assert result_settings.crf == 18


def test_advanced_export_dialog_reactivity():
    dummy_parent = Gtk.Window()
    dlg = AdvancedExportDialog(
        parent=dummy_parent,
        on_export=lambda s: None,
    )

    # Find webm container index
    webm_idx = None
    for i, c in enumerate(dlg.containers):
        if c[0] == "webm":
            webm_idx = i
            break

    if webm_idx is not None:
        dlg.row_container.set_selected(webm_idx)
        # Verify video codecs updated to webm compatible (vp9 or av1)
        v_codecs = [c[0] for c in dlg._current_video_codecs]
        assert "libx264" not in v_codecs
        assert any(c in v_codecs for c in ("libvpx-vp9", "libsvtav1", "libaom-av1"))

        # Verify audio codec is libopus
        a_codecs = [c[0] for c in dlg._current_audio_codecs]
        assert "aac" not in a_codecs
        assert "libopus" in a_codecs

    # Find mov container index
    mov_idx = None
    for i, c in enumerate(dlg.containers):
        if c[0] == "mov":
            mov_idx = i
            break

    if mov_idx is not None:
        dlg.row_container.set_selected(mov_idx)
        # Find prores codec index if available
        prores_idx = None
        for i, c in enumerate(dlg._current_video_codecs):
            if c[0] == "prores_ks":
                prores_idx = i
                break
        if prores_idx is not None:
            dlg.row_video_codec.set_selected(prores_idx)
            # Quality and preset should be disabled for ProRes
            assert not dlg.row_quality.get_sensitive()
            assert not dlg.row_preset.get_sensitive()

        # Find flac audio index
        flac_idx = None
        for i, c in enumerate(dlg._current_audio_codecs):
            if c[0] == "flac":
                flac_idx = i
                break
        if flac_idx is not None:
            dlg.row_audio_codec.set_selected(flac_idx)
            # Bitrate should be disabled for FLAC
            assert not dlg.row_audio_bitrate.get_sensitive()


def test_mainwindow_splitbutton_integration():
    from video_cut.window import MainWindow
    win = MainWindow()

    # btn_export is a primary suggested-action button; advanced export is in sandwich menu
    assert isinstance(win.btn_export, Gtk.Button)
    assert win.btn_export.has_css_class("suggested-action")

    # When timeline is empty, export should be hidden and insensitive
    win._update_ui_state()
    assert not win.btn_export.get_sensitive()
    assert not win.btn_export.get_visible()
    assert not win.action_adv_export.get_enabled()

    # Add a mock segment
    win.timeline.add_video(Path("/tmp/clip.mp4"), source_duration=5.0)
    win._update_ui_state()
    assert win.btn_export.get_sensitive()
    assert win.btn_export.get_visible()
    assert win.action_adv_export.get_enabled()

