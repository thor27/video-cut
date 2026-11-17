"""UI and interaction tests for OverlayDialog."""

from pathlib import Path
from unittest.mock import MagicMock

import gi
import pytest

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk

from video_cut.i18n import _
from video_cut.models import OverlayItem, Segment
from video_cut.overlay_dialog import OverlayDialog


@pytest.fixture(scope="session", autouse=True)
def init_gtk():
    """Ensure GTK/Adwaita is initialized."""
    Adw.init()


def test_overlay_dialog_lifecycle(tmp_path: Path):
    """Test dialog creation, adding elements, undo/redo, reordering, and apply callback."""
    video_path = tmp_path / "clip.mp4"
    video_path.write_bytes(b"dummy")

    seg = Segment(
        id="seg_test",
        source_path=video_path,
        start_time=0.0,
        end_time=10.0,
        source_duration=10.0,
        overlays=[
            OverlayItem(item_type="text", text="Existing Watermark", font_size=32, x=0.5, y=0.5),
        ],
    )

    applied_items: list[OverlayItem] = []
    def on_apply(items: list[OverlayItem]):
        applied_items.extend(items)

    parent = Gtk.Window()
    dialog = OverlayDialog(
        parent=parent,
        segment=seg,
        on_apply=on_apply,
        current_time=2.0,
    )

    # 1. Check initial state (nothing selected -> properties panel visible with placeholder)
    assert len(dialog.items) == 1
    assert dialog.items[0].text == "Existing Watermark"
    assert dialog.current_time == 2.0
    assert dialog.prop_sidebar.get_visible() is True
    assert not hasattr(dialog, "prop_separator")

    # 2. Add text item (selected -> properties panel shows item attributes)
    dialog._on_tool_add_text()
    assert len(dialog.items) == 2
    assert dialog.selected_item is not None
    assert dialog.selected_item.item_type == "text"
    text_item = dialog.selected_item
    assert text_item.text == _("Sample Text")
    assert dialog.prop_sidebar.get_visible() is True
    assert not hasattr(dialog, "prop_separator")

    # Deselect item -> properties panel remains visible with placeholder
    dialog.selected_item_id = None
    dialog._refresh_canvas_and_properties()
    assert dialog.prop_sidebar.get_visible() is True
    assert not hasattr(dialog, "prop_separator")

    # 3. Add rectangle shape
    dialog._on_tool_add_shape("rect")
    assert len(dialog.items) == 3
    rect_item = dialog.selected_item
    assert rect_item.item_type == "rect"

    # 4. Add circle shape
    dialog._on_tool_add_shape("circle")
    assert len(dialog.items) == 4
    circle_item = dialog.selected_item
    assert circle_item.item_type == "circle"

    # 5. Test undo and redo
    dialog.undo()
    assert len(dialog.items) == 3
    dialog.undo()
    assert len(dialog.items) == 2
    dialog.redo()
    assert len(dialog.items) == 3

    # 6. Test layer z-ordering
    dialog.selected_item_id = rect_item.id
    dialog._change_z_order(rect_item, "top")
    assert dialog.items[-1].id == rect_item.id

    dialog._change_z_order(rect_item, "bottom")
    assert dialog.items[0].id == rect_item.id

    # 7. Test canvas dragging (move)
    dialog._drag_mode = "MOVE"
    dialog._drag_item_id = rect_item.id
    dialog._item_state_at_drag_start = rect_item.copy()
    init_x, init_y = rect_item.x, rect_item.y
    dialog._on_canvas_drag_update(None, 50.0, 50.0)
    assert rect_item.x != init_x or rect_item.y != init_y
    dialog._on_canvas_drag_end(None, 50.0, 50.0)
    assert dialog._drag_mode == "NONE"

    # 8. Test delete selected
    prev_count = len(dialog.items)
    dialog._on_delete_selected()
    assert len(dialog.items) == prev_count - 1

    # 9. Test playback toggling
    dialog._toggle_playback()
    assert dialog.is_playing is True
    dialog._toggle_playback()
    assert dialog.is_playing is False

    # 10. Test apply
    dialog._on_apply_clicked()
    assert len(applied_items) == len(dialog.items)


def test_overlay_dialog_resize_and_rotate_gestures(tmp_path: Path):
    """Test interactive resize handles and rotation gestures on overlay items."""
    video_path = tmp_path / "clip.mp4"
    video_path.write_bytes(b"dummy")

    item = OverlayItem(
        item_type="rect",
        x=0.5,
        y=0.5,
        width=0.3,
        height=0.3,
        rotation=0.0,
    )
    seg = Segment(
        id="seg_test",
        source_path=video_path,
        start_time=0.0,
        end_time=5.0,
        source_duration=5.0,
        overlays=[item],
    )

    parent = Gtk.Window()
    dialog = OverlayDialog(parent=parent, segment=seg, on_apply=lambda x: None)
    dialog.selected_item_id = dialog.items[0].id
    dialog._canvas_rect = (0.0, 0.0, 1000.0, 1000.0)
    target_item = dialog.selected_item
    assert target_item is not None

    # 1. Test East handle resize
    dialog._drag_mode = "RESIZE_E"
    dialog._drag_item_id = target_item.id
    dialog._item_state_at_drag_start = target_item.copy()
    init_w = target_item.width
    dialog._on_canvas_drag_update(None, 100.0, 0.0)
    assert target_item.width > init_w
    dialog._on_canvas_drag_end(None, 100.0, 0.0)

    # 2. Test South handle resize
    dialog._drag_mode = "RESIZE_S"
    dialog._drag_item_id = target_item.id
    dialog._item_state_at_drag_start = target_item.copy()
    init_h = target_item.height
    dialog._on_canvas_drag_update(None, 0.0, 100.0)
    assert target_item.height > init_h
    dialog._on_canvas_drag_end(None, 0.0, 100.0)

    # 3. Test Rotation handle drag
    dialog._drag_mode = "ROTATE"
    dialog._drag_item_id = target_item.id
    dialog._item_state_at_drag_start = target_item.copy()
    dialog._drag_start_x = 500.0
    dialog._drag_start_y = 300.0
    dialog._on_canvas_drag_update(None, 200.0, 200.0)
    assert target_item.rotation != 0.0
    dialog._on_canvas_drag_end(None, 200.0, 200.0)


def test_overlay_dialog_media_and_emojis(tmp_path: Path):
    """Test adding media items (SVG, GIF, images) and emoji picker integration."""
    video_path = tmp_path / "clip.mp4"
    video_path.write_bytes(b"dummy")

    seg = Segment(
        id="seg_test",
        source_path=video_path,
        start_time=0.0,
        end_time=5.0,
        source_duration=5.0,
    )

    parent = Gtk.Window()
    dialog = OverlayDialog(parent=parent, segment=seg, on_apply=lambda x: None)

    # 1. Add SVG image
    svg_file = tmp_path / "logo.svg"
    svg_file.write_text("<svg></svg>")
    dialog._add_media_item(svg_file)
    assert len(dialog.items) == 1
    assert dialog.items[0].item_type == "svg"

    # 2. Add GIF
    gif_file = tmp_path / "dance.gif"
    gif_file.write_bytes(b"GIF89a")
    dialog._add_media_item(gif_file)
    assert len(dialog.items) == 2
    assert dialog.items[1].item_type == "gif"

    # 3. Add Emoji item
    dialog._add_emoji_item("🚀")
    assert len(dialog.items) == 3
    assert dialog.items[2].text == "🚀"

    # 4. Scrubber & Time updates
    dialog._seek_relative(1.0)
    assert dialog.current_time >= 1.0


def test_overlay_canvas_rendering(tmp_path: Path):
    """Test canvas rendering generates valid texture and handles allocation without foreign struct errors."""
    video_path = tmp_path / "clip.mp4"
    video_path.write_bytes(b"dummy")

    seg = Segment(
        id="seg_test",
        source_path=video_path,
        start_time=0.0,
        end_time=5.0,
        source_duration=5.0,
    )
    parent = Gtk.Window()
    dialog = OverlayDialog(parent=parent, segment=seg, on_apply=lambda x: None)

    # Allocate canvas size
    dialog._on_viewport_allocated(800, 450)
    assert dialog._canvas_alloc_size[0] > 0
    assert dialog._canvas_alloc_size[1] > 0
    assert dialog._canvas_rect[2] > 0
    assert dialog._canvas_rect[3] > 0

    # Add text and shapes
    dialog._on_tool_add_text()
    dialog._on_tool_add_shape("rect")
    dialog._on_tool_add_shape("circle")

    # Render canvas
    dialog._render_overlay_canvas()
    paintable = dialog.overlay_picture.get_paintable()
    assert paintable is not None
    assert paintable.get_intrinsic_width() == dialog._canvas_alloc_size[0]
    assert paintable.get_intrinsic_height() == dialog._canvas_alloc_size[1]


def test_overlay_file_dialog_filters(tmp_path: Path, monkeypatch):
    """Test that _on_tool_add_media configures Gio.ListModel filters without TypeError."""
    video_path = tmp_path / "clip.mp4"
    video_path.write_bytes(b"dummy")

    seg = Segment(id="seg_test", source_path=video_path, start_time=0.0, end_time=5.0, source_duration=5.0)
    parent = Gtk.Window()
    dialog = OverlayDialog(parent=parent, segment=seg, on_apply=lambda x: None)

    # Mock Gtk.FileDialog.open so it doesn't display an interactive dialog
    mock_open = MagicMock()
    monkeypatch.setattr(Gtk.FileDialog, "open", mock_open)

    dialog._on_tool_add_media()
    assert mock_open.called


def test_overlay_keyboard_shortcuts(tmp_path: Path):
    """Test keyboard shortcuts: Space for play, Delete for remove, Ctrl+Z for undo."""
    from gi.repository import Gdk

    video_path = tmp_path / "clip.mp4"
    video_path.write_bytes(b"dummy")

    seg = Segment(id="seg_test", source_path=video_path, start_time=0.0, end_time=5.0, source_duration=5.0)
    parent = Gtk.Window()
    dialog = OverlayDialog(parent=parent, segment=seg, on_apply=lambda x: None)

    # 1. Space toggles playback
    assert dialog.is_playing is False
    dialog._on_key_pressed(None, Gdk.KEY_space, 0, Gdk.ModifierType(0))
    assert dialog.is_playing is True
    dialog._on_key_pressed(None, Gdk.KEY_space, 0, Gdk.ModifierType(0))
    assert dialog.is_playing is False

    # 2. Add item and delete via keypress
    dialog._on_tool_add_text()
    assert len(dialog.items) == 1
    assert dialog.selected_item_id is not None
    dialog._on_key_pressed(None, Gdk.KEY_Delete, 0, Gdk.ModifierType(0))
    assert len(dialog.items) == 0
    assert dialog.selected_item_id is None
    assert dialog.prop_sidebar.get_visible() is True

    # 3. Undo keypress
    dialog._on_key_pressed(None, Gdk.KEY_z, 0, Gdk.ModifierType.CONTROL_MASK)
    assert len(dialog.items) == 1


