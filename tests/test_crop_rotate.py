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
from video_cut.i18n import _


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


def test_crop_rotate_preset_i18n():
    from video_cut.crop_rotate_dialog import get_preset_label, get_preset_tooltip
    from video_cut.i18n import set_language

    set_language("pt_BR")
    assert get_preset_label("Free") == "Livre"
    assert get_preset_label("Original") == "Original"
    assert get_preset_label("16:9") == "16:9"
    assert get_preset_tooltip("Free") == "Livre (sem restrição)"
    assert get_preset_tooltip("Original") == "Proporção original"
    assert get_preset_tooltip("16:9") == "16:9 Widescreen"

    set_language("en_US")
    assert get_preset_label("Free") == "Free"
    assert get_preset_label("Original") == "Original"
    assert get_preset_tooltip("Free") == "Freeform (unconstrained)"


def test_preview_picture_dynamic_allocation():
    try:
        Gtk.init()
    except Exception:
        pass

    parent = Gtk.Window()
    seg = Segment(source_path=Path("/tmp/fake.mp4"), source_duration=5.0, crop=(0.0, 0.0, 1.0, 1.0))
    dialog = CropRotateDialog(parent=parent, segment=seg, on_apply=lambda *a: None)

    # Initial default render
    dialog._render_preview(width=748, height=495)
    rect1 = dialog._img_rect
    assert rect1[2] > 0 and rect1[3] > 0

    # Allocate larger size (e.g. 900x600) via PreviewPicture
    dialog.preview_picture.do_size_allocate(900, 600, -1)
    rect2 = dialog._img_rect
    assert rect2 != rect1
    assert rect2[2] >= rect1[2]
    assert dialog.preview_picture.get_paintable() is not None


def test_aspect_presets_include_3_4_and_9_21():
    preset_dict = dict(ASPECT_PRESETS)
    assert "3:4" in preset_dict
    assert abs(preset_dict["3:4"] - (3.0 / 4.0)) < 1e-6
    assert "9:21" in preset_dict
    assert abs(preset_dict["9:21"] - (9.0 / 21.0)) < 1e-6

    labels = [label for label, _ in ASPECT_PRESETS]
    expected_order = ["Free", "Original", "16:9", "9:16", "1:1", "4:3", "3:4", "21:9", "9:21"]
    assert labels == expected_order


def test_crop_rotate_dialog_reset_button_has_no_icon():
    try:
        Gtk.init()
    except Exception:
        pass
    parent = Gtk.Window()
    seg = Segment(source_path=Path("/tmp/fake.mp4"), source_duration=5.0)
    dialog = CropRotateDialog(parent=parent, segment=seg, on_apply=lambda *a: None)

    def find_buttons(widget):
        btns = []
        if isinstance(widget, Gtk.Button):
            btns.append(widget)
        child = widget.get_first_child() if hasattr(widget, "get_first_child") else None
        while child:
            btns.extend(find_buttons(child))
            child = child.get_next_sibling()
        return btns

    buttons = find_buttons(dialog)
    reset_btns = [b for b in buttons if b.get_label() == _("Reset")]
    assert len(reset_btns) == 1
    # Verify child is not a Gtk.Box with an icon
    assert not isinstance(reset_btns[0].get_child(), Gtk.Box)


