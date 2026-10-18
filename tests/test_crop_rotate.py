"""Automated tests for Crop & Rotate feature."""

import pytest
from pathlib import Path
from unittest.mock import MagicMock
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gtk, Gdk, Adw, GdkPixbuf

from video_cut.models import Segment, ProjectTimeline
from video_cut.services.export_service import ExportService
from video_cut.crop_rotate_dialog import CropRotateDialog, ASPECT_PRESETS


def test_segment_transform_defaults():
    seg = Segment(source_path=Path("/tmp/sample.mp4"), source_duration=10.0)
    assert seg.crop is None
    assert seg.rotation == 0
    assert seg.hflip is False
    assert seg.vflip is False
    assert seg.has_transform is False


def test_segment_transform_has_transform():
    seg = Segment(source_path=Path("/tmp/sample.mp4"), source_duration=10.0, rotation=90)
    assert seg.has_transform is True

    seg2 = Segment(source_path=Path("/tmp/sample.mp4"), source_duration=10.0, hflip=True)
    assert seg2.has_transform is True

    seg3 = Segment(source_path=Path("/tmp/sample.mp4"), source_duration=10.0, vflip=True)
    assert seg3.has_transform is True

    seg4 = Segment(source_path=Path("/tmp/sample.mp4"), source_duration=10.0, crop=(0.1, 0.1, 0.8, 0.8))
    assert seg4.has_transform is True

    # Full frame crop should count as no transform
    seg5 = Segment(source_path=Path("/tmp/sample.mp4"), source_duration=10.0, crop=(0.0, 0.0, 1.0, 1.0))
    assert seg5.has_transform is False


def test_segment_clone_and_copy_preserve_transforms():
    seg = Segment(
        source_path=Path("/tmp/sample.mp4"),
        source_duration=10.0,
        start_time=2.0,
        end_time=8.0,
        crop=(0.05, 0.1, 0.9, 0.8),
        rotation=270,
        hflip=True,
        vflip=False,
    )

    copied = seg.copy()
    assert copied.id == seg.id
    assert copied.crop == (0.05, 0.1, 0.9, 0.8)
    assert copied.rotation == 270
    assert copied.hflip is True
    assert copied.vflip is False

    cloned = seg.clone(new_start=3.0, new_end=7.0)
    assert cloned.id != seg.id
    assert cloned.start_time == 3.0
    assert cloned.end_time == 7.0
    assert cloned.crop == (0.05, 0.1, 0.9, 0.8)
    assert cloned.rotation == 270
    assert cloned.hflip is True
    assert cloned.vflip is False


def test_timeline_undo_redo_preserves_transforms():
    timeline = ProjectTimeline()
    seg = timeline.add_video("/tmp/sample.mp4", 10.0)

    # Save initial state
    timeline.save_state(0.0)

    # Apply transform
    seg.rotation = 90
    seg.crop = (0.2, 0.2, 0.6, 0.6)
    seg.hflip = True

    # Undo
    restored_time = timeline.undo(2.0)
    assert restored_time == 0.0
    cur_seg = timeline.segments[0]
    assert cur_seg.rotation == 0
    assert cur_seg.crop is None
    assert cur_seg.hflip is False

    # Redo
    restored_time = timeline.redo(0.0)
    assert restored_time == 2.0
    cur_seg = timeline.segments[0]
    assert cur_seg.rotation == 90
    assert cur_seg.crop == (0.2, 0.2, 0.6, 0.6)
    assert cur_seg.hflip is True


