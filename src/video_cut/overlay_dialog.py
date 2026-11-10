"""Modal dialog for creating and editing video overlays, text, shapes, media, and stickers."""

from __future__ import annotations

import io
import json
import math
import uuid
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any

import cairo
import gi
from PIL import Image

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Pango", "1.0")
from gi.repository import Adw, Gdk, GdkPixbuf, Gio, GLib, Gtk, Pango

from .clipboard_controller import parse_uri_list
from .i18n import _
from .models import OverlayItem, Segment
from .player import TransformedPaintable
from .services.ffmpeg_service import FFmpegService
from .services.overlay_audio_player import OverlayAudioPlayer
from .services.overlay_service import OverlayRenderService, draw_rounded_rect, parse_color
from .services.private_dir import private_dir
from .services.probe_service import ProbeService
from .utils import clamp, format_time


class PreviewPicture(Gtk.Picture):
    """Subclass of Gtk.Picture that tracks size allocations for pixel-perfect overlay rendering."""

    def __init__(self, on_allocate: Callable[[int, int], None] | None = None) -> None:
        super().__init__()
        self.on_allocate = on_allocate
        self._last_alloc: tuple[int, int] = (0, 0)

    def do_size_allocate(self, width: int, height: int, baseline: int) -> None:
        Gtk.Picture.do_size_allocate(self, width, height, baseline)
        if width > 10 and height > 10 and (width, height) != self._last_alloc:
            self._last_alloc = (width, height)
            if self.on_allocate:
                self.on_allocate(width, height)


class WorkspaceScrolledWindow(Gtk.ScrolledWindow):
    """Subclass of Gtk.ScrolledWindow that tracks viewport container resize allocations."""

    def __init__(self, on_allocate: Callable[[int, int], None] | None = None) -> None:
        super().__init__()
        self.on_allocate = on_allocate
        self._last_alloc: tuple[int, int] = (0, 0)

    def do_size_allocate(self, width: int, height: int, baseline: int) -> None:
        Gtk.ScrolledWindow.do_size_allocate(self, width, height, baseline)
        if width > 10 and height > 10 and (width, height) != self._last_alloc:
            self._last_alloc = (width, height)
            if self.on_allocate:
                self.on_allocate(width, height)