def test_crop_rotate_dialog_hit_testing():
    try:
        Gtk.init()
    except Exception:
        pass
    parent = Gtk.Window()
    seg = Segment(source_path=Path("/tmp/fake.mp4"), source_duration=5.0)
    dialog = CropRotateDialog(parent=parent, segment=seg, on_apply=lambda *a: None)

    dialog._img_rect = (100, 100, 400, 300)
    dialog.crop = (0.2, 0.2, 0.6, 0.6)
    # bx = 180, by = 160, bw = 240, bh = 180
    bx, by, bw, bh = 180, 160, 240, 180

    # Corners (within 14px of corner points)
    assert dialog._hit_test(bx + 2, by + 2) == "RESIZE_NW"
    assert dialog._hit_test(bx + bw - 2, by + 2) == "RESIZE_NE"
    assert dialog._hit_test(bx + 2, by + bh - 2) == "RESIZE_SW"
    assert dialog._hit_test(bx + bw - 2, by + bh - 2) == "RESIZE_SE"

    # Edges (outside corners, within 10px of border)
    assert dialog._hit_test(bx + bw / 2, by) == "RESIZE_N"
    assert dialog._hit_test(bx + 30, by + 2) == "RESIZE_N"
    assert dialog._hit_test(bx + bw / 2, by + bh) == "RESIZE_S"
    assert dialog._hit_test(bx + 30, by + bh - 2) == "RESIZE_S"
    assert dialog._hit_test(bx, by + bh / 2) == "RESIZE_W"
    assert dialog._hit_test(bx + 2, by + 30) == "RESIZE_W"
    assert dialog._hit_test(bx + bw, by + bh / 2) == "RESIZE_E"
    assert dialog._hit_test(bx + bw - 2, by + 30) == "RESIZE_E"

    # Inside (move)
    assert dialog._hit_test(bx + bw / 2, by + bh / 2) == "MOVE"

    # Outside
    assert dialog._hit_test(bx - 30, by - 30) == "OUTSIDE"
    assert dialog._hit_test(bx + bw + 30, by + bh + 30) == "OUTSIDE"


def test_free_proportion_resizing_respects_edges_and_corners():
    try:
        Gtk.init()
    except Exception:
        pass
    parent = Gtk.Window()
    seg = Segment(source_path=Path("/tmp/fake.mp4"), source_duration=5.0)
    dialog = CropRotateDialog(parent=parent, segment=seg, on_apply=lambda *a: None)

    dialog.selected_preset = "Free"
    dialog._render_preview = MagicMock()
    dialog._img_rect = (0, 0, 1000, 1000)
    dialog.crop = (0.2, 0.2, 0.5, 0.5)

    gesture = MagicMock()

    # 1. Drag N edge: MUST only modify ny and nh, leaving nx and nw completely unchanged
    dialog._drag_mode = "RESIZE_N"
    dialog._crop_at_drag_start = (0.2, 0.2, 0.5, 0.5)
    dialog._on_drag_update(gesture, offset_x=100, offset_y=-50)
    nx, ny, nw, nh = dialog.crop
    assert nx == 0.2  # Unchanged horizontally!
    assert nw == 0.5  # Unchanged horizontally!
    assert abs(ny - 0.15) < 1e-4  # Moved up
    assert abs(nh - 0.55) < 1e-4  # Height expanded

    # 2. Drag S edge: MUST only modify nh, leaving nx, ny, nw completely unchanged
    dialog._drag_mode = "RESIZE_S"
    dialog._crop_at_drag_start = (0.2, 0.2, 0.5, 0.5)
    dialog._on_drag_update(gesture, offset_x=80, offset_y=40)
    nx, ny, nw, nh = dialog.crop
    assert nx == 0.2  # Unchanged
    assert ny == 0.2  # Unchanged
    assert nw == 0.5  # Unchanged
    assert abs(nh - 0.54) < 1e-4  # Height expanded down

    # 3. Drag W edge: MUST only modify nx and nw, leaving ny and nh completely unchanged
    dialog._drag_mode = "RESIZE_W"
    dialog._crop_at_drag_start = (0.2, 0.2, 0.5, 0.5)
    dialog._on_drag_update(gesture, offset_x=-60, offset_y=70)
    nx, ny, nw, nh = dialog.crop
    assert ny == 0.2  # Unchanged vertically!
    assert nh == 0.5  # Unchanged vertically!
    assert abs(nx - 0.14) < 1e-4  # Moved left
    assert abs(nw - 0.56) < 1e-4  # Width expanded

    # 4. Drag E edge: MUST only modify nw, leaving nx, ny, nh completely unchanged
    dialog._drag_mode = "RESIZE_E"
    dialog._crop_at_drag_start = (0.2, 0.2, 0.5, 0.5)
    dialog._on_drag_update(gesture, offset_x=50, offset_y=90)
    nx, ny, nw, nh = dialog.crop
    assert nx == 0.2  # Unchanged
    assert ny == 0.2  # Unchanged
    assert nh == 0.5  # Unchanged
    assert abs(nw - 0.55) < 1e-4  # Width expanded

    # 5. Drag Corner NW: modifies both axes
    dialog._drag_mode = "RESIZE_NW"
    dialog._crop_at_drag_start = (0.2, 0.2, 0.5, 0.5)
    dialog._on_drag_update(gesture, offset_x=-50, offset_y=-50)
    nx, ny, nw, nh = dialog.crop
    assert abs(nx - 0.15) < 1e-4
    assert abs(ny - 0.15) < 1e-4
    assert abs(nw - 0.55) < 1e-4
    assert abs(nh - 0.55) < 1e-4