def test_overlay_image_aspect_ratio_and_corner_resize(tmp_path: Path):
    """Test that imported media preserves source aspect ratio and corner resizing locks aspect ratio."""
    from PIL import Image

    video_path = tmp_path / "clip.mp4"
    video_path.write_bytes(b"dummy")

    seg = Segment(id="seg_test", source_path=video_path, start_time=0.0, end_time=5.0, source_duration=5.0)
    parent = Gtk.Window()
    dialog = OverlayDialog(parent=parent, segment=seg, on_apply=lambda x: None)

    dialog._get_video_aspect = lambda: 16.0 / 9.0

    # 1. 2:1 Landscape Image (400x200)
    img_path = tmp_path / "banner.png"
    Image.new("RGBA", (400, 200), (255, 0, 0, 255)).save(img_path)
    dialog._add_media_item(img_path)

    item = dialog.items[-1]
    video_aspect = 16.0 / 9.0
    pixel_aspect = (item.width / item.height) * video_aspect
    assert abs(pixel_aspect - 2.0) < 0.05

    # 2. 1:1 Emoji
    dialog._add_emoji_item("🔥")
    emoji_item = dialog.items[-1]
    emoji_pixel_aspect = (emoji_item.width / emoji_item.height) * video_aspect
    assert abs(emoji_pixel_aspect - 1.0) < 0.05

    # 3. 1:1 Circle
    dialog._on_tool_add_shape("circle")
    circle_item = dialog.items[-1]
    circle_pixel_aspect = (circle_item.width / circle_item.height) * video_aspect
    assert abs(circle_pixel_aspect - 1.0) < 0.05

    # 4. Corner resize maintains aspect ratio
    dialog.selected_item_id = item.id
    dialog._drag_mode = "RESIZE_SE"
    dialog._drag_start_x = 100
    dialog._drag_start_y = 100
    dialog._item_state_at_drag_start = item.copy()
    dialog._canvas_rect = (0, 0, 800, 450)

    dialog._on_canvas_drag_update(None, 50.0, 50.0)
    new_pixel_aspect = (item.width / item.height) * video_aspect
    assert abs(new_pixel_aspect - 2.0) < 0.05


def test_overlay_top_bottom_resize_anchoring(tmp_path: Path):
    """Test that dragging N (top) or S (bottom) handles scales proportionally and anchors the opposite edge."""
    video_path = tmp_path / "clip.mp4"
    video_path.write_bytes(b"dummy")

    seg = Segment(id="seg_test", source_path=video_path, start_time=0.0, end_time=5.0, source_duration=5.0)
    parent = Gtk.Window()
    dialog = OverlayDialog(parent=parent, segment=seg, on_apply=lambda x: None)

    dialog._get_video_aspect = lambda: 16.0 / 9.0
    dialog._add_emoji_item("⭐")
    item = dialog.items[-1]

    dialog.selected_item_id = item.id
    dialog._drag_start_x = 400
    dialog._drag_start_y = 200
    dialog._canvas_rect = (0, 0, 800, 450)

    # 1. Drag North (top) handle UP (offset_y < 0)
    dialog._drag_mode = "RESIZE_N"
    dialog._item_state_at_drag_start = item.copy()
    init_bottom = item.y + item.height / 2.0
    init_w = item.width
    init_h = item.height

    dialog._on_canvas_drag_update(None, 0.0, -45.0)
    # Height and width both increased proportionally
    assert item.height > init_h
    assert item.width > init_w
    # Bottom edge remained anchored!
    assert abs((item.y + item.height / 2.0) - init_bottom) < 0.001

    # 2. Drag South (bottom) handle DOWN (offset_y > 0)
    dialog._drag_mode = "RESIZE_S"
    dialog._item_state_at_drag_start = item.copy()
    init_top = item.y - item.height / 2.0
    init_w2 = item.width
    init_h2 = item.height

    dialog._on_canvas_drag_update(None, 0.0, 45.0)
    # Height and width both increased proportionally
    assert item.height > init_h2
    assert item.width > init_w2
    # Top edge remained anchored!
    assert abs((item.y - item.height / 2.0) - init_top) < 0.001

    # 3. Drag North handle DOWN (offset_y > 0) -> should shrink
    dialog._drag_mode = "RESIZE_N"
    dialog._item_state_at_drag_start = item.copy()
    init_bottom3 = item.y + item.height / 2.0
    init_h3 = item.height
    dialog._on_canvas_drag_update(None, 0.0, 20.0)
    assert item.height < init_h3
    assert abs((item.y + item.height / 2.0) - init_bottom3) < 0.001

    # 4. Drag South handle UP (offset_y < 0) -> should shrink
    dialog._drag_mode = "RESIZE_S"
    dialog._item_state_at_drag_start = item.copy()
    init_top4 = item.y - item.height / 2.0
    init_h4 = item.height
    dialog._on_canvas_drag_update(None, 0.0, -20.0)
    assert item.height < init_h4
    assert abs((item.y - item.height / 2.0) - init_top4) < 0.001

    # 5. Drag East handle RIGHT (offset_x > 0) -> should grow proportionally, left edge anchored
    dialog._drag_mode = "RESIZE_E"
    dialog._item_state_at_drag_start = item.copy()
    init_left5 = item.x - item.width / 2.0
    init_w5 = item.width
    dialog._on_canvas_drag_update(None, 40.0, 0.0)
    assert item.width > init_w5
    assert abs((item.x - item.width / 2.0) - init_left5) < 0.001

    # 6. Drag West handle LEFT (offset_x < 0) -> should grow proportionally, right edge anchored
    dialog._drag_mode = "RESIZE_W"
    dialog._item_state_at_drag_start = item.copy()
    init_right6 = item.x + item.width / 2.0
    init_w6 = item.width
    dialog._on_canvas_drag_update(None, -40.0, 0.0)
    assert item.width > init_w6
    assert abs((item.x + item.width / 2.0) - init_right6) < 0.001

    # 7. Drag NW corner UP-LEFT (offset_x < 0, offset_y < 0) -> grows, SE anchored
    dialog._drag_mode = "RESIZE_NW"
    dialog._item_state_at_drag_start = item.copy()
    init_se_x = item.x + item.width / 2.0
    init_se_y = item.y + item.height / 2.0
    init_w7 = item.width
    dialog._on_canvas_drag_update(None, -30.0, -30.0)
    assert item.width > init_w7
    assert abs((item.x + item.width / 2.0) - init_se_x) < 0.001
    assert abs((item.y + item.height / 2.0) - init_se_y) < 0.001

    # 8. Drag SE corner DOWN-RIGHT (offset_x > 0, offset_y > 0) -> grows, NW anchored
    dialog._drag_mode = "RESIZE_SE"
    dialog._item_state_at_drag_start = item.copy()
    init_nw_x = item.x - item.width / 2.0
    init_nw_y = item.y - item.height / 2.0
    init_w8 = item.width
    dialog._on_canvas_drag_update(None, 30.0, 30.0)
    assert item.width > init_w8
    assert abs((item.x - item.width / 2.0) - init_nw_x) < 0.001
    assert abs((item.y - item.height / 2.0) - init_nw_y) < 0.001


def test_overlay_dialog_font_selection_and_sidebar_properties(tmp_path: Path):
    """Test standard GTK FontDialogButton integration and fixed properties sidebar sizing."""
    video_path = tmp_path / "clip.mp4"
    video_path.write_bytes(b"dummy")

    seg = Segment(id="seg_test", source_path=video_path, start_time=0.0, end_time=5.0, source_duration=5.0)
    parent = Gtk.Window()
    dialog = OverlayDialog(parent=parent, segment=seg, on_apply=lambda x: None)

    # Verify sidebar has properties-sidebar CSS class and fixed 290px width request
    assert dialog.prop_sidebar.has_css_class("properties-sidebar")
    assert dialog.prop_sidebar.get_size_request()[0] == 290

    dialog.allocate(1120, 750, -1, None)
    assert dialog.prop_sidebar.get_width() >= 290

    # Add text element and verify properties panel update
    dialog._on_tool_add_text()
    text_item = dialog.selected_item
    assert text_item is not None
    assert text_item.item_type == "text"
    assert dialog.prop_sidebar.get_size_request()[0] == 290
    dialog.allocate(1120, 750, -1, None)
    assert dialog.prop_sidebar.get_width() >= 290

    # Properties panel rendered text controls including font dialog button
    children = []
    child = dialog.prop_container.get_first_child()
    while child:
        children.append(child)
        child = child.get_next_sibling()
    assert len(children) > 0

    # Switch to shape element and verify properties panel updates cleanly and maintains 290px width
    dialog._on_tool_add_shape("rect")
    rect_item = dialog.selected_item
    assert rect_item is not None
    assert rect_item.item_type == "rect"
    assert dialog.prop_sidebar.get_size_request()[0] == 290
    dialog.allocate(1120, 750, -1, None)
    assert dialog.prop_sidebar.get_width() >= 290


def test_overlay_dialog_position_and_size_spinners(tmp_path: Path):
    """Test Position (X, Y) and Size (W, H) spinbuttons and real-time 2-way sync."""
    video_path = tmp_path / "clip.mp4"
    video_path.write_bytes(b"dummy")

    seg = Segment(id="seg_test", source_path=video_path, start_time=0.0, end_time=5.0, source_duration=5.0)
    parent = Gtk.Window()
    dialog = OverlayDialog(parent=parent, segment=seg, on_apply=lambda x: None)

    dialog._on_tool_add_shape("rect")
    item = dialog.selected_item
    assert item is not None
    vw, vh = dialog._get_video_pixel_dimensions()

    assert dialog.spin_pos_x is not None
    assert dialog.spin_pos_y is not None
    assert dialog.spin_size_w is not None
    assert dialog.spin_size_h is not None

    # Initial spinner values should match item normalized coordinates
    assert abs(dialog.spin_pos_x.get_value() - item.x * vw) < 2
    assert abs(dialog.spin_pos_y.get_value() - item.y * vh) < 2
    assert abs(dialog.spin_size_w.get_value() - item.width * vw) < 2
    assert abs(dialog.spin_size_h.get_value() - item.height * vh) < 2

    # Change Position Spinners -> updates item
    dialog.spin_pos_x.set_value(300)
    assert abs(item.x - (300.0 / vw)) < 0.001

    dialog.spin_pos_y.set_value(200)
    assert abs(item.y - (200.0 / vh)) < 0.001

    # Change Size Spinners -> updates item
    dialog.spin_size_w.set_value(400)
    assert abs(item.width - (400.0 / vw)) < 0.001

    dialog.spin_size_h.set_value(250)
    assert abs(item.height - (250.0 / vh)) < 0.001

    # Allow off-canvas pasteboard coordinates (negative or > vw)
    dialog.spin_pos_x.set_value(-150)
    assert item.x < 0.0
    dialog.spin_pos_y.set_value(vh + 100)
    assert item.y > 1.0

    # Drag update syncs back to spinners
    item.x = 0.5
    item.y = 0.5
    dialog._sync_position_and_size_spinners(item)
    assert abs(dialog.spin_pos_x.get_value() - round(0.5 * vw)) < 1
    assert abs(dialog.spin_pos_y.get_value() - round(0.5 * vh)) < 1