class OverlayDialog(Adw.Window):
    """Interactive Inkscape-inspired vector & media overlay editor for a video segment."""

    def __init__(
        self,
        parent: Gtk.Window,
        segment: Segment,
        on_apply: Callable[[list[OverlayItem]], None],
        current_time: float = 0.0,
        thumbnail_service: Any = None,
    ) -> None:
        super().__init__()
        self.set_transient_for(parent)
        self.set_modal(True)
        self.set_destroy_with_parent(True)
        self.set_default_size(1120, 750)

        self.segment = segment
        self.on_apply = on_apply
        self.thumbnail_service = thumbnail_service

        # Working overlays list (cloned from segment)
        self.items: list[OverlayItem] = [it.copy() for it in segment.overlays]
        self.selected_item_ids: set[str] = set()

        # Playback & Scrubbing state
        self.clip_duration: float = max(0.1, segment.duration)
        self.current_time: float = clamp(current_time - segment.start_time, 0.0, self.clip_duration)
        self.is_playing: bool = False
        self._playback_timer: int | None = None
        self._play_on_seek_done: bool = False

        # Undo / Redo history
        self._undo_stack: list[list[OverlayItem]] = []
        self._redo_stack: list[list[OverlayItem]] = []

        # Canvas drag & transform tracking
        self._drag_mode: str = "NONE"
        self._drag_item_id: str | None = None
        self._drag_start_x: float = 0.0
        self._drag_start_y: float = 0.0
        self._item_state_at_drag_start: OverlayItem | None = None
        self._selected_states_at_drag_start: dict[str, OverlayItem] = {}
        self._multi_selection_bounds_at_drag_start: tuple[float, float, float, float] | None = None
        self._rubberband_start: tuple[float, float] | None = None
        self._rubberband_current: tuple[float, float] | None = None
        self._initial_selected_before_drag: set[str] = set()
        self._items_state_before_drag: list[OverlayItem] | None = None
        self._current_cursor: str = "default"

        # Reference video frame & layout
        self._canvas_alloc_size: tuple[int, int] = (1, 1)
        self._viewport_size: tuple[int, int] = (0, 0)
        self._base_pixbuf: GdkPixbuf.Pixbuf | None = None
        self._cairo_frame_surface: cairo.ImageSurface | None = None
        self._canvas_rect: tuple[float, float, float, float] = (0, 0, 1, 1)  # (x, y, w, h)

        # Zoom state
        self._zoom_factor: float = 1.0
        self._initial_scroll_centered: bool = False
        self.btn_zoom_fit: Gtk.Button | None = None
        self.lbl_zoom: Gtk.Label | None = None

        # Position & Size inspector widgets
        self.spin_pos_x: Gtk.SpinButton | None = None
        self.spin_pos_y: Gtk.SpinButton | None = None
        self.spin_size_w: Gtk.SpinButton | None = None
        self.spin_size_h: Gtk.SpinButton | None = None
        self._updating_transform_spinners: bool = False

        # Element selector dropdown
        self.element_dropdown: Gtk.DropDown | None = None
        self._updating_element_dropdown: bool = False


        # Native MediaFile playback
        self._media_file: Gtk.MediaFile | None = None
        self._transformed_paintable: TransformedPaintable | None = None
        self._mf_handlers: list[int] = []
        self._audio_player: OverlayAudioPlayer = OverlayAudioPlayer()

        self.set_title(_("Overlays & Drawings — {name}").format(name=segment.source_name))

        self._build_ui()
        self._setup_canvas_gestures()
        self._setup_key_controller()
        self._init_video_player()
        self._preload_video_clips()
        self._update_undo_redo_ui()

        self.connect("close-request", self._on_close_request)

    def _preload_video_clips(self) -> None:
        """Preload any video clips in cache for instant 60fps playback."""
        for it in self.items:
            if it.media_path and (
                it.item_type == "video"
                or Path(it.media_path).suffix.lower() in (".mp4", ".webm", ".mov", ".mkv", ".avi")
            ):
                OverlayRenderService.get_video_clip(it.media_path)

    @property
    def selected_item_id(self) -> str | None:
        if not self.selected_item_ids:
            return None
        for it in reversed(self.items):
            if it.id in self.selected_item_ids:
                return it.id
        return next(iter(self.selected_item_ids))

    @selected_item_id.setter
    def selected_item_id(self, value: str | None) -> None:
        if value is None:
            self.selected_item_ids.clear()
        else:
            self.selected_item_ids = {value}

    @property
    def selected_item(self) -> OverlayItem | None:
        sid = self.selected_item_id
        if not sid:
            return None
        for it in self.items:
            if it.id == sid:
                return it
        return None

    @property
    def selected_items(self) -> list[OverlayItem]:
        if not self.selected_item_ids:
            return []
        return [it for it in self.items if it.id in self.selected_item_ids]

    def _get_multi_selection_bounds(self) -> tuple[float, float, float, float] | None:
        """Return (min_x, min_y, max_x, max_y) in normalized canvas coordinates."""
        items = self.selected_items
        if not items:
            return None
        min_x = min(it.x - it.width / 2.0 for it in items)
        max_x = max(it.x + it.width / 2.0 for it in items)
        min_y = min(it.y - it.height / 2.0 for it in items)
        max_y = max(it.y + it.height / 2.0 for it in items)
        return (min_x, min_y, max_x, max_y)

    def _save_undo_state(self) -> None:
        """Push a snapshot of all overlay items onto the local undo stack."""
        snapshot = [it.copy() for it in self.items]
        self._undo_stack.append(snapshot)
        if len(self._undo_stack) > 50:
            self._undo_stack.pop(0)
        self._redo_stack.clear()
        self._update_undo_redo_ui()

    def undo(self) -> None:
        if not self._undo_stack:
            return
        current = [it.copy() for it in self.items]
        self._redo_stack.append(current)
        prev = self._undo_stack.pop()
        self.items = [it.copy() for it in prev]
        self.selected_item_ids = {i for i in self.selected_item_ids if any(it.id == i for it in self.items)}
        if not self.selected_item_ids and self.items:
            self.selected_item_ids = {self.items[-1].id}
        self._refresh_canvas_and_properties()
        self._update_undo_redo_ui()

    def redo(self) -> None:
        if not self._redo_stack:
            return
        current = [it.copy() for it in self.items]
        self._undo_stack.append(current)
        nxt = self._redo_stack.pop()
        self.items = [it.copy() for it in nxt]
        self.selected_item_ids = {i for i in self.selected_item_ids if any(it.id == i for it in self.items)}
        if not self.selected_item_ids and self.items:
            self.selected_item_ids = {self.items[-1].id}
        self._refresh_canvas_and_properties()
        self._update_undo_redo_ui()

    def _update_undo_redo_ui(self) -> None:
        if hasattr(self, "btn_undo"):
            self.btn_undo.set_sensitive(len(self._undo_stack) > 0)
        if hasattr(self, "btn_redo"):
            self.btn_redo.set_sensitive(len(self._redo_stack) > 0)

    # --------------------------------------------------------------------------
    # UI Building
    # --------------------------------------------------------------------------

    def _build_ui(self) -> None:
        toolbar_view = Adw.ToolbarView()
        self.set_content(toolbar_view)

        # Header Bar
        toolbar_view.add_top_bar(self._build_header_bar())

        # Main horizontal split: [Tools Sidebar | Center Canvas + Controls | Properties Sidebar]
        content_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        toolbar_view.set_content(content_box)

        # 1. Left Tools Palette
        content_box.append(self._build_tools_palette())

        # 2. Center Work Area (Video Canvas + Transport Controls)
        center_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        center_box.set_vexpand(True)
        center_box.set_hexpand(True)
        center_box.set_margin_start(8)
        center_box.set_margin_end(8)
        center_box.set_margin_top(8)
        center_box.set_margin_bottom(8)

        center_box.append(self._build_canvas_area())
        center_box.append(self._build_transport_controls())
        content_box.append(center_box)

        # 3. Right Property Inspector Sidebar
        self.prop_sidebar = self._build_properties_sidebar()
        content_box.append(self.prop_sidebar)

        self._update_properties_panel()

    def _build_header_bar(self) -> Adw.HeaderBar:
        hb = Adw.HeaderBar()
        hb.set_show_title(True)

        btn_cancel = Gtk.Button(label=_("Cancel"))
        btn_cancel.connect("clicked", lambda _: self.close())
        hb.pack_start(btn_cancel)

        # Undo / Redo
        undo_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        undo_box.add_css_class("linked")

        self.btn_undo = Gtk.Button.new_from_icon_name("edit-undo-symbolic")
        self.btn_undo.set_tooltip_text(_("Undo (Ctrl+Z)"))
        self.btn_undo.connect("clicked", lambda _: self.undo())
        undo_box.append(self.btn_undo)

        self.btn_redo = Gtk.Button.new_from_icon_name("edit-redo-symbolic")
        self.btn_redo.set_tooltip_text(_("Redo (Ctrl+Shift+Z)"))
        self.btn_redo.connect("clicked", lambda _: self.redo())
        undo_box.append(self.btn_redo)

        hb.pack_start(undo_box)

        # Apply button (rightmost)
        btn_apply = Gtk.Button(label=_("Apply"))
        btn_apply.add_css_class("suggested-action")
        btn_apply.connect("clicked", lambda _: self._on_apply_clicked())
        hb.pack_end(btn_apply)

        # Global delete button (side of the apply button at the right top corner)
        self.btn_delete_all = Gtk.Button.new_from_icon_name("user-trash-symbolic")
        self.btn_delete_all.set_tooltip_text(_("Remove all overlays for this segment"))
        self.btn_delete_all.add_css_class("destructive-action")
        self.btn_delete_all.set_sensitive(bool(self.items))
        self.btn_delete_all.connect("clicked", lambda _: self._on_delete_all_clicked())
        hb.pack_end(self.btn_delete_all)

        return hb

    def _build_tools_palette(self) -> Gtk.Box:
        palette = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        palette.add_css_class("tools-palette")
        palette.set_margin_start(8)
        palette.set_margin_end(4)
        palette.set_margin_top(12)
        palette.set_margin_bottom(12)

        def make_tool_btn(icon_name: str, tooltip: str, callback: Callable[[], None]) -> Gtk.Button:
            btn = Gtk.Button.new_from_icon_name(icon_name)
            btn.add_css_class("flat")
            btn.set_tooltip_text(tooltip)
            btn.connect("clicked", lambda _: callback())
            return btn

        # Add Text
        palette.append(make_tool_btn("insert-text-symbolic", _("Add Text"), self._on_tool_add_text))

        # Add Rectangle
        palette.append(make_tool_btn("shape-rectangle-symbolic", _("Add Rectangle"), lambda: self._on_tool_add_shape("rect")))

        # Add Circle
        palette.append(make_tool_btn("shape-circle-symbolic", _("Add Circle / Ellipse"), lambda: self._on_tool_add_shape("circle")))

        # Add Image / Video
        palette.append(make_tool_btn("image-x-generic-symbolic", _("Add Image, SVG, GIF or Video"), self._on_tool_add_media))

        # Add Audio Track
        palette.append(make_tool_btn("audio-x-generic-symbolic", _("Add Audio Track"), self._on_tool_add_audio))

        # Add Emoji (using native Gtk.EmojiChooser)
        self.btn_emoji = Gtk.Button.new_from_icon_name("face-smile-symbolic")
        self.btn_emoji.add_css_class("flat")
        self.btn_emoji.set_tooltip_text(_("Add Emoji"))
        self.emoji_chooser = Gtk.EmojiChooser()
        self.emoji_chooser.set_parent(self.btn_emoji)
        self.emoji_chooser.connect("emoji-picked", self._on_emoji_picked)
        self.btn_emoji.connect("clicked", lambda _: self.emoji_chooser.popup())
        palette.append(self.btn_emoji)

        return palette

    def _build_canvas_area(self) -> Gtk.Widget:
        # Scrolled Window Viewport with Pasteboard Workspace
        self.canvas_scrolled = WorkspaceScrolledWindow(on_allocate=self._on_viewport_allocated)
        self.canvas_scrolled.set_hexpand(True)
        self.canvas_scrolled.set_vexpand(True)
        self.canvas_scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self.canvas_scrolled.add_css_class("view")
        self.canvas_scrolled.add_css_class("overlay-workspace")

        # Canvas Overlay Container
        self.canvas_overlay = Gtk.Overlay()
        self.canvas_overlay.set_halign(Gtk.Align.START)
        self.canvas_overlay.set_valign(Gtk.Align.START)

        # Preview box holding Video Frame using Gtk.Fixed for exact pixel placement
        self.preview_box = Gtk.Fixed()
        self.preview_box.set_halign(Gtk.Align.START)
        self.preview_box.set_valign(Gtk.Align.START)

        # Video Frame Picture Underneath
        self.preview_picture = Gtk.Picture.new()
        self.preview_picture.set_can_shrink(True)
        self.preview_picture.set_content_fit(Gtk.ContentFit.FILL)
        self.preview_picture.set_halign(Gtk.Align.START)
        self.preview_picture.set_valign(Gtk.Align.START)
        self.preview_picture.add_css_class("overlay-video-frame")
        self.preview_box.put(self.preview_picture, 0, 0)

        self.canvas_overlay.set_child(self.preview_box)

        # Interactive Overlay Picture on top (receives gestures & renders memory texture)
        self.overlay_picture = PreviewPicture(on_allocate=self._on_canvas_allocated)
        self.overlay_picture.set_can_shrink(True)
        self.overlay_picture.set_content_fit(Gtk.ContentFit.FILL)
        self.overlay_picture.set_halign(Gtk.Align.START)
        self.overlay_picture.set_valign(Gtk.Align.START)
        self.canvas_overlay.add_overlay(self.overlay_picture)

        self.canvas_scrolled.set_child(self.canvas_overlay)
        return self.canvas_scrolled

    def _on_viewport_allocated(self, width: int, height: int) -> None:
        self._viewport_size = (width, height)
        is_first = not self._initial_scroll_centered
        if is_first:
            self._initial_scroll_centered = True
            self._update_canvas_geometry(center_on_video=True)
            GLib.idle_add(self._center_scroll_on_video)
        else:
            self._update_canvas_geometry()

    def _on_canvas_allocated(self, width: int, height: int) -> None:
        self._canvas_alloc_size = (width, height)

    def _build_transport_controls(self) -> Gtk.Box:
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        bar.add_css_class("overlay-transport-bar")
        bar.set_margin_top(4)
        bar.set_margin_bottom(4)

        # Play / Pause
        self.btn_play = Gtk.Button.new_from_icon_name("media-playback-start-symbolic")
        self.btn_play.add_css_class("circular")
        self.btn_play.set_tooltip_text(_("Play (Space)"))
        self.btn_play.connect("clicked", lambda _: self._toggle_playback())
        bar.append(self.btn_play)

        # Step back
        btn_step_back = Gtk.Button.new_from_icon_name("media-seek-backward-symbolic")
        btn_step_back.add_css_class("flat")
        btn_step_back.set_tooltip_text(_("Step 1 second back"))
        btn_step_back.connect("clicked", lambda _: self._seek_relative(-1.0))
        bar.append(btn_step_back)

        # Step forward
        btn_step_fwd = Gtk.Button.new_from_icon_name("media-seek-forward-symbolic")
        btn_step_fwd.add_css_class("flat")
        btn_step_fwd.set_tooltip_text(_("Step 1 second forward"))
        btn_step_fwd.connect("clicked", lambda _: self._seek_relative(1.0))
        bar.append(btn_step_fwd)

        # Scrubber Scale
        self.scrubber = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0.0, self.clip_duration, 0.05)
        self.scrubber.set_hexpand(True)
        self.scrubber.set_value(self.current_time)
        self.scrubber.connect("value-changed", self._on_scrubber_changed)
        bar.append(self.scrubber)

        # Time label
        self.lbl_time = Gtk.Label(label=f"{format_time(self.current_time)} / {format_time(self.clip_duration)}")
        self.lbl_time.add_css_class("numeric")
        bar.append(self.lbl_time)

        # Transport Separator
        bar.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))

        # Separate Fit to Window button
        self.btn_zoom_fit = Gtk.Button.new_from_icon_name("zoom-fit-best-symbolic")
        self.btn_zoom_fit.add_css_class("flat")
        self.btn_zoom_fit.set_tooltip_text(_("Fit to Window (Ctrl+0)"))
        self.btn_zoom_fit.connect("clicked", lambda _: self._zoom_fit())
        bar.append(self.btn_zoom_fit)

        # Zoom +/- group
        zoom_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        zoom_box.add_css_class("linked")

        btn_zoom_out = Gtk.Button.new_from_icon_name("zoom-out-symbolic")
        btn_zoom_out.add_css_class("flat")
        btn_zoom_out.set_tooltip_text(_("Zoom Out (Ctrl+-)"))
        btn_zoom_out.connect("clicked", lambda _: self._zoom_out())
        zoom_box.append(btn_zoom_out)

        self.lbl_zoom = Gtk.Label(label="100%")
        self.lbl_zoom.add_css_class("numeric")
        self.lbl_zoom.set_margin_start(4)
        self.lbl_zoom.set_margin_end(4)
        zoom_box.append(self.lbl_zoom)

        btn_zoom_in = Gtk.Button.new_from_icon_name("zoom-in-symbolic")
        btn_zoom_in.add_css_class("flat")
        btn_zoom_in.set_tooltip_text(_("Zoom In (Ctrl++)"))
        btn_zoom_in.connect("clicked", lambda _: self._zoom_in())
        zoom_box.append(btn_zoom_in)

        bar.append(zoom_box)

        return bar


    def _build_properties_sidebar(self) -> Gtk.Box:
        sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        sidebar.add_css_class("properties-sidebar")
        sidebar.set_size_request(290, -1)
        sidebar.set_hexpand(False)
        sidebar.set_vexpand(True)
        sidebar.set_margin_start(8)
        sidebar.set_margin_end(8)
        sidebar.set_margin_top(10)
        sidebar.set_margin_bottom(10)

        # Header title
        title_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        lbl_head = Gtk.Label(label=_("Properties"), xalign=0)
        lbl_head.add_css_class("title-4")
        lbl_head.set_hexpand(True)
        title_box.append(lbl_head)
        sidebar.append(title_box)

        # Element Selection Dropdown and regular Delete Button side-by-side
        dropdown_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.element_dropdown = Gtk.DropDown()
        self.element_dropdown.set_hexpand(True)
        self.element_dropdown.set_tooltip_text(_("Select element"))
        self.element_dropdown.connect("notify::selected", self._on_element_dropdown_selected)
        dropdown_row.append(self.element_dropdown)

        self.btn_delete_selected_prop = Gtk.Button.new_from_icon_name("user-trash-symbolic")
        self.btn_delete_selected_prop.set_tooltip_text(_("Delete selected element (Delete)"))
        self.btn_delete_selected_prop.add_css_class("destructive-action")
        self.btn_delete_selected_prop.set_sensitive(False)
        self.btn_delete_selected_prop.connect("clicked", lambda _: self._on_delete_selected())
        dropdown_row.append(self.btn_delete_selected_prop)

        sidebar.append(dropdown_row)

        # Scrolled container for dynamic properties
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.EXTERNAL, Gtk.PolicyType.AUTOMATIC)
        scroll.set_size_request(234, -1)
        scroll.set_propagate_natural_width(False)
        scroll.set_hexpand(False)
        scroll.set_vexpand(True)

        self.prop_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.prop_container.set_hexpand(False)
        scroll.set_child(self.prop_container)

        sidebar.append(scroll)

        self._update_properties_panel()
        return sidebar

    # --------------------------------------------------------------------------
    # Element Selector & Properties Panel Updates
    # --------------------------------------------------------------------------

    def _on_element_dropdown_selected(self, drop: Gtk.DropDown, _pspec) -> None:
        if self._updating_element_dropdown:
            return
        idx = drop.get_selected()
        if idx == 0:
            GLib.idle_add(self._on_dropdown_selection_idle, None)
        elif len(self.selected_items) > 1:
            if idx == 1:
                return
            elif 2 <= idx <= len(self.items) + 1:
                item = self.items[idx - 2]
                GLib.idle_add(self._on_dropdown_selection_idle, item)
        elif 1 <= idx <= len(self.items):
            item = self.items[idx - 1]
            if self.selected_item_id != item.id:
                GLib.idle_add(self._on_dropdown_selection_idle, item)

    def _on_dropdown_selection_idle(self, item: OverlayItem | None) -> bool:
        if item is None:
            if self.selected_item_ids:
                self.selected_item_ids.clear()
                self._refresh_canvas_and_properties()
            return False
        if self.selected_item_id != item.id or len(self.selected_items) > 1:
            self.selected_item_ids = {item.id}
            self._refresh_canvas_and_properties()
            self._scroll_to_item(item)
        return False

    def _scroll_to_item(self, item: OverlayItem) -> None:
        """Scroll the viewport smoothly to center on the given element."""
        if not hasattr(self, "canvas_scrolled"):
            return
        rx, ry, rw, rh = self._canvas_rect
        w, h = self._canvas_alloc_size
        vp_w, vp_h = self._viewport_size
        if vp_w <= 20 or vp_h <= 20:
            vp_w = max(100, self.canvas_scrolled.get_width())
            vp_h = max(100, self.canvas_scrolled.get_height())
            if vp_w <= 20 or vp_h <= 20:
                vp_w = 780
                vp_h = 480

        cx = rx + item.x * rw
        cy = ry + item.y * rh
        target_x = cx - vp_w / 2.0
        target_y = cy - vp_h / 2.0

        hadj = self.canvas_scrolled.get_hadjustment()
        vadj = self.canvas_scrolled.get_vadjustment()
        if hadj:
            hadj.set_value(clamp(target_x, 0.0, max(0.0, float(w - vp_w))))
        if vadj:
            vadj.set_value(clamp(target_y, 0.0, max(0.0, float(h - vp_h))))

    def _get_item_display_name(self, item: OverlayItem) -> str:
        """Generate a concise, human-friendly label for the element dropdown."""
        if item.item_type == "text":
            txt = (item.text or "").strip()
            if not txt:
                return _("Text")
            if len(txt) > 12:
                txt = txt[:11] + "…"
            return f'{_("Text")}: "{txt}"'
        elif item.item_type == "emoji":
            return f'{_("Emoji")}: {item.text or "😀"}'
        elif item.item_type == "rect":
            return _("Rectangle")
        elif item.item_type == "square":
            return _("Square")
        elif item.item_type == "circle":
            return _("Circle")
        elif item.item_type == "ellipse":
            return _("Ellipse")
        elif item.item_type == "image":
            name = Path(item.media_path).name if item.media_path else _("Image")
            if len(name) > 14:
                name = name[:12] + "…"
            return f'{_("Image")}: {name}'
        elif item.item_type == "svg":
            name = Path(item.media_path).name if item.media_path else _("SVG")
            if len(name) > 14:
                name = name[:12] + "…"
            return f'{_("SVG")}: {name}'
        elif item.item_type == "gif":
            name = Path(item.media_path).name if item.media_path else _("GIF")
            if len(name) > 14:
                name = name[:12] + "…"
            return f'{_("GIF")}: {name}'
        elif item.item_type == "video":
            name = Path(item.media_path).name if item.media_path else _("Video")
            if len(name) > 14:
                name = name[:12] + "…"
            return f'{_("Video")}: {name}'
        elif item.item_type == "audio":
            name = Path(item.media_path).name if item.media_path else _("Audio Track")
            if len(name) > 14:
                name = name[:12] + "…"
            return f'{_("Audio Track")}: {name}'
        return item.item_type.capitalize()

    def _update_element_dropdown(self) -> None:
        """Refresh the items list in the top element selector dropdown."""
        if not hasattr(self, "element_dropdown") or not self.element_dropdown:
            return
        self._updating_element_dropdown = True
        try:
            if not self.items:
                cur_model = self.element_dropdown.get_model()
                no_elem_str = _("No elements")
                if not (isinstance(cur_model, Gtk.StringList) and cur_model.get_n_items() == 1 and cur_model.get_string(0) == no_elem_str):
                    self.element_dropdown.set_model(Gtk.StringList.new([no_elem_str]))
                self.element_dropdown.set_sensitive(False)
                return

            no_sel_label = f"({_('No selection')})"
            labels: list[str] = [no_sel_label]
            selected_idx = 0

            if len(self.selected_items) > 1:
                labels.append(_("({count} elements selected)").format(count=len(self.selected_items)))
                selected_idx = 1
                for i, it in enumerate(self.items):
                    lbl = self._get_item_display_name(it)
                    labels.append(f"{i + 1}. {lbl}")
            else:
                for i, it in enumerate(self.items):
                    lbl = self._get_item_display_name(it)
                    labels.append(f"{i + 1}. {lbl}")
                    if it.id == self.selected_item_id:
                        selected_idx = i + 1

            cur_model = self.element_dropdown.get_model()
            same_labels = (
                isinstance(cur_model, Gtk.StringList)
                and cur_model.get_n_items() == len(labels)
                and all(cur_model.get_string(i) == labels[i] for i in range(len(labels)))
            )
            if not same_labels:
                self.element_dropdown.set_model(Gtk.StringList.new(labels))

            self.element_dropdown.set_sensitive(True)
            if self.element_dropdown.get_selected() != selected_idx:
                self.element_dropdown.set_selected(selected_idx)
        finally:
            self._updating_element_dropdown = False
            if hasattr(self, "btn_delete_all") and self.btn_delete_all:
                self.btn_delete_all.set_sensitive(bool(self.items))

    def _update_properties_panel(self) -> None:
        self._update_element_dropdown()

        # Clear previous property widgets
        while child := self.prop_container.get_first_child():
            self.prop_container.remove(child)

        items = self.selected_items
        has_items = bool(items)

        # Keep panel permanently visible with fixed compact size
        if hasattr(self, "prop_sidebar"):
            self.prop_sidebar.set_visible(True)

        if not has_items:
            self.spin_pos_x = None
            self.spin_pos_y = None
            self.spin_size_w = None
            self.spin_size_h = None

            # Elegant empty placeholder state
            empty_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            empty_box.set_vexpand(True)
            empty_box.set_valign(Gtk.Align.CENTER)
            empty_box.set_halign(Gtk.Align.CENTER)
            empty_box.set_margin_top(60)

            empty_icon = Gtk.Image.new_from_icon_name("edit-select-all-symbolic")
            empty_icon.set_pixel_size(32)
            empty_icon.add_css_class("dim-label")
            empty_box.append(empty_icon)

            if hasattr(self, "btn_delete_selected_prop"):
                self.btn_delete_selected_prop.set_sensitive(False)
            empty_lbl = Gtk.Label(label=_("No element selected"))
            empty_lbl.add_css_class("dim-label")
            empty_lbl.add_css_class("caption")
            empty_box.append(empty_lbl)

            hint_lbl = Gtk.Label(label=_("Click an item to edit"))
            hint_lbl.add_css_class("dim-label")
            hint_lbl.add_css_class("caption")
            empty_box.append(hint_lbl)

            self.prop_container.append(empty_box)
            return

        if hasattr(self, "btn_delete_selected_prop"):
            self.btn_delete_selected_prop.set_sensitive(True)

        if len(items) > 1:
            self._append_multi_properties(items)
            return

        item = items[0]
        # Text Specific Properties
        if item.item_type == "text":
            self._append_text_properties(item)

        # Emoji Specific Properties
        elif item.item_type == "emoji":
            self._append_emoji_properties(item)

        # Shape Specific Properties
        elif item.item_type in ("rect", "square", "circle", "ellipse"):
            self._append_shape_properties(item)

        # Media Specific Properties
        elif item.item_type in ("image", "svg", "gif", "video"):
            self._append_media_properties(item)

        # Audio Specific Properties
        elif item.item_type == "audio":
            self._append_audio_properties(item)

        # Common Transform & Layer Ordering
        self._append_common_properties(item)

    def _auto_fit_text_box_height(self, item: OverlayItem) -> None:
        """Ensure the text item's height is large enough to fit all its lines of text."""
        if item.item_type != "text":
            return
        vw, vh = self._get_video_pixel_dimensions()
        if vh <= 0:
            vh = 1080
        if vw <= 0:
            vw = 1920

        display_text = item.text or ""
        if not display_text:
            return

        ref_scale = max(0.2, vh / 1080.0)
        eff_font_size = max(8.0, float(item.font_size) * ref_scale)
        line_height = eff_font_size * 1.25
        pad = max(4.0 * ref_scale, 4.0)

        box_w_px = max(20.0, item.width * vw)
        max_line_w = max(10.0, box_w_px - pad * 2.0)

        # Estimate line count based on average character width (~0.55 of eff_font_size)
        avg_char_w = max(4.0, eff_font_size * 0.55)
        chars_per_line = max(1, int(max_line_w / avg_char_w))

        total_lines = 0
        for paragraph in display_text.split("\n"):
            if not paragraph:
                total_lines += 1
                continue
            words = paragraph.split(" ")
            curr_len = 0
            para_lines = 1
            for word in words:
                w_len = len(word)
                if curr_len + w_len + (1 if curr_len else 0) <= chars_per_line:
                    curr_len += w_len + (1 if curr_len else 0)
                else:
                    para_lines += 1
                    curr_len = w_len
            total_lines += max(1, para_lines)

        needed_px = total_lines * line_height + pad * 2.0 + eff_font_size * 0.6
        needed_norm_h = round(needed_px / vh, 4)
        if needed_norm_h > item.height:
            item.height = min(0.95, needed_norm_h)

    def _create_font_button(self, initial_font: str, on_changed: Callable[[str], None]) -> Gtk.Widget:
        """Create a configured FontDialogButton (or fallback FontButton) with auto-ellipsizing label."""
        curr_font = initial_font or "Sans"
        try:
            fd = Gtk.FontDialog.new()
            fd.set_title(_("Select Font"))
            sys_lang = Pango.language_get_default()
            if sys_lang:
                fd.set_language(sys_lang)
            font_btn = Gtk.FontDialogButton.new(fd)
            font_btn.set_level(Gtk.FontLevel.FAMILY)
            font_btn.set_use_size(False)
            font_btn.set_use_font(False)
            font_btn.set_hexpand(True)
            font_btn.set_tooltip_text(_("Select font family"))
            desc = Pango.FontDescription.from_string(curr_font)
            font_btn.set_font_desc(desc)

            def ellipsize_font_btn(btn_widget: Gtk.Widget) -> None:
                def _walk(w: Gtk.Widget) -> None:
                    c = w.get_first_child()
                    while c:
                        if isinstance(c, Gtk.Label):
                            c.set_ellipsize(Pango.EllipsizeMode.END)
                            c.set_max_width_chars(10)
                        _walk(c)
                        c = c.get_next_sibling()
                _walk(btn_widget)

            ellipsize_font_btn(font_btn)

            def on_font_picked(b: Gtk.FontDialogButton, _pspec: Any) -> None:
                try:
                    new_desc = b.get_font_desc()
                    if new_desc:
                        fam = new_desc.get_family()
                        if fam:
                            on_changed(fam)
                except Exception as err:
                    print(f"Error updating font family: {err}")
                GLib.idle_add(lambda: ellipsize_font_btn(b))

            font_btn.connect("notify::font-desc", on_font_picked)
            return font_btn
        except Exception:
            fb = Gtk.FontButton()
            fb.set_use_size(False)
            fb.set_use_font(False)
            fb.set_level(Gtk.FontChooserLevel.FAMILY)
            fb.set_font(curr_font)
            sys_lang = Pango.language_get_default()
            if sys_lang:
                try:
                    fb.set_language(sys_lang.to_string())
                except Exception:
                    pass
            fb.connect("font-set", lambda b: on_changed(b.get_font_desc().get_family()))
            return fb

    def _append_text_properties(self, item: OverlayItem) -> None:
        # Multi-line Text View (increased vertical size by 50%)
        lbl = Gtk.Label(label=_("Text Content:"), xalign=0)
        self.prop_container.append(lbl)

        txt_view = Gtk.TextView()
        txt_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        txt_view.set_hexpand(True)
        txt_view.set_top_margin(6)
        txt_view.set_bottom_margin(6)
        txt_view.set_left_margin(8)
        txt_view.set_right_margin(8)
        txt_buf = txt_view.get_buffer()
        txt_buf.set_text(item.text or "")

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_min_content_height(90)
        scrolled.set_max_content_height(150)
        scrolled.set_child(txt_view)
        scrolled.add_css_class("card")
        self.prop_container.append(scrolled)

        def on_text_changed(buf):
            start_it = buf.get_start_iter()
            end_it = buf.get_end_iter()
            item.text = buf.get_text(start_it, end_it, False)
            self._auto_fit_text_box_height(item)
            self._trigger_canvas_redraw()
            self._sync_position_and_size_spinners(item)
            self._update_element_dropdown()

        txt_buf.connect("changed", on_text_changed)

        # Single Standard GTK / GNOME Font Family Selector
        font_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        font_lbl = Gtk.Label(label=_("Font:"), xalign=0)
        font_box.append(font_lbl)

        font_btn = self._create_font_button(
            item.font_family or "Sans",
            lambda fam: self._set_item_attr(item, "font_family", fam),
        )
        font_box.append(font_btn)
        self.prop_container.append(font_box)

        # 1. Size, Style (Bold, Italic) - moved before Color/Background
        style_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        lbl_sz = Gtk.Label(label=_("Size:"), xalign=0)
        style_row.append(lbl_sz)

        spin_sz = Gtk.SpinButton.new_with_range(8, 200, 2)
        spin_sz.set_value(float(item.font_size))
        spin_sz.set_width_chars(3)
        spin_sz.set_hexpand(True)
        def on_size_changed(s):
            item.font_size = int(s.get_value())
            self._auto_fit_text_box_height(item)
            self._trigger_canvas_redraw()
            self._sync_position_and_size_spinners(item)
        spin_sz.connect("value-changed", on_size_changed)
        style_row.append(spin_sz)

        # Bold & Italic (with Italic label rendered in italic!)
        style_toggles = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        style_toggles.add_css_class("linked")

        btn_bold = Gtk.ToggleButton()
        lbl_b = Gtk.Label()
        lbl_b.set_markup("<b>B</b>")
        btn_bold.set_child(lbl_b)
        btn_bold.set_active(item.bold)
        btn_bold.set_tooltip_text(_("Bold"))
        btn_bold.connect("toggled", lambda b: self._set_item_attr(item, "bold", b.get_active()))
        style_toggles.append(btn_bold)

        btn_italic = Gtk.ToggleButton()
        lbl_i = Gtk.Label()
        lbl_i.set_markup("<i>I</i>")
        btn_italic.set_child(lbl_i)
        btn_italic.set_active(item.italic)
        btn_italic.set_tooltip_text(_("Italic"))
        btn_italic.connect("toggled", lambda b: self._set_item_attr(item, "italic", b.get_active()))
        style_toggles.append(btn_italic)

        style_row.append(style_toggles)
        self.prop_container.append(style_row)

        # 2. Single label: Color with both text color and background buttons together
        color_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        lbl_col = Gtk.Label(label=_("Color:"), xalign=0)
        lbl_col.set_hexpand(True)
        color_row.append(lbl_col)

        color_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        color_box.add_css_class("linked")

        color_btn = self._create_color_button(item.font_color, lambda c: self._set_item_attr(item, "font_color", c))
        color_btn.set_tooltip_text(_("Text Color"))
        color_box.append(color_btn)

        bg_btn = self._create_color_button(
            item.bg_color or "transparent",
            lambda c: self._set_item_attr(item, "bg_color", c if c != "rgba(0,0,0,0)" else None),
        )
        bg_btn.set_tooltip_text(_("Background Color"))
        color_box.append(bg_btn)

        color_row.append(color_box)
        self.prop_container.append(color_row)

        # 3. Text Alignment (Horizontal & Vertical on the same line, no label "Vertical")
        align_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        lbl_align = Gtk.Label(label=_("Align:"), xalign=0)
        lbl_align.set_hexpand(True)
        align_row.append(lbl_align)

        # Horizontal alignment
        align_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        align_box.add_css_class("linked")

        btn_left = Gtk.ToggleButton()
        btn_left.set_icon_name("format-justify-left-symbolic")
        btn_left.set_tooltip_text(_("Align Left"))

        btn_center = Gtk.ToggleButton()
        btn_center.set_icon_name("format-justify-center-symbolic")
        btn_center.set_tooltip_text(_("Align Center"))

        btn_right = Gtk.ToggleButton()
        btn_right.set_icon_name("format-justify-right-symbolic")
        btn_right.set_tooltip_text(_("Align Right"))

        btn_center.set_group(btn_left)
        btn_right.set_group(btn_left)

        curr_align = getattr(item, "text_align", "center") or "center"
        if curr_align == "left":
            btn_left.set_active(True)
        elif curr_align == "right":
            btn_right.set_active(True)
        else:
            btn_center.set_active(True)

        def on_align_left(b):
            if b.get_active():
                self._set_item_attr(item, "text_align", "left")

        def on_align_center(b):
            if b.get_active():
                self._set_item_attr(item, "text_align", "center")

        def on_align_right(b):
            if b.get_active():
                self._set_item_attr(item, "text_align", "right")

        btn_left.connect("toggled", on_align_left)
        btn_center.connect("toggled", on_align_center)
        btn_right.connect("toggled", on_align_right)

        align_box.append(btn_left)
        align_box.append(btn_center)
        align_box.append(btn_right)
        align_row.append(align_box)

        # Vertical alignment
        v_align_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        v_align_box.add_css_class("linked")
        v_align_box.set_margin_start(4)

        btn_vtop = Gtk.ToggleButton()
        btn_vtop.set_icon_name("format-valign-top-symbolic")
        btn_vtop.set_tooltip_text(_("Align Top"))

        btn_vmid = Gtk.ToggleButton()
        btn_vmid.set_icon_name("format-valign-middle-symbolic")
        btn_vmid.set_tooltip_text(_("Align Middle"))

        btn_vbot = Gtk.ToggleButton()
        btn_vbot.set_icon_name("format-valign-bottom-symbolic")
        btn_vbot.set_tooltip_text(_("Align Bottom"))

        btn_vmid.set_group(btn_vtop)
        btn_vbot.set_group(btn_vtop)

        curr_valign = getattr(item, "text_valign", "middle") or "middle"
        if curr_valign == "top":
            btn_vtop.set_active(True)
        elif curr_valign == "bottom":
            btn_vbot.set_active(True)
        else:
            btn_vmid.set_active(True)

        def on_valign_top(b):
            if b.get_active():
                self._set_item_attr(item, "text_valign", "top")

        def on_valign_mid(b):
            if b.get_active():
                self._set_item_attr(item, "text_valign", "middle")

        def on_valign_bot(b):
            if b.get_active():
                self._set_item_attr(item, "text_valign", "bottom")

        btn_vtop.connect("toggled", on_valign_top)
        btn_vmid.connect("toggled", on_valign_mid)
        btn_vbot.connect("toggled", on_valign_bot)

        v_align_box.append(btn_vtop)
        v_align_box.append(btn_vmid)
        v_align_box.append(btn_vbot)
        align_row.append(v_align_box)

        self.prop_container.append(align_row)

    def _append_emoji_properties(self, item: OverlayItem) -> None:
        # Large emoji preview
        preview_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        preview_box.set_halign(Gtk.Align.CENTER)
        lbl_char = Gtk.Label(label=item.text or "😀")
        lbl_char.add_css_class("title-1")
        preview_box.append(lbl_char)
        self.prop_container.append(preview_box)

    def _append_shape_properties(self, item: OverlayItem) -> None:
        # Fill Color Row
        fill_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        fill_lbl = Gtk.Label(label=_("Fill Color:"), xalign=0)
        fill_lbl.set_hexpand(True)
        fill_row.append(fill_lbl)
        fill_btn = self._create_color_button(item.fill_color, lambda c: self._set_item_attr(item, "fill_color", c))
        fill_row.append(fill_btn)
        self.prop_container.append(fill_row)

        # Border Color & Width Row
        border_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        border_lbl = Gtk.Label(label=_("Border:"), xalign=0)
        border_lbl.set_hexpand(True)
        border_row.append(border_lbl)

        spin_bw = Gtk.SpinButton.new_with_range(0, 30, 1)
        spin_bw.set_value(float(item.border_width))
        spin_bw.connect("value-changed", lambda s: self._set_item_attr(item, "border_width", int(s.get_value())))
        border_row.append(spin_bw)

        border_btn = self._create_color_button(item.border_color, lambda c: self._set_item_attr(item, "border_color", c))
        border_row.append(border_btn)
        self.prop_container.append(border_row)

        # Corner Radius (for rect)
        if item.item_type in ("rect", "square"):
            cr_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            cr_lbl = Gtk.Label(label=_("Corner Radius:"), xalign=0)
            cr_lbl.set_hexpand(True)
            cr_row.append(cr_lbl)

            scale_cr = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0.0, 0.5, 0.02)
            scale_cr.set_value(item.corner_radius)
            scale_cr.connect("value-changed", lambda sc: self._set_item_attr(item, "corner_radius", float(sc.get_value())))
            scale_cr.set_size_request(100, -1)
            cr_row.append(scale_cr)
            self.prop_container.append(cr_row)

    def _append_media_properties(self, item: OverlayItem) -> None:
        if item.item_type in ("gif", "video"):
            chk_loop = Gtk.CheckButton(label=_("Loop continuously"))
            chk_loop.set_active(item.is_loop)
            chk_loop.connect("toggled", lambda b: self._set_item_attr(item, "is_loop", b.get_active()))
            self.prop_container.append(chk_loop)

        if item.item_type == "video" or (item.media_path and Path(item.media_path).suffix.lower() in (".mp4", ".mov", ".webm", ".mkv", ".avi")):
            chk_audio = Gtk.CheckButton(label=_("Allow audio"))
            chk_audio.set_active(getattr(item, "enable_audio", False))
            self.prop_container.append(chk_audio)

            vol_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            vol_box.set_visible(chk_audio.get_active())

            vol_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            vol_lbl = Gtk.Label(label=_("Audio Volume:"), xalign=0)
            vol_lbl.set_hexpand(True)
            vol_row.append(vol_lbl)

            cur_vol = float(getattr(item, "audio_volume", 1.0))
            vol_val_lbl = Gtk.Label(label=f"{int(round(cur_vol * 100))}%")
            vol_val_lbl.add_css_class("dim-label")
            vol_row.append(vol_val_lbl)
            vol_box.append(vol_row)

            scale_vol = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0.0, 2.0, 0.05)
            scale_vol.set_value(cur_vol)
            scale_vol.set_size_request(120, -1)

            def on_video_vol_changed(sc: Gtk.Scale) -> None:
                val = float(sc.get_value())
                self._set_item_attr(item, "audio_volume", val)
                vol_val_lbl.set_label(f"{int(round(val * 100))}%")
                if hasattr(self, "_audio_player") and self._audio_player:
                    self._audio_player.update_playback(self.items, self.current_time, self.is_playing)

            scale_vol.connect("value-changed", on_video_vol_changed)
            vol_box.append(scale_vol)
            self.prop_container.append(vol_box)

            def on_allow_audio_toggled(b: Gtk.CheckButton) -> None:
                act = b.get_active()
                self._set_item_attr(item, "enable_audio", act)
                vol_box.set_visible(act)
                if hasattr(self, "_audio_player") and self._audio_player:
                    self._audio_player.sync_items(self.items)

            chk_audio.connect("toggled", on_allow_audio_toggled)

    def _append_audio_properties(self, item: OverlayItem) -> None:
        filename = Path(item.media_path).name if item.media_path else _("No file")
        name_lbl = Gtk.Label(label=filename, xalign=0)
        name_lbl.add_css_class("dim-label")
        name_lbl.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        self.prop_container.append(name_lbl)

        total_dur = ProbeService.probe_duration(item.media_path) if item.media_path else None
        max_dur = total_dur if total_dur is not None and total_dur > 0 else 3600.0

        if total_dur is not None:
            dur_lbl = Gtk.Label(label=_("Duration: {dur:.2f}s").format(dur=total_dur), xalign=0)
            dur_lbl.add_css_class("caption")
            self.prop_container.append(dur_lbl)

        # Volume slider
        vol_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        vol_lbl = Gtk.Label(label=_("Volume:"), xalign=0)
        vol_lbl.set_hexpand(True)
        vol_row.append(vol_lbl)

        cur_vol = float(getattr(item, "audio_volume", 1.0))
        vol_val_lbl = Gtk.Label(label=f"{int(round(cur_vol * 100))}%")
        vol_val_lbl.add_css_class("dim-label")
        vol_row.append(vol_val_lbl)
        self.prop_container.append(vol_row)

        scale_vol = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0.0, 2.0, 0.05)
        scale_vol.set_value(cur_vol)
        scale_vol.set_size_request(120, -1)

        def on_audio_vol_changed(sc: Gtk.Scale) -> None:
            val = float(sc.get_value())
            self._set_item_attr(item, "audio_volume", val)
            vol_val_lbl.set_label(f"{int(round(val * 100))}%")
            if hasattr(self, "_audio_player") and self._audio_player:
                self._audio_player.update_playback(self.items, self.current_time, self.is_playing)

        scale_vol.connect("value-changed", on_audio_vol_changed)
        self.prop_container.append(scale_vol)

        # Audio Start
        row_start = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        lbl_start = Gtk.Label(label=_("Start (s):"), xalign=0)
        lbl_start.set_hexpand(True)
        row_start.append(lbl_start)

        spin_start = Gtk.SpinButton.new_with_range(0.0, max_dur, 0.1)
        spin_start.set_digits(2)
        spin_start.set_value(float(item.audio_start))
        row_start.append(spin_start)
        self.prop_container.append(row_start)

        # Audio Finish
        row_end = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        lbl_end = Gtk.Label(label=_("Finish (s):"), xalign=0)
        lbl_end.set_hexpand(True)
        row_end.append(lbl_end)

        spin_end = Gtk.SpinButton.new_with_range(0.0, max_dur, 0.1)
        spin_end.set_digits(2)
        cur_end = float(item.audio_end) if item.audio_end is not None else max_dur
        spin_end.set_value(cur_end)
        row_end.append(spin_end)
        self.prop_container.append(row_end)

        def on_start_changed(s: Gtk.SpinButton) -> None:
            val = float(s.get_value())
            item.audio_start = val
            if item.audio_end is not None and item.audio_end < val:
                spin_end.set_value(val)
            self._trigger_canvas_redraw()

        def on_end_changed(s: Gtk.SpinButton) -> None:
            val = float(s.get_value())
            item.audio_end = val
            if val < item.audio_start:
                spin_start.set_value(val)
            self._trigger_canvas_redraw()

        spin_start.connect("value-changed", on_start_changed)
        spin_end.connect("value-changed", on_end_changed)

        chk_loop = Gtk.CheckButton(label=_("Loop continuously"))
        chk_loop.set_active(item.is_loop)
        chk_loop.connect("toggled", lambda b: self._set_item_attr(item, "is_loop", b.get_active()))
        self.prop_container.append(chk_loop)

    def _append_common_properties(self, target: OverlayItem | list[OverlayItem]) -> None:
        self.prop_container.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        vw, vh = self._get_video_pixel_dimensions()

        if isinstance(target, list):
            items = target
            is_multi = len(items) > 1
            primary = items[0] if items else None
        else:
            items = [target]
            is_multi = False
            primary = target

        if not primary:
            return

        if is_multi:
            bounds = self._get_multi_selection_bounds()
            if bounds:
                cur_px_x = int(round(((bounds[0] + bounds[2]) / 2.0) * vw))
                cur_px_y = int(round(((bounds[1] + bounds[3]) / 2.0) * vh))
            else:
                cur_px_x = int(round(primary.x * vw))
                cur_px_y = int(round(primary.y * vh))
        else:
            cur_px_x = int(round(primary.x * vw))
            cur_px_y = int(round(primary.y * vh))

        # Position header
        pos_header = Gtk.Label(label=_("Position (px):"), xalign=0)
        pos_header.add_css_class("dim-label")
        self.prop_container.append(pos_header)

        # Position (X, Y) in video pixels on the same line
        pos_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        lbl_x = Gtk.Label(label=_("X:"), xalign=0)
        lbl_x.set_tooltip_text(_("X Position (Horizontal)"))
        pos_row.append(lbl_x)
        self.spin_pos_x = Gtk.SpinButton.new_with_range(-vw, 2 * vw, 1)
        self.spin_pos_x.set_value(cur_px_x)
        self.spin_pos_x.set_hexpand(True)
        self.spin_pos_x.set_tooltip_text(_("X Position (Horizontal)"))
        self.spin_pos_x.connect("value-changed", self._on_pos_x_changed)
        pos_row.append(self.spin_pos_x)

        lbl_y = Gtk.Label(label=_("Y:"), xalign=0)
        lbl_y.set_tooltip_text(_("Y Position (Vertical)"))
        lbl_y.set_margin_start(4)
        pos_row.append(lbl_y)
        self.spin_pos_y = Gtk.SpinButton.new_with_range(-vh, 2 * vh, 1)
        self.spin_pos_y.set_value(cur_px_y)
        self.spin_pos_y.set_hexpand(True)
        self.spin_pos_y.set_tooltip_text(_("Y Position (Vertical)"))
        self.spin_pos_y.connect("value-changed", self._on_pos_y_changed)
        pos_row.append(self.spin_pos_y)
        self.prop_container.append(pos_row)

        has_non_audio = any(it.item_type != "audio" for it in items) if is_multi else (primary.item_type != "audio")
        if has_non_audio:
            # Size header
            size_header = Gtk.Label(label=_("Size (px):"), xalign=0)
            size_header.add_css_class("dim-label")
            self.prop_container.append(size_header)

            if is_multi:
                bounds = self._get_multi_selection_bounds()
                if bounds:
                    cur_px_w = max(1, int(round((bounds[2] - bounds[0]) * vw)))
                    cur_px_h = max(1, int(round((bounds[3] - bounds[1]) * vh)))
                else:
                    cur_px_w = max(1, int(round(primary.width * vw)))
                    cur_px_h = max(1, int(round(primary.height * vh)))
            else:
                cur_px_w = max(1, int(round(primary.width * vw)))
                cur_px_h = max(1, int(round(primary.height * vh)))

            size_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            lbl_w = Gtk.Label(label=_("W:"), xalign=0)
            lbl_w.set_tooltip_text(_("Width"))
            size_row.append(lbl_w)
            self.spin_size_w = Gtk.SpinButton.new_with_range(1, 3 * vw, 1)
            self.spin_size_w.set_value(cur_px_w)
            self.spin_size_w.set_hexpand(True)
            self.spin_size_w.set_tooltip_text(_("Width"))
            self.spin_size_w.connect("value-changed", self._on_size_w_changed)
            size_row.append(self.spin_size_w)

            lbl_h = Gtk.Label(label=_("H:"), xalign=0)
            lbl_h.set_tooltip_text(_("Height"))
            lbl_h.set_margin_start(4)
            size_row.append(lbl_h)
            self.spin_size_h = Gtk.SpinButton.new_with_range(1, 3 * vh, 1)
            self.spin_size_h.set_value(cur_px_h)
            self.spin_size_h.set_hexpand(True)
            self.spin_size_h.set_tooltip_text(_("Height"))
            self.spin_size_h.connect("value-changed", self._on_size_h_changed)
            size_row.append(self.spin_size_h)
            self.prop_container.append(size_row)

            if not is_multi and primary.item_type not in ("emoji", "text"):
                chk_prop = Gtk.CheckButton(label=_("Keep proportions"))
                chk_prop.set_active(getattr(primary, "keep_aspect_ratio", True))
                chk_prop.connect("toggled", lambda b: self._set_item_attr(primary, "keep_aspect_ratio", b.get_active()))
                self.prop_container.append(chk_prop)

            # Rotation spinbutton
            rot_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            rot_lbl = Gtk.Label(label=_("Rotation:"), xalign=0)
            rot_lbl.set_hexpand(True)
            rot_row.append(rot_lbl)

            spin_rot = Gtk.SpinButton.new_with_range(0, 360, 5)
            spin_rot.set_value(float(primary.rotation))
            if is_multi:
                def on_multi_rot(s):
                    val = float(s.get_value())
                    delta = val - primary.rotation
                    self._save_undo_state()
                    for it in items:
                        it.rotation = round((it.rotation + delta) % 360.0, 1)
                    self._trigger_canvas_redraw()
                spin_rot.connect("value-changed", on_multi_rot)
            else:
                spin_rot.connect("value-changed", lambda s: self._set_item_attr(primary, "rotation", float(s.get_value())))
            rot_row.append(spin_rot)
            self.prop_container.append(rot_row)

            # Opacity slider
            op_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            op_lbl = Gtk.Label(label=_("Opacity:"), xalign=0)
            op_lbl.set_hexpand(True)
            op_row.append(op_lbl)

            scale_op = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0.05, 1.0, 0.05)
            scale_op.set_value(primary.opacity)
            if is_multi:
                scale_op.connect("value-changed", lambda sc: self._set_multi_attr(items, "opacity", round(float(sc.get_value()), 2)))
            else:
                scale_op.connect("value-changed", lambda sc: self._set_item_attr(primary, "opacity", float(sc.get_value())))
            scale_op.set_size_request(110, -1)
            op_row.append(scale_op)
            self.prop_container.append(op_row)
        else:
            self.spin_size_w = None
            self.spin_size_h = None

        # Layer Ordering
        layer_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        layer_lbl = Gtk.Label(label=_("Layer:"), xalign=0)
        layer_lbl.set_hexpand(True)
        layer_row.append(layer_lbl)

        layer_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        layer_box.add_css_class("linked")

        btn_top = Gtk.Button.new_from_icon_name("go-top-symbolic")
        btn_top.set_tooltip_text(_("Bring to Front (Home / Ctrl+Shift+])"))
        btn_top.connect("clicked", lambda _: self._change_multi_z_order(items, "top") if is_multi else self._change_z_order(primary, "top"))
        layer_box.append(btn_top)

        btn_up = Gtk.Button.new_from_icon_name("go-up-symbolic")
        btn_up.set_tooltip_text(_("Bring Forward (Page Up / Ctrl+])"))
        btn_up.connect("clicked", lambda _: self._change_multi_z_order(items, "up") if is_multi else self._change_z_order(primary, "up"))
        layer_box.append(btn_up)

        btn_down = Gtk.Button.new_from_icon_name("go-down-symbolic")
        btn_down.set_tooltip_text(_("Send Backward (Page Down / Ctrl+[)"))
        btn_down.connect("clicked", lambda _: self._change_multi_z_order(items, "down") if is_multi else self._change_z_order(primary, "down"))
        layer_box.append(btn_down)

        btn_bottom = Gtk.Button.new_from_icon_name("go-bottom-symbolic")
        btn_bottom.set_tooltip_text(_("Send to Back (End / Ctrl+Shift+[)"))
        btn_bottom.connect("clicked", lambda _: self._change_multi_z_order(items, "bottom") if is_multi else self._change_z_order(primary, "bottom"))
        layer_box.append(btn_bottom)

        layer_row.append(layer_box)
        self.prop_container.append(layer_row)

    def _on_pos_x_changed(self, spin: Gtk.SpinButton) -> None:
        if self._updating_transform_spinners:
            return
        vw, _ = self._get_video_pixel_dimensions()
        if vw <= 0:
            return
        new_x = float(spin.get_value()) / vw
        if len(self.selected_items) > 1:
            bounds = self._get_multi_selection_bounds()
            if not bounds:
                return
            old_cx = (bounds[0] + bounds[2]) / 2.0
            dx = new_x - old_cx
            if abs(dx) > 1e-5:
                self._save_undo_state()
                for it in self.selected_items:
                    it.x += dx
                self._update_canvas_geometry()
        elif self.selected_item:
            if abs(new_x - self.selected_item.x) > 1e-5:
                self._save_undo_state()
                self.selected_item.x = new_x
                self._update_canvas_geometry()

    def _on_pos_y_changed(self, spin: Gtk.SpinButton) -> None:
        if self._updating_transform_spinners:
            return
        _, vh = self._get_video_pixel_dimensions()
        if vh <= 0:
            return
        new_y = float(spin.get_value()) / vh
        if len(self.selected_items) > 1:
            bounds = self._get_multi_selection_bounds()
            if not bounds:
                return
            old_cy = (bounds[1] + bounds[3]) / 2.0
            dy = new_y - old_cy
            if abs(dy) > 1e-5:
                self._save_undo_state()
                for it in self.selected_items:
                    it.y += dy
                self._update_canvas_geometry()
        elif self.selected_item:
            if abs(new_y - self.selected_item.y) > 1e-5:
                self._save_undo_state()
                self.selected_item.y = new_y
                self._update_canvas_geometry()

    def _on_size_w_changed(self, spin: Gtk.SpinButton) -> None:
        if self._updating_transform_spinners:
            return
        vw, _ = self._get_video_pixel_dimensions()
        if vw <= 0:
            return
        new_w = max(0.01, float(spin.get_value()) / vw)
        if len(self.selected_items) > 1:
            bounds = self._get_multi_selection_bounds()
            if not bounds:
                return
            old_bw = max(0.001, bounds[2] - bounds[0])
            scale_x = new_w / old_bw
            if abs(scale_x - 1.0) > 1e-4:
                self._save_undo_state()
                cx = (bounds[0] + bounds[2]) / 2.0
                for it in self.selected_items:
                    it.x = cx + (it.x - cx) * scale_x
                    it.width = max(0.01, it.width * scale_x)
                self._update_canvas_geometry()
        elif self.selected_item:
            if abs(new_w - self.selected_item.width) > 1e-5:
                self._save_undo_state()
                is_prop = (self.selected_item.item_type != "text") and (
                    (self.selected_item.item_type == "emoji") or getattr(self.selected_item, "keep_aspect_ratio", True)
                )
                if is_prop and self.selected_item.width > 0:
                    ratio = self.selected_item.height / self.selected_item.width
                    self.selected_item.width = new_w
                    self.selected_item.height = max(0.01, new_w * ratio)
                    self._sync_position_and_size_spinners(self.selected_item)
                else:
                    self.selected_item.width = new_w
                self._update_canvas_geometry()

    def _on_size_h_changed(self, spin: Gtk.SpinButton) -> None:
        if self._updating_transform_spinners:
            return
        _, vh = self._get_video_pixel_dimensions()
        if vh <= 0:
            return
        new_h = max(0.01, float(spin.get_value()) / vh)
        if len(self.selected_items) > 1:
            bounds = self._get_multi_selection_bounds()
            if not bounds:
                return
            old_bh = max(0.001, bounds[3] - bounds[1])
            scale_y = new_h / old_bh
            if abs(scale_y - 1.0) > 1e-4:
                self._save_undo_state()
                cy = (bounds[1] + bounds[3]) / 2.0
                for it in self.selected_items:
                    it.y = cy + (it.y - cy) * scale_y
                    it.height = max(0.01, it.height * scale_y)
                self._update_canvas_geometry()
        elif self.selected_item:
            if abs(new_h - self.selected_item.height) > 1e-5:
                self._save_undo_state()
                is_prop = (self.selected_item.item_type != "text") and (
                    (self.selected_item.item_type == "emoji") or getattr(self.selected_item, "keep_aspect_ratio", True)
                )
                if is_prop and self.selected_item.height > 0:
                    ratio = self.selected_item.width / self.selected_item.height
                    self.selected_item.height = new_h
                    self.selected_item.width = max(0.01, new_h * ratio)
                    self._sync_position_and_size_spinners(self.selected_item)
                else:
                    self.selected_item.height = new_h
                self._update_canvas_geometry()

    def _sync_position_and_size_spinners(self, item: OverlayItem | None = None) -> None:
        if self._updating_transform_spinners:
            return
        self._updating_transform_spinners = True
        try:
            vw, vh = self._get_video_pixel_dimensions()
            if vw <= 0 or vh <= 0:
                return

            if len(self.selected_items) > 1:
                bounds = self._get_multi_selection_bounds()
                if bounds:
                    min_x, min_y, max_x, max_y = bounds
                    cx = (min_x + max_x) / 2.0
                    cy = (min_y + max_y) / 2.0
                    bw = max_x - min_x
                    bh = max_y - min_y
                    if self.spin_pos_x:
                        self.spin_pos_x.set_value(int(round(cx * vw)))
                    if self.spin_pos_y:
                        self.spin_pos_y.set_value(int(round(cy * vh)))
                    if self.spin_size_w:
                        self.spin_size_w.set_value(max(1, int(round(bw * vw))))
                    if self.spin_size_h:
                        self.spin_size_h.set_value(max(1, int(round(bh * vh))))
            elif item or self.selected_item:
                target = item or self.selected_item
                if self.spin_pos_x and target:
                    self.spin_pos_x.set_value(int(round(target.x * vw)))
                if self.spin_pos_y and target:
                    self.spin_pos_y.set_value(int(round(target.y * vh)))
                if self.spin_size_w and target:
                    self.spin_size_w.set_value(max(1, int(round(target.width * vw))))
                if self.spin_size_h and target:
                    self.spin_size_h.set_value(max(1, int(round(target.height * vh))))
        finally:
            self._updating_transform_spinners = False


    def _create_color_button(self, initial_color: str, on_changed: Callable[[str], None]) -> Gtk.Widget:
        """Create a native GTK 4 ColorDialogButton."""
        dialog = Gtk.ColorDialog.new()
        btn = Gtk.ColorDialogButton.new(dialog)
        r, g, b, a = parse_color(initial_color)
        rgba = Gdk.RGBA()
        rgba.red, rgba.green, rgba.blue, rgba.alpha = r, g, b, a
        btn.set_rgba(rgba)

        def on_notify_rgba(b, pspec):
            new_rgba = b.get_rgba()
            hex_str = f"#{int(new_rgba.red*255):02x}{int(new_rgba.green*255):02x}{int(new_rgba.blue*255):02x}"
            if new_rgba.alpha < 0.99:
                hex_str += f"{int(new_rgba.alpha*255):02x}"
            on_changed(hex_str)

        btn.connect("notify::rgba", on_notify_rgba)
        return btn

    def _set_item_attr(self, item: OverlayItem, attr: str, val: Any) -> None:
        setattr(item, attr, val)
        self._trigger_canvas_redraw()

    def _set_multi_attr(self, items: list[OverlayItem], attr: str, val: Any) -> None:
        self._save_undo_state()
        for it in items:
            setattr(it, attr, val)
        self._trigger_canvas_redraw()

    def _change_z_order(self, item: OverlayItem, direction: str, save_undo: bool = True) -> None:
        idx = self.items.index(item) if item in self.items else -1
        if idx < 0:
            return
        if save_undo:
            self._save_undo_state()
        if direction == "top":
            self.items.remove(item)
            self.items.append(item)
        elif direction == "bottom":
            self.items.remove(item)
            self.items.insert(0, item)
        elif direction == "up" and idx < len(self.items) - 1:
            self.items[idx], self.items[idx + 1] = self.items[idx + 1], self.items[idx]
        elif direction == "down" and idx > 0:
            self.items[idx], self.items[idx - 1] = self.items[idx - 1], self.items[idx]

        for i, it in enumerate(self.items):
            it.z_index = i
        self._trigger_canvas_redraw()

    def _change_multi_z_order(self, items: list[OverlayItem], direction: str) -> None:
        if not items:
            return
        self._save_undo_state()
        if direction in ("up", "top"):
            for it in sorted(items, key=lambda i: i.z_index, reverse=True):
                self._change_z_order(it, direction, save_undo=False)
        else:
            for it in sorted(items, key=lambda i: i.z_index):
                self._change_z_order(it, direction, save_undo=False)
        self._refresh_canvas_and_properties()

    def _align_selected(self, mode: str) -> None:
        """Align selected elements relative to their collective bounding box."""
        items = self.selected_items
        if len(items) < 2:
            return
        self._save_undo_state()
        bounds = self._get_multi_selection_bounds()
        if not bounds:
            return
        min_x, min_y, max_x, max_y = bounds
        center_x = (min_x + max_x) / 2.0
        center_y = (min_y + max_y) / 2.0

        for it in items:
            if mode == "left":
                it.x = min_x + it.width / 2.0
            elif mode == "center":
                it.x = center_x
            elif mode == "right":
                it.x = max_x - it.width / 2.0
            elif mode == "top":
                it.y = min_y + it.height / 2.0
            elif mode == "middle":
                it.y = center_y
            elif mode == "bottom":
                it.y = max_y - it.height / 2.0

        self._refresh_canvas_and_properties()

    def _distribute_selected(self, direction: str) -> None:
        """Distribute selected elements evenly along horizontal or vertical axis."""
        items = self.selected_items
        if len(items) < 2:
            return
        self._save_undo_state()

        if direction == "horizontal":
            sorted_items = sorted(items, key=lambda it: it.x)
            first = sorted_items[0]
            last = sorted_items[-1]
            first_left = first.x - first.width / 2.0
            last_right = last.x + last.width / 2.0
            total_span = last_right - first_left
            sum_widths = sum(it.width for it in sorted_items)
            total_gaps = total_span - sum_widths

            if total_gaps >= 0 and len(sorted_items) > 2:
                gap = total_gaps / (len(sorted_items) - 1)
                curr_x = first_left
                for it in sorted_items:
                    it.x = curr_x + it.width / 2.0
                    curr_x += it.width + gap
            else:
                step = (last.x - first.x) / (len(sorted_items) - 1)
                for i, it in enumerate(sorted_items):
                    it.x = first.x + i * step

        elif direction == "vertical":
            sorted_items = sorted(items, key=lambda it: it.y)
            first = sorted_items[0]
            last = sorted_items[-1]
            first_top = first.y - first.height / 2.0
            last_bottom = last.y + last.height / 2.0
            total_span = last_bottom - first_top
            sum_heights = sum(it.height for it in sorted_items)
            total_gaps = total_span - sum_heights

            if total_gaps >= 0 and len(sorted_items) > 2:
                gap = total_gaps / (len(sorted_items) - 1)
                curr_y = first_top
                for it in sorted_items:
                    it.y = curr_y + it.height / 2.0
                    curr_y += it.height + gap
            else:
                step = (last.y - first.y) / (len(sorted_items) - 1)
                for i, it in enumerate(sorted_items):
                    it.y = first.y + i * step

        self._refresh_canvas_and_properties()

    def _append_alignment_and_distribution(self, items: list[OverlayItem]) -> None:
        align_header = Gtk.Label(label=_("Alignment & Distribution:"), xalign=0)
        align_header.add_css_class("dim-label")
        self.prop_container.append(align_header)

        # Horizontal row: Left, Center, Right | Distribute Horizontal
        h_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        h_row.add_css_class("linked")
        h_row.set_halign(Gtk.Align.FILL)

        btn_aleft = Gtk.Button.new_from_icon_name("align-horizontal-left-symbolic")
        btn_aleft.set_tooltip_text(_("Align Left"))
        btn_aleft.set_hexpand(True)
        btn_aleft.connect("clicked", lambda _: self._align_selected("left"))
        h_row.append(btn_aleft)

        btn_acenter = Gtk.Button.new_from_icon_name("align-horizontal-center-symbolic")
        btn_acenter.set_tooltip_text(_("Align Center"))
        btn_acenter.set_hexpand(True)
        btn_acenter.connect("clicked", lambda _: self._align_selected("center"))
        h_row.append(btn_acenter)

        btn_aright = Gtk.Button.new_from_icon_name("align-horizontal-right-symbolic")
        btn_aright.set_tooltip_text(_("Align Right"))
        btn_aright.set_hexpand(True)
        btn_aright.connect("clicked", lambda _: self._align_selected("right"))
        h_row.append(btn_aright)

        btn_dhoriz = Gtk.Button.new_from_icon_name("distribute-horizontal-symbolic")
        btn_dhoriz.set_tooltip_text(_("Distribute Horizontally"))
        btn_dhoriz.set_hexpand(True)
        btn_dhoriz.set_sensitive(len(items) >= 3)
        btn_dhoriz.connect("clicked", lambda _: self._distribute_selected("horizontal"))
        h_row.append(btn_dhoriz)

        self.prop_container.append(h_row)

        # Vertical row: Top, Middle, Bottom | Distribute Vertical
        v_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        v_row.add_css_class("linked")
        v_row.set_halign(Gtk.Align.FILL)

        btn_atop = Gtk.Button.new_from_icon_name("align-vertical-top-symbolic")
        btn_atop.set_tooltip_text(_("Align Top"))
        btn_atop.set_hexpand(True)
        btn_atop.connect("clicked", lambda _: self._align_selected("top"))
        v_row.append(btn_atop)

        btn_amiddle = Gtk.Button.new_from_icon_name("align-vertical-center-symbolic")
        btn_amiddle.set_tooltip_text(_("Align Middle"))
        btn_amiddle.set_hexpand(True)
        btn_amiddle.connect("clicked", lambda _: self._align_selected("middle"))
        v_row.append(btn_amiddle)

        btn_abottom = Gtk.Button.new_from_icon_name("align-vertical-bottom-symbolic")
        btn_abottom.set_tooltip_text(_("Align Bottom"))
        btn_abottom.set_hexpand(True)
        btn_abottom.connect("clicked", lambda _: self._align_selected("bottom"))
        v_row.append(btn_abottom)

        btn_dvert = Gtk.Button.new_from_icon_name("distribute-vertical-symbolic")
        btn_dvert.set_tooltip_text(_("Distribute Vertically"))
        btn_dvert.set_hexpand(True)
        btn_dvert.set_sensitive(len(items) >= 3)
        btn_dvert.connect("clicked", lambda _: self._distribute_selected("vertical"))
        v_row.append(btn_dvert)

        self.prop_container.append(v_row)

    def _append_multi_text_properties(self, items: list[OverlayItem]) -> None:
        first = items[0]
        # Font family button
        font_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        font_lbl = Gtk.Label(label=_("Font:"), xalign=0)
        font_box.append(font_lbl)

        font_btn = self._create_font_button(
            first.font_family or "Sans",
            lambda fam: self._set_multi_attr(items, "font_family", fam),
        )
        font_box.append(font_btn)
        self.prop_container.append(font_box)

        # Size, Style (Bold, Italic)
        style_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        lbl_sz = Gtk.Label(label=_("Size:"), xalign=0)
        style_row.append(lbl_sz)

        spin_sz = Gtk.SpinButton.new_with_range(8, 200, 2)
        spin_sz.set_value(float(first.font_size))
        spin_sz.set_width_chars(3)
        spin_sz.set_hexpand(True)

        def on_multi_size_changed(s):
            val = int(s.get_value())
            self._save_undo_state()
            for it in items:
                it.font_size = val
                self._auto_fit_text_box_height(it)
            self._trigger_canvas_redraw()
            self._sync_position_and_size_spinners()

        spin_sz.connect("value-changed", on_multi_size_changed)
        style_row.append(spin_sz)

        style_toggles = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        style_toggles.add_css_class("linked")

        btn_bold = Gtk.ToggleButton()
        lbl_b = Gtk.Label()
        lbl_b.set_markup("<b>B</b>")
        btn_bold.set_child(lbl_b)
        btn_bold.set_active(first.bold)
        btn_bold.set_tooltip_text(_("Bold"))
        btn_bold.connect("toggled", lambda b: self._set_multi_attr(items, "bold", b.get_active()))
        style_toggles.append(btn_bold)

        btn_italic = Gtk.ToggleButton()
        lbl_i = Gtk.Label()
        lbl_i.set_markup("<i>I</i>")
        btn_italic.set_child(lbl_i)
        btn_italic.set_active(first.italic)
        btn_italic.set_tooltip_text(_("Italic"))
        btn_italic.connect("toggled", lambda b: self._set_multi_attr(items, "italic", b.get_active()))
        style_toggles.append(btn_italic)

        style_row.append(style_toggles)
        self.prop_container.append(style_row)

        # Color row (Text Color and Background Color)
        color_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        lbl_col = Gtk.Label(label=_("Color:"), xalign=0)
        lbl_col.set_hexpand(True)
        color_row.append(lbl_col)

        color_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        color_box.add_css_class("linked")

        color_btn = self._create_color_button(first.font_color, lambda c: self._set_multi_attr(items, "font_color", c))
        color_btn.set_tooltip_text(_("Text Color"))
        color_box.append(color_btn)

        bg_btn = self._create_color_button(
            first.bg_color or "transparent",
            lambda c: self._set_multi_attr(items, "bg_color", c if c != "rgba(0,0,0,0)" else None),
        )
        bg_btn.set_tooltip_text(_("Background Color"))
        color_box.append(bg_btn)

        color_row.append(color_box)
        self.prop_container.append(color_row)

    def _append_multi_shape_properties(self, items: list[OverlayItem]) -> None:
        first = items[0]
        # Fill Color Row
        fill_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        fill_lbl = Gtk.Label(label=_("Fill Color:"), xalign=0)
        fill_lbl.set_hexpand(True)
        fill_row.append(fill_lbl)
        fill_btn = self._create_color_button(first.fill_color or "#3584e4", lambda c: self._set_multi_attr(items, "fill_color", c))
        fill_row.append(fill_btn)
        self.prop_container.append(fill_row)

        # Border Color & Width Row
        border_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        border_lbl = Gtk.Label(label=_("Border:"), xalign=0)
        border_lbl.set_hexpand(True)
        border_row.append(border_lbl)

        spin_bw = Gtk.SpinButton.new_with_range(0, 30, 1)
        spin_bw.set_value(float(first.border_width))
        spin_bw.connect("value-changed", lambda s: self._set_multi_attr(items, "border_width", int(s.get_value())))
        border_row.append(spin_bw)

        border_btn = self._create_color_button(first.border_color or "#1c71d8", lambda c: self._set_multi_attr(items, "border_color", c))
        border_row.append(border_btn)
        self.prop_container.append(border_row)

    def _append_multi_properties(self, items: list[OverlayItem]) -> None:
        """Render properties panel when multiple elements are selected."""
        self._append_alignment_and_distribution(items)
        if all(it.item_type == "text" for it in items):
            self._append_multi_text_properties(items)
        elif all(it.item_type in ("rect", "square", "circle", "ellipse") for it in items):
            self._append_multi_shape_properties(items)
        self._append_common_properties(items)

    # --------------------------------------------------------------------------
    # Tool Palette Actions
    # --------------------------------------------------------------------------


    def _on_tool_add_text(self) -> None:
        self._save_undo_state()
        item = OverlayItem(
            item_type="text",
            text=_("Sample Text"),
            font_size=42,
            font_color="#ffffff",
            bold=True,
            x=0.5,
            y=0.5,
            width=0.35,
            height=0.20,
            keep_aspect_ratio=False,
            z_index=len(self.items),
        )
        self._auto_fit_text_box_height(item)
        self.items.append(item)
        self.selected_item_id = item.id
        self._refresh_canvas_and_properties()

    def _on_tool_add_shape(self, shape_type: str) -> None:
        self._save_undo_state()
        video_aspect = max(0.01, self._get_video_aspect())
        if shape_type in ("circle", "ellipse"):
            # Ensure 1:1 pixel aspect ratio for a true circle
            norm_w = 0.22
            norm_h = min(0.6, norm_w * video_aspect)
        else:
            norm_w = 0.25
            norm_h = 0.2

        item = OverlayItem(
            item_type=shape_type,
            fill_color="#ff5500",
            border_color="#ffffff",
            border_width=3,
            corner_radius=0.1 if shape_type == "rect" else 0.0,
            x=0.5,
            y=0.5,
            width=round(norm_w, 4),
            height=round(norm_h, 4),
            z_index=len(self.items),
            keep_aspect_ratio=False,
        )
        self.items.append(item)
        self.selected_item_id = item.id
        self._refresh_canvas_and_properties()

    def _on_tool_add_media(self) -> None:
        dialog = Gtk.FileDialog.new()
        dialog.set_title(_("Choose Overlay Image, SVG, GIF or Video"))

        filters_store = Gio.ListStore.new(Gtk.FileFilter)
        filter_all = Gtk.FileFilter.new()
        filter_all.set_name(_("All Supported Overlays"))
        for ext in ("*.png", "*.jpg", "*.jpeg", "*.webp", "*.svg", "*.gif", "*.mp4", "*.webm", "*.mov", "*.mkv", "*.avi"):
            filter_all.add_pattern(ext)
        filters_store.append(filter_all)
        dialog.set_filters(filters_store)

        def on_open_finish(dlg, res):
            try:
                gfile = dlg.open_finish(res)
                if gfile:
                    media_path = Path(gfile.get_path())
                    self._add_media_item(media_path)
            except Exception as e:
                print(f"Error opening overlay media: {e}")

        dialog.open(self, None, on_open_finish)

    def _add_media_item(self, media_path: Path) -> None:
        ext = media_path.suffix.lower()
        itype = "image"
        if ext == ".svg":
            itype = "svg"
        elif ext == ".gif":
            itype = "gif"
        elif ext in (".mp4", ".webm", ".mov", ".mkv", ".avi"):
            itype = "video"

        # Determine natural dimensions of the media
        img_w, img_h = 200, 200
        if itype == "video":
            meta = ProbeService.probe(media_path)
            if meta and meta.width > 0 and meta.height > 0:
                img_w, img_h = meta.width, meta.height
            OverlayRenderService.get_video_clip(media_path)
        else:
            try:
                info, w, h = GdkPixbuf.Pixbuf.get_file_info(str(media_path))
                if info and w > 0 and h > 0:
                    img_w, img_h = w, h
                else:
                    with Image.open(media_path) as img:
                        img_w, img_h = img.width, img.height
            except Exception:
                try:
                    with Image.open(media_path) as img:
                        img_w, img_h = img.width, img.height
                except Exception:
                    pass

        img_aspect = max(0.01, float(img_w) / float(img_h))
        video_aspect = max(0.01, self._get_video_aspect())

        # Fit while preserving natural aspect ratio:
        # pixel_w / pixel_h = (w_norm * video_w) / (h_norm * video_h) = (w_norm / h_norm) * video_aspect = img_aspect
        # => w_norm / h_norm = img_aspect / video_aspect
        if img_aspect >= 1.0:
            norm_w = 0.35
            norm_h = norm_w * (video_aspect / img_aspect)
            if norm_h > 0.7:
                norm_w *= (0.7 / norm_h)
                norm_h = 0.7
        else:
            norm_h = 0.35
            norm_w = norm_h * (img_aspect / video_aspect)
            if norm_w > 0.7:
                norm_h *= (0.7 / norm_w)
                norm_w = 0.7

        self._save_undo_state()
        item = OverlayItem(
            item_type=itype,
            media_path=media_path,
            x=0.5,
            y=0.5,
            width=round(norm_w, 4),
            height=round(norm_h, 4),
            z_index=len(self.items),
        )
        self.items.append(item)
        self.selected_item_id = item.id
        self._refresh_canvas_and_properties()

    def _on_tool_add_audio(self) -> None:
        dialog = Gtk.FileDialog.new()
        dialog.set_title(_("Choose Audio Track"))

        filters_store = Gio.ListStore.new(Gtk.FileFilter)
        filter_audio = Gtk.FileFilter.new()
        filter_audio.set_name(_("Audio Files (*.mp3, *.wav, *.aac, *.m4a, *.flac, *.ogg)"))
        for ext in ("*.mp3", "*.wav", "*.aac", "*.m4a", "*.flac", "*.ogg", "*.opus"):
            filter_audio.add_pattern(ext)
        filters_store.append(filter_audio)
        dialog.set_filters(filters_store)

        def on_open_finish(dlg, res):
            try:
                gfile = dlg.open_finish(res)
                if gfile:
                    audio_path = Path(gfile.get_path())
                    self._add_audio_item(audio_path)
            except Exception as e:
                print(f"Error opening audio track: {e}")

        dialog.open(self, None, on_open_finish)

    def _add_audio_item(self, audio_path: Path) -> None:
        total_dur = ProbeService.probe_duration(audio_path)
        self._save_undo_state()
        item = OverlayItem(
            item_type="audio",
            media_path=audio_path,
            audio_start=0.0,
            audio_end=total_dur,
            is_loop=False,
            x=0.5,
            y=0.85,
            width=0.4,
            height=0.08,
            z_index=len(self.items),
        )
        self.items.append(item)
        self.selected_item_id = item.id
        self._refresh_canvas_and_properties()

    def _on_emoji_picked(self, _chooser: Gtk.EmojiChooser, emoji_text: str) -> None:
        """Handle selection from native Gtk.EmojiChooser."""
        if hasattr(self, "emoji_chooser"):
            self.emoji_chooser.popdown()
        self._add_emoji_item(emoji_text)

    def _add_emoji_item(self, emoji_text: str) -> None:
        """Add an emoji as an overlay item."""
        self._save_undo_state()
        video_aspect = max(0.01, self._get_video_aspect())
        norm_w = 0.2
        norm_h = min(0.6, norm_w * video_aspect)

        item = OverlayItem(
            item_type="emoji",
            text=emoji_text,
            font_size=72,
            font_color="#ffffff",
            x=0.5,
            y=0.5,
            width=round(norm_w, 4),
            height=round(norm_h, 4),
            z_index=len(self.items),
        )
        self.items.append(item)
        self.selected_item_id = item.id
        self._refresh_canvas_and_properties()




    # --------------------------------------------------------------------------
    # Cut / Copy / Paste / Duplicate / Delete
    # --------------------------------------------------------------------------

    _clipboard_items: list[OverlayItem] | None = None
    _clipboard_item: OverlayItem | None = None
    _clipboard_paste_count: int = 0

    def _copy_selected(self) -> None:
        """Copy selected overlay item(s) to internal and system clipboard."""
        items = self.selected_items
        if not items:
            return
        OverlayDialog._clipboard_items = [it.clone() for it in items]
        OverlayDialog._clipboard_item = items[0].clone()
        OverlayDialog._clipboard_paste_count = 0

        display = Gdk.Display.get_default()
        if display:
            try:
                items_data = []
                for it in items:
                    d = asdict(it)
                    if d.get("media_path") is not None:
                        d["media_path"] = str(d["media_path"])
                    items_data.append(d)
                payload = json.dumps({
                    "__videocut_overlay_multi__": True,
                    "items": items_data,
                    "__videocut_overlay__": True,
                    "data": items_data[0],
                })
                display.get_clipboard().set(payload)
            except Exception as e:
                print(f"Error copying overlay to system clipboard: {e}")

    def _cut_selected(self) -> None:
        """Cut selected overlay item(s) to clipboard and delete them."""
        items = self.selected_items
        if not items:
            return
        self._copy_selected()
        self._on_delete_selected()

    def _can_paste(self) -> bool:
        """Return True if internal or system clipboard contains pasteable overlay content."""
        if getattr(OverlayDialog, "_clipboard_items", None) or OverlayDialog._clipboard_item is not None:
            return True
        display = Gdk.Display.get_default()
        if display:
            cb = display.get_clipboard()
            if cb:
                fmts = cb.get_formats()
                if (
                    fmts.contain_gtype(Gdk.Texture)
                    or fmts.contain_gtype(Gdk.FileList)
                    or fmts.contain_gtype(str)
                    or fmts.contain_mime_type("text/plain")
                    or fmts.contain_mime_type("image/png")
                    or fmts.contain_mime_type("image/jpeg")
                ):
                    return True
        return False

    def _paste(self) -> None:
        """Paste overlay item(s) from internal or system clipboard."""
        display = Gdk.Display.get_default()
        cb = display.get_clipboard() if display else None

        # 1. If internal clipboard is set and clipboard is local (or no system clipboard)
        has_internal = bool(getattr(OverlayDialog, "_clipboard_items", None) or OverlayDialog._clipboard_item is not None)
        if has_internal and (cb is None or cb.is_local()):
            self._paste_internal_item()
            return

        # 2. Check system clipboard
        if cb is not None:
            fmts = cb.get_formats()

            # A. Image texture
            if (
                fmts.contain_gtype(Gdk.Texture)
                or fmts.contain_mime_type("image/png")
                or fmts.contain_mime_type("image/jpeg")
            ):
                cb.read_texture_async(None, self._on_clipboard_texture_read)
                return

            # B. File list
            if fmts.contain_gtype(Gdk.FileList):
                cb.read_value_async(Gdk.FileList, GLib.PRIORITY_DEFAULT, None, self._on_clipboard_file_list_read)
                return

            # C. Text
            if fmts.contain_gtype(str) or fmts.contain_mime_type("text/plain") or fmts.contain_mime_type("text/uri-list"):
                cb.read_text_async(None, self._on_clipboard_text_read)
                return

        # 3. Fallback to internal item
        if has_internal:
            self._paste_internal_item()

    def _paste_internal_item(self) -> None:
        clip_items = getattr(OverlayDialog, "_clipboard_items", None)
        if not clip_items:
            if OverlayDialog._clipboard_item is not None:
                clip_items = [OverlayDialog._clipboard_item]
            else:
                return

        OverlayDialog._clipboard_paste_count += 1
        offset = 0.03 * ((OverlayDialog._clipboard_paste_count - 1) % 6 + 1)
        self._save_undo_state()
        new_ids = set()
        for src_item in clip_items:
            new_item = src_item.clone()
            new_item.x = clamp(new_item.x + offset, -1.0, 2.0)
            new_item.y = clamp(new_item.y + offset, -1.0, 2.0)
            new_item.z_index = len(self.items)
            self.items.append(new_item)
            new_ids.add(new_item.id)

        self.selected_item_ids = new_ids
        self._refresh_canvas_and_properties()

    def _duplicate_selected(self) -> None:
        """Duplicate currently selected overlay item(s)."""
        items = self.selected_items
        if not items:
            return
        self._save_undo_state()
        new_ids = set()
        for item in items:
            new_item = item.clone()
            new_item.x = clamp(new_item.x + 0.03, -1.0, 2.0)
            new_item.y = clamp(new_item.y + 0.03, -1.0, 2.0)
            new_item.z_index = len(self.items)
            self.items.append(new_item)
            new_ids.add(new_item.id)

        self.selected_item_ids = new_ids
        self._refresh_canvas_and_properties()

    def _on_clipboard_texture_read(self, cb: Gdk.Clipboard, res: Gio.AsyncResult) -> None:
        try:
            texture = cb.read_texture_finish(res)
            if texture:
                temp_dir = private_dir("video_cut_clipboard")
                temp_path = temp_dir / f"clip_{uuid.uuid4().hex[:8]}.png"
                texture.save_to_png(str(temp_path))

                norm_w, norm_h = self._get_default_element_dimensions(self._get_video_aspect())
                self._save_undo_state()
                item = OverlayItem(
                    item_type="image",
                    media_path=temp_path,
                    x=0.5,
                    y=0.5,
                    width=round(norm_w, 4),
                    height=round(norm_h, 4),
                    z_index=len(self.items),
                )
                self.items.append(item)
                self.selected_item_id = item.id
                self._refresh_canvas_and_properties()
                return
        except Exception as e:
            print(f"Error reading texture from clipboard in overlay editor: {e}")
        if OverlayDialog._clipboard_item is not None:
            self._paste_internal_item()

    def _on_clipboard_file_list_read(self, cb: Gdk.Clipboard, res: Gio.AsyncResult) -> None:
        try:
            val = cb.read_value_finish(res)
            if val:
                for gfile in val.get_files():
                    p = gfile.get_path()
                    if p:
                        path_obj = Path(p)
                        ext = path_obj.suffix.lower()
                        item_type = "image"
                        if ext == ".svg":
                            item_type = "svg"
                        elif ext == ".gif":
                            item_type = "gif"
                        elif ext in (".mp4", ".webm", ".mov", ".mkv", ".avi"):
                            item_type = "video"
                        elif ext in (".mp3", ".wav", ".aac", ".ogg", ".flac", ".m4a"):
                            item_type = "audio"

                        norm_w, norm_h = self._get_default_element_dimensions(self._get_video_aspect())
                        self._save_undo_state()
                        item = OverlayItem(
                            item_type=item_type,
                            media_path=path_obj,
                            x=0.5,
                            y=0.5,
                            width=round(norm_w, 4),
                            height=round(norm_h, 4),
                            z_index=len(self.items),
                        )
                        self.items.append(item)
                        self.selected_item_id = item.id
                        self._refresh_canvas_and_properties()
                        return
        except Exception as e:
            print(f"Error reading file list from clipboard in overlay editor: {e}")
        if OverlayDialog._clipboard_item is not None:
            self._paste_internal_item()

    def _on_clipboard_text_read(self, cb: Gdk.Clipboard, res: Gio.AsyncResult) -> None:
        try:
            text = cb.read_text_finish(res)
            if text:
                text_clean = text.strip()
                # 1. Try JSON VideoCut overlay representation (multi-item or single)
                if text_clean.startswith("{") and text_clean.endswith("}"):
                    if "__videocut_overlay_multi__" in text_clean:
                        parsed = json.loads(text_clean)
                        if parsed.get("__videocut_overlay_multi__") and "items" in parsed:
                            self._save_undo_state()
                            new_ids = set()
                            for d in parsed["items"]:
                                new_item = OverlayItem.from_dict(d)
                                if new_item is None:
                                    continue
                                new_item.x = clamp(new_item.x + 0.03, -1.0, 2.0)
                                new_item.y = clamp(new_item.y + 0.03, -1.0, 2.0)
                                new_item.z_index = len(self.items)
                                self.items.append(new_item)
                                new_ids.add(new_item.id)
                            self.selected_item_ids = new_ids
                            self._refresh_canvas_and_properties()
                            return
                    elif "__videocut_overlay__" in text_clean:
                        parsed = json.loads(text_clean)
                        if parsed.get("__videocut_overlay__") and "data" in parsed:
                            new_item = OverlayItem.from_dict(parsed["data"])
                            if new_item is None:
                                return
                            new_item.x = clamp(new_item.x + 0.03, 0.05, 0.95)
                            new_item.y = clamp(new_item.y + 0.03, 0.05, 0.95)
                            new_item.z_index = len(self.items)

                            self._save_undo_state()
                            self.items.append(new_item)
                            self.selected_item_id = new_item.id
                            self._refresh_canvas_and_properties()
                            return

                # 2. Check if text is URI list pointing to files
                uris = parse_uri_list(text)
                if uris:
                    p = uris[0]
                    if p.exists():
                        ext = p.suffix.lower()
                        item_type = "image"
                        if ext == ".svg":
                            item_type = "svg"
                        elif ext == ".gif":
                            item_type = "gif"
                        elif ext in (".mp4", ".webm", ".mov", ".mkv", ".avi"):
                            item_type = "video"
                        elif ext in (".mp3", ".wav", ".aac", ".ogg", ".flac", ".m4a"):
                            item_type = "audio"

                        norm_w, norm_h = self._get_default_element_dimensions(self._get_video_aspect())
                        self._save_undo_state()
                        item = OverlayItem(
                            item_type=item_type,
                            media_path=p,
                            x=0.5,
                            y=0.5,
                            width=round(norm_w, 4),
                            height=round(norm_h, 4),
                            z_index=len(self.items),
                        )
                        self.items.append(item)
                        self.selected_item_id = item.id
                        self._refresh_canvas_and_properties()
                        return

                # 3. Plain text string -> create new Text overlay!
                norm_w, norm_h = self._get_default_element_dimensions(self._get_video_aspect())
                self._save_undo_state()
                item = OverlayItem(
                    item_type="text",
                    text=text_clean,
                    x=0.5,
                    y=0.5,
                    width=round(norm_w, 4),
                    height=round(norm_h, 4),
                    z_index=len(self.items),
                )
                self.items.append(item)
                self.selected_item_id = item.id
                self._refresh_canvas_and_properties()
                return
        except Exception as e:
            print(f"Error reading text from clipboard in overlay editor: {e}")

        if OverlayDialog._clipboard_item is not None:
            self._paste_internal_item()

    def _on_delete_selected(self) -> None:
        items = self.selected_items
        if not items:
            return
        self._save_undo_state()
        for item in items:
            if item in self.items:
                self.items.remove(item)
        self.selected_item_ids.clear()
        self._refresh_canvas_and_properties()

    def _select_all(self) -> None:
        if not self.items:
            return
        self.selected_item_ids = {it.id for it in self.items}
        self._refresh_canvas_and_properties()

    # --------------------------------------------------------------------------
    # Canvas Rendering & Gestures
    # --------------------------------------------------------------------------

    def _refresh_canvas_and_properties(self) -> None:
        self._update_canvas_geometry()
        self._update_properties_panel()

    def _trigger_canvas_redraw(self) -> None:
        self._render_overlay_canvas()

    def _get_video_aspect(self) -> float:
        """Compute the intrinsic aspect ratio (width / height) of the video segment."""
        if self._media_file and self._media_file.is_prepared():
            vw = float(self._media_file.get_intrinsic_width())
            vh = float(self._media_file.get_intrinsic_height())
            if vw > 0 and vh > 0:
                rot = self.segment.rotation % 360
                if rot in (90, 270):
                    vw, vh = vh, vw
                if self.segment.crop:
                    _, _, cw, ch = self.segment.crop
                    vw *= max(0.01, cw)
                    vh *= max(0.01, ch)
                if vh > 0:
                    return vw / vh

        if self._base_pixbuf:
            bw = float(self._base_pixbuf.get_width())
            bh = float(self._base_pixbuf.get_height())
            if bh > 0:
                return bw / bh

        return 16.0 / 9.0

    def _get_video_pixel_dimensions(self) -> tuple[int, int]:
        """Compute the intrinsic pixel dimensions (width, height) of the video segment after crop/rotation."""
        vw, vh = 0, 0
        if self._media_file and self._media_file.is_prepared():
            vw = self._media_file.get_intrinsic_width()
            vh = self._media_file.get_intrinsic_height()
        if (vw <= 0 or vh <= 0) and self._base_pixbuf:
            vw = self._base_pixbuf.get_width()
            vh = self._base_pixbuf.get_height()
        if vw <= 0 or vh <= 0:
            vw, vh = 1920, 1080

        rot = self.segment.rotation % 360
        if rot in (90, 270):
            vw, vh = vh, vw
        if self.segment.crop:
            _, _, cw, ch = self.segment.crop
            vw = max(1, int(round(vw * cw)))
            vh = max(1, int(round(vh * ch)))
        return (vw, vh)

    # --------------------------------------------------------------------------
    # Zoom & Geometry Controls
    # --------------------------------------------------------------------------

    ZOOM_LEVELS = [0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0, 4.0]

    def _zoom_in(self) -> None:
        for z in self.ZOOM_LEVELS:
            if z > self._zoom_factor + 0.05:
                self._set_zoom(z)
                return
        self._set_zoom(4.0)

    def _zoom_out(self) -> None:
        for z in reversed(self.ZOOM_LEVELS):
            if z < self._zoom_factor - 0.05:
                self._set_zoom(z)
                return
        self._set_zoom(0.25)

    def _zoom_fit(self) -> None:
        self._zoom_factor = 1.0
        if hasattr(self, "lbl_zoom") and self.lbl_zoom:
            self.lbl_zoom.set_label("100%")
        self._update_canvas_geometry(center_on_video=True)
        GLib.idle_add(self._center_scroll_on_video)

    def _center_scroll_on_video(self) -> None:
        if not hasattr(self, "canvas_scrolled"):
            return
        rx, ry, rw, rh = self._canvas_rect
        w, h = self._canvas_alloc_size
        vp_w, vp_h = self._viewport_size
        if vp_w <= 20 or vp_h <= 20:
            vp_w = max(100, self.canvas_scrolled.get_width())
            vp_h = max(100, self.canvas_scrolled.get_height())
            if vp_w <= 20 or vp_h <= 20:
                vp_w = 780
                vp_h = 480

        target_x = (rx + rw / 2.0) - vp_w / 2.0
        target_y = (ry + rh / 2.0) - vp_h / 2.0

        hadj = self.canvas_scrolled.get_hadjustment()
        vadj = self.canvas_scrolled.get_vadjustment()
        if hadj:
            if hadj.get_upper() < float(w):
                hadj.set_upper(float(w))
                hadj.set_page_size(float(vp_w))
            max_x = max(0.0, float(w - vp_w))
            hadj.set_value(clamp(target_x, 0.0, max_x))
        if vadj:
            if vadj.get_upper() < float(h):
                vadj.set_upper(float(h))
                vadj.set_page_size(float(vp_h))
            max_y = max(0.0, float(h - vp_h))
            vadj.set_value(clamp(target_y, 0.0, max_y))

    def _set_zoom(self, factor: float) -> None:
        self._zoom_factor = round(clamp(factor, 0.25, 4.0), 2)
        if hasattr(self, "lbl_zoom") and self.lbl_zoom:
            self.lbl_zoom.set_label(f"{int(round(self._zoom_factor * 100))}%")
        self._update_canvas_geometry()

    def _update_canvas_geometry(self, center_on_video: bool = False) -> None:
        if not hasattr(self, "canvas_scrolled") or not hasattr(self, "canvas_overlay"):
            return
        vp_w, vp_h = self._viewport_size
        if vp_w <= 20 or vp_h <= 20:
            vp_w = max(100, self.canvas_scrolled.get_width())
            vp_h = max(100, self.canvas_scrolled.get_height())
            if vp_w <= 20 or vp_h <= 20:
                vp_w = 780
                vp_h = 480
        aspect = self._get_video_aspect()
        margin = 40.0
        avail_w = max(100.0, float(vp_w) - 2 * margin)
        avail_h = max(100.0, float(vp_h) - 2 * margin)
        container_aspect = avail_w / avail_h
        if container_aspect > aspect:
            base_h = avail_h
            base_w = round(base_h * aspect)
        else:
            base_w = avail_w
            base_h = round(base_w / aspect)

        video_w = max(40.0, round(base_w * self._zoom_factor))
        video_h = max(40.0, round(base_h * self._zoom_factor))

        base_pad_x = max(float(vp_w), 800.0)
        base_pad_y = max(float(vp_h), 800.0)

        # Check if any element goes beyond the generous base padding
        min_x = 0.0
        max_x = 1.0
        min_y = 0.0
        max_y = 1.0
        for it in self.items:
            hw = max(it.width, 0.02) / 2.0
            hh = max(it.height, 0.02) / 2.0
            radius = math.hypot(hw, hh) + 0.05
            min_x = min(min_x, it.x - radius)
            max_x = max(max_x, it.x + radius)
            min_y = min(min_y, it.y - radius)
            max_y = max(max_y, it.y + radius)

        # Only expand in stable 500px increments if an item is placed far beyond base padding
        left_overflow_raw = max(0.0, -min_x * video_w - (base_pad_x - 300.0))
        right_overflow_raw = max(0.0, (max_x - 1.0) * video_w - (base_pad_x - 300.0))
        top_overflow_raw = max(0.0, -min_y * video_h - (base_pad_y - 300.0))
        bottom_overflow_raw = max(0.0, (max_y - 1.0) * video_h - (base_pad_y - 300.0))

        left_overflow_px = math.ceil(left_overflow_raw / 500.0) * 500.0
        right_overflow_px = math.ceil(right_overflow_raw / 500.0) * 500.0
        top_overflow_px = math.ceil(top_overflow_raw / 500.0) * 500.0
        bottom_overflow_px = math.ceil(bottom_overflow_raw / 500.0) * 500.0

        pad_left = base_pad_x + left_overflow_px
        pad_right = base_pad_x + right_overflow_px
        pad_top = base_pad_y + top_overflow_px
        pad_bottom = base_pad_y + bottom_overflow_px

        rx = pad_left
        ry = pad_top
        rw = video_w
        rh = video_h

        canvas_w = int(round(pad_left + video_w + pad_right))
        canvas_h = int(round(pad_top + video_h + pad_bottom))

        prev_w, prev_h = self._canvas_alloc_size
        prev_rx, prev_ry, prev_rw, prev_rh = self._canvas_rect

        self._canvas_rect = (rx, ry, rw, rh)
        self._canvas_alloc_size = (canvas_w, canvas_h)

        if hasattr(self, "preview_box"):
            self.preview_box.set_size_request(canvas_w, canvas_h)
            if hasattr(self, "preview_picture"):
                self.preview_box.move(self.preview_picture, int(round(rx)), int(round(ry)))
        if hasattr(self, "preview_picture"):
            self.preview_picture.set_size_request(int(round(rw)), int(round(rh)))
        if hasattr(self, "overlay_picture"):
            self.overlay_picture.set_size_request(canvas_w, canvas_h)
        if hasattr(self, "canvas_overlay"):
            self.canvas_overlay.set_size_request(canvas_w, canvas_h)

        # Smooth scroll adjustment preservation or centering
        if hasattr(self, "canvas_scrolled"):
            hadj = self.canvas_scrolled.get_hadjustment()
            vadj = self.canvas_scrolled.get_vadjustment()
            if hadj and hadj.get_upper() < float(canvas_w):
                hadj.set_upper(float(canvas_w))
                hadj.set_page_size(float(vp_w))
            if vadj and vadj.get_upper() < float(canvas_h):
                vadj.set_upper(float(canvas_h))
                vadj.set_page_size(float(vp_h))
            if center_on_video:
                target_x = (rx + rw / 2.0) - vp_w / 2.0
                target_y = (ry + rh / 2.0) - vp_h / 2.0
                if hadj:
                    hadj.set_value(clamp(target_x, 0.0, max(0.0, float(canvas_w - vp_w))))
                if vadj:
                    vadj.set_value(clamp(target_y, 0.0, max(0.0, float(canvas_h - vp_h))))
            elif prev_w > 0 and prev_h > 0:
                # If zoom changed, keep current view centered on the same point of the video
                if prev_rw > 0 and prev_rh > 0 and (rw != prev_rw or rh != prev_rh):
                    norm_cx = (hadj.get_value() + vp_w / 2.0 - prev_rx) / prev_rw
                    norm_cy = (vadj.get_value() + vp_h / 2.0 - prev_ry) / prev_rh
                    target_x = rx + norm_cx * rw - vp_w / 2.0
                    target_y = ry + norm_cy * rh - vp_h / 2.0
                    if hadj:
                        hadj.set_value(clamp(target_x, 0.0, max(0.0, float(canvas_w - vp_w))))
                    if vadj:
                        vadj.set_value(clamp(target_y, 0.0, max(0.0, float(canvas_h - vp_h))))
                else:
                    # When moving objects within visible area or pasteboard, keep scroll in the exact same position
                    shift_x = rx - prev_rx
                    shift_y = ry - prev_ry
                    if shift_x != 0 and hadj:
                        hadj.set_value(clamp(hadj.get_value() + shift_x, 0.0, max(0.0, float(canvas_w - vp_w))))
                    if shift_y != 0 and vadj:
                        vadj.set_value(clamp(vadj.get_value() + shift_y, 0.0, max(0.0, float(canvas_h - vp_h))))

        self._render_overlay_canvas()

    def _render_overlay_canvas(self) -> None:
        """Render overlay items and selection gizmo to an in-memory Cairo surface and upload to texture."""
        if not hasattr(self, "overlay_picture"):
            return

        w, h = self._canvas_alloc_size
        if w <= 1 or h <= 1:
            return

        rx, ry, rw, rh = self._canvas_rect

        # 1. Create transparent Cairo ARGB32 surface
        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
        cr = cairo.Context(surf)

        cr.set_operator(cairo.OPERATOR_CLEAR)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)

        # 2. Render overlays unclipped across video frame and pasteboard
        if rw > 1 and rh > 1:
            cr.save()
            cr.translate(rx, ry)
            sorted_items = sorted(self.items, key=lambda it: it.z_index)
            for it in sorted_items:
                try:
                    OverlayRenderService.render_item(cr, it, rw, rh, current_time=self.current_time)
                except Exception as err:
                    print(f"Warning: Failed to render item {it.id} on canvas: {err}")
            cr.restore()

            # 3. Render Selection Box and Transform Handles on top
            if self.selected_items:
                self._draw_selection_gizmo(cr, rx, ry, rw, rh)

            # 4. Render rubberband marquee rectangle if active
            if self._drag_mode == "RUBBERBAND" and self._rubberband_start and self._rubberband_current:
                sx, sy = self._rubberband_start
                cx, cy = self._rubberband_current
                rb_x = min(sx, cx)
                rb_y = min(sy, cy)
                rb_w = abs(cx - sx)
                rb_h = abs(cy - sy)
                if rb_w > 1 and rb_h > 1:
                    ar, ag, ab = self._get_system_accent_color()
                    cr.save()
                    cr.new_path()
                    cr.rectangle(rb_x, rb_y, rb_w, rb_h)
                    cr.set_source_rgba(ar, ag, ab, 0.18)
                    cr.fill_preserve()
                    cr.set_source_rgba(ar, ag, ab, 0.85)
                    cr.set_line_width(1.0)
                    cr.stroke()
                    cr.restore()

        # 4. Upload to Gdk.MemoryTexture (no C cairo foreign struct converter issues)
        try:
            surf.flush()
            stride = surf.get_stride()
            data = surf.get_data().tobytes()
            glib_bytes = GLib.Bytes.new(data)
            texture = Gdk.MemoryTexture.new(
                w, h, Gdk.MemoryFormat.B8G8R8A8_PREMULTIPLIED, glib_bytes, stride
            )
            self.overlay_picture.set_paintable(texture)
        except Exception as e:
            print(f"Error updating overlay texture: {e}")

    def _get_system_accent_color(self) -> tuple[float, float, float]:
        """Retrieve system accent color (R, G, B in 0..1), matching system theme."""
        try:
            ctx = self.get_style_context()
            if ctx:
                for col_name in ("accent_color", "accent_bg_color"):
                    success, col = ctx.lookup_color(col_name)
                    if success and col:
                        return (col.red, col.green, col.blue)
        except Exception:
            pass
        return (0.2, 0.6, 1.0)

    def _draw_selection_gizmo(
        self,
        cr: cairo.Context,
        rx: float,
        ry: float,
        rw: float,
        rh: float,
    ) -> None:
        """Draw bounding box, resize handles, and rotation stalk around selected item(s)."""
        items = self.selected_items
        if not items:
            return

        ar, ag, ab = self._get_system_accent_color()

        if len(items) == 1:
            item = items[0]
            cx = rx + item.x * rw
            cy = ry + item.y * rh
            w = max(4.0, item.width * rw)
            h = max(4.0, item.height * rh)

            cr.save()
            cr.new_path()
            try:
                cr.translate(cx, cy)
                if item.rotation != 0.0:
                    cr.rotate(math.radians(item.rotation))

                half_w = w / 2.0
                half_h = h / 2.0

                if item.item_type == "audio":
                    pill_radius = min(half_h, 16.0)
                    draw_rounded_rect(cr, -half_w - 3.0, -half_h - 3.0, w + 6.0, h + 6.0, pill_radius + 2.0)
                    cr.set_source_rgba(ar, ag, ab, 0.9)
                    cr.set_line_width(2.0)
                    cr.stroke()
                else:
                    # Bounding box
                    cr.set_source_rgba(ar, ag, ab, 0.9)
                    cr.set_line_width(1.5)
                    cr.rectangle(-half_w, -half_h, w, h)
                    cr.stroke()

                    # Rotation stalk & handle
                    cr.set_source_rgba(ar, ag, ab, 0.8)
                    cr.move_to(0, -half_h)
                    cr.line_to(0, -half_h - 22.0)
                    cr.stroke()

                    # Rotation circular handle
                    cr.new_sub_path()
                    cr.arc(0, -half_h - 22.0, 5.0, 0, 2 * math.pi)
                    cr.close_path()
                    cr.set_source_rgba(1.0, 1.0, 1.0, 1.0)
                    cr.fill_preserve()
                    cr.set_source_rgba(ar, ag, ab, 1.0)
                    cr.set_line_width(1.5)
                    cr.stroke()

                    # 8 Square resize handles
                    handle_sz = 8.0
                    handles = [
                        (-half_w, -half_h),           # NW
                        (0, -half_h),                 # N
                        (half_w, -half_h),            # NE
                        (half_w, 0),                  # E
                        (half_w, half_h),             # SE
                        (0, half_h),                  # S
                        (-half_w, half_h),            # SW
                        (-half_w, 0),                 # W
                    ]
                    for hx, hy in handles:
                        cr.rectangle(hx - handle_sz / 2.0, hy - handle_sz / 2.0, handle_sz, handle_sz)
                        cr.set_source_rgba(1.0, 1.0, 1.0, 1.0)
                        cr.fill_preserve()
                        cr.set_source_rgba(ar, ag, ab, 1.0)
                        cr.set_line_width(1.5)
                        cr.stroke()
            finally:
                cr.restore()
            return

        # MULTI-SELECTION GIZMO
        # 1. Draw subtle dashed outline for each item
        for it in items:
            cx = rx + it.x * rw
            cy = ry + it.y * rh
            w = max(4.0, it.width * rw)
            h = max(4.0, it.height * rh)
            cr.save()
            cr.new_path()
            try:
                cr.translate(cx, cy)
                if it.rotation != 0.0:
                    cr.rotate(math.radians(it.rotation))
                cr.set_source_rgba(ar, ag, ab, 0.5)
                cr.set_line_width(1.0)
                cr.set_dash([3.0, 3.0])
                cr.rectangle(-w / 2.0, -h / 2.0, w, h)
                cr.stroke()
            finally:
                cr.restore()

        # 2. Draw collective bounding box and 8 handles
        bounds = self._get_multi_selection_bounds()
        if bounds:
            min_x, min_y, max_x, max_y = bounds
            bx = rx + min_x * rw
            by = ry + min_y * rh
            bw = (max_x - min_x) * rw
            bh = (max_y - min_y) * rh

            cr.save()
            cr.new_path()
            try:
                cr.set_source_rgba(ar, ag, ab, 0.9)
                cr.set_line_width(1.5)
                cr.rectangle(bx, by, bw, bh)
                cr.stroke()

                handle_sz = 8.0
                handles = [
                    (bx, by),                     # NW
                    (bx + bw / 2.0, by),          # N
                    (bx + bw, by),                # NE
                    (bx + bw, by + bh / 2.0),     # E
                    (bx + bw, by + bh),           # SE
                    (bx + bw / 2.0, by + bh),     # S
                    (bx, by + bh),                # SW
                    (bx, by + bh / 2.0),          # W
                ]
                for hx, hy in handles:
                    cr.rectangle(hx - handle_sz / 2.0, hy - handle_sz / 2.0, handle_sz, handle_sz)
                    cr.set_source_rgba(1.0, 1.0, 1.0, 1.0)
                    cr.fill_preserve()
                    cr.set_source_rgba(ar, ag, ab, 1.0)
                    cr.set_line_width(1.5)
                    cr.stroke()
            finally:
                cr.restore()

    def _setup_canvas_gestures(self) -> None:
        # Motion controller
        motion = Gtk.EventControllerMotion.new()
        motion.connect("motion", self._on_canvas_motion)
        motion.connect("leave", self._on_canvas_leave)
        self.overlay_picture.add_controller(motion)

        # Drag gesture
        drag = Gtk.GestureDrag.new()
        drag.connect("drag-begin", self._on_canvas_drag_begin)
        drag.connect("drag-update", self._on_canvas_drag_update)
        drag.connect("drag-end", self._on_canvas_drag_end)
        self.overlay_picture.add_controller(drag)

        # Click gesture for selecting elements
        click = Gtk.GestureClick.new()
        click.connect("pressed", self._on_canvas_click)
        self.overlay_picture.add_controller(click)

        # Right-click gesture for context menu
        right_click = Gtk.GestureClick.new()
        right_click.set_button(3)
        right_click.connect("pressed", self._on_canvas_right_click)
        self.overlay_picture.add_controller(right_click)

        # Scroll controller for Ctrl+Wheel zooming
        scroll = Gtk.EventControllerScroll.new(
            Gtk.EventControllerScrollFlags.VERTICAL | Gtk.EventControllerScrollFlags.DISCRETE
        )
        scroll.connect("scroll", self._on_canvas_scroll)
        self.overlay_picture.add_controller(scroll)

    def _on_canvas_scroll(self, ctrl: Gtk.EventControllerScroll, dx: float, dy: float) -> bool:
        state = ctrl.get_current_event_state()
        if state & Gdk.ModifierType.CONTROL_MASK:
            if dy < 0:
                self._zoom_in()
            elif dy > 0:
                self._zoom_out()
            return True
        return False

    def _setup_key_controller(self) -> None:
        key_ctrl = Gtk.EventControllerKey.new()
        key_ctrl.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        key_ctrl.connect("key-pressed", self._on_key_pressed)
        self.add_controller(key_ctrl)

    def _on_key_pressed(self, _ctrl, keyval: int, _keycode: int, state: Gdk.ModifierType) -> bool:
        focus = self.get_focus()
        is_entry = isinstance(focus, (Gtk.Entry, Gtk.Editable, Gtk.TextView))
        is_ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
        is_shift = bool(state & Gdk.ModifierType.SHIFT_MASK)

        if not is_entry:
            if keyval in (Gdk.KEY_Delete, Gdk.KEY_BackSpace, Gdk.KEY_KP_Delete):
                if self.selected_items:
                    self._on_delete_selected()
                    return True
            elif keyval == Gdk.KEY_space:
                self._toggle_playback()
                return True
            elif self.selected_items:
                items = self.selected_items
                if not is_ctrl:
                    if keyval in (Gdk.KEY_Page_Up, Gdk.KEY_KP_Page_Up):
                        if len(items) == 1:
                            self._change_z_order(items[0], "up")
                        else:
                            self._change_multi_z_order(items, "up")
                        return True
                    elif keyval in (Gdk.KEY_Page_Down, Gdk.KEY_KP_Page_Down):
                        if len(items) == 1:
                            self._change_z_order(items[0], "down")
                        else:
                            self._change_multi_z_order(items, "down")
                        return True
                    elif keyval in (Gdk.KEY_Home, Gdk.KEY_KP_Home):
                        if len(items) == 1:
                            self._change_z_order(items[0], "top")
                        else:
                            self._change_multi_z_order(items, "top")
                        return True
                    elif keyval in (Gdk.KEY_End, Gdk.KEY_KP_End):
                        if len(items) == 1:
                            self._change_z_order(items[0], "bottom")
                        else:
                            self._change_multi_z_order(items, "bottom")
                        return True
                    elif keyval in (Gdk.KEY_Left, Gdk.KEY_KP_Left):
                        vw, _ = self._get_video_pixel_dimensions()
                        step = (10.0 if is_shift else 1.0) / max(1.0, float(vw))
                        self._save_undo_state()
                        for it in items:
                            it.x -= step
                        self._update_canvas_geometry()
                        self._sync_position_and_size_spinners()
                        return True
                    elif keyval in (Gdk.KEY_Right, Gdk.KEY_KP_Right):
                        vw, _ = self._get_video_pixel_dimensions()
                        step = (10.0 if is_shift else 1.0) / max(1.0, float(vw))
                        self._save_undo_state()
                        for it in items:
                            it.x += step
                        self._update_canvas_geometry()
                        self._sync_position_and_size_spinners()
                        return True
                    elif keyval in (Gdk.KEY_Up, Gdk.KEY_KP_Up):
                        _, vh = self._get_video_pixel_dimensions()
                        step = (10.0 if is_shift else 1.0) / max(1.0, float(vh))
                        self._save_undo_state()
                        for it in items:
                            it.y -= step
                        self._update_canvas_geometry()
                        self._sync_position_and_size_spinners()
                        return True
                    elif keyval in (Gdk.KEY_Down, Gdk.KEY_KP_Down):
                        _, vh = self._get_video_pixel_dimensions()
                        step = (10.0 if is_shift else 1.0) / max(1.0, float(vh))
                        self._save_undo_state()
                        for it in items:
                            it.y += step
                        self._update_canvas_geometry()
                        self._sync_position_and_size_spinners()
                        return True

        # Ctrl shortcuts
        if is_ctrl:
            if not is_entry:
                if not is_shift and keyval in (Gdk.KEY_a, Gdk.KEY_A):
                    self._select_all()
                    return True
                elif not is_shift and keyval in (Gdk.KEY_c, Gdk.KEY_C):
                    if self.selected_items:
                        self._copy_selected()
                        return True
                elif not is_shift and keyval in (Gdk.KEY_x, Gdk.KEY_X):
                    if self.selected_items:
                        self._cut_selected()
                        return True
                elif not is_shift and keyval in (Gdk.KEY_v, Gdk.KEY_V):
                    self._paste()
                    return True
                elif not is_shift and keyval in (Gdk.KEY_d, Gdk.KEY_D):
                    if self.selected_items:
                        self._duplicate_selected()
                        return True
            if not is_shift and keyval in (Gdk.KEY_z, Gdk.KEY_Z):
                self.undo()
                return True
            elif (is_shift and keyval in (Gdk.KEY_z, Gdk.KEY_Z)) or keyval in (Gdk.KEY_y, Gdk.KEY_Y):
                self.redo()
                return True
            elif keyval in (Gdk.KEY_plus, Gdk.KEY_equal, Gdk.KEY_KP_Add):
                self._zoom_in()
                return True
            elif keyval in (Gdk.KEY_minus, Gdk.KEY_underscore, Gdk.KEY_KP_Subtract):
                self._zoom_out()
                return True
            elif keyval in (Gdk.KEY_0, Gdk.KEY_KP_0):
                self._zoom_fit()
                return True
            elif self.selected_items:
                items = self.selected_items
                if not is_shift and keyval in (Gdk.KEY_bracketright, Gdk.KEY_braceright):
                    if len(items) == 1:
                        self._change_z_order(items[0], "up")
                    else:
                        self._change_multi_z_order(items, "up")
                    return True
                elif not is_shift and keyval in (Gdk.KEY_bracketleft, Gdk.KEY_braceleft):
                    if len(items) == 1:
                        self._change_z_order(items[0], "down")
                    else:
                        self._change_multi_z_order(items, "down")
                    return True
                elif (is_shift and keyval in (Gdk.KEY_bracketright, Gdk.KEY_braceright)) or keyval in (Gdk.KEY_Home, Gdk.KEY_KP_Home):
                    if len(items) == 1:
                        self._change_z_order(items[0], "top")
                    else:
                        self._change_multi_z_order(items, "top")
                    return True
                elif (is_shift and keyval in (Gdk.KEY_bracketleft, Gdk.KEY_braceleft)) or keyval in (Gdk.KEY_End, Gdk.KEY_KP_End):
                    if len(items) == 1:
                        self._change_z_order(items[0], "bottom")
                    else:
                        self._change_multi_z_order(items, "bottom")
                    return True

        return False

    def _hit_test(self, mx: float, my: float) -> tuple[str, str | None]:
        """Determine hit handle or item under mouse cursor. Returns (mode, item_id)."""
        rx, ry, rw, rh = self._canvas_rect
        if rw <= 0 or rh <= 0:
            return ("NONE", None)

        selected_items = self.selected_items

        # Multi-selection handles and item bodies
        if len(selected_items) > 1:
            bounds = self._get_multi_selection_bounds()
            if bounds:
                min_x, min_y, max_x, max_y = bounds
                bx = rx + min_x * rw
                by = ry + min_y * rh
                bw = (max_x - min_x) * rw
                bh = (max_y - min_y) * rh
                tol = 10.0

                # Check 8 multi-selection box handles
                if abs(mx - bx) <= tol and abs(my - by) <= tol:
                    return ("RESIZE_MULTI_NW", selected_items[0].id)
                if abs(mx - (bx + bw)) <= tol and abs(my - by) <= tol:
                    return ("RESIZE_MULTI_NE", selected_items[0].id)
                if abs(mx - bx) <= tol and abs(my - (by + bh)) <= tol:
                    return ("RESIZE_MULTI_SW", selected_items[0].id)
                if abs(mx - (bx + bw)) <= tol and abs(my - (by + bh)) <= tol:
                    return ("RESIZE_MULTI_SE", selected_items[0].id)
                if abs(mx - (bx + bw / 2.0)) <= tol and abs(my - by) <= tol:
                    return ("RESIZE_MULTI_N", selected_items[0].id)
                if abs(mx - (bx + bw / 2.0)) <= tol and abs(my - (by + bh)) <= tol:
                    return ("RESIZE_MULTI_S", selected_items[0].id)
                if abs(mx - bx) <= tol and abs(my - (by + bh / 2.0)) <= tol:
                    return ("RESIZE_MULTI_W", selected_items[0].id)
                if abs(mx - (bx + bw)) <= tol and abs(my - (by + bh / 2.0)) <= tol:
                    return ("RESIZE_MULTI_E", selected_items[0].id)

            # Check if inside any already selected item (from top z-index to bottom)
            for it in sorted(selected_items, key=lambda i: i.z_index, reverse=True):
                cx = rx + it.x * rw
                cy = ry + it.y * rh
                w = max(4.0, it.width * rw)
                h = max(4.0, it.height * rh)

                dx = mx - cx
                dy = my - cy
                rad = -math.radians(it.rotation)
                local_x = dx * math.cos(rad) - dy * math.sin(rad)
                local_y = dx * math.sin(rad) + dy * math.cos(rad)

                if -w / 2.0 <= local_x <= w / 2.0 and -h / 2.0 <= local_y <= h / 2.0:
                    return ("MOVE_MULTI", it.id)

        # Single selected item handles and body
        elif len(selected_items) == 1:
            selected = selected_items[0]
            cx = rx + selected.x * rw
            cy = ry + selected.y * rh
            w = max(4.0, selected.width * rw)
            h = max(4.0, selected.height * rh)

            dx = mx - cx
            dy = my - cy
            rad = -math.radians(selected.rotation)
            local_x = dx * math.cos(rad) - dy * math.sin(rad)
            local_y = dx * math.sin(rad) + dy * math.cos(rad)

            half_w = w / 2.0
            half_h = h / 2.0

            if selected.item_type != "audio":
                # Rotation handle (radius 10)
                if math.hypot(local_x, local_y - (-half_h - 22.0)) <= 10.0:
                    return ("ROTATE", selected.id)

                tol = 10.0
                if abs(local_x - (-half_w)) <= tol and abs(local_y - (-half_h)) <= tol:
                    return ("RESIZE_NW", selected.id)
                if abs(local_x - half_w) <= tol and abs(local_y - (-half_h)) <= tol:
                    return ("RESIZE_NE", selected.id)
                if abs(local_x - (-half_w)) <= tol and abs(local_y - half_h) <= tol:
                    return ("RESIZE_SW", selected.id)
                if abs(local_x - half_w) <= tol and abs(local_y - half_h) <= tol:
                    return ("RESIZE_SE", selected.id)
                if abs(local_x) <= tol and abs(local_y - (-half_h)) <= tol:
                    return ("RESIZE_N", selected.id)
                if abs(local_x) <= tol and abs(local_y - half_h) <= tol:
                    return ("RESIZE_S", selected.id)
                if abs(local_x - (-half_w)) <= tol and abs(local_y) <= tol:
                    return ("RESIZE_W", selected.id)
                if abs(local_x - half_w) <= tol and abs(local_y) <= tol:
                    return ("RESIZE_E", selected.id)

            if -half_w <= local_x <= half_w and -half_h <= local_y <= half_h:
                return ("MOVE", selected.id)

        # Hit test any item in canvas from top to bottom
        for it in sorted(self.items, key=lambda i: i.z_index, reverse=True):
            cx = rx + it.x * rw
            cy = ry + it.y * rh
            w = max(4.0, it.width * rw)
            h = max(4.0, it.height * rh)

            dx = mx - cx
            dy = my - cy
            rad = -math.radians(it.rotation)
            local_x = dx * math.cos(rad) - dy * math.sin(rad)
            local_y = dx * math.sin(rad) + dy * math.cos(rad)

            if -w / 2.0 <= local_x <= w / 2.0 and -h / 2.0 <= local_y <= h / 2.0:
                return ("MOVE", it.id)

        return ("NONE", None)

    def _on_canvas_motion(self, _ctrl, x: float, y: float) -> None:
        if self._drag_mode != "NONE":
            return
        hit_mode, _ = self._hit_test(x, y)
        cursor_map = {
            "ROTATE": "grab",
            "RESIZE_NW": "nwse-resize",
            "RESIZE_SE": "nwse-resize",
            "RESIZE_NE": "nesw-resize",
            "RESIZE_SW": "nesw-resize",
            "RESIZE_W": "ew-resize",
            "RESIZE_E": "ew-resize",
            "RESIZE_N": "ns-resize",
            "RESIZE_S": "ns-resize",
            "MOVE": "move",
            "MOVE_MULTI": "move",
            "RESIZE_MULTI_NW": "nwse-resize",
            "RESIZE_MULTI_SE": "nwse-resize",
            "RESIZE_MULTI_NE": "nesw-resize",
            "RESIZE_MULTI_SW": "nesw-resize",
            "RESIZE_MULTI_W": "ew-resize",
            "RESIZE_MULTI_E": "ew-resize",
            "RESIZE_MULTI_N": "ns-resize",
            "RESIZE_MULTI_S": "ns-resize",
        }
        self._set_cursor(cursor_map.get(hit_mode, "default"))

    def _on_canvas_leave(self, _ctrl) -> None:
        if self._drag_mode == "NONE":
            self._set_cursor("default")

    def _set_cursor(self, cursor_name: str) -> None:
        if self._current_cursor != cursor_name:
            self._current_cursor = cursor_name
            if hasattr(self, "overlay_picture"):
                self.overlay_picture.set_cursor_from_name(cursor_name)

    def _on_canvas_click(self, gesture: Gtk.GestureClick | None, _n_press: int, x: float, y: float) -> None:
        state = gesture.get_current_event_state() if gesture is not None else Gdk.ModifierType(0)
        is_ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
        is_shift = bool(state & Gdk.ModifierType.SHIFT_MASK)
        hit_mode, hit_id = self._hit_test(x, y)

        if is_ctrl or is_shift:
            if hit_id:
                if hit_id in self.selected_item_ids:
                    self.selected_item_ids.remove(hit_id)
                else:
                    self.selected_item_ids.add(hit_id)
                self._refresh_canvas_and_properties()
            return

        # No Ctrl or Shift:
        if not hit_id:
            if self.selected_item_ids:
                self.selected_item_ids.clear()
                self._refresh_canvas_and_properties()
        else:
            if hit_id not in self.selected_item_ids:
                self.selected_item_ids = {hit_id}
                self._refresh_canvas_and_properties()

    def _on_canvas_right_click(self, _gesture: Gtk.GestureClick, _n_press: int, x: float, y: float) -> None:
        hit_mode, hit_id = self._hit_test(x, y)
        if hit_id:
            if hit_id not in self.selected_item_ids:
                self.selected_item_ids = {hit_id}
                self._refresh_canvas_and_properties()
        else:
            if self.selected_item_ids:
                self.selected_item_ids.clear()
                self._refresh_canvas_and_properties()
        self._show_context_menu(x, y)

    def _show_context_menu(self, x: float, y: float) -> None:
        if not hasattr(self, "_context_popover") or self._context_popover is None:
            self._context_popover = Gtk.Popover()
            self._context_popover.set_parent(self.overlay_picture)
            self._context_popover.set_has_arrow(False)
            self._context_popover.set_autohide(True)
            self._context_popover.set_halign(Gtk.Align.START)
            self._context_popover.set_position(Gtk.PositionType.BOTTOM)
            self._context_popover.add_css_class("menu")
            self._context_popover.add_css_class("square-menu")
            self._context_popover.connect("closed", lambda _: self.set_focus(None))

        has_selected = len(self.selected_items) > 0
        has_items = len(self.items) > 0
        can_paste = self._can_paste()

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.set_margin_top(4)
        box.set_margin_bottom(4)
        box.set_margin_start(4)
        box.set_margin_end(4)

        def make_item(
            label: str,
            icon_name: str,
            shortcut: str,
            callback: Callable[[], None],
            sensitive: bool,
            is_destructive: bool = False,
        ) -> Gtk.Button:
            btn = Gtk.Button()
            btn.add_css_class("flat")
            if is_destructive:
                btn.add_css_class("destructive-action")
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            icon = Gtk.Image.new_from_icon_name(icon_name)
            icon.set_pixel_size(16)
            row.append(icon)
            lbl = Gtk.Label(label=label, xalign=0.0)
            lbl.set_hexpand(True)
            row.append(lbl)
            if shortcut:
                sc = Gtk.Label(label=shortcut, xalign=1.0)
                sc.add_css_class("dim-label")
                row.append(sc)
            btn.set_child(row)
            btn.set_sensitive(sensitive)

            def on_click(_):
                if hasattr(self, "_context_popover") and self._context_popover:
                    self._context_popover.popdown()
                callback()

            btn.connect("clicked", on_click)
            return btn

        # Cut, Copy, Paste, Duplicate, Select All
        box.append(make_item(_("Cut"), "edit-cut-symbolic", "Ctrl+X", self._cut_selected, has_selected))
        box.append(make_item(_("Copy"), "edit-copy-symbolic", "Ctrl+C", self._copy_selected, has_selected))
        box.append(make_item(_("Paste"), "edit-paste-symbolic", "Ctrl+V", self._paste, can_paste))
        box.append(make_item(_("Duplicate"), "edit-copy-symbolic", "Ctrl+D", self._duplicate_selected, has_selected))
        box.append(make_item(_("Select All"), "edit-select-all-symbolic", "Ctrl+A", self._select_all, has_items))

        if has_selected:
            items = self.selected_items
            box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
            if len(items) == 1:
                sel = items[0]
                box.append(make_item(_("Bring Forward"), "go-up-symbolic", "Ctrl+]", lambda: self._change_z_order(sel, "up"), True))
                box.append(make_item(_("Send Backward"), "go-down-symbolic", "Ctrl+[", lambda: self._change_z_order(sel, "down"), True))
                box.append(make_item(_("Bring to Front"), "go-top-symbolic", "Ctrl+Shift+]", lambda: self._change_z_order(sel, "top"), True))
                box.append(make_item(_("Send to Back"), "go-bottom-symbolic", "Ctrl+Shift+[", lambda: self._change_z_order(sel, "bottom"), True))
            else:
                box.append(make_item(_("Bring Forward"), "go-up-symbolic", "Ctrl+]", lambda: self._change_multi_z_order(items, "up"), True))
                box.append(make_item(_("Send Backward"), "go-down-symbolic", "Ctrl+[", lambda: self._change_multi_z_order(items, "down"), True))
                box.append(make_item(_("Bring to Front"), "go-top-symbolic", "Ctrl+Shift+]", lambda: self._change_multi_z_order(items, "top"), True))
                box.append(make_item(_("Send to Back"), "go-bottom-symbolic", "Ctrl+Shift+[", lambda: self._change_multi_z_order(items, "bottom"), True))

        box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
        box.append(make_item(_("Delete"), "user-trash-symbolic", "Del", self._on_delete_selected, has_selected, is_destructive=True))

        self._context_popover.set_child(box)
        rect = Gdk.Rectangle()
        rect.x = int(x)
        rect.y = int(y)
        rect.width = 1
        rect.height = 1
        self._context_popover.set_pointing_to(rect)
        self._context_popover.popup()

    def _on_canvas_drag_begin(self, gesture: Gtk.GestureDrag | None, start_x: float, start_y: float) -> None:
        state = gesture.get_current_event_state() if gesture is not None else Gdk.ModifierType(0)
        is_ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
        is_shift = bool(state & Gdk.ModifierType.SHIFT_MASK)
        hit_mode, hit_id = self._hit_test(start_x, start_y)

        # 1. Rubberband selection on empty space
        if hit_mode == "NONE" or not hit_id:
            self._drag_mode = "RUBBERBAND"
            self._drag_start_x = start_x
            self._drag_start_y = start_y
            self._rubberband_start = (start_x, start_y)
            self._rubberband_current = (start_x, start_y)
            if not (is_ctrl or is_shift):
                self.selected_item_ids.clear()
            self._rubberband_initial_selection = set(self.selected_item_ids)
            self._trigger_canvas_redraw()
            return

        # 2. Resizing multi-selection bounding box
        if hit_mode.startswith("RESIZE_MULTI_"):
            self._items_state_before_drag = [it.copy() for it in self.items]
            self._selected_states_at_drag_start = {it.id: it.copy() for it in self.selected_items}
            self._multi_selection_bounds_at_drag_start = self._get_multi_selection_bounds()
            self._drag_mode = hit_mode
            self._drag_start_x = start_x
            self._drag_start_y = start_y
            return

        # 3. Moving multi-selection elements
        if hit_mode == "MOVE_MULTI":
            self._items_state_before_drag = [it.copy() for it in self.items]
            self._selected_states_at_drag_start = {it.id: it.copy() for it in self.selected_items}
            self._drag_mode = "MOVE_MULTI"
            self._drag_item_id = hit_id
            self._drag_start_x = start_x
            self._drag_start_y = start_y
            return

        # 4. Single item interaction (or starting selection with modifier)
        if is_ctrl or is_shift:
            if hit_id in self.selected_item_ids:
                self.selected_item_ids.remove(hit_id)
            else:
                self.selected_item_ids.add(hit_id)
            self._refresh_canvas_and_properties()
            return

        if hit_id not in self.selected_item_ids:
            self.selected_item_ids = {hit_id}

        self._items_state_before_drag = [it.copy() for it in self.items]
        self._drag_mode = hit_mode
        self._drag_item_id = hit_id
        self._drag_start_x = start_x
        self._drag_start_y = start_y
        item = self.selected_item
        if item:
            self._item_state_at_drag_start = item.copy()
        self._update_properties_panel()
        self._trigger_canvas_redraw()

    def _on_canvas_drag_update(self, gesture: Gtk.GestureDrag | None, offset_x: float, offset_y: float) -> None:
        if self._drag_mode == "NONE":
            return

        rx, ry, rw, rh = self._canvas_rect
        if rw <= 0 or rh <= 0:
            return

        # A. Rubberband selection
        if self._drag_mode == "RUBBERBAND":
            if not self._rubberband_start:
                return
            cur_x = self._drag_start_x + offset_x
            cur_y = self._drag_start_y + offset_y
            self._rubberband_current = (cur_x, cur_y)

            sx, sy = self._rubberband_start
            rb_min_x, rb_max_x = min(sx, cur_x), max(sx, cur_x)
            rb_min_y, rb_max_y = min(sy, cur_y), max(sy, cur_y)

            state = gesture.get_current_event_state() if gesture is not None else Gdk.ModifierType(0)
            is_ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
            is_shift = bool(state & Gdk.ModifierType.SHIFT_MASK)
            new_selected = set(getattr(self, "_rubberband_initial_selection", set()))

            for it in self.items:
                it_cx = rx + it.x * rw
                it_cy = ry + it.y * rh
                it_w = max(4.0, it.width * rw)
                it_h = max(4.0, it.height * rh)
                it_min_x = it_cx - it_w / 2.0
                it_max_x = it_cx + it_w / 2.0
                it_min_y = it_cy - it_h / 2.0
                it_max_y = it_cy + it_h / 2.0

                intersects = (it_min_x <= rb_max_x and it_max_x >= rb_min_x and
                              it_min_y <= rb_max_y and it_max_y >= rb_min_y)
                if intersects:
                    new_selected.add(it.id)
                elif not (is_ctrl or is_shift):
                    new_selected.discard(it.id)

            self.selected_item_ids = new_selected
            self._trigger_canvas_redraw()
            return

        # B. Moving multi-selection
        if self._drag_mode == "MOVE_MULTI":
            dx_norm = offset_x / rw
            dy_norm = offset_y / rh
            for it in self.selected_items:
                init = self._selected_states_at_drag_start.get(it.id)
                if init:
                    it.x = clamp(init.x + dx_norm, -1.0, 2.0)
                    it.y = clamp(init.y + dy_norm, -1.0, 2.0)
            self._sync_position_and_size_spinners()
            self._trigger_canvas_redraw()
            return

        # C. Resizing multi-selection bounding box
        if self._drag_mode.startswith("RESIZE_MULTI_"):
            handle_dir = self._drag_mode[len("RESIZE_MULTI_"):]
            dx_norm = offset_x / rw
            dy_norm = offset_y / rh
            init_bounds = self._multi_selection_bounds_at_drag_start
            if not init_bounds:
                return
            init_min_x, init_min_y, init_max_x, init_max_y = init_bounds
            init_w = max(0.001, init_max_x - init_min_x)
            init_h = max(0.001, init_max_y - init_min_y)

            new_min_x, new_max_x = init_min_x, init_max_x
            new_min_y, new_max_y = init_min_y, init_max_y

            if "E" in handle_dir:
                new_max_x = max(init_min_x + 0.02, init_max_x + dx_norm)
            elif "W" in handle_dir:
                new_min_x = min(init_max_x - 0.02, init_min_x + dx_norm)

            if "S" in handle_dir:
                new_max_y = max(init_min_y + 0.02, init_max_y + dy_norm)
            elif "N" in handle_dir:
                new_min_y = min(init_max_y - 0.02, init_min_y + dy_norm)

            new_w = new_max_x - new_min_x
            new_h = new_max_y - new_min_y
            scale_x = new_w / init_w
            scale_y = new_h / init_h

            anchor_x = init_min_x if "E" in handle_dir else (init_max_x if "W" in handle_dir else (init_min_x + init_max_x) / 2.0)
            new_anchor_x = new_min_x if "E" in handle_dir else (new_max_x if "W" in handle_dir else (new_min_x + new_max_x) / 2.0)

            anchor_y = init_min_y if "S" in handle_dir else (init_max_y if "N" in handle_dir else (init_min_y + init_max_y) / 2.0)
            new_anchor_y = new_min_y if "S" in handle_dir else (new_max_y if "N" in handle_dir else (new_min_y + new_max_y) / 2.0)

            for it in self.selected_items:
                init = self._selected_states_at_drag_start.get(it.id)
                if init:
                    it.width = max(0.02, init.width * scale_x)
                    it.height = max(0.02, init.height * scale_y)
                    it.x = new_anchor_x + (init.x - anchor_x) * scale_x
                    it.y = new_anchor_y + (init.y - anchor_y) * scale_y

            self._sync_position_and_size_spinners()
            self._trigger_canvas_redraw()
            return

        # D. Single item drag (Rotate, Move, Resize)
        if not self._item_state_at_drag_start:
            return
        item = self.selected_item
        if not item:
            return

        init = self._item_state_at_drag_start

        # Rotation dragging
        if self._drag_mode == "ROTATE":
            cx = rx + init.x * rw
            cy = ry + init.y * rh
            cur_x = self._drag_start_x + offset_x
            cur_y = self._drag_start_y + offset_y
            angle = math.degrees(math.atan2(cur_y - cy, cur_x - cx)) + 90.0
            item.rotation = round(angle % 360.0, 1)

        # Move dragging
        elif self._drag_mode == "MOVE":
            dx_norm = offset_x / rw
            dy_norm = offset_y / rh
            item.x = clamp(init.x + dx_norm, -1.0, 2.0)
            item.y = clamp(init.y + dy_norm, -1.0, 2.0)
            self._sync_position_and_size_spinners(item)

        # Resizing dragging
        elif self._drag_mode.startswith("RESIZE_"):
            handle_dir = self._drag_mode[len("RESIZE_"):]
            rad = math.radians(init.rotation)
            proj_dx = (offset_x * math.cos(rad) + offset_y * math.sin(rad)) / rw
            proj_dy = (-offset_x * math.sin(rad) + offset_y * math.cos(rad)) / rh

            is_proportional = (item.item_type != "text") and (
                (item.item_type == "emoji") or getattr(item, "keep_aspect_ratio", True)
            )

            dw = 0.0
            dh = 0.0

            if "E" in handle_dir:
                dw = proj_dx
            elif "W" in handle_dir:
                dw = -proj_dx

            if "S" in handle_dir:
                dh = proj_dy
            elif "N" in handle_dir:
                dh = -proj_dy

            if is_proportional and init.width > 0 and init.height > 0:
                scale_w = (init.width + dw) / init.width
                scale_h = (init.height + dh) / init.height

                if "E" in handle_dir or "W" in handle_dir:
                    if "N" in handle_dir or "S" in handle_dir:
                        scale = scale_w if abs(scale_w - 1.0) > abs(scale_h - 1.0) else scale_h
                    else:
                        scale = scale_w
                else:
                    scale = scale_h

                scale = max(0.05, scale)
                new_w = max(0.04, init.width * scale)
                new_h = max(0.04, init.height * scale)
                dw = new_w - init.width
                dh = new_h - init.height
            else:
                new_w = max(0.04, init.width + dw)
                new_h = max(0.04, init.height + dh)
                dw = new_w - init.width
                dh = new_h - init.height

            local_dcx = 0.0
            local_dcy = 0.0

            if "E" in handle_dir:
                local_dcx = dw / 2.0
            elif "W" in handle_dir:
                local_dcx = -dw / 2.0

            if "S" in handle_dir:
                local_dcy = dh / 2.0
            elif "N" in handle_dir:
                local_dcy = -dh / 2.0

            world_dcx = local_dcx * math.cos(rad) - local_dcy * math.sin(rad)
            world_dcy = local_dcx * math.sin(rad) + local_dcy * math.cos(rad)

            item.width = round(new_w, 4)
            item.height = round(new_h, 4)
            item.x = clamp(init.x + world_dcx, -1.0, 2.0)
            item.y = clamp(init.y + world_dcy, -1.0, 2.0)
            self._sync_position_and_size_spinners(item)

        self._trigger_canvas_redraw()

    def _on_canvas_drag_end(self, gesture: Gtk.GestureDrag | None, offset_x: float, offset_y: float) -> None:
        if self._drag_mode == "NONE":
            return

        # A. Rubberband selection finished
        if self._drag_mode == "RUBBERBAND":
            self._drag_mode = "NONE"
            self._rubberband_start = None
            self._rubberband_current = None
            self._rubberband_initial_selection = None
            self._refresh_canvas_and_properties()
            return

        # B. Multi-selection move finished
        if self._drag_mode == "MOVE_MULTI":
            if abs(offset_x) < 3.0 and abs(offset_y) < 3.0:
                state = gesture.get_current_event_state() if gesture is not None else Gdk.ModifierType(0)
                if not (state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK)):
                    if self._drag_item_id:
                        self.selected_item_ids = {self._drag_item_id}
                        self._refresh_canvas_and_properties()
                        self._drag_mode = "NONE"
                        self._items_state_before_drag = None
                        self._selected_states_at_drag_start.clear()
                        return

            has_changed = False
            for it in self.selected_items:
                init = self._selected_states_at_drag_start.get(it.id)
                if init and (init.x != it.x or init.y != it.y):
                    has_changed = True
                    break
            if has_changed and self._items_state_before_drag:
                self._undo_stack.append(self._items_state_before_drag)
                if len(self._undo_stack) > 50:
                    self._undo_stack.pop(0)
                self._redo_stack.clear()
                self._update_undo_redo_ui()
            self._items_state_before_drag = None
            self._selected_states_at_drag_start.clear()
            self._drag_mode = "NONE"
            self._drag_item_id = None
            self._set_cursor("default")
            self._update_canvas_geometry()
            self._sync_position_and_size_spinners()
            self._trigger_canvas_redraw()
            return

        # C. Multi-selection resize finished
        if self._drag_mode.startswith("RESIZE_MULTI_"):
            has_changed = False
            for it in self.selected_items:
                init = self._selected_states_at_drag_start.get(it.id)
                if init and (init.x != it.x or init.y != it.y or init.width != it.width or init.height != it.height):
                    has_changed = True
                    break
            if has_changed and self._items_state_before_drag:
                self._undo_stack.append(self._items_state_before_drag)
                if len(self._undo_stack) > 50:
                    self._undo_stack.pop(0)
                self._redo_stack.clear()
                self._update_undo_redo_ui()
            self._items_state_before_drag = None
            self._selected_states_at_drag_start.clear()
            self._multi_selection_bounds_at_drag_start = None
            self._drag_mode = "NONE"
            self._set_cursor("default")
            self._update_canvas_geometry()
            self._sync_position_and_size_spinners()
            self._trigger_canvas_redraw()
            return

        # D. Single item drag finished
        if getattr(self, "_items_state_before_drag", None) is not None:
            has_changed = False
            if self.selected_item and self._item_state_at_drag_start:
                s = self._item_state_at_drag_start
                c = self.selected_item
                if (s.x != c.x or s.y != c.y or s.width != c.width or
                    s.height != c.height or s.rotation != c.rotation):
                    has_changed = True
            if has_changed:
                self._undo_stack.append(self._items_state_before_drag)
                if len(self._undo_stack) > 50:
                    self._undo_stack.pop(0)
                self._redo_stack.clear()
                self._update_undo_redo_ui()
            self._items_state_before_drag = None
        if self.selected_item:
            self._sync_position_and_size_spinners(self.selected_item)
        self._drag_mode = "NONE"
        self._drag_item_id = None
        self._item_state_at_drag_start = None
        self._set_cursor("default")
        self._update_canvas_geometry()


    # --------------------------------------------------------------------------
    # Video & Playback Scrubbing
    # --------------------------------------------------------------------------

    def _init_video_player(self) -> None:
        """Initialize Gtk.MediaFile for hardware-accelerated playback and frame extraction."""
        # Initial frame extraction via FFmpeg
        self._load_reference_frame()

        # Connect Gtk.MediaFile
        try:
            if self.segment.source_path.exists():
                self._media_file = Gtk.MediaFile.new_for_filename(str(self.segment.source_path))

                self._transformed_paintable = TransformedPaintable(
                    base=self._media_file,
                    rotation=self.segment.rotation,
                    hflip=self.segment.hflip,
                    vflip=self.segment.vflip,
                    crop=self.segment.crop,
                )
                self.preview_picture.set_paintable(self._transformed_paintable)

                def on_prepared(mf, _pspec):
                    if mf.is_prepared():
                        self._update_canvas_geometry()
                        self._seek_media_file(self.current_time, force=True)
                        self._render_overlay_canvas()

                def on_seeking_notify(mf, _pspec):
                    is_seeking = mf.get_property("seeking")
                    if not is_seeking and self._play_on_seek_done:
                        self._play_on_seek_done = False
                        if self.is_playing and not mf.get_playing():
                            mf.play()

                self._mf_handlers = [
                    self._media_file.connect("notify::prepared", on_prepared),
                    self._media_file.connect("notify::seeking", on_seeking_notify),
                ]
        except Exception as e:
            print(f"Warning: could not initialize MediaFile: {e}")
            self._media_file = None
            self._transformed_paintable = None

    def _load_reference_frame(self) -> None:
        """Extract frame from video at current time and set to preview picture."""
        seek_target = self.segment.start_time + self.current_time
        raw = FFmpegService.extract_frame_to_bytes(self.segment.source_path, seek_target)
        if raw:
            try:
                bio = io.BytesIO(raw)
                with Image.open(bio) as img:
                    img_rgba = img.convert("RGBA")
                    out_bio = io.BytesIO()
                    img_rgba.save(out_bio, format="PNG")
                    png_bytes = out_bio.getvalue()
                    loader = GdkPixbuf.PixbufLoader.new_with_type("png")
                    loader.write(png_bytes)
                    loader.close()
                    pb = loader.get_pixbuf()
                    if pb:
                        self._base_pixbuf = pb
                        if not self._media_file:
                            self.preview_picture.set_paintable(Gdk.Texture.new_for_pixbuf(pb))
                        self._cairo_frame_surface = cairo.ImageSurface.create_from_png(io.BytesIO(png_bytes))
            except Exception as e:
                print(f"Error loading reference frame: {e}")

        self._update_canvas_geometry()
        self._trigger_canvas_redraw()

    def _seek_media_file(self, rel_time: float, force: bool = False) -> None:
        """Seek video media file to segment timestamp."""
        if not self._media_file or not self._media_file.is_prepared():
            return
        if not force and self._media_file.get_property("seeking"):
            return
        abs_ts = self.segment.start_time + rel_time
        us = int(abs_ts * 1_000_000)
        self._media_file.seek(us)

    def _toggle_playback(self) -> None:
        if self.is_playing:
            self._pause_playback()
        else:
            self._start_playback()

    def _start_playback(self) -> None:
        if self.is_playing:
            return
        self.is_playing = True
        self.btn_play.set_icon_name("media-playback-pause-symbolic")
        self.btn_play.set_tooltip_text(_("Pause (Space)"))

        if self.current_time >= self.clip_duration - 0.05:
            self.current_time = 0.0
            self.scrubber.set_value(0.0)
            self._seek_media_file(0.0, force=True)

        if self._media_file and self._media_file.is_prepared():
            if self._media_file.get_property("seeking"):
                self._play_on_seek_done = True
            else:
                self._media_file.play()

        if self._playback_timer is None:
            self._playback_timer = GLib.timeout_add(33, self._on_playback_tick)

        if hasattr(self, "_audio_player") and self._audio_player:
            self._audio_player.update_playback(self.items, self.current_time, is_playing=True)

    def _pause_playback(self) -> None:
        self.is_playing = False
        self._play_on_seek_done = False
        self.btn_play.set_icon_name("media-playback-start-symbolic")
        self.btn_play.set_tooltip_text(_("Play (Space)"))
        if self._playback_timer is not None:
            GLib.source_remove(self._playback_timer)
            self._playback_timer = None
        if self._media_file:
            self._media_file.pause()
        if hasattr(self, "_audio_player") and self._audio_player:
            self._audio_player.pause_all()

    def _on_playback_tick(self) -> bool:
        if not self.is_playing:
            return False

        if self._media_file and self._media_file.is_prepared():
            stream_ts = self._media_file.get_timestamp() / 1_000_000.0
            rel_t = stream_ts - self.segment.start_time
            is_ended = self._media_file.get_ended()

            if rel_t >= self.clip_duration or is_ended:
                # Loop back to beginning of segment
                self.current_time = 0.0
                self.scrubber.set_value(0.0)
                self.lbl_time.set_label(f"{format_time(0.0)} / {format_time(self.clip_duration)}")
                self._play_on_seek_done = True
                self._seek_media_file(0.0, force=True)
                if hasattr(self, "_audio_player") and self._audio_player:
                    self._audio_player.update_playback(self.items, 0.0, is_playing=True, master_seeking=True)
                return True

            if rel_t >= 0.0:
                self.current_time = rel_t
                self.scrubber.set_value(self.current_time)
                self.lbl_time.set_label(f"{format_time(self.current_time)} / {format_time(self.clip_duration)}")
        else:
            self.current_time += 0.033
            if self.current_time >= self.clip_duration:
                self.current_time = 0.0
            self.scrubber.set_value(self.current_time)
            self.lbl_time.set_label(f"{format_time(self.current_time)} / {format_time(self.clip_duration)}")

        if hasattr(self, "_audio_player") and self._audio_player:
            self._audio_player.update_playback(self.items, self.current_time, is_playing=True)

        # Redraw overlays if there are dynamic elements
        if any(it.item_type in ("gif", "video") for it in self.items):
            self._trigger_canvas_redraw()

        return True

    def _seek_relative(self, delta_s: float) -> None:
        new_t = clamp(self.current_time + delta_s, 0.0, self.clip_duration)
        self.scrubber.set_value(new_t)

    def _on_scrubber_changed(self, scale: Gtk.Scale) -> None:
        val = scale.get_value()
        if abs(val - self.current_time) > 0.01:
            self.current_time = val
            self.lbl_time.set_label(f"{format_time(self.current_time)} / {format_time(self.clip_duration)}")
            if self._media_file and self._media_file.is_prepared():
                self._seek_media_file(self.current_time, force=True)
            elif not self.is_playing:
                self._load_reference_frame()
            if hasattr(self, "_audio_player") and self._audio_player:
                self._audio_player.update_playback(self.items, self.current_time, is_playing=self.is_playing, master_seeking=True)
            self._trigger_canvas_redraw()

    # --------------------------------------------------------------------------
    # Dialog Finish & Cleanup
    # --------------------------------------------------------------------------

    def _cleanup(self) -> None:
        self._pause_playback()
        if hasattr(self, "_context_popover") and self._context_popover:
            try:
                self._context_popover.unparent()
            except Exception:
                pass
            self._context_popover = None
        if hasattr(self, "_audio_player") and self._audio_player:
            self._audio_player.cleanup()
        if self._media_file:
            for hid in self._mf_handlers:
                try:
                    self._media_file.disconnect(hid)
                except Exception:
                    pass
            self._mf_handlers.clear()
            self._media_file = None
        if self._transformed_paintable:
            self._transformed_paintable.detach()
            self._transformed_paintable = None

    def _on_apply_clicked(self) -> None:
        self._cleanup()
        self.on_apply([it.copy() for it in self.items])
        self.close()

    def _on_delete_all_clicked(self) -> None:
        if not self.items:
            return

        def _do_clear_and_close() -> None:
            self.items.clear()
            self._cleanup()
            if self.on_apply:
                self.on_apply([])
            self.close()

        if hasattr(Adw, "AlertDialog"):
            dlg = Adw.AlertDialog.new(
                _("Remove All Overlays?"),
                _("All overlay items on this segment will be permanently deleted."),
            )
            dlg.add_response("cancel", _("Cancel"))
            dlg.add_response("delete", _("Delete All"))
            dlg.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
            dlg.set_default_response("cancel")
            dlg.set_close_response("cancel")

            def on_response(source: Any, result: Any) -> None:
                if isinstance(result, str):
                    resp = result
                else:
                    target = source if hasattr(source, "choose_finish") else dlg
                    try:
                        resp = target.choose_finish(result)
                    except Exception:
                        resp = None
                if resp == "delete":
                    _do_clear_and_close()

            dlg.choose(self, None, on_response)
        elif hasattr(Adw, "MessageDialog"):
            dlg = Adw.MessageDialog.new(
                self,
                _("Remove All Overlays?"),
                _("All overlay items on this segment will be permanently deleted."),
            )
            dlg.add_response("cancel", _("Cancel"))
            dlg.add_response("delete", _("Delete All"))
            dlg.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
            dlg.set_default_response("cancel")
            dlg.set_close_response("cancel")

            def on_response(_d: Any, resp: str) -> None:
                if resp == "delete":
                    _do_clear_and_close()

            dlg.connect("response", on_response)
            dlg.present()
        else:
            _do_clear_and_close()

    def _on_close_request(self, *_) -> bool:
        self._cleanup()
        return False