def test_rotation_inverts_preset_without_resetting_crop():
    try:
        Gtk.init()
    except Exception:
        pass
    parent = Gtk.Window()
    seg = Segment(source_path=Path("/tmp/fake.mp4"), source_duration=5.0)
    dialog = CropRotateDialog(parent=parent, segment=seg, on_apply=lambda *a: None)

    # 1. Select 4:3 preset
    btn_4_3 = dialog._preset_buttons["4:3"]
    btn_4_3.set_active(True)
    assert dialog.selected_preset == "4:3"
    crop_before = dialog.crop

    # 2. Rotate CW
    dialog._rotate_cw()
    assert dialog.rotation == 90
    assert dialog.selected_preset == "3:4"
    assert dialog._preset_buttons["3:4"].get_active() is True
    assert dialog._preset_buttons["4:3"].get_active() is False
    # Verify crop was rotated (ox, oy, ow, oh) -> (1-(oy+oh), ox, oh, ow)
    ox, oy, ow, oh = crop_before
    expected_crop = (1.0 - (oy + oh), ox, oh, ow)
    assert abs(dialog.crop[0] - expected_crop[0]) < 1e-4
    assert abs(dialog.crop[1] - expected_crop[1]) < 1e-4
    assert abs(dialog.crop[2] - expected_crop[2]) < 1e-4
    assert abs(dialog.crop[3] - expected_crop[3]) < 1e-4

    # 3. Rotate CW again -> inverts back to 4:3
    dialog._rotate_cw()
    assert dialog.rotation == 180
    assert dialog.selected_preset == "4:3"
    assert dialog._preset_buttons["4:3"].get_active() is True

    # 4. Select 16:9 and rotate CCW -> inverts to 9:16
    btn_16_9 = dialog._preset_buttons["16:9"]
    btn_16_9.set_active(True)
    assert dialog.selected_preset == "16:9"
    dialog._rotate_ccw()
    assert dialog.selected_preset == "9:16"
    assert dialog._preset_buttons["9:16"].get_active() is True

    # 5. Select 21:9 and rotate CW -> inverts to 9:21
    btn_21_9 = dialog._preset_buttons["21:9"]
    btn_21_9.set_active(True)
    assert dialog.selected_preset == "21:9"
    dialog._rotate_cw()
    assert dialog.selected_preset == "9:21"
    assert dialog._preset_buttons["9:21"].get_active() is True

    # 6. Free preset remains Free on rotation
    dialog._preset_buttons["Free"].set_active(True)
    assert dialog.selected_preset == "Free"
    dialog._rotate_cw()
    assert dialog.selected_preset == "Free"
    assert dialog._preset_buttons["Free"].get_active() is True