def test_overlay_dialog_zoom_controls_and_pasteboard(tmp_path: Path):
    """Test deterministic canvas Zoom (In, Out, Fit) and pasteboard geometry."""
    video_path = tmp_path / "clip.mp4"
    video_path.write_bytes(b"dummy")

    seg = Segment(id="seg_test", source_path=video_path, start_time=0.0, end_time=5.0, source_duration=5.0)
    parent = Gtk.Window()
    dialog = OverlayDialog(parent=parent, segment=seg, on_apply=lambda x: None)

    # Initial zoom factor is 1.0 (Fit)
    assert dialog._zoom_factor == 1.0
    assert dialog.btn_zoom_fit is not None
    assert dialog.lbl_zoom is not None
    assert dialog.lbl_zoom.get_label() == "100%"

    # Zoom In steps deterministically
    dialog._zoom_in()
    assert dialog._zoom_factor == 1.25
    assert dialog.lbl_zoom.get_label() == "125%"

    dialog._zoom_in()
    assert dialog._zoom_factor == 1.5
    assert dialog.lbl_zoom.get_label() == "150%"

    # Zoom Out steps deterministically
    dialog._zoom_out()
    assert dialog._zoom_factor == 1.25
    assert dialog.lbl_zoom.get_label() == "125%"

    dialog._zoom_out()
    assert dialog._zoom_factor == 1.0
    assert dialog.lbl_zoom.get_label() == "100%"

    dialog._zoom_out()
    assert dialog._zoom_factor == 0.75
    assert dialog.lbl_zoom.get_label() == "75%"

    # Zoom Fit resets to 1.0
    dialog._zoom_fit()
    assert dialog._zoom_factor == 1.0
    assert dialog.lbl_zoom.get_label() == "100%"

    # Base pasteboard padding is at least 150px
    pad_x, pad_y, vid_w, vid_h = dialog._canvas_rect
    assert pad_x >= 150.0
    assert pad_y >= 150.0

    # Zoom out to 75%, 50%, and 25% visibly shrinks video dimensions
    dialog._zoom_out()
    assert dialog._zoom_factor == 0.75
    assert dialog.lbl_zoom.get_label() == "75%"
    vid_w_75 = dialog._canvas_rect[2]
    vid_h_75 = dialog._canvas_rect[3]
    assert vid_w_75 < vid_w
    assert vid_h_75 < vid_h

    dialog._zoom_out()
    assert dialog._zoom_factor == 0.5
    assert dialog.lbl_zoom.get_label() == "50%"
    vid_w_50 = dialog._canvas_rect[2]
    vid_h_50 = dialog._canvas_rect[3]
    assert vid_w_50 < vid_w_75
    assert vid_h_50 < vid_h

    dialog._zoom_out()
    assert dialog._zoom_factor == 0.25
    assert dialog.lbl_zoom.get_label() == "25%"
    vid_w_25 = dialog._canvas_rect[2]
    vid_h_25 = dialog._canvas_rect[3]
    assert vid_w_25 < vid_w_50
    assert vid_h_25 < vid_h

    # Reset to fit
    dialog._zoom_fit()
    assert dialog._zoom_factor == 1.0

    # Objects placed in the pasteboard outside the video bounds dynamically expand pasteboard
    dialog._on_tool_add_shape("circle")
    circle = dialog.selected_item
    assert circle is not None
    circle.x = -0.3
    circle.y = 1.3
    dialog._update_canvas_geometry()

    pad_x2, pad_y2, vid_w2, vid_h2 = dialog._canvas_rect
    # Test initial start centering and fit button scroll centering
    dialog._viewport_size = (800, 600)
    dialog._center_scroll_on_video()
    rx, ry, rw, rh = dialog._canvas_rect
    w, h = dialog._canvas_alloc_size
    hadj = dialog.canvas_scrolled.get_hadjustment()
    vadj = dialog.canvas_scrolled.get_vadjustment()
    target_x = max(0.0, min(float(w - 800), (rx + rw / 2.0) - 400.0))
    target_y = max(0.0, min(float(h - 600), (ry + rh / 2.0) - 300.0))
    assert abs(hadj.get_value() - target_x) < 2.0
    assert abs(vadj.get_value() - target_y) < 2.0

    # Manually scroll away
    hadj.set_value(0.0)
    vadj.set_value(0.0)
    assert hadj.get_value() != target_x or target_x == 0.0

    # Fit button centers scroll on video
    dialog._zoom_fit()
    dialog._center_scroll_on_video()
    rx, ry, rw, rh = dialog._canvas_rect
    w, h = dialog._canvas_alloc_size
    target_x = max(0.0, min(float(w - 800), (rx + rw / 2.0) - 400.0))
    target_y = max(0.0, min(float(h - 600), (ry + rh / 2.0) - 300.0))
    assert abs(hadj.get_value() - target_x) < 2.0
    assert abs(vadj.get_value() - target_y) < 2.0


def test_overlay_keep_proportions_toggle_and_sync(tmp_path: Path):
    """Test keep_aspect_ratio checkbox and 2-way spinbutton proportional updates."""
    video_path = tmp_path / "clip.mp4"
    video_path.write_bytes(b"dummy")

    seg = Segment(
        id="seg_test",
        source_path=video_path,
        start_time=0.0,
        end_time=5.0,
        source_duration=5.0,
    )
    parent = Gtk.Window()
    dialog = OverlayDialog(parent=parent, segment=seg, on_apply=lambda x: None)
    dialog._on_viewport_allocated(800, 600)

    dialog._on_tool_add_shape("rect")
    item = dialog.selected_item
    assert item is not None
    assert item.keep_aspect_ratio is False

    # Enable keep_aspect_ratio for proportional testing
    item.keep_aspect_ratio = True

    # Initial sizes
    init_w = item.width
    init_h = item.height
    aspect = init_h / init_w

    vw, vh = dialog._get_video_pixel_dimensions()

    # 1. Update W spinbutton -> H should update proportionally
    new_px_w = int(round(init_w * vw)) * 2
    dialog.spin_size_w.set_value(new_px_w)
    assert abs(item.width - (new_px_w / vw)) < 0.01
    assert abs((item.height / item.width) - aspect) < 0.01

    # 2. Update H spinbutton -> W should update proportionally
    cur_h = item.height
    new_px_h = int(round(cur_h * vh)) // 2
    dialog.spin_size_h.set_value(new_px_h)
    assert abs(item.height - (new_px_h / vh)) < 0.01
    assert abs((item.height / item.width) - aspect) < 0.01

    # 3. Disable keep_aspect_ratio
    item.keep_aspect_ratio = False
    cur_h = item.height
    new_px_w2 = int(round(item.width * vw)) + 50
    dialog.spin_size_w.set_value(new_px_w2)
    # W changed, but H should stay unchanged
    assert abs(item.width - (new_px_w2 / vw)) < 0.01
    assert abs(item.height - cur_h) < 0.001


def test_overlay_element_selection_dropdown_and_scroll(tmp_path: Path):
    """Test element selection dropdown listing items and viewport scrolling to item."""
    video_path = tmp_path / "clip.mp4"
    video_path.write_bytes(b"dummy")

    seg = Segment(
        id="seg_test",
        source_path=video_path,
        start_time=0.0,
        end_time=5.0,
        source_duration=5.0,
    )
    parent = Gtk.Window()
    dialog = OverlayDialog(parent=parent, segment=seg, on_apply=lambda x: None)
    dialog._on_viewport_allocated(800, 600)

    # Initially empty items -> disabled dropdown with "No elements"
    assert dialog.element_dropdown.get_sensitive() is False
    assert dialog.element_dropdown.get_model().get_n_items() == 1
    assert _("No elements") in dialog.element_dropdown.get_model().get_string(0)

    # Add text item (dropdown has [No selection, 1. Text: ...])
    dialog._on_tool_add_text()
    assert dialog.element_dropdown.get_model().get_n_items() == 2
    assert _("No selection") in dialog.element_dropdown.get_model().get_string(0)
    assert dialog._get_item_display_name(dialog.items[0]) in dialog.element_dropdown.get_model().get_string(1)
    assert dialog.element_dropdown.get_selected() == 1
    assert dialog.selected_item_id == dialog.items[0].id

    # Add circle item
    dialog._on_tool_add_shape("circle")
    assert dialog.element_dropdown.get_model().get_n_items() == 3
    assert dialog._get_item_display_name(dialog.items[1]) in dialog.element_dropdown.get_model().get_string(2)
    assert dialog.selected_item_id == dialog.items[1].id
    assert dialog.element_dropdown.get_selected() == 2

    # Add audio item
    audio_path = tmp_path / "track.mp3"
    audio_path.write_bytes(b"dummy_audio")
    dialog._add_audio_item(audio_path)
    assert dialog.element_dropdown.get_model().get_n_items() == 4
    assert dialog._get_item_display_name(dialog.items[2]) in dialog.element_dropdown.get_model().get_string(3)

    # Select first item from dropdown (index 1)
    dialog.element_dropdown.set_selected(1)
    dialog._on_element_dropdown_selected(dialog.element_dropdown, None)
    if hasattr(dialog, "_dropdown_idle_id") and dialog._dropdown_idle_id:
        GLib.source_remove(dialog._dropdown_idle_id)
        dialog._dropdown_idle_id = None
        dialog._on_dropdown_selection_idle(dialog.items[0])
    for _i in range(15):
        GLib.MainContext.default().iteration(False)
    assert dialog.selected_item_id == dialog.items[0].id

    # Select index 0 (No selection) -> deselects
    dialog.element_dropdown.set_selected(0)
    dialog._on_element_dropdown_selected(dialog.element_dropdown, None)
    if hasattr(dialog, "_dropdown_idle_id") and dialog._dropdown_idle_id:
        GLib.source_remove(dialog._dropdown_idle_id)
        dialog._dropdown_idle_id = None
        dialog._on_dropdown_selection_idle(None)
    for _i in range(15):
        GLib.MainContext.default().iteration(False)
    assert dialog.selected_item_id is None
    assert dialog.element_dropdown.get_selected() == 0

    # Verify scrolling to item
    dialog._scroll_to_item(dialog.items[0])
    hadj = dialog.canvas_scrolled.get_hadjustment()
    vadj = dialog.canvas_scrolled.get_vadjustment()
    assert hadj is not None
    assert vadj is not None


