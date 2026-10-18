"""Modal dialog for interactive Crop, Rotate, and Flip transformations on video segments."""

from __future__ import annotations
import io
import math
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Optional, Tuple
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gtk, Gdk, Adw, GdkPixbuf, GLib
import cairo

from .models import Segment
from .services.probe_service import ProbeService
from .utils import clamp


# Aspect ratio preset dictionary: label -> ratio (width / height) or None
ASPECT_PRESETS = [
    ("Free", None),
    ("Original", -1.0),
    ("16:9", 16.0 / 9.0),
    ("9:16", 9.0 / 16.0),
    ("1:1", 1.0),
    ("4:3", 4.0 / 3.0),
    ("21:9", 21.0 / 9.0),
]


class CropRotateDialog(Adw.Window):
    """Modal dialog allowing users to crop, rotate, and flip a video segment with live preview."""

    def __init__(
        self,
        parent: Gtk.Window,
        segment: Segment,
        on_apply: Callable[[Optional[Tuple[float, float, float, float]], int, bool, bool], None],
        current_time: Optional[float] = None,
    ) -> None:
        super().__init__()
        self.set_transient_for(parent)
        self.set_modal(True)
        self.set_destroy_with_parent(True)
        self.set_default_size(780, 680)

        self.segment = segment
        self.on_apply = on_apply
        self.current_time = current_time

        # Transform state
        self.rotation: int = segment.rotation % 360
        self.hflip: bool = bool(segment.hflip)
        self.vflip: bool = bool(segment.vflip)
        self.crop: Tuple[float, float, float, float] = (
            segment.crop if segment.crop is not None else (0.0, 0.0, 1.0, 1.0)
        )
        self.selected_preset: str = "Free"

        # Frame pixbufs and surfaces
        self.base_pixbuf: Optional[GdkPixbuf.Pixbuf] = None
        self._transformed_pixbuf: Optional[GdkPixbuf.Pixbuf] = None
        self._cairo_frame_surface: Optional[cairo.ImageSurface] = None
        self._load_reference_frame()

        # Canvas geometry caches
        self._img_rect: Tuple[int, int, int, int] = (0, 0, 1, 1)  # (x, y, w, h) in preview coordinates
        self._drag_mode: str = "NONE"
        self._drag_start_x: float = 0.0
        self._drag_start_y: float = 0.0
        self._crop_at_drag_start: Tuple[float, float, float, float] = self.crop

        title = f"Crop & Rotate — {segment.source_name}"
        self.set_title(title)

        self._build_ui()
        self._update_transformed_pixbuf()

    def _load_reference_frame(self) -> None:
        """Extract or load a reference frame for the preview."""
        path = self.segment.source_path
        ext = path.suffix.lower()

        # 1. If it's a still image file, load directly
        if ext in (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".svg"):
            try:
                self.base_pixbuf = GdkPixbuf.Pixbuf.new_from_file(str(path))
                return
            except Exception:
                pass

        # 2. Extract frame from video using FFmpeg
        target_t = (
            self.current_time
            if (self.current_time is not None and self.segment.start_time <= self.current_time <= self.segment.end_time)
            else (self.segment.start_time + self.segment.end_time) / 2.0
        )

        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            tmp_path = Path(tmp.name)

        try:
            cmd = [
                "ffmpeg",
                "-y",
                "-ss",
                f"{target_t:.3f}",
                "-i",
                str(path),
                "-frames:v",
                "1",
                "-q:v",
                "2",
                str(tmp_path),
            ]
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
            if tmp_path.exists() and tmp_path.stat().st_size > 0:
                self.base_pixbuf = GdkPixbuf.Pixbuf.new_from_file(str(tmp_path))
        except Exception:
            pass
        finally:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass

        # 3. Fallback to existing segment thumbnail
        if self.base_pixbuf is None and self.segment.thumbnails:
            for thumb in self.segment.thumbnails:
                if thumb.exists():
                    try:
                        self.base_pixbuf = GdkPixbuf.Pixbuf.new_from_file(str(thumb))
                        break
                    except Exception:
                        pass

        # 4. Final placeholder fallback if nothing could be loaded
        if self.base_pixbuf is None:
            self.base_pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 640, 360)
            self.base_pixbuf.fill(0x333333FF)

    def _update_transformed_pixbuf(self) -> None:
        """Apply current rotation and flips to base_pixbuf and update the rendered frame surface."""
        if not self.base_pixbuf:
            return

        pb = self.base_pixbuf
        rot = self.rotation % 360
        if rot == 90:
            pb = pb.rotate_simple(GdkPixbuf.PixbufRotation.CLOCKWISE)
        elif rot == 180:
            pb = pb.rotate_simple(GdkPixbuf.PixbufRotation.UPSIDEDOWN)
        elif rot == 270:
            pb = pb.rotate_simple(GdkPixbuf.PixbufRotation.COUNTERCLOCKWISE)

        if self.hflip:
            pb = pb.flip(True)
        if self.vflip:
            pb = pb.flip(False)

        self._transformed_pixbuf = pb
        try:
            ok, data = pb.save_to_bufferv("png", [], [])
            if ok:
                self._cairo_frame_surface = cairo.ImageSurface.create_from_png(io.BytesIO(data))
            else:
                self._cairo_frame_surface = None
        except Exception:
            self._cairo_frame_surface = None

        self._update_readout_label()
        self._render_preview()

    def _build_ui(self) -> None:
        toolbar_view = Adw.ToolbarView()
        self.set_content(toolbar_view)

        # Header bar
        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(False)
        header.set_show_start_title_buttons(False)

        btn_cancel = Gtk.Button(label="Cancel")
        btn_cancel.connect("clicked", lambda _: self.close())
        header.pack_start(btn_cancel)

        self.btn_apply = Gtk.Button(label="Apply")
        self.btn_apply.add_css_class("suggested-action")
        self.btn_apply.connect("clicked", self._on_apply_clicked)
        header.pack_end(self.btn_apply)

        toolbar_view.add_top_bar(header)

        # Main vertical container
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        main_box.set_margin_start(16)
        main_box.set_margin_end(16)
        main_box.set_margin_top(12)
        main_box.set_margin_bottom(16)
        toolbar_view.set_content(main_box)

        # Interactive Picture preview (avoids PyGObject cairo.Context foreign converter bug)
        self.preview_picture = Gtk.Picture()
        self.preview_picture.set_vexpand(True)
        self.preview_picture.set_hexpand(True)
        self.preview_picture.set_can_shrink(True)
        self.preview_picture.set_content_fit(Gtk.ContentFit.CONTAIN)

        # Gestures on preview picture
        self._setup_preview_gestures()
        main_box.append(self.preview_picture)

        # Re-render when widget size changes
        self.preview_picture.connect("notify::width", lambda *a: self._render_preview())
        self.preview_picture.connect("notify::height", lambda *a: self._render_preview())

        # Controls container
        controls_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        main_box.append(controls_box)

        # Toolbar Row 1: Rotation & Flip buttons + Reset
        row1 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        row1.set_halign(Gtk.Align.CENTER)

        # Transform group (linked)
        tf_group = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        tf_group.add_css_class("linked")

        btn_rot_ccw = Gtk.Button.new_from_icon_name("object-rotate-left-symbolic")
        btn_rot_ccw.set_tooltip_text("Rotate 90° Counter-Clockwise")
        btn_rot_ccw.connect("clicked", lambda _: self._rotate_ccw())
        tf_group.append(btn_rot_ccw)

        btn_rot_cw = Gtk.Button.new_from_icon_name("object-rotate-right-symbolic")
        btn_rot_cw.set_tooltip_text("Rotate 90° Clockwise")
        btn_rot_cw.connect("clicked", lambda _: self._rotate_cw())
        tf_group.append(btn_rot_cw)

        btn_flip_h = Gtk.Button.new_from_icon_name("object-flip-horizontal-symbolic")
        btn_flip_h.set_tooltip_text("Flip Horizontally")
        btn_flip_h.connect("clicked", lambda _: self._toggle_hflip())
        tf_group.append(btn_flip_h)

        btn_flip_v = Gtk.Button.new_from_icon_name("object-flip-vertical-symbolic")
        btn_flip_v.set_tooltip_text("Flip Vertically")
        btn_flip_v.connect("clicked", lambda _: self._toggle_vflip())
        tf_group.append(btn_flip_v)

        row1.append(tf_group)

        # Reset button
        btn_reset = Gtk.Button()
        reset_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        reset_icon = Gtk.Image.new_from_icon_name("view-refresh-symbolic")
        reset_lbl = Gtk.Label(label="Reset")
        reset_box.append(reset_icon)
        reset_box.append(reset_lbl)
        btn_reset.set_child(reset_box)
        btn_reset.set_tooltip_text("Reset crop, rotation, and flip")
        btn_reset.connect("clicked", lambda _: self._reset_transforms())
        row1.append(btn_reset)

        controls_box.append(row1)

        # Toolbar Row 2: Aspect Ratio Presets
        row2 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row2.set_halign(Gtk.Align.CENTER)

        lbl_aspect = Gtk.Label(label="Aspect Ratio:")
        lbl_aspect.add_css_class("dim-label")
        row2.append(lbl_aspect)

        presets_group = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        presets_group.add_css_class("linked")
        self._preset_buttons: dict[str, Gtk.ToggleButton] = {}

        for label, _ in ASPECT_PRESETS:
            btn = Gtk.ToggleButton(label=label)
            if label == "Free":
                btn.set_active(True)
            btn.connect("toggled", self._on_preset_toggled, label)
            presets_group.append(btn)
            self._preset_buttons[label] = btn

        row2.append(presets_group)
        controls_box.append(row2)

        # Readout badge
        self.lbl_readout = Gtk.Label()
        self.lbl_readout.add_css_class("dim-label")
        self.lbl_readout.set_halign(Gtk.Align.CENTER)
        controls_box.append(self.lbl_readout)

    def _update_readout_label(self) -> None:
        """Update resolution and crop readout text."""
        if not self._transformed_pixbuf:
            return
        img_w = self._transformed_pixbuf.get_width()
        img_h = self._transformed_pixbuf.get_height()
        cx, cy, cw, ch = self.crop
        crop_w = int(round(cw * img_w))
        crop_h = int(round(ch * img_h))
        rot_str = f" • {self.rotation}°" if self.rotation != 0 else ""
        flip_str = ""
        if self.hflip and self.vflip:
            flip_str = " • Flip H+V"
        elif self.hflip:
            flip_str = " • Flip H"
        elif self.vflip:
            flip_str = " • Flip V"

        self.lbl_readout.set_label(f"Crop: {crop_w} × {crop_h} px ({self.selected_preset}){rot_str}{flip_str}")

    def _render_preview(self) -> None:
        """Render the composite preview image (transformed frame + shroud + crop box + handles)."""
        if not self._cairo_frame_surface:
            return

        w = self.preview_picture.get_width()
        h = self.preview_picture.get_height()
        if w <= 50 or h <= 50:
            w = 740
            h = 440

        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
        cr = cairo.Context(surf)

        # 1. Dark canvas background
        cr.set_source_rgb(0.12, 0.12, 0.14)
        cr.paint()

        img_w = self._cairo_frame_surface.get_width()
        img_h = self._cairo_frame_surface.get_height()
        if img_w <= 0 or img_h <= 0:
            return

        # 2. Fit transformed video frame with 24px margins
        pad = 24.0
        avail_w = max(10.0, w - 2.0 * pad)
        avail_h = max(10.0, h - 2.0 * pad)
        scale = min(avail_w / float(img_w), avail_h / float(img_h))

        dest_w = int(round(img_w * scale))
        dest_h = int(round(img_h * scale))
        dest_x = int(round((w - dest_w) / 2.0))
        dest_y = int(round((h - dest_h) / 2.0))
        self._img_rect = (dest_x, dest_y, dest_w, dest_h)

        # Draw frame
        cr.save()
        cr.translate(dest_x, dest_y)
        cr.scale(scale, scale)
        cr.set_source_surface(self._cairo_frame_surface, 0, 0)
        cr.paint()
        cr.restore()

        # 3. Crop Box geometry in pixels
        cx, cy, cw, ch = self.crop
        bx = dest_x + cx * dest_w
        by = dest_y + cy * dest_h
        bw = cw * dest_w
        bh = ch * dest_h

        # 4. Draw darkened shroud outside crop rectangle
        cr.set_source_rgba(0.0, 0.0, 0.0, 0.6)
        if by > dest_y:
            cr.rectangle(dest_x, dest_y, dest_w, by - dest_y)
            cr.fill()
        if by + bh < dest_y + dest_h:
            cr.rectangle(dest_x, by + bh, dest_w, (dest_y + dest_h) - (by + bh))
            cr.fill()
        if bx > dest_x:
            cr.rectangle(dest_x, by, bx - dest_x, bh)
            cr.fill()
        if bx + bw < dest_x + dest_w:
            cr.rectangle(bx + bw, by, (dest_x + dest_w) - (bx + bw), bh)
            cr.fill()

        # 5. Rule of thirds grid lines
        cr.save()
        cr.set_source_rgba(1.0, 1.0, 1.0, 0.35)
        cr.set_line_width(1.0)
        cr.set_dash([4.0, 4.0])
        cr.move_to(bx + bw / 3.0, by)
        cr.line_to(bx + bw / 3.0, by + bh)
        cr.move_to(bx + 2.0 * bw / 3.0, by)
        cr.line_to(bx + 2.0 * bw / 3.0, by + bh)
        cr.move_to(bx, by + bh / 3.0)
        cr.line_to(bx + bw, by + bh / 3.0)
        cr.move_to(bx, by + 2.0 * bh / 3.0)
        cr.line_to(bx + bw, by + 2.0 * bh / 3.0)
        cr.stroke()
        cr.restore()

        # 6. Main crop box border
        cr.set_source_rgba(0.0, 0.0, 0.0, 0.5)
        cr.set_line_width(3.0)
        cr.rectangle(bx, by, bw, bh)
        cr.stroke()

        cr.set_source_rgba(1.0, 1.0, 1.0, 0.95)
        cr.set_line_width(1.5)
        cr.rectangle(bx, by, bw, bh)
        cr.stroke()

        # 7. Handles: 4 corner brackets and 4 edge midpoint bars
        cr.set_source_rgba(1.0, 1.0, 1.0, 1.0)
        cr.set_line_width(3.0)
        handle_len = 16.0

        # Corner NW
        cr.move_to(bx, by + handle_len)
        cr.line_to(bx, by)
        cr.line_to(bx + handle_len, by)
        # Corner NE
        cr.move_to(bx + bw - handle_len, by)
        cr.line_to(bx + bw, by)
        cr.line_to(bx + bw, by + handle_len)
        # Corner SW
        cr.move_to(bx, by + bh - handle_len)
        cr.line_to(bx, by + bh)
        cr.line_to(bx + handle_len, by + bh)
        # Corner SE
        cr.move_to(bx + bw - handle_len, by + bh)
        cr.line_to(bx + bw, by + bh)
        cr.line_to(bx + bw, by + bh - handle_len)
        cr.stroke()

        # Edge handles (midpoints)
        mid_bar = 8.0
        # N edge
        cr.move_to(bx + bw / 2.0 - mid_bar, by)
        cr.line_to(bx + bw / 2.0 + mid_bar, by)
        # S edge
        cr.move_to(bx + bw / 2.0 - mid_bar, by + bh)
        cr.line_to(bx + bw / 2.0 + mid_bar, by + bh)
        # W edge
        cr.move_to(bx, by + bh / 2.0 - mid_bar)
        cr.line_to(bx, by + bh / 2.0 + mid_bar)
        # E edge
        cr.move_to(bx + bw, by + bh / 2.0 - mid_bar)
        cr.line_to(bx + bw, by + bh / 2.0 + mid_bar)
        cr.stroke()

        # 8. Convert to Gdk.Texture and set on Gtk.Picture
        try:
            bio = io.BytesIO()
            surf.write_to_png(bio)
            bio.seek(0)
            glib_bytes = GLib.Bytes.new(bio.read())
            texture = Gdk.Texture.new_from_bytes(glib_bytes)
            self.preview_picture.set_paintable(texture)
        except Exception as e:
            pass

    def _setup_preview_gestures(self) -> None:
        # 1. Motion controller for cursor updates
        motion = Gtk.EventControllerMotion.new()
        motion.connect("motion", self._on_mouse_motion)
        motion.connect("leave", self._on_mouse_leave)
        self.preview_picture.add_controller(motion)

        # 2. Drag gesture for crop adjustment
        drag = Gtk.GestureDrag.new()
        drag.connect("drag-begin", self._on_drag_begin)
        drag.connect("drag-update", self._on_drag_update)
        drag.connect("drag-end", self._on_drag_end)
        self.preview_picture.add_controller(drag)

    def _hit_test(self, mx: float, my: float) -> str:
        """Return the handle or region under cursor (mx, my)."""
        dest_x, dest_y, dest_w, dest_h = self._img_rect
        if dest_w <= 0 or dest_h <= 0:
            return "NONE"

        cx, cy, cw, ch = self.crop
        bx = dest_x + cx * dest_w
        by = dest_y + cy * dest_h
        bw = cw * dest_w
        bh = ch * dest_h

        thresh = 14.0

        # Corners
        if abs(mx - bx) <= thresh and abs(my - by) <= thresh:
            return "RESIZE_NW"
        if abs(mx - (bx + bw)) <= thresh and abs(my - by) <= thresh:
            return "RESIZE_NE"
        if abs(mx - bx) <= thresh and abs(my - (by + bh)) <= thresh:
            return "RESIZE_SW"
        if abs(mx - (bx + bw)) <= thresh and abs(my - (by + bh)) <= thresh:
            return "RESIZE_SE"

        # Edges
        if abs(mx - bx) <= thresh and by <= my <= by + bh:
            return "RESIZE_W"
        if abs(mx - (bx + bw)) <= thresh and by <= my <= by + bh:
            return "RESIZE_E"
        if abs(my - by) <= thresh and bx <= mx <= bx + bw:
            return "RESIZE_N"
        if abs(my - (by + bh)) <= thresh and bx <= mx <= bx + bw:
            return "RESIZE_S"

        # Inside crop box
        if bx < mx < bx + bw and by < my < by + bh:
            return "MOVE"

        # Outside
        return "OUTSIDE"

    def _on_mouse_motion(self, _c: Gtk.EventControllerMotion, x: float, y: float) -> None:
        if self._drag_mode != "NONE":
            return
        hit = self._hit_test(x, y)
        cursor_name = "default"
        if hit in ("RESIZE_NW", "RESIZE_SE"):
            cursor_name = "nwse-resize"
        elif hit in ("RESIZE_NE", "RESIZE_SW"):
            cursor_name = "nesw-resize"
        elif hit in ("RESIZE_W", "RESIZE_E"):
            cursor_name = "ew-resize"
        elif hit in ("RESIZE_N", "RESIZE_S"):
            cursor_name = "ns-resize"
        elif hit == "MOVE":
            cursor_name = "move"

        self.preview_picture.set_cursor_from_name(cursor_name)

    def _on_mouse_leave(self, _c: Gtk.EventControllerMotion) -> None:
        if self._drag_mode == "NONE":
            self.preview_picture.set_cursor_from_name("default")

    def _on_drag_begin(self, gesture: Gtk.GestureDrag, start_x: float, start_y: float) -> None:
        self._drag_mode = self._hit_test(start_x, start_y)
        self._drag_start_x = start_x
        self._drag_start_y = start_y
        self._crop_at_drag_start = self.crop

    def _on_drag_update(self, gesture: Gtk.GestureDrag, offset_x: float, offset_y: float) -> None:
        if self._drag_mode == "NONE" or self._drag_mode == "OUTSIDE":
            return

        dest_x, dest_y, dest_w, dest_h = self._img_rect
        if dest_w <= 0 or dest_h <= 0:
            return

        ndx = offset_x / float(dest_w)
        ndy = offset_y / float(dest_h)

        init_x, init_y, init_w, init_h = self._crop_at_drag_start
        min_dim = 0.05

        if self._drag_mode == "MOVE":
            nx = clamp(init_x + ndx, 0.0, 1.0 - init_w)
            ny = clamp(init_y + ndy, 0.0, 1.0 - init_h)
            self.crop = (nx, ny, init_w, init_h)
        else:
            # Resizing
            target_ratio = self._get_current_aspect_ratio()
            nx, ny, nw, nh = init_x, init_y, init_w, init_h

            if "W" in self._drag_mode:
                max_w = init_x + init_w
                nw = clamp(init_w - ndx, min_dim, max_w)
                nx = init_x + (init_w - nw)
            elif "E" in self._drag_mode:
                nw = clamp(init_w + ndx, min_dim, 1.0 - init_x)

            if "N" in self._drag_mode:
                max_h = init_y + init_h
                nh = clamp(init_h - ndy, min_dim, max_h)
                ny = init_y + (init_h - nh)
            elif "S" in self._drag_mode:
                nh = clamp(init_h + ndy, min_dim, 1.0 - init_y)

            # Apply aspect ratio constraint if active
            if target_ratio is not None and self._transformed_pixbuf:
                img_pixel_ratio = self._transformed_pixbuf.get_width() / float(self._transformed_pixbuf.get_height())
                # Desired (nw / nh) in normalized coordinates: target_ratio / img_pixel_ratio
                norm_ratio = target_ratio / img_pixel_ratio

                if self._drag_mode in ("RESIZE_W", "RESIZE_E"):
                    nh = clamp(nw / norm_ratio, min_dim, 1.0)
                    ny = clamp(init_y + (init_h - nh) / 2.0, 0.0, 1.0 - nh)
                elif self._drag_mode in ("RESIZE_N", "RESIZE_S"):
                    nw = clamp(nh * norm_ratio, min_dim, 1.0)
                    nx = clamp(init_x + (init_w - nw) / 2.0, 0.0, 1.0 - nw)
                else:
                    # Corner resize: preserve aspect ratio using dominant axis
                    candidate_h = nw / norm_ratio
                    if candidate_h <= 1.0:
                        nh = candidate_h
                    else:
                        nh = 1.0
                        nw = nh * norm_ratio

                    if "N" in self._drag_mode:
                        ny = clamp(init_y + (init_h - nh), 0.0, 1.0 - nh)
                    if "W" in self._drag_mode:
                        nx = clamp(init_x + (init_w - nw), 0.0, 1.0 - nw)

            # Bound checks
            nx = clamp(nx, 0.0, 1.0 - nw)
            ny = clamp(ny, 0.0, 1.0 - nh)
            nw = clamp(nw, min_dim, 1.0 - nx)
            nh = clamp(nh, min_dim, 1.0 - ny)

            self.crop = (nx, ny, nw, nh)

        self._update_readout_label()
        self._render_preview()

    def _on_drag_end(self, gesture: Gtk.GestureDrag, offset_x: float, offset_y: float) -> None:
        self._drag_mode = "NONE"
        self.preview_picture.set_cursor_from_name("default")

    def _get_current_aspect_ratio(self) -> Optional[float]:
        """Return the numerical aspect ratio for the selected preset or None."""
        for label, ratio in ASPECT_PRESETS:
            if label == self.selected_preset:
                if ratio == -1.0 and self._transformed_pixbuf:
                    return self._transformed_pixbuf.get_width() / float(self._transformed_pixbuf.get_height())
                return ratio
        return None

    def _on_preset_toggled(self, btn: Gtk.ToggleButton, preset_label: str) -> None:
        if not btn.get_active():
            # Keep at least one button selected
            if self.selected_preset == preset_label:
                btn.set_active(True)
            return

        self.selected_preset = preset_label
        for name, other_btn in self._preset_buttons.items():
            if name != preset_label and other_btn.get_active():
                other_btn.set_active(False)

        ratio = self._get_current_aspect_ratio()
        if ratio is not None and self._transformed_pixbuf:
            img_pixel_ratio = self._transformed_pixbuf.get_width() / float(self._transformed_pixbuf.get_height())
            norm_ratio = ratio / img_pixel_ratio

            if norm_ratio >= 1.0:
                cw = 1.0
                ch = 1.0 / norm_ratio
            else:
                ch = 1.0
                cw = norm_ratio

            cx = (1.0 - cw) / 2.0
            cy = (1.0 - ch) / 2.0
            self.crop = (cx, cy, cw, ch)
        elif preset_label == "Free":
            pass

        self._update_readout_label()
        self._render_preview()

    def _rotate_cw(self) -> None:
        """Rotate 90 degrees clockwise."""
        self.rotation = (self.rotation + 90) % 360
        # Rotate crop box coordinates 90 deg clockwise
        ox, oy, ow, oh = self.crop
        nx = 1.0 - (oy + oh)
        ny = ox
        nw = oh
        nh = ow
        self.crop = (clamp(nx, 0.0, 1.0), clamp(ny, 0.0, 1.0), clamp(nw, 0.01, 1.0), clamp(nh, 0.01, 1.0))
        self._update_transformed_pixbuf()

    def _rotate_ccw(self) -> None:
        """Rotate 90 degrees counter-clockwise."""
        self.rotation = (self.rotation - 90) % 360
        # Rotate crop box coordinates 90 deg counter-clockwise
        ox, oy, ow, oh = self.crop
        nx = oy
        ny = 1.0 - (ox + ow)
        nw = oh
        nh = ow
        self.crop = (clamp(nx, 0.0, 1.0), clamp(ny, 0.0, 1.0), clamp(nw, 0.01, 1.0), clamp(nh, 0.01, 1.0))
        self._update_transformed_pixbuf()

    def _toggle_hflip(self) -> None:
        """Toggle horizontal flip."""
        self.hflip = not self.hflip
        ox, oy, ow, oh = self.crop
        nx = 1.0 - (ox + ow)
        self.crop = (clamp(nx, 0.0, 1.0), oy, ow, oh)
        self._update_transformed_pixbuf()

    def _toggle_vflip(self) -> None:
        """Toggle vertical flip."""
        self.vflip = not self.vflip
        ox, oy, ow, oh = self.crop
        ny = 1.0 - (oy + oh)
        self.crop = (ox, clamp(ny, 0.0, 1.0), ow, oh)
        self._update_transformed_pixbuf()

    def _reset_transforms(self) -> None:
        """Reset all transformations and crop to full frame."""
        self.rotation = 0
        self.hflip = False
        self.vflip = False
        self.crop = (0.0, 0.0, 1.0, 1.0)
        self.selected_preset = "Free"
        for name, btn in self._preset_buttons.items():
            btn.set_active(name == "Free")
        self._update_transformed_pixbuf()

    def _on_apply_clicked(self, _btn: Gtk.Button) -> None:
        """Commit transforms and close dialog."""
        # Normalize crop: if virtually entire frame, set to None
        cx, cy, cw, ch = self.crop
        final_crop: Optional[Tuple[float, float, float, float]] = self.crop
        if abs(cx) < 1e-4 and abs(cy) < 1e-4 and abs(cw - 1.0) < 1e-4 and abs(ch - 1.0) < 1e-4:
            final_crop = None

        if self.on_apply:
            self.on_apply(final_crop, self.rotation, self.hflip, self.vflip)
        self.close()