def test_export_service_rotation_filters():
    service = ExportService()

    # 90 CW
    seg_90 = Segment(source_path=Path("/tmp/sample.mp4"), source_duration=5.0, rotation=90)
    cmd_90 = " ".join(service.build_ffmpeg_command([seg_90], "/tmp/out.mp4", 1280, 720))
    assert "transpose=1" in cmd_90

    # 180
    seg_180 = Segment(source_path=Path("/tmp/sample.mp4"), source_duration=5.0, rotation=180)
    cmd_180 = " ".join(service.build_ffmpeg_command([seg_180], "/tmp/out.mp4", 1280, 720))
    assert "transpose=1,transpose=1" in cmd_180

    # 270 CW (90 CCW)
    seg_270 = Segment(source_path=Path("/tmp/sample.mp4"), source_duration=5.0, rotation=270)
    cmd_270 = " ".join(service.build_ffmpeg_command([seg_270], "/tmp/out.mp4", 1280, 720))
    assert "transpose=2" in cmd_270


def test_export_service_flip_filters():
    service = ExportService()

    # HFlip
    seg_h = Segment(source_path=Path("/tmp/sample.mp4"), source_duration=5.0, hflip=True)
    cmd_h = " ".join(service.build_ffmpeg_command([seg_h], "/tmp/out.mp4", 1280, 720))
    assert "hflip" in cmd_h

    # VFlip
    seg_v = Segment(source_path=Path("/tmp/sample.mp4"), source_duration=5.0, vflip=True)
    cmd_v = " ".join(service.build_ffmpeg_command([seg_v], "/tmp/out.mp4", 1280, 720))
    assert "vflip" in cmd_v


def test_export_service_crop_filter():
    service = ExportService()
    seg_crop = Segment(
        source_path=Path("/tmp/sample.mp4"),
        source_duration=5.0,
        crop=(0.1, 0.15, 0.8, 0.7),
    )
    cmd = " ".join(service.build_ffmpeg_command([seg_crop], "/tmp/out.mp4", 1280, 720))
    assert "crop=" in cmd
    assert "floor(iw*0.80000/2)*2" in cmd
    assert "floor(ih*0.70000/2)*2" in cmd
    assert "floor(iw*0.10000/2)*2" in cmd
    assert "floor(ih*0.15000/2)*2" in cmd


def test_export_service_combined_filter_order():
    service = ExportService()
    seg = Segment(
        source_path=Path("/tmp/sample.mp4"),
        source_duration=5.0,
        rotation=90,
        hflip=True,
        crop=(0.05, 0.05, 0.9, 0.9),
    )
    cmd = " ".join(service.build_ffmpeg_command([seg], "/tmp/out.mp4", 1280, 720))

    # Order must be: trim -> setpts -> transpose=1 -> hflip -> crop -> scale -> pad
    idx_trim = cmd.find("trim=")
    idx_setpts = cmd.find("setpts=")
    idx_trans = cmd.find("transpose=1")
    idx_flip = cmd.find("hflip")
    idx_crop = cmd.find("crop=")
    idx_scale = cmd.find("scale=")
    idx_pad = cmd.find("pad=")

    assert idx_trim < idx_setpts < idx_trans < idx_flip < idx_crop < idx_scale < idx_pad


def test_crop_rotate_dialog_geometry_and_rotations():
    # Initialize Gtk if needed
    try:
        Gtk.init()
    except Exception:
        pass

    parent = Gtk.Window()
    seg = Segment(source_path=Path("/tmp/fake.mp4"), source_duration=5.0, rotation=0, crop=(0.1, 0.2, 0.6, 0.4))

    applied_results = []
    def on_apply(crop, rot, hf, vf):
        applied_results.append((crop, rot, hf, vf))

    dialog = CropRotateDialog(parent=parent, segment=seg, on_apply=on_apply)

    # Initial state
    assert dialog.rotation == 0
    assert dialog.crop == (0.1, 0.2, 0.6, 0.4)

    # 1. Rotate CW 90°
    # crop: (0.1, 0.2, 0.6, 0.4)
    # new_x = 1 - (0.2 + 0.4) = 0.4
    # new_y = 0.1
    # new_w = 0.4
    # new_h = 0.6
    dialog._rotate_cw()
    assert dialog.rotation == 90
    assert abs(dialog.crop[0] - 0.4) < 1e-4
    assert abs(dialog.crop[1] - 0.1) < 1e-4
    assert abs(dialog.crop[2] - 0.4) < 1e-4
    assert abs(dialog.crop[3] - 0.6) < 1e-4

    # 2. Toggle Flip H
    dialog._toggle_hflip()
    assert dialog.hflip is True
    # new_x = 1 - (0.4 + 0.4) = 0.2
    assert abs(dialog.crop[0] - 0.2) < 1e-4

    # 3. Toggle Flip V
    dialog._toggle_vflip()
    assert dialog.vflip is True
    # new_y = 1 - (0.1 + 0.6) = 0.3
    assert abs(dialog.crop[1] - 0.3) < 1e-4

    # 4. Reset
    dialog._reset_transforms()
    assert dialog.rotation == 0
    assert dialog.hflip is False
    assert dialog.vflip is False
    assert dialog.crop == (0.0, 0.0, 1.0, 1.0)
    assert dialog.selected_preset == "Free"

    # 5. Apply
    dialog._on_apply_clicked(dialog.btn_apply)
    assert len(applied_results) == 1
    # Full frame crop (0, 0, 1, 1) normalizes to None
    assert applied_results[0][0] is None
    assert applied_results[0][1] == 0
    assert applied_results[0][2] is False
    assert applied_results[0][3] is False