def test_overlay_video_enable_audio_and_audio_item(tmp_path: Path, monkeypatch):
    """Test video overlay allow audio checkbox and audio overlay item properties."""
    video_path = tmp_path / "clip.mp4"
    video_path.write_bytes(b"dummy")

    seg = Segment(
        id="seg_test",
        source_path=video_path,
        start_time=0.0,
        end_time=10.0,
        source_duration=10.0,
    )
    parent = Gtk.Window()
    dialog = OverlayDialog(parent=parent, segment=seg, on_apply=lambda x: None)
    dialog._on_viewport_allocated(800, 600)

    # 1. Video overlay allow audio
    sub_video = tmp_path / "sub.mp4"
    sub_video.write_bytes(b"dummy_video")
    dialog._add_media_item(sub_video)
    vid_item = dialog.selected_item
    assert vid_item is not None
    assert vid_item.item_type == "video"
    assert vid_item.enable_audio is False

    # Toggle allow audio
    dialog._set_item_attr(vid_item, "enable_audio", True)
    assert vid_item.enable_audio is True

    # 2. Audio overlay item
    audio_file = tmp_path / "song.mp3"
    audio_file.write_bytes(b"audio")

    # Mock ProbeService.probe_duration to return 45.0 seconds
    from video_cut.services.probe_service import ProbeService
    monkeypatch.setattr(ProbeService, "probe_duration", lambda p: 45.0)

    dialog._add_audio_item(audio_file)
    audio_item = dialog.selected_item
    assert audio_item is not None
    assert audio_item.item_type == "audio"
    assert audio_item.audio_start == 0.0
    assert audio_item.audio_end == 45.0
    assert audio_item.is_loop is False

    # Modify audio timing and loop
    dialog._set_item_attr(audio_item, "audio_start", 5.0)
    dialog._set_item_attr(audio_item, "audio_end", 30.0)
    dialog._set_item_attr(audio_item, "is_loop", True)

    assert audio_item.audio_start == 5.0
    assert audio_item.audio_end == 30.0
    assert audio_item.is_loop is True

    # 3. Size and Rotation properties must be hidden for audio items
    assert dialog.spin_size_w is None
    assert dialog.spin_size_h is None

    # Verify rotation label/input is not in properties container
    child = dialog.prop_container.get_first_child()
    labels = []
    while child:
        if isinstance(child, Gtk.Label):
            labels.append(child.get_label())
        elif isinstance(child, Gtk.Box):
            sub = child.get_first_child()
            while sub:
                if isinstance(sub, Gtk.Label):
                    labels.append(sub.get_label())
                sub = sub.get_next_sibling()
        child = child.get_next_sibling()
    assert _("Rotation:") not in labels
    assert _("Size (px):") not in labels

    # 4. On canvas screen, rotation and resize handles must be hidden (hit test returns NONE or MOVE only)
    dialog._canvas_rect = (0.0, 0.0, 1000.0, 500.0)
    audio_item.x = 0.5
    audio_item.y = 0.5
    audio_item.width = 0.3
    audio_item.height = 0.1
    # Center of audio item: (500, 250), half_w = 150, half_h = 25
    # Position where rotation stalk would be: local (0, -half_h - 22) -> (500, 203)
    mode, hit_item_id = dialog._hit_test(500.0, 203.0)
    assert mode != "ROTATE"

    # Position where NW corner resize handle would be: local (-150, -25) -> (350, 225)
    mode, hit_item_id = dialog._hit_test(350.0, 225.0)
    assert not mode.startswith("RESIZE_")

    # Inside audio badge:
    mode, hit_id = dialog._hit_test(500.0, 250.0)
    assert mode == "MOVE"
    assert hit_id == audio_item.id

    # 5. Press play with audio item present: must not segfault or crash
    dialog._start_playback()
    assert dialog.is_playing is True
    # Run several playback ticks
    for _step in range(5):
        dialog._on_playback_tick()
    dialog._pause_playback()
    assert dialog.is_playing is False
    dialog._cleanup()


def test_overlay_emoji_proportions_and_no_checkbox(tmp_path: Path):
    """Test that emoji items do not show keep proportions checkbox, and are always kept proportional."""
    video_path = tmp_path / "clip.mp4"
    video_path.write_bytes(b"dummy")

    seg = Segment(id="seg_1", source_path=video_path, start_time=0.0, end_time=5.0, source_duration=5.0)
    dialog = OverlayDialog(parent=Gtk.Window(), segment=seg, on_apply=lambda x: None)
    dialog._on_viewport_allocated(800, 600)

    dialog._add_emoji_item("🚀")
    item = dialog.selected_item
    assert item is not None
    assert item.item_type == "emoji"

    # Verify no CheckButton for "Keep proportions" in properties container
    chk_prop_found = False
    child = dialog.prop_container.get_first_child()
    while child:
        if isinstance(child, Gtk.CheckButton) and _("Keep proportions") in (child.get_label() or ""):
            chk_prop_found = True
            break
        child = child.get_next_sibling()
    assert not chk_prop_found

    # Changing W changes H proportionally
    init_w = item.width
    init_h = item.height
    dialog.spin_size_w.set_value(dialog.spin_size_w.get_value() * 2)
    assert abs(item.width - init_w * 2) < 0.05
    assert abs(item.height - init_h * 2) < 0.05

    # Changing H changes W proportionally
    prev_w = item.width
    prev_h = item.height
    dialog.spin_size_h.set_value(dialog.spin_size_h.get_value() * 0.5)
    assert abs(item.height - prev_h * 0.5) < 0.05
    assert abs(item.width - prev_w * 0.5) < 0.05

    # Test canvas resize drag maintains proportions
    dialog._canvas_rect = (0.0, 0.0, 800.0, 600.0)
    dialog._drag_mode = "RESIZE_SE"
    dialog._drag_start_x = 400.0
    dialog._drag_start_y = 300.0
    dialog._item_state_at_drag_start = item.copy()
    initial_ratio = item.height / item.width
    dialog._on_canvas_drag_update(None, 50.0, 50.0)
    new_ratio = item.height / item.width
    assert abs(new_ratio - initial_ratio) < 0.01
    dialog._cleanup()


def test_overlay_compact_xy_and_wh_rows(tmp_path: Path):
    """Test that X/Y and W/H inputs have headers, are placed side by side, and have full tooltips."""
    video_path = tmp_path / "clip.mp4"
    video_path.write_bytes(b"dummy")

    seg = Segment(id="seg_1", source_path=video_path, start_time=0.0, end_time=5.0, source_duration=5.0)
    dialog = OverlayDialog(parent=Gtk.Window(), segment=seg, on_apply=lambda x: None)
    dialog._on_viewport_allocated(800, 600)

    # Sidebar width should be 290px
    assert dialog.prop_sidebar.get_size_request() == (290, -1)

    dialog._on_tool_add_shape("rect")
    assert dialog.spin_pos_x is not None
    assert dialog.spin_pos_y is not None
    assert dialog.spin_size_w is not None
    assert dialog.spin_size_h is not None

    # Check headers above position and size
    child = dialog.prop_container.get_first_child()
    headers = []
    while child:
        if isinstance(child, Gtk.Label) and child.has_css_class("dim-label"):
            headers.append(child.get_label())
        child = child.get_next_sibling()
    assert _("Position (px):") in headers
    assert _("Size (px):") in headers

    # X and Y must share the same parent container (single line)
    assert dialog.spin_pos_x.get_parent() is not None
    assert dialog.spin_pos_x.get_parent() == dialog.spin_pos_y.get_parent()

    # W and H must share the same parent container (single line)
    assert dialog.spin_size_w.get_parent() is not None
    assert dialog.spin_size_w.get_parent() == dialog.spin_size_h.get_parent()

    # Check tooltips on W and H spinbuttons
    assert dialog.spin_size_w.get_tooltip_text() == _("Width")
    assert dialog.spin_size_h.get_tooltip_text() == _("Height")
    dialog._cleanup()


def test_overlay_layer_shortcuts(tmp_path: Path):
    """Test keyboard shortcuts for layer organization (Page Up/Down, Home/End, Ctrl+[, Ctrl+])."""
    video_path = tmp_path / "clip.mp4"
    video_path.write_bytes(b"dummy")

    seg = Segment(id="seg_1", source_path=video_path, start_time=0.0, end_time=5.0, source_duration=5.0)
    dialog = OverlayDialog(parent=Gtk.Window(), segment=seg, on_apply=lambda x: None)
    dialog._on_viewport_allocated(800, 600)

    dialog._on_tool_add_shape("rect")     # item 0
    dialog._on_tool_add_shape("circle")   # item 1
    dialog._on_tool_add_shape("square")   # item 2

    _item0, item1, _item2 = dialog.items[0], dialog.items[1], dialog.items[2]

    # Select middle item (item1, index 1)
    dialog.selected_item_id = item1.id
    dialog._refresh_canvas_and_properties()

    # 1. Page_Up -> moves up from index 1 to index 2
    handled = dialog._on_key_pressed(None, Gdk.KEY_Page_Up, 0, Gdk.ModifierType(0))
    assert handled is True
    assert dialog.items.index(item1) == 2

    # 2. Page_Down -> moves down from index 2 to index 1
    handled = dialog._on_key_pressed(None, Gdk.KEY_Page_Down, 0, Gdk.ModifierType(0))
    assert handled is True
    assert dialog.items.index(item1) == 1

    # 3. Home -> moves to top (index 2)
    handled = dialog._on_key_pressed(None, Gdk.KEY_Home, 0, Gdk.ModifierType(0))
    assert handled is True
    assert dialog.items.index(item1) == 2

    # 4. End -> moves to bottom (index 0)
    handled = dialog._on_key_pressed(None, Gdk.KEY_End, 0, Gdk.ModifierType(0))
    assert handled is True
    assert dialog.items.index(item1) == 0

    # 5. Ctrl+] -> moves up from index 0 to index 1
    handled = dialog._on_key_pressed(None, Gdk.KEY_bracketright, 0, Gdk.ModifierType.CONTROL_MASK)
    assert handled is True
    assert dialog.items.index(item1) == 1

    # 6. Ctrl+[ -> moves down from index 1 to index 0
    handled = dialog._on_key_pressed(None, Gdk.KEY_bracketleft, 0, Gdk.ModifierType.CONTROL_MASK)
    assert handled is True
    assert dialog.items.index(item1) == 0

    # 7. Ctrl+Shift+] -> moves to top (index 2)
    handled = dialog._on_key_pressed(None, Gdk.KEY_bracketright, 0, Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK)
    assert handled is True
    assert dialog.items.index(item1) == 2

    # 8. Ctrl+Shift+[ -> moves to bottom (index 0)
    handled = dialog._on_key_pressed(None, Gdk.KEY_bracketleft, 0, Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK)
    assert handled is True
    assert dialog.items.index(item1) == 0
    dialog._cleanup()