def test_fixed_proportion_growth_stops_when_one_dimension_hits_boundary():
    try:
        Gtk.init()
    except Exception:
        pass
    parent = Gtk.Window()
    seg = Segment(source_path=Path("/tmp/fake.mp4"), source_duration=5.0)
    dialog = CropRotateDialog(parent=parent, segment=seg, on_apply=lambda *a: None)
    dialog._render_preview = MagicMock()
    dialog._img_rect = (0, 0, 1000, 1000)

    # Set up a mock transformed pixbuf with 1920x1080 (16:9)
    dialog._transformed_pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 1920, 1080)

    gesture = MagicMock()

    # 1. Select 16:9 on 16:9 image (norm_ratio = 1.0)
    dialog.selected_preset = "16:9"

    # Place initial crop at (0.2, 0.4, 0.4, 0.4)
    # Available width to right: 1.0 - 0.2 = 0.8
    # Available height to bottom: 1.0 - 0.4 = 0.6  <-- height runs out first!
    dialog._drag_mode = "RESIZE_SE"
    dialog._crop_at_drag_start = (0.2, 0.4, 0.4, 0.4)

    # Drag SE by huge offset: +800px X and +800px Y
    dialog._on_drag_update(gesture, offset_x=800, offset_y=800)
    nx, ny, nw, nh = dialog.crop

    # Height reached boundary at 1.0 (0.4 + 0.6 = 1.0)
    assert abs(ny + nh - 1.0) < 1e-4
    assert abs(nh - 0.6) < 1e-4

    # Width MUST stop growing too, preserving exact proportion!
    # Even though horizontal space had 0.8 available, width must stop at 0.6!
    assert abs(nw - 0.6) < 1e-4
    assert abs(nx + nw - 0.8) < 1e-4
    assert abs(nw / nh - 1.0) < 1e-4

    # 2. Drag NW when width runs out first
    # Place initial crop at (0.3, 0.5, 0.3, 0.3)
    # Available width to left: 0.3 + 0.3 = 0.6  <-- width runs out first!
    # Available height to top: 0.5 + 0.3 = 0.8
    dialog._drag_mode = "RESIZE_NW"
    dialog._crop_at_drag_start = (0.3, 0.5, 0.3, 0.3)

    # Drag NW up-left by -800px X and -800px Y
    dialog._on_drag_update(gesture, offset_x=-800, offset_y=-800)
    nx, ny, nw, nh = dialog.crop

    # Left edge reached boundary at 0.0
    assert abs(nx - 0.0) < 1e-4
    assert abs(nw - 0.6) < 1e-4

    # Height MUST stop growing too, preserving exact proportion!
    # Even though top had 0.8 available, height must stop at 0.6!
    assert abs(nh - 0.6) < 1e-4
    assert abs(ny - 0.2) < 1e-4
    assert abs(nw / nh - 1.0) < 1e-4

    # 3. Select 4:3 preset on 16:9 image (norm_ratio = (4/3)/(16/9) = 0.75)
    dialog.selected_preset = "4:3"
    # Place initial crop at (0.2, 0.5, 0.3, 0.4) -> ratio 0.3/0.4 = 0.75
    # Available height to bottom: 1.0 - 0.5 = 0.5
    # Drag S down by +500px
    dialog._drag_mode = "RESIZE_S"
    dialog._crop_at_drag_start = (0.2, 0.5, 0.3, 0.4)
    dialog._on_drag_update(gesture, offset_x=0, offset_y=500)
    nx, ny, nw, nh = dialog.crop

    # Height reached boundary at 1.0
    assert abs(ny + nh - 1.0) < 1e-4
    assert abs(nh - 0.5) < 1e-4

    # Width stopped growing at 0.5 * 0.75 = 0.375, preserving exact ratio!
    assert abs(nw - 0.375) < 1e-4
    assert abs(nw / nh - 0.75) < 1e-4