def test_crop_rotate_dialog_drawing_renders_without_error():
    try:
        Gtk.init()
    except Exception:
        pass

    parent = Gtk.Window()
    seg = Segment(source_path=Path("/tmp/fake.mp4"), source_duration=5.0, crop=(0.1, 0.1, 0.8, 0.8))
    dialog = CropRotateDialog(parent=parent, segment=seg, on_apply=lambda *a: None)

    # Must execute render preview and set paintable without raising any exception
    dialog._render_preview()
    assert dialog.preview_picture.get_paintable() is not None

    # Rotate and render again
    dialog._rotate_cw()
    dialog._render_preview()
    assert dialog.preview_picture.get_paintable() is not None


def test_transformed_paintable_and_player_transform(tmp_path):
    from video_cut.player import TransformedPaintable, VideoPlayerController

    # Create dummy mp4 if /tmp/test_vid.mp4 doesn't exist
    vid_path = tmp_path / "test.mp4"
    if not vid_path.exists():
        import subprocess
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=duration=1:size=160x120:rate=10", str(vid_path)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    # Test TransformedPaintable with a texture
    pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 160, 120)
    texture = Gdk.Texture.new_for_pixbuf(pixbuf)
    tp = TransformedPaintable(texture, rotation=90, hflip=True, vflip=False, crop=(0.1, 0.2, 0.6, 0.4))
    assert tp.rotation == 90
    assert tp.hflip is True
    assert tp.vflip is False
    assert tp.crop == (0.1, 0.2, 0.6, 0.4)
    assert tp.do_get_intrinsic_width() > 0
    assert tp.do_get_intrinsic_height() > 0

    s = Gtk.Snapshot.new()
    tp.snapshot(s, 200, 300)

    # Update transform
    tp.update_transform(rotation=180, hflip=False, vflip=True, crop=None)
    assert tp.rotation == 180
    assert tp.hflip is False
    assert tp.vflip is True
    assert tp.crop == (0.0, 0.0, 1.0, 1.0)

    tp.detach()

    # VideoPlayerController test
    timeline = ProjectTimeline()
    seg = timeline.add_video(vid_path, 1.0)
    seg.rotation = 90
    seg.crop = (0.1, 0.1, 0.8, 0.8)

    pic = Gtk.Picture()
    ctrl = VideoPlayerController(timeline, pic, lambda t: None, lambda s: None)
    ctrl.seek_to_global_time(0.0)

    # Paintable should be TransformedPaintable
    paintable = pic.get_paintable()
    assert isinstance(paintable, TransformedPaintable)
    assert paintable.rotation == 90

    # Reset transform and call refresh_current_transform
    seg.rotation = 0
    seg.crop = None
    ctrl.refresh_current_transform()
    assert pic.get_paintable() == ctrl.current_media_file

    ctrl.clear()