def test_overlay_text_properties_improvements(tmp_path: Path):
    """Test text multi-line TextView, vertical alignment, and unconstrained proportions."""
    video_path = tmp_path / "clip.mp4"
    video_path.write_bytes(b"dummy")

    seg = Segment(id="seg_1", source_path=video_path, start_time=0.0, end_time=5.0, source_duration=5.0)
    dialog = OverlayDialog(parent=Gtk.Window(), segment=seg, on_apply=lambda x: None)
    dialog._on_viewport_allocated(800, 600)

    dialog._on_tool_add_text()
    item = dialog.selected_item
    assert item is not None
    assert item.item_type == "text"

    # Text box should NOT have "Keep proportions" checkbox
    chk_prop_found = False
    child = dialog.prop_container.get_first_child()
    while child:
        if isinstance(child, Gtk.CheckButton) and _("Keep proportions") in (child.get_label() or ""):
            chk_prop_found = True
            break
        child = child.get_next_sibling()
    assert not chk_prop_found

    # Text box should behave as unconstrained: changing W does not change H
    init_h = item.height
    dialog.spin_size_w.set_value(dialog.spin_size_w.get_value() + 50)
    assert item.height == init_h

    # Changing H does not change W
    init_w = item.width
    dialog.spin_size_h.set_value(dialog.spin_size_h.get_value() + 50)
    assert item.width == init_w

    # Find TextView, alignment buttons, and color buttons
    text_view = None
    scrolled_win = None
    italic_label = None
    bold_label = None
    color_buttons = []
    h_align_buttons = []
    v_align_buttons = []

    def inspect_widget(w):
        nonlocal text_view, scrolled_win, italic_label, bold_label
        if isinstance(w, Gtk.ScrolledWindow) and scrolled_win is None:
            scrolled_win = w
        elif isinstance(w, Gtk.TextView):
            text_view = w
        elif isinstance(w, Gtk.ColorDialogButton):
            color_buttons.append(w)
        elif isinstance(w, Gtk.ToggleButton):
            icon = w.get_icon_name()
            if icon and "format-justify" in icon:
                h_align_buttons.append(w)
            elif icon and ("format-valign" in icon or "align-vertical" in icon):
                v_align_buttons.append(w)
            child = w.get_child()
            if isinstance(child, Gtk.Label):
                if "I" in child.get_label():
                    italic_label = child
                elif "B" in child.get_label():
                    bold_label = child
        c = w.get_first_child()
        while c:
            inspect_widget(c)
            c = c.get_next_sibling()

    inspect_widget(dialog.prop_container)

    assert text_view is not None
    assert scrolled_win is not None
    # Verify text area vertical size is 50% larger (min 90, max 150)
    assert scrolled_win.get_min_content_height() == 90
    assert scrolled_win.get_max_content_height() == 150

    # Test setting multi-line text and auto-fitting vertical height
    init_h = item.height
    buf = text_view.get_buffer()
    buf.set_text("Line 1\nLine 2\nLine 3\nLine 4\nLine 5 with long text that wraps around nicely\nLine 6")
    assert "Line 1" in item.text
    # Height should have expanded to fit the extra lines
    assert item.height > init_h

    # Verify horizontal alignment buttons (left, center, right)
    assert len(h_align_buttons) == 3
    h_align_buttons[0].set_active(True)
    assert item.text_align == "left"
    h_align_buttons[2].set_active(True)
    assert item.text_align == "right"
    h_align_buttons[1].set_active(True)
    assert item.text_align == "center"

    # Verify vertical alignment buttons (top, middle, bottom)
    assert len(v_align_buttons) == 3
    v_align_buttons[0].set_active(True)
    assert item.text_valign == "top"
    v_align_buttons[2].set_active(True)
    assert item.text_valign == "bottom"
    v_align_buttons[1].set_active(True)
    assert item.text_valign == "middle"

    assert italic_label is not None
    assert bold_label is not None
    dialog._cleanup()


def test_overlay_opacity_and_shape_keep_proportions_default(tmp_path: Path):
    """Test that square and circle have keep_aspect_ratio=False by default,
    and text, shapes, emoji, and media have Opacity slider in properties."""
    video_path = tmp_path / "clip.mp4"
    video_path.write_bytes(b"dummy")

    seg = Segment(id="seg_1", source_path=video_path, start_time=0.0, end_time=5.0, source_duration=5.0)
    dialog = OverlayDialog(parent=Gtk.Window(), segment=seg, on_apply=lambda x: None)
    dialog._on_viewport_allocated(800, 600)

    # 1. Square / Rect: keep_aspect_ratio should be False by default
    dialog._on_tool_add_shape("rect")
    rect_item = dialog.selected_item
    assert rect_item is not None
    assert rect_item.keep_aspect_ratio is False

    # Opacity slider must be present for shape
    scales: list[Gtk.Scale] = []
    def find_scales(w: Gtk.Widget) -> None:
        if isinstance(w, Gtk.Scale):
            scales.append(w)
        child = w.get_first_child()
        while child:
            find_scales(child)
            child = child.get_next_sibling()
    find_scales(dialog.prop_container)
    assert len(scales) >= 1
    # Check value can change item opacity
    op_scale = scales[-1]
    op_scale.set_value(0.75)
    assert abs(rect_item.opacity - 0.75) < 0.01

    # 2. Circle: keep_aspect_ratio should be False by default
    dialog._on_tool_add_shape("circle")
    circle_item = dialog.selected_item
    assert circle_item is not None
    assert circle_item.keep_aspect_ratio is False

    # Opacity slider present for circle
    scales.clear()
    find_scales(dialog.prop_container)
    assert len(scales) >= 1
    circle_scale = scales[-1]
    circle_scale.set_value(0.5)
    assert abs(circle_item.opacity - 0.5) < 0.01

    # 3. Text: Opacity slider must be present
    dialog._on_tool_add_text()
    text_item = dialog.selected_item
    assert text_item is not None
    scales.clear()
    find_scales(dialog.prop_container)
    assert len(scales) >= 1
    text_scale = scales[-1]
    text_scale.set_value(0.8)
    assert abs(text_item.opacity - 0.8) < 0.01

    dialog._cleanup()


def test_overlay_undo_redo_movement(tmp_path: Path):
    """Test that moving an overlay via canvas drag or spinners properly records undo and redo states."""
    video_path = tmp_path / "clip.mp4"
    video_path.write_bytes(b"dummy")

    seg = Segment(id="seg_1", source_path=video_path, start_time=0.0, end_time=5.0, source_duration=5.0)
    dialog = OverlayDialog(parent=Gtk.Window(), segment=seg, on_apply=lambda x: None)
    dialog._on_viewport_allocated(800, 600)

    dialog._on_tool_add_shape("rect")
    item = dialog.selected_item
    assert item is not None
    orig_x, orig_y = item.x, item.y

    # Simulate canvas drag movement
    rx, ry, rw, rh = dialog._canvas_rect
    start_x = rx + orig_x * rw
    start_y = ry + orig_y * rh

    dialog._on_canvas_drag_begin(None, start_x, start_y)
    dialog._on_canvas_drag_update(None, 40.0, 50.0)
    dialog._on_canvas_drag_end(None, 40.0, 50.0)

    moved_x, moved_y = item.x, item.y
    assert moved_x != orig_x
    assert moved_y != orig_y

    # Undo should restore pre-drag position
    dialog.undo()
    assert abs(dialog.selected_item.x - orig_x) < 1e-4
    assert abs(dialog.selected_item.y - orig_y) < 1e-4

    # Redo should restore moved position
    dialog.redo()
    assert abs(dialog.selected_item.x - moved_x) < 1e-4
    assert abs(dialog.selected_item.y - moved_y) < 1e-4

    dialog._cleanup()


def test_overlay_header_global_delete_and_sidebar_delete(tmp_path: Path):
    """Test global delete in headerbar and regular delete button beside dropdown."""
    video_path = tmp_path / "clip.mp4"
    video_path.write_bytes(b"dummy")

    applied_items: list[list[OverlayItem]] = []
    seg = Segment(id="seg_1", source_path=video_path, start_time=0.0, end_time=5.0, source_duration=5.0)
    dialog = OverlayDialog(parent=Gtk.Window(), segment=seg, on_apply=lambda items: applied_items.append(items))
    dialog._on_viewport_allocated(800, 600)

    # 1. Global delete button in header bar
    assert hasattr(dialog, "btn_delete_all")
    assert dialog.btn_delete_all is not None

    # 2. Sidebar delete button next to dropdown
    assert hasattr(dialog, "btn_delete_selected_prop")
    # Initially no selection -> disabled
    assert dialog.btn_delete_selected_prop.get_sensitive() is False

    dialog._on_tool_add_shape("rect")
    assert dialog.btn_delete_selected_prop.get_sensitive() is True
    assert len(dialog.items) == 1

    # Verify orange badge title is NOT present in properties container
    labels: list[str] = []
    def find_labels(w: Gtk.Widget):
        if isinstance(w, Gtk.Label):
            labels.append(w.get_label())
        child = w.get_first_child()
        while child:
            find_labels(child)
            child = child.get_next_sibling()
    find_labels(dialog.prop_container)
    assert _("Rectangle") not in labels

    # Click sidebar delete button -> deletes element
    dialog.btn_delete_selected_prop.emit("clicked")
    assert len(dialog.items) == 0
    assert dialog.selected_item_id is None
    assert dialog.btn_delete_selected_prop.get_sensitive() is False

    # Add items back and test global delete
    dialog._on_tool_add_shape("rect")
    dialog._on_tool_add_text()
    assert len(dialog.items) == 2
    assert dialog.btn_delete_all.get_sensitive() is True

    # Test AlertDialog choose callback with "cancel" -> items should not be cleared
    from unittest.mock import MagicMock, patch
    with patch.object(Adw.AlertDialog, "choose") as mock_choose, patch.object(Adw.AlertDialog, "choose_finish", return_value="cancel"):
        def fake_choose_cancel(*args, **kwargs):
            cb = args[-1] if args else kwargs.get("callback")
            if callable(cb):
                cb(None, MagicMock())
        mock_choose.side_effect = fake_choose_cancel
        dialog._on_delete_all_clicked()
        assert len(dialog.items) == 2

    # Test AlertDialog choose callback with "delete" -> clears items, applies [], closes
    with patch.object(Adw.AlertDialog, "choose") as mock_choose, patch.object(Adw.AlertDialog, "choose_finish", return_value="delete"):
        def fake_choose_delete(*args, **kwargs):
            cb = args[-1] if args else kwargs.get("callback")
            if callable(cb):
                cb(None, MagicMock())
        mock_choose.side_effect = fake_choose_delete
        dialog._on_delete_all_clicked()
        assert len(dialog.items) == 0
        assert len(applied_items) >= 1
        assert applied_items[-1] == []

    dialog._cleanup()


def test_overlay_delete_all_translations():
    """Verify that Delete All and dialog button texts are properly translated in pt_BR."""
    from video_cut.i18n import _, init_i18n
    init_i18n("pt_BR")
    assert _("Delete All") == "Excluir tudo"
    assert _("Remove All Overlays?") == "Remover todas as sobreposições?"
    assert _("Cancel") == "Cancelar"


def test_overlay_text_properties_order_and_combined_color_align(tmp_path: Path):
    """Test that text properties puts Size/Bold/Italic before Color, combines colors, and combines align."""
    video_path = tmp_path / "clip.mp4"
    video_path.write_bytes(b"dummy")

    seg = Segment(id="seg_1", source_path=video_path, start_time=0.0, end_time=5.0, source_duration=5.0)
    dialog = OverlayDialog(parent=Gtk.Window(), segment=seg, on_apply=lambda x: None)
    dialog._on_viewport_allocated(800, 600)

    dialog._on_tool_add_text()
    item = dialog.selected_item
    assert item is not None

    # Check labels in property container
    labels: list[str] = []
    def find_labels(w: Gtk.Widget):
        if isinstance(w, Gtk.Label):
            lbl = w.get_label()
            if lbl:
                labels.append(lbl)
        child = w.get_first_child()
        while child:
            find_labels(child)
            child = child.get_next_sibling()
    find_labels(dialog.prop_container)

    # Size: must come before Color:
    assert _("Size:") in labels
    assert _("Color:") in labels
    size_idx = labels.index(_("Size:"))
    color_idx = labels.index(_("Color:"))
    assert size_idx < color_idx

    # No standalone "Background:" label (it's combined under "Color:")
    assert _("Background:") not in labels

    # No standalone "Vertical:" label (vertical align is inline under "Align:")
    assert _("Vertical:") not in labels
    assert _("Align:") in labels

    dialog._cleanup()


def test_segment_card_double_click_opens_overlay(tmp_path: Path):
    """Test that double clicking a SegmentCard calls on_overlay."""
    from video_cut.segment_card import SegmentCard
    video_path = tmp_path / "clip.mp4"
    video_path.write_bytes(b"dummy")

    overlay_requested_ids: list[str] = []
    seg = Segment(id="seg_card_test", source_path=video_path, start_time=0.0, end_time=5.0, source_duration=5.0)

    card = SegmentCard(
        segment=seg,
        index=0,
        is_selected=False,
        on_select=lambda sid: None,
        on_overlay=lambda sid: overlay_requested_ids.append(sid),
    )

    # Double click on filmstrip triggers on_overlay
    card._on_filmstrip_click(None, 2, 50.0, 30.0)
    assert len(overlay_requested_ids) == 1
    assert overlay_requested_ids[0] == "seg_card_test"


def test_overlay_font_dialog_and_safe_fallback(tmp_path: Path):
    """Test that font dialog and safe font fallback prevent crashes."""
    import cairo

    from video_cut.services.overlay_service import OverlayRenderService

    video_path = tmp_path / "clip.mp4"
    video_path.write_bytes(b"dummy")

    seg = Segment(id="seg_font_test", source_path=video_path, start_time=0.0, end_time=5.0, source_duration=5.0)
    parent = Gtk.Window()
    dialog = OverlayDialog(parent=parent, segment=seg, on_apply=lambda x: None)

    # Adding text element works cleanly
    dialog._on_tool_add_text()

    # Test safe font fallback in OverlayRenderService._render_text:
    # Even if an invalid font family or non-existent font name is given, it renders safely
    item = OverlayItem(
        id="item_text_invalid_font",
        item_type="text",
        text="Special Characters: áéíóú çãõ 🚀",
        font_family="NonExistentFont12345!@#$",
        font_size=32,
        width=0.5,
        height=0.3,
    )
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 200, 200)
    cr = cairo.Context(surface)
    # Should not raise exception
    OverlayRenderService._render_text(cr, item, item.text, 200.0, 100.0, 1080.0)
    surface.flush()

    # Verify canvas redraw also completes safely without crashing
    dialog.items.append(item)
    dialog._render_overlay_canvas()

    dialog._cleanup()


def test_overlay_empty_text_renders_no_text_and_no_sample_text(tmp_path: Path):
    """Test that when a text item is empty, no text is rendered (no fallback to 'Sample Text')."""
    import cairo

    from video_cut.services.overlay_service import OverlayRenderService

    video_path = tmp_path / "clip.mp4"
    video_path.write_bytes(b"dummy")

    seg = Segment(id="seg_empty_text", source_path=video_path, start_time=0.0, end_time=5.0, source_duration=5.0)
    parent = Gtk.Window()
    dialog = OverlayDialog(parent=parent, segment=seg, on_apply=lambda x: None)

    dialog._on_tool_add_text()
    item = dialog.selected_item
    assert item is not None
    assert item.item_type == "text"

    # Now simulate user clearing the text in properties text area
    item.text = ""

    # Check dropdown display name for empty text
    display_name = dialog._get_item_display_name(item)
    assert display_name == _("Text")
    assert "Sample Text" not in display_name

    # Render on transparent surface and verify no pixel is drawn when text is empty
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 200, 200)
    cr = cairo.Context(surface)
    OverlayRenderService.render_item(cr, item, 200.0, 100.0)
    surface.flush()
    # All pixels remain completely transparent/empty (0)
    assert not any(b != 0 for b in bytes(surface.get_data()))

    # Redraw canvas does not crash and renders with no text
    dialog._render_overlay_canvas()
    dialog._cleanup()


def test_overlay_cut_copy_paste_and_context_menu(tmp_path):
    """Test cut, copy, paste, duplicate, keyboard shortcuts, and context menu in overlay editor."""
    video_path = tmp_path / "test.mp4"
    video_path.touch()
    seg = Segment(id="seg_cp_test", source_path=video_path, start_time=0.0, end_time=5.0, source_duration=5.0)
    parent = Gtk.Window()
    dialog = OverlayDialog(parent=parent, segment=seg, on_apply=lambda x: None)

    # 1. Add item
    dialog._on_tool_add_text()
    original_item = dialog.selected_item
    assert original_item is not None
    original_item.text = "CopyPasteTest"
    orig_id = original_item.id

    # 2. Test Copy
    dialog._copy_selected()
    assert OverlayDialog._clipboard_item is not None
    assert OverlayDialog._clipboard_item.text == "CopyPasteTest"
    assert OverlayDialog._can_paste(dialog) is True

    # 3. Test Paste
    dialog._paste()
    assert len(dialog.items) == 2
    pasted_item = dialog.selected_item
    assert pasted_item is not None
    assert pasted_item.id != orig_id
    assert pasted_item.text == "CopyPasteTest"

    # 4. Test Duplicate
    dialog._duplicate_selected()
    assert len(dialog.items) == 3
    dup_item = dialog.selected_item
    assert dup_item is not None
    assert dup_item.id != pasted_item.id
    assert dup_item.text == "CopyPasteTest"

    # 5. Test Cut
    cut_id = dup_item.id
    dialog._cut_selected()
    assert len(dialog.items) == 2
    assert all(it.id != cut_id for it in dialog.items)
    assert OverlayDialog._clipboard_item is not None
    assert OverlayDialog._clipboard_item.text == "CopyPasteTest"

    # 6. Test Paste again after Cut
    dialog._paste()
    assert len(dialog.items) == 3

    # 7. Test Undo / Redo for cut & paste
    dialog.undo()
    assert len(dialog.items) == 2
    dialog.redo()
    assert len(dialog.items) == 3

    # 8. Test Keyboard shortcuts Ctrl+C, Ctrl+V, Ctrl+X, Ctrl+D
    # Ctrl+C
    ctrl = Gdk.ModifierType.CONTROL_MASK
    handled = dialog._on_key_pressed(None, Gdk.KEY_c, 0, ctrl)
    assert handled is True

    # Ctrl+V
    handled = dialog._on_key_pressed(None, Gdk.KEY_v, 0, ctrl)
    assert handled is True
    assert len(dialog.items) == 4

    # Ctrl+D
    handled = dialog._on_key_pressed(None, Gdk.KEY_d, 0, ctrl)
    assert handled is True
    assert len(dialog.items) == 5

    # Ctrl+X
    handled = dialog._on_key_pressed(None, Gdk.KEY_x, 0, ctrl)
    assert handled is True
    assert len(dialog.items) == 4

    # 9. Test Context Menu popup and closing
    dialog._on_canvas_right_click(None, 1, 100.0, 100.0)
    assert hasattr(dialog, "_context_popover")
    assert dialog._context_popover is not None

    # Close context menu
    dialog._context_popover.popdown()

    # 10. Verify z-order shortcuts (Page_Up, Page_Down, Home, End) work after context menu
    dialog.selected_item_id = dialog.items[1].id
    assert dialog.selected_item is not None
    cur_z = dialog.selected_item.z_index

    # Page_Down
    handled = dialog._on_key_pressed(None, Gdk.KEY_Page_Down, 0, Gdk.ModifierType(0))
    assert handled is True
    assert dialog.selected_item.z_index < cur_z

    # Page_Up
    cur_z = dialog.selected_item.z_index
    handled = dialog._on_key_pressed(None, Gdk.KEY_Page_Up, 0, Gdk.ModifierType(0))
    assert handled is True
    assert dialog.selected_item.z_index > cur_z

    # Home (bring to top)
    handled = dialog._on_key_pressed(None, Gdk.KEY_Home, 0, Gdk.ModifierType(0))
    assert handled is True
    assert dialog.selected_item.z_index == len(dialog.items) - 1

    # End (send to bottom)
    handled = dialog._on_key_pressed(None, Gdk.KEY_End, 0, Gdk.ModifierType(0))
    assert handled is True
    assert dialog.selected_item.z_index == 0

    dialog._cleanup()


class MockGesture:
    def __init__(self, state=Gdk.ModifierType(0)):
        self._state = state

    def get_current_event_state(self):
        return self._state


def test_multi_element_selection_ctrl_shift_click(tmp_path: Path):
    """Test multi-element selection using Ctrl+click and Shift+click."""
    video_path = tmp_path / "test.mp4"
    video_path.touch()
    seg = Segment(id="seg_multi_1", source_path=video_path, start_time=0.0, end_time=5.0, source_duration=5.0)
    dialog = OverlayDialog(parent=Gtk.Window(), segment=seg, on_apply=lambda x: None)
    dialog._on_viewport_allocated(800, 600)

    # Add 3 items
    dialog._on_tool_add_shape("rect")
    item1 = dialog.selected_item
    assert item1 is not None
    item1.x, item1.y = 0.2, 0.2

    dialog._on_tool_add_shape("circle")
    item2 = dialog.selected_item
    assert item2 is not None
    item2.x, item2.y = 0.5, 0.5

    dialog._on_tool_add_text()
    item3 = dialog.selected_item
    assert item3 is not None
    item3.x, item3.y = 0.8, 0.8

    assert dialog.selected_item_ids == {item3.id}

    rx, ry, rw, rh = dialog._canvas_rect
    pt1_x, pt1_y = rx + item1.x * rw, ry + item1.y * rh
    pt2_x, pt2_y = rx + item2.x * rw, ry + item2.y * rh

    # Ctrl+click item1 -> {item1.id, item3.id}
    ctrl_gesture = MockGesture(Gdk.ModifierType.CONTROL_MASK)
    dialog._on_canvas_click(ctrl_gesture, 1, pt1_x, pt1_y)
    assert dialog.selected_item_ids == {item1.id, item3.id}
    assert len(dialog.selected_items) == 2

    # Shift+click item2 -> {item1.id, item2.id, item3.id}
    shift_gesture = MockGesture(Gdk.ModifierType.SHIFT_MASK)
    dialog._on_canvas_click(shift_gesture, 1, pt2_x, pt2_y)
    assert dialog.selected_item_ids == {item1.id, item2.id, item3.id}
    assert len(dialog.selected_items) == 3

    # Ctrl+click item1 again -> toggles off -> {item2.id, item3.id}
    dialog._on_canvas_click(ctrl_gesture, 1, pt1_x, pt1_y)
    assert dialog.selected_item_ids == {item2.id, item3.id}

    # Click empty space without modifiers -> deselects all
    none_gesture = MockGesture(Gdk.ModifierType(0))
    dialog._on_canvas_click(none_gesture, 1, rx + 0.01 * rw, ry + 0.99 * rh)
    assert len(dialog.selected_item_ids) == 0

    dialog._cleanup()


def test_multi_element_rubberband_selection(tmp_path: Path):
    """Test rubberband marquee rectangle drag selection."""
    video_path = tmp_path / "test.mp4"
    video_path.touch()
    seg = Segment(id="seg_rb_test", source_path=video_path, start_time=0.0, end_time=5.0, source_duration=5.0)
    dialog = OverlayDialog(parent=Gtk.Window(), segment=seg, on_apply=lambda x: None)
    dialog._on_viewport_allocated(800, 600)

    dialog._on_tool_add_shape("rect")
    item1 = dialog.selected_item
    assert item1 is not None
    item1.x, item1.y = 0.2, 0.2
    item1.width, item1.height = 0.1, 0.1

    dialog._on_tool_add_shape("rect")
    item2 = dialog.selected_item
    assert item2 is not None
    item2.x, item2.y = 0.35, 0.25
    item2.width, item2.height = 0.1, 0.1

    dialog._on_tool_add_shape("rect")
    item3 = dialog.selected_item
    assert item3 is not None
    item3.x, item3.y = 0.8, 0.8
    item3.width, item3.height = 0.1, 0.1

    rx, ry, rw, rh = dialog._canvas_rect

    # Start rubberband drag at (0.1, 0.1) in normalized coords
    start_x = rx + 0.1 * rw
    start_y = ry + 0.1 * rh
    none_gesture = MockGesture(Gdk.ModifierType(0))
    dialog._on_canvas_drag_begin(none_gesture, start_x, start_y)
    assert dialog._drag_mode == "RUBBERBAND"

    # Drag over items 1 and 2 to (0.45, 0.35)
    offset_x = 0.35 * rw
    offset_y = 0.25 * rh
    dialog._on_canvas_drag_update(none_gesture, offset_x, offset_y)
    assert item1.id in dialog.selected_item_ids
    assert item2.id in dialog.selected_item_ids
    assert item3.id not in dialog.selected_item_ids

    # Finish rubberband drag
    dialog._on_canvas_drag_end(none_gesture, offset_x, offset_y)
    assert dialog._drag_mode == "NONE"
    assert dialog.selected_item_ids == {item1.id, item2.id}

    # Verify system accent color resolution
    accent_rgb = dialog._get_system_accent_color()
    assert isinstance(accent_rgb, tuple) and len(accent_rgb) == 3
    assert all(0.0 <= val <= 1.0 for val in accent_rgb)

    dialog._cleanup()


def test_multi_element_alignment_and_distribution(tmp_path: Path):
    """Test inter-object alignment and distribution."""
    video_path = tmp_path / "test.mp4"
    video_path.touch()
    seg = Segment(id="seg_align_test", source_path=video_path, start_time=0.0, end_time=5.0, source_duration=5.0)
    dialog = OverlayDialog(parent=Gtk.Window(), segment=seg, on_apply=lambda x: None)
    dialog._on_viewport_allocated(800, 600)

    dialog._on_tool_add_shape("rect")
    item1 = dialog.selected_item
    assert item1 is not None
    item1.x, item1.y, item1.width, item1.height = 0.2, 0.2, 0.1, 0.1

    dialog._on_tool_add_shape("rect")
    item2 = dialog.selected_item
    assert item2 is not None
    item2.x, item2.y, item2.width, item2.height = 0.4, 0.5, 0.1, 0.1

    dialog._on_tool_add_shape("rect")
    item3 = dialog.selected_item
    assert item3 is not None
    item3.x, item3.y, item3.width, item3.height = 0.8, 0.8, 0.1, 0.1

    dialog.selected_item_ids = {item1.id, item2.id, item3.id}

    # Bounds: min_x = 0.2 - 0.05 = 0.15, max_x = 0.8 + 0.05 = 0.85
    #         min_y = 0.2 - 0.05 = 0.15, max_y = 0.8 + 0.05 = 0.85

    def reset_positions():
        item1.x, item1.y = 0.2, 0.2
        item2.x, item2.y = 0.4, 0.5
        item3.x, item3.y = 0.8, 0.8

    # 1. Align Left -> all items left edge should equal min_x (0.15)
    reset_positions()
    dialog._align_selected("left")
    for it in dialog.selected_items:
        assert round(it.x - it.width / 2.0, 3) == 0.15

    # 2. Align Right -> all items right edge should equal max_x (0.85)
    reset_positions()
    dialog._align_selected("right")
    for it in dialog.selected_items:
        assert round(it.x + it.width / 2.0, 3) == 0.85

    # 3. Align Center -> all items x should equal (0.15 + 0.85)/2 = 0.5
    reset_positions()
    dialog._align_selected("center")
    for it in dialog.selected_items:
        assert round(it.x, 3) == 0.5

    # 4. Align Top -> all items top edge should equal min_y (0.15)
    reset_positions()
    dialog._align_selected("top")
    for it in dialog.selected_items:
        assert round(it.y - it.height / 2.0, 3) == 0.15

    # 5. Align Bottom -> all items bottom edge should equal max_y (0.85)
    reset_positions()
    dialog._align_selected("bottom")
    for it in dialog.selected_items:
        assert round(it.y + it.height / 2.0, 3) == 0.85

    # 6. Align Middle -> all items y should equal 0.5
    reset_positions()
    dialog._align_selected("middle")
    for it in dialog.selected_items:
        assert round(it.y, 3) == 0.5

    # 7. Distribute Horizontally
    item1.x = 0.1
    item2.x = 0.2
    item3.x = 0.9
    dialog._distribute_selected("horizontal")
    # Distance between item1 and item2 should equal distance between item2 and item3
    d1 = item2.x - item1.x
    d2 = item3.x - item2.x
    assert abs(d1 - d2) < 0.001

    # 8. Distribute Vertically
    item1.y = 0.1
    item2.y = 0.2
    item3.y = 0.9
    dialog._distribute_selected("vertical")
    d1_y = item2.y - item1.y
    d2_y = item3.y - item2.y
    assert abs(d1_y - d2_y) < 0.001

    dialog._cleanup()


def test_multi_element_move_and_resize(tmp_path: Path):
    """Test moving and resizing multiple selected elements."""
    video_path = tmp_path / "test.mp4"
    video_path.touch()
    seg = Segment(id="seg_mv_rs_test", source_path=video_path, start_time=0.0, end_time=5.0, source_duration=5.0)
    dialog = OverlayDialog(parent=Gtk.Window(), segment=seg, on_apply=lambda x: None)
    dialog._on_viewport_allocated(800, 600)

    dialog._on_tool_add_shape("rect")
    item1 = dialog.selected_item
    assert item1 is not None
    item1.x, item1.y, item1.width, item1.height = 0.2, 0.3, 0.1, 0.1

    dialog._on_tool_add_shape("rect")
    item2 = dialog.selected_item
    assert item2 is not None
    item2.x, item2.y, item2.width, item2.height = 0.4, 0.5, 0.1, 0.1

    dialog.selected_item_ids = {item1.id, item2.id}
    rx, ry, rw, rh = dialog._canvas_rect

    # 1. Multi Move via drag
    start_x = rx + item1.x * rw
    start_y = ry + item1.y * rh
    none_gesture = MockGesture(Gdk.ModifierType(0))
    dialog._on_canvas_drag_begin(none_gesture, start_x, start_y)
    assert dialog._drag_mode == "MOVE_MULTI"

    dialog._on_canvas_drag_update(none_gesture, 0.1 * rw, 0.05 * rh)
    assert round(item1.x, 2) == 0.3
    assert round(item1.y, 2) == 0.35
    assert round(item2.x, 2) == 0.5
    assert round(item2.y, 2) == 0.55

    dialog._on_canvas_drag_end(none_gesture, 0.1 * rw, 0.05 * rh)
    assert dialog._drag_mode == "NONE"

    # 2. Keyboard arrow nudge on multi selection
    dialog._on_key_pressed(None, Gdk.KEY_Right, 0, Gdk.ModifierType.SHIFT_MASK)
    # Both items should have shifted to the right by 10px
    vw, _ = dialog._get_video_pixel_dimensions()
    step = 10.0 / float(vw)
    assert abs(item1.x - (0.3 + step)) < 0.001
    assert abs(item2.x - (0.5 + step)) < 0.001

    # 3. Multi Resize via bounding box East handle
    bounds = dialog._get_multi_selection_bounds()
    assert bounds is not None
    min_x, min_y, max_x, max_y = bounds
    east_x = rx + max_x * rw
    east_y = ry + ((min_y + max_y) / 2.0) * rh

    dialog._on_canvas_drag_begin(none_gesture, east_x, east_y)
    assert dialog._drag_mode == "RESIZE_MULTI_E"

    init_w1 = item1.width
    init_w2 = item2.width
    dialog._on_canvas_drag_update(none_gesture, 0.1 * rw, 0.0)
    assert item1.width > init_w1
    assert item2.width > init_w2

    dialog._on_canvas_drag_end(none_gesture, 0.1 * rw, 0.0)
    assert dialog._drag_mode == "NONE"

    dialog._cleanup()


def test_multi_element_cut_copy_paste_duplicate_delete(tmp_path: Path):
    """Test cut, copy, paste, duplicate, and delete with multiple selected elements."""
    video_path = tmp_path / "test.mp4"
    video_path.touch()
    seg = Segment(id="seg_multi_edit", source_path=video_path, start_time=0.0, end_time=5.0, source_duration=5.0)
    dialog = OverlayDialog(parent=Gtk.Window(), segment=seg, on_apply=lambda x: None)
    dialog._on_viewport_allocated(800, 600)

    dialog._on_tool_add_shape("rect")
    item1 = dialog.selected_item
    assert item1 is not None

    dialog._on_tool_add_text()
    item2 = dialog.selected_item
    assert item2 is not None

    dialog.selected_item_ids = {item1.id, item2.id}
    assert len(dialog.selected_items) == 2

    # 1. Multi Copy
    dialog._copy_selected()
    assert OverlayDialog._clipboard_items is not None
    assert len(OverlayDialog._clipboard_items) == 2

    # 2. Multi Paste
    dialog._paste()
    assert len(dialog.items) == 4
    assert len(dialog.selected_items) == 2
    for it in dialog.selected_items:
        assert it.id != item1.id and it.id != item2.id

    # 3. Multi Duplicate
    dialog._duplicate_selected()
    assert len(dialog.items) == 6
    assert len(dialog.selected_items) == 2

    # 4. Multi Delete
    dialog._on_delete_selected()
    assert len(dialog.items) == 4
    assert len(dialog.selected_items) == 0

    # 5. Multi Cut
    dialog.selected_item_ids = {dialog.items[0].id, dialog.items[1].id}
    dialog._cut_selected()
    assert len(dialog.items) == 2
    assert len(dialog.selected_items) == 0

    # 6. Undo restores the cut items
    dialog.undo()
    assert len(dialog.items) == 4

    dialog._cleanup()


def test_multi_element_properties_panel(tmp_path: Path):
    """Test that properties panel adapts when multiple elements are selected."""
    video_path = tmp_path / "test.mp4"
    video_path.touch()
    seg = Segment(id="seg_prop_multi", source_path=video_path, start_time=0.0, end_time=5.0, source_duration=5.0)
    dialog = OverlayDialog(parent=Gtk.Window(), segment=seg, on_apply=lambda x: None)
    dialog._on_viewport_allocated(800, 600)

    # Add 2 text elements
    dialog._on_tool_add_text()
    item1 = dialog.selected_item
    dialog._on_tool_add_text()
    item2 = dialog.selected_item
    assert item1 is not None and item2 is not None

    dialog.selected_item_ids = {item1.id, item2.id}
    dialog._update_properties_panel()

    labels: list[str] = []
    def find_labels(w: Gtk.Widget):
        if isinstance(w, Gtk.Label):
            lbl = w.get_label()
            if lbl:
                labels.append(lbl)
        child = w.get_first_child()
        while child:
            find_labels(child)
            child = child.get_next_sibling()
    find_labels(dialog.prop_container)

    # Dropdown displays translated count
    cur_model = dialog.element_dropdown.get_model()
    assert cur_model.get_string(1) == _("({count} elements selected)").format(count=2)

    # Multi selection alignment header is present at the top
    assert any("Alignment & Distribution" in lbl_text or "Alinhamento e distribuição" in lbl_text for lbl_text in labels)
    # Redundant count header is NOT in prop_container (it's in the dropdown above)
    assert not any("2 elements selected" in lbl_text or "2 elementos selecionados" in lbl_text for lbl_text in labels)

    # Both are text -> text formatting controls appear
    assert _("Size:") in labels
    assert _("Color:") in labels
    assert _("Position (px):") in labels
    assert _("Size (px):") in labels
    assert _("Layer:") in labels

    # Now add a shape -> heterogeneous selection
    dialog._on_tool_add_shape("rect")
    shape_item = dialog.selected_item
    assert shape_item is not None

    dialog.selected_item_ids = {item1.id, shape_item.id}
    dialog._update_properties_panel()

    labels.clear()
    find_labels(dialog.prop_container)
    assert any("Alignment & Distribution" in lbl_text or "Alinhamento e distribuição" in lbl_text for lbl_text in labels)
    assert _("Position (px):") in labels
    assert _("Size (px):") in labels
    assert _("Layer:") in labels
    # Heterogeneous: neither Text Size nor Shape Fill Color/Border should appear
    assert _("Size:") not in labels
    assert _("Fill Color:") not in labels
    assert _("Border:") not in labels

    dialog._cleanup()


def test_overlay_context_menu_scroll_stability_and_separator_removal(tmp_path: Path):
    """Test separator removal, context menu styling (no arrow, square-menu), and stationary scroll on move."""
    video_path = tmp_path / "clip.mp4"
    video_path.write_bytes(b"dummy")

    seg = Segment(id="seg_test", source_path=video_path, start_time=0.0, end_time=5.0, source_duration=5.0)
    parent = Gtk.Window()
    dialog = OverlayDialog(parent=parent, segment=seg, on_apply=lambda x: None)

    # 1. Separator between properties sidebar and canvas was removed
    assert not hasattr(dialog, "prop_separator")

    # 2. Context popover styling and configuration
    dialog._on_tool_add_text()
    dialog._show_context_menu(150.0, 200.0)
    assert dialog._context_popover is not None
    assert dialog._context_popover.get_has_arrow() is False
    assert dialog._context_popover.get_autohide() is True
    assert dialog._context_popover.get_halign() == Gtk.Align.START
    assert dialog._context_popover.get_position() == Gtk.PositionType.BOTTOM
    assert dialog._context_popover.has_css_class("menu")
    assert dialog._context_popover.has_css_class("square-menu")

    # 3. Canvas scroll stability when moving elements
    dialog._viewport_size = (800, 600)
    dialog._update_canvas_geometry(center_on_video=True)
    dialog._center_scroll_on_video()
    hadj = dialog.canvas_scrolled.get_hadjustment()
    vadj = dialog.canvas_scrolled.get_vadjustment()
    initial_h = hadj.get_value()
    initial_v = vadj.get_value()

    # Move selected item within visible/video bounds
    assert dialog.selected_item is not None
    dialog.selected_item.x += 0.05
    dialog.selected_item.y += 0.05
    dialog._update_canvas_geometry()

    # Scroll should remain stationary (not jump or shift)
    assert hadj.get_value() == initial_h
    assert vadj.get_value() == initial_v

    dialog._cleanup()


def test_overlay_multi_alignment_icons_and_select_all(tmp_path: Path):
    """Test multi-element alignment icons (image-style) and Select All (method, Ctrl+A, context menu)."""
    video_path = tmp_path / "clip.mp4"
    video_path.write_bytes(b"dummy")

    seg = Segment(id="seg_test", source_path=video_path, start_time=0.0, end_time=5.0, source_duration=5.0)
    parent = Gtk.Window()
    dialog = OverlayDialog(parent=parent, segment=seg, on_apply=lambda x: None)

    dialog._on_tool_add_text()
    dialog._on_tool_add_shape("rect")
    dialog._on_tool_add_shape("circle")
    assert len(dialog.items) == 3

    # 1. Multi-element alignment icons: ensure regular object alignment icons are used
    dialog.selected_item_ids = {it.id for it in dialog.items}
    dialog._update_properties_panel()

    icon_names = []
    def find_button_icons(widget):
        if isinstance(widget, Gtk.Button):
            icon = widget.get_icon_name()
            if icon:
                icon_names.append(icon)
        child = widget.get_first_child()
        while child:
            find_button_icons(child)
            child = child.get_next_sibling()

    find_button_icons(dialog.prop_container)
    assert "align-horizontal-left-symbolic" in icon_names
    assert "align-horizontal-center-symbolic" in icon_names
    assert "align-horizontal-right-symbolic" in icon_names
    assert "align-vertical-top-symbolic" in icon_names
    assert "align-vertical-center-symbolic" in icon_names
    assert "align-vertical-bottom-symbolic" in icon_names
    # Should NOT use text justification icons for multi-element alignment
    assert "format-justify-left-symbolic" not in icon_names
    assert "format-valign-top-symbolic" not in icon_names

    # 2. _select_all() method
    dialog.selected_item_ids.clear()
    dialog._refresh_canvas_and_properties()
    assert len(dialog.selected_item_ids) == 0

    dialog._select_all()
    assert len(dialog.selected_item_ids) == 3
    assert dialog.selected_item_ids == {it.id for it in dialog.items}

    # 3. Ctrl+A shortcut
    dialog.selected_item_ids.clear()
    dialog._refresh_canvas_and_properties()
    res = dialog._on_key_pressed(None, Gdk.KEY_a, 0, Gdk.ModifierType.CONTROL_MASK)
    assert res is True
    assert len(dialog.selected_item_ids) == 3

    # 4. Context menu Select All button
    dialog.selected_item_ids.clear()
    dialog._refresh_canvas_and_properties()
    dialog._show_context_menu(100.0, 100.0)

    select_all_btn = None
    def find_select_all(widget):
        nonlocal select_all_btn
        if isinstance(widget, Gtk.Button):
            child = widget.get_child()
            if child and isinstance(child, Gtk.Box):
                row_child = child.get_first_child()
                while row_child:
                    if isinstance(row_child, Gtk.Label) and (row_child.get_label() == _("Select All") or "Select All" in row_child.get_label()):
                        select_all_btn = widget
                        return
                    row_child = row_child.get_next_sibling()
        child = widget.get_first_child()
        while child:
            find_select_all(child)
            child = child.get_next_sibling()

    find_select_all(dialog._context_popover.get_child())
    assert select_all_btn is not None
    assert select_all_btn.get_sensitive() is True

    # Clicking the Select All button in context menu selects all items
    select_all_btn.emit("clicked")
    assert len(dialog.selected_item_ids) == 3

    dialog._cleanup()








