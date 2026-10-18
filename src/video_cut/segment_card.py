"""Segment Card widget showing thumbnails, duration, and interactive controls."""

from pathlib import Path
from typing import Any, Callable, List, Optional
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Gdk, Adw, GObject, GLib

from .models import Segment
from .utils import format_time, clamp


class SegmentCard(Gtk.Box):
    """Widget representing a single video segment in the timeline filmstrip."""

    __gtype_name__ = "SegmentCard"

    def __init__(
        self,
        segment: Segment,
        index: int,
        is_selected: bool,
        on_select: Callable[[str], None],
        on_scrub_to_source_time: Optional[Callable[[str, float], None]] = None,
        on_move_left: Callable[[str], None] = lambda _: None,
        on_move_right: Callable[[str], None] = lambda _: None,
        on_split: Callable[[str], None] = lambda _: None,
        on_delete: Callable[[str], None] = lambda _: None,
        on_copy: Optional[Callable[[str], None]] = None,
        on_cut: Optional[Callable[[str], None]] = None,
        on_paste: Optional[Callable[[str], None]] = None,
        on_drop_reorder: Callable[[str, str], None] = lambda _a, _b: None,
        can_delete: bool = True,
        can_paste: bool = False,
        can_move_left: bool = True,
        can_move_right: bool = True,
        on_scrub_start: Optional[Callable[["SegmentCard", float, float], None]] = None,
        on_scrub_update: Optional[Callable[["SegmentCard", float, float], None]] = None,
        on_scrub_end: Optional[Callable[[], None]] = None,
        timeline_widget: Optional[Any] = None,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self._timeline_widget = timeline_widget
        self.segment = segment
        self.index = index
        self._is_selected = is_selected
        self.can_delete = can_delete
        self.can_paste = can_paste
        self.can_move_left = can_move_left
        self.can_move_right = can_move_right
        self.on_select = on_select
        self.on_scrub_to_source_time = on_scrub_to_source_time
        self.on_scrub_start = on_scrub_start
        self.on_scrub_update = on_scrub_update
        self.on_scrub_end = on_scrub_end
        self.on_move_left = on_move_left
        self.on_move_right = on_move_right
        self.on_split = on_split
        self.on_delete = on_delete
        self.on_copy = on_copy
        self.on_cut = on_cut
        self.on_paste = on_paste
        self.on_drop_reorder = on_drop_reorder
        self._popover_close_timer: Optional[int] = None
        self._is_drag_scrubbing: bool = False

        self.add_css_class("segment-card")
        if is_selected:
            self.add_css_class("segment-selected")

        self.set_overflow(Gtk.Overflow.VISIBLE)
        self.set_valign(Gtk.Align.CENTER)
        self.set_hexpand(False)

        # Left click gesture across entire card ensuring it becomes selected whenever clicked
        card_click = Gtk.GestureClick.new()
        card_click.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        card_click.connect("pressed", lambda _g, _n, _x, _y: self.on_select(self.segment.id))
        self.add_controller(card_click)

        # Right click gesture for context menu
        right_click = Gtk.GestureClick.new()
        right_click.set_button(Gdk.BUTTON_SECONDARY)
        right_click.connect("pressed", self._on_right_click)
        self.add_controller(right_click)

        # Context menu setup
        self._build_context_menu()
        self.connect("unrealize", self._on_card_unrealize)

        # 1. Top bar: Index, duration badge, move left/right buttons
        self._build_header()

        # 2. Filmstrip area with playhead needle overlay
        self._build_filmstrip()

        # 3. Setup drag and drop
        self._setup_drag_and_drop()

    def _build_header(self) -> None:
        self.header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self.header.add_css_class("segment-header")
        self.header.set_overflow(Gtk.Overflow.VISIBLE)
        self.header.set_cursor_from_name("grab")
        self.header.set_tooltip_text("Drag header to reorder clip")
        self.header.set_size_request(-1, 34)

        # Visual drag handle indicator
        self.handle_icon = Gtk.Image.new_from_icon_name("list-drag-handle-symbolic")
        self.handle_icon.set_pixel_size(14)
        self.handle_icon.add_css_class("dim-label")
        self.handle_icon.set_valign(Gtk.Align.CENTER)
        self.header.append(self.handle_icon)

        # Segment index badge
        self.idx_label = Gtk.Label(label=f"#{self.index + 1}")
        self.idx_label.add_css_class("segment-index-badge")
        self.idx_label.set_valign(Gtk.Align.CENTER)
        self.header.append(self.idx_label)

        # Duration badge
        dur_str = format_time(self.segment.duration, show_millis=False)
        self.dur_label = Gtk.Label(label=dur_str)
        self.dur_label.add_css_class("segment-duration-badge")
        self.dur_label.set_valign(Gtk.Align.CENTER)
        self.header.append(self.dur_label)

        # Spacer
        self.header_spacer = Gtk.Box()
        self.header_spacer.set_hexpand(True)
        self.header.append(self.header_spacer)

        # Move Left button (set_focusable(False) prevents stealing Spacebar)
        self.btn_left = Gtk.Button.new_from_icon_name("go-previous-symbolic")
        self.btn_left.set_tooltip_text("Move clip left (Alt+Left)")
        self.btn_left.set_focusable(False)
        self.btn_left.set_cursor_from_name("default")
        self.btn_left.add_css_class("flat")
        self.btn_left.add_css_class("circular")
        self.btn_left.set_sensitive(self.can_move_left)
        self.btn_left.connect("clicked", lambda _: self.on_move_left(self.segment.id))
        self.header.append(self.btn_left)

        # Move Right button
        self.btn_right = Gtk.Button.new_from_icon_name("go-next-symbolic")
        self.btn_right.set_tooltip_text("Move clip right (Alt+Right)")
        self.btn_right.set_focusable(False)
        self.btn_right.set_cursor_from_name("default")
        self.btn_right.add_css_class("flat")
        self.btn_right.add_css_class("circular")
        self.btn_right.set_sensitive(self.can_move_right)
        self.btn_right.connect("clicked", lambda _: self.on_move_right(self.segment.id))
        self.header.append(self.btn_right)

        # Split Pill: standalone fallback button for unit tests and API compatibility
        self._standalone_split_pill = Gtk.Button()
        self._standalone_split_pill.add_css_class("playhead-split-btn")
        self._standalone_split_pill.set_cursor_from_name("pointer")
        self._standalone_split_pill.set_tooltip_text("Split clip at cursor")
        self._standalone_split_pill.set_focusable(False)
        self._standalone_split_pill.set_size_request(50, 24)
        self._standalone_split_pill.set_visible(False)

        pill_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        split_icon = Gtk.Image.new_from_icon_name("video-split-symbolic")
        split_icon.set_pixel_size(12)
        split_icon.add_css_class("split-pill-icon")
        split_lbl = Gtk.Label(label="Split")
        split_lbl.add_css_class("split-pill-text")
        pill_box.append(split_icon)
        pill_box.append(split_lbl)
        self._standalone_split_pill.set_child(pill_box)
        self._standalone_split_pill.connect("clicked", lambda _: self._on_split_pill_clicked())

        self._split_pill_x = 0
        self._standalone_split_pill.get_margin_start = lambda: self._split_pill_x

        # Detect motion on header to show split pill when hovering near needle
        header_motion = Gtk.EventControllerMotion.new()
        header_motion.connect("motion", self._on_header_motion)
        header_motion.connect("leave", self._on_header_leave)
        self.header.add_controller(header_motion)

        # Append header directly to card — completely unobstructed for move buttons and drag-and-drop
        self.append(self.header)

    @property
    def split_pill(self) -> Gtk.Button:
        if self._timeline_widget is not None and hasattr(self._timeline_widget, "split_pill"):
            return self._timeline_widget.split_pill
        return self._standalone_split_pill

    @property
    def btn_split_popover(self) -> Gtk.Button:
        if self._timeline_widget is not None and hasattr(self._timeline_widget, "split_pill"):
            return self._timeline_widget.split_pill
        return self._popover_btn

    @property
    def header_overlay(self) -> Gtk.Widget:
        return self

    @property
    def split_fixed(self) -> Gtk.Widget:
        return self

    def set_timeline_widget(self, tw: Any) -> None:
        self._timeline_widget = tw

    def set_card_width(self, width: int) -> None:
        """Set target pixel width and adaptively collapse header widgets if narrow."""
        self._target_width = width
        self.set_size_request(width, -1)
        # Adaptively hide secondary controls if width is too narrow (calibrated for 0 overflows)
        show_nav = (width >= 180)
        show_dur = (width >= 110)
        show_handle = (width >= 60)

        self.btn_left.set_visible(show_nav)
        self.btn_right.set_visible(show_nav)
        self.dur_label.set_visible(show_dur)
        self.handle_icon.set_visible(show_handle)

    def _build_context_menu(self) -> None:
        """Create native Popover context menu for clip actions."""
        self.context_menu = Gtk.Popover()
        self.context_menu.set_parent(self)
        self.context_menu.set_has_arrow(False)
        self.context_menu.set_autohide(True)
        self.context_menu.set_halign(Gtk.Align.START)
        self.context_menu.set_position(Gtk.PositionType.BOTTOM)
        self.context_menu.add_css_class("menu")

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.set_margin_top(4)
        box.set_margin_bottom(4)
        box.set_margin_start(4)
        box.set_margin_end(4)

        def make_menu_item(icon_name: str, label_text: str, accel_text: str, callback: Callable[[], None], is_destructive: bool = False) -> Gtk.Button:
            btn = Gtk.Button()
            btn.add_css_class("flat")
            if is_destructive:
                btn.add_css_class("destructive-action")
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            icon = Gtk.Image.new_from_icon_name(icon_name)
            icon.set_pixel_size(16)
            row.append(icon)
            lbl = Gtk.Label(label=label_text, xalign=0)
            lbl.set_hexpand(True)
            row.append(lbl)
            if accel_text:
                accel = Gtk.Label(label=accel_text)
                accel.add_css_class("dim-label")
                row.append(accel)
            btn.set_child(row)
            btn.connect("clicked", lambda _: self._on_menu_item_clicked(callback))
            return btn

        self.menu_split = make_menu_item("video-split-symbolic", "Split at Cursor", "S", self._menu_action_split)
        box.append(self.menu_split)

        sep0 = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        box.append(sep0)

        self.menu_cut = make_menu_item("edit-cut-symbolic", "Cut", "Ctrl+X", self._menu_action_cut)
        box.append(self.menu_cut)

        self.menu_copy = make_menu_item("edit-copy-symbolic", "Copy", "Ctrl+C", self._menu_action_copy)
        box.append(self.menu_copy)

        self.menu_paste = make_menu_item("edit-paste-symbolic", "Paste", "Ctrl+V", self._menu_action_paste)
        box.append(self.menu_paste)

        sep1 = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        box.append(sep1)

        self.menu_move_left = make_menu_item("go-previous-symbolic", "Move Clip Left", "Alt+←", self._menu_action_move_left)
        box.append(self.menu_move_left)

        self.menu_move_right = make_menu_item("go-next-symbolic", "Move Clip Right", "Alt+→", self._menu_action_move_right)
        box.append(self.menu_move_right)

        sep2 = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        box.append(sep2)

        self.menu_delete = make_menu_item("user-trash-symbolic", "Delete", "Del", self._menu_action_delete, is_destructive=True)
        box.append(self.menu_delete)

        self.context_menu.set_child(box)

    def _on_menu_item_clicked(self, action: Callable[[], None]) -> None:
        self.context_menu.popdown()
        action()

    def _menu_action_split(self) -> None:
        if self.on_split:
            self.on_split(self.segment.id)

    def _menu_action_cut(self) -> None:
        if self.can_delete and self.on_cut:
            self.on_cut(self.segment.id)

    def _menu_action_copy(self) -> None:
        if self.on_copy:
            self.on_copy(self.segment.id)

    def _menu_action_paste(self) -> None:
        if self.can_paste and self.on_paste:
            self.on_paste(self.segment.id)

    def _menu_action_move_left(self) -> None:
        if self.can_move_left and self.on_move_left:
            self.on_move_left(self.segment.id)

    def _menu_action_move_right(self) -> None:
        if self.can_move_right and self.on_move_right:
            self.on_move_right(self.segment.id)

    def _menu_action_delete(self) -> None:
        if self.can_delete and self.on_delete:
            self.on_delete(self.segment.id)

    def _update_context_menu_state(self) -> None:
        self.menu_split.set_sensitive(True)
        self.menu_cut.set_sensitive(self.can_delete)
        self.menu_copy.set_sensitive(True)
        self.menu_paste.set_sensitive(self.can_paste)
        self.menu_move_left.set_sensitive(self.can_move_left)
        self.menu_move_right.set_sensitive(self.can_move_right)
        self.menu_delete.set_sensitive(self.can_delete)

    def _on_right_click(self, gesture, n_press: int, x: float, y: float) -> None:
        self._cancel_popover_timer()
        if hasattr(self, "split_pill"):
            self.split_pill.set_visible(False)
        if hasattr(self, "split_popover"):
            self.split_popover.popdown()
        self.on_select(self.segment.id)
        self._update_context_menu_state()
        rect = Gdk.Rectangle()
        rect.x = int(x)
        rect.y = int(y)
        rect.width = 1
        rect.height = 1
        self.context_menu.set_pointing_to(rect)
        if self.get_native() is not None:
            self.context_menu.popup()

    def set_can_paste(self, can_paste: bool) -> None:
        self.can_paste = can_paste
        if hasattr(self, "menu_paste"):
            self.menu_paste.set_sensitive(can_paste)

    def set_can_delete(self, can_delete: bool) -> None:
        self.can_delete = can_delete
        if hasattr(self, "menu_cut"):
            self.menu_cut.set_sensitive(can_delete)
        if hasattr(self, "menu_delete"):
            self.menu_delete.set_sensitive(can_delete)

    def _on_card_unrealize(self, widget) -> None:
        self._cancel_popover_timer()
        if hasattr(self, "split_popover") and self.split_popover.get_parent():
            self.split_popover.unparent()
        if hasattr(self, "context_menu") and self.context_menu.get_parent():
            self.context_menu.unparent()

    def _build_filmstrip(self) -> None:
        """Create the row of thumbnail pictures with the overlay playhead needle."""
        self.filmstrip_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=1)
        self.filmstrip_box.add_css_class("segment-filmstrip")
        self.filmstrip_box.set_size_request(-1, 68)
        # 7px start and end margins allow the 14px ball to extend 7px outside the image at boundaries
        self.filmstrip_box.set_margin_start(7)
        self.filmstrip_box.set_margin_end(7)
        # 7px top margin aligns the equator (50%) of the 14px ball with the top of the image
        self.filmstrip_box.set_margin_top(7)
        self.filmstrip_box.set_cursor_from_name("ew-resize")

        self.overlay = Gtk.Overlay()
        self.overlay.set_overflow(Gtk.Overflow.VISIBLE)
        self.overlay.set_child(self.filmstrip_box)
        self.overlay.set_cursor_from_name("ew-resize")

        # The Playhead Needle: container with perfectly centralized knob and thin bar
        self.needle = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.needle.add_css_class("playhead-needle-container")
        self.needle.set_halign(Gtk.Align.START)
        self.needle.set_valign(Gtk.Align.FILL)
        self.needle.set_size_request(14, 68)
        self.needle.set_cursor_from_name("ew-resize")
        self.needle.set_visible(False)

        # 1. Orange ball (14px diameter) with ew-resize cursor for scrubbing
        self.needle_knob = Gtk.Box()
        self.needle_knob.add_css_class("playhead-needle-knob")
        self.needle_knob.set_halign(Gtk.Align.CENTER)
        self.needle_knob.set_size_request(14, 14)
        self.needle_knob.set_cursor_from_name("ew-resize")
        self.needle_knob.set_tooltip_text("Playhead cursor • Drag to scrub • Press S to split")
        self.needle.append(self.needle_knob)

        # 2. Split Popover directly anchored to the needle knob (kept for API compatibility)
        self.split_popover = Gtk.Popover()
        self.split_popover.add_css_class("playhead-split-popover")
        self.split_popover.set_parent(self.needle_knob)
        self.split_popover.set_position(Gtk.PositionType.TOP)
        self.split_popover.set_has_arrow(False)
        self.split_popover.set_offset(0, -3)
        self.split_popover.set_autohide(True)

        self._popover_btn = Gtk.Button()
        self._popover_btn.add_css_class("playhead-split-btn")
        self._popover_btn.set_cursor_from_name("pointer")
        self._popover_btn.set_tooltip_text("Split clip at cursor")
        self._popover_btn.set_focusable(False)

        popover_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        split_icon = Gtk.Image.new_from_icon_name("video-split-symbolic")
        split_icon.set_pixel_size(12)
        split_icon.add_css_class("split-pill-icon")
        split_lbl = Gtk.Label(label="Split")
        split_lbl.add_css_class("split-pill-text")
        popover_box.append(split_icon)
        popover_box.append(split_lbl)
        self._popover_btn.set_child(popover_box)
        self._popover_btn.connect("clicked", lambda _: self._on_popover_split_clicked())
        self.split_popover.set_child(self._popover_btn)

        # Unparent cleanly when knob is unrealized
        self.needle_knob.connect("unrealize", self._on_knob_unrealize)

        # 3. Thinner vertical bar (2px wide), centralized inside container
        needle_bar = Gtk.Box()
        needle_bar.add_css_class("playhead-needle-bar")
        needle_bar.set_halign(Gtk.Align.CENTER)
        needle_bar.set_vexpand(True)
        needle_bar.set_size_request(2, -1)
        needle_bar.set_cursor_from_name("ew-resize")
        needle_bar.set_tooltip_text("Playhead cursor • Drag to scrub • Press S to split")
        self.needle.append(needle_bar)

        self.overlay.add_overlay(self.needle)

        self.update_thumbnails(self.segment.thumbnails)
        self.append(self.overlay)

        # Scrubbing and dragging gestures on the filmstrip & needle (using CAPTURE so knob dragging is never blocked)
        click_gesture = Gtk.GestureClick.new()
        click_gesture.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        click_gesture.connect("pressed", self._on_filmstrip_click)
        click_gesture.connect("released", self._on_filmstrip_release)
        self.overlay.add_controller(click_gesture)

        drag_gesture = Gtk.GestureDrag.new()
        drag_gesture.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        drag_gesture.connect("drag-begin", self._on_filmstrip_drag_begin)
        drag_gesture.connect("drag-update", self._on_filmstrip_drag_update)
        drag_gesture.connect("drag-end", self._on_filmstrip_drag_end)
        self.overlay.add_controller(drag_gesture)

        # Motion controller for showing split pill when hovering near needle
        filmstrip_motion = Gtk.EventControllerMotion.new()
        filmstrip_motion.connect("motion", self._on_filmstrip_motion)
        filmstrip_motion.connect("leave", self._on_filmstrip_leave)
        self.overlay.add_controller(filmstrip_motion)

    def show_split_pill(self) -> None:
        self._cancel_popover_timer()
        if self._timeline_widget is not None:
            self._timeline_widget.show_split_pill()
        else:
            self._standalone_split_pill.set_visible(True)

    def hide_split_pill(self) -> None:
        if self._timeline_widget is not None:
            self._timeline_widget.hide_split_pill()
        else:
            self._standalone_split_pill.set_visible(False)

    def _cancel_popover_timer(self) -> None:
        if self._popover_close_timer is not None:
            GLib.source_remove(self._popover_close_timer)
            self._popover_close_timer = None
        if self._timeline_widget is not None:
            self._timeline_widget._cancel_split_timer()

    def _schedule_popover_close(self) -> None:
        self._cancel_popover_timer()
        if self._timeline_widget is not None:
            self._timeline_widget._schedule_split_close()
        else:
            self._popover_close_timer = GLib.timeout_add(300, self._do_close_popover)

    def _do_close_popover(self) -> bool:
        self._popover_close_timer = None
        self.hide_split_pill()
        if hasattr(self, "split_popover"):
            self.split_popover.popdown()
        return GLib.SOURCE_REMOVE

    def _on_filmstrip_motion(self, controller, x: float, y: float) -> None:
        if self._is_drag_scrubbing or not self.needle.get_visible():
            return
        needle_x = self.needle.get_margin_start() + 7
        if abs(x - needle_x) <= 32:
            self.show_split_pill()
        else:
            self._schedule_popover_close()

    def _on_filmstrip_leave(self, controller) -> None:
        self._schedule_popover_close()

    def _on_header_motion(self, controller, x: float, y: float) -> None:
        if self._is_drag_scrubbing or not self.needle.get_visible():
            return
        needle_x = self.needle.get_margin_start() + 7
        if abs(x - needle_x) <= 35:
            self.show_split_pill()
        else:
            self._schedule_popover_close()

    def _on_header_leave(self, controller) -> None:
        self._schedule_popover_close()

    def _on_split_pill_clicked(self) -> None:
        self._cancel_popover_timer()
        self.hide_split_pill()
        if self.on_split:
            self.on_split(self.segment.id)

    def _on_knob_unrealize(self, widget) -> None:
        self._cancel_popover_timer()
        if self.split_popover.get_parent():
            self.split_popover.unparent()

    def _on_popover_split_clicked(self) -> None:
        self._on_split_pill_clicked()

    def update_thumbnails(self, thumbnails: List[Path]) -> None:
        """Populate the filmstrip box with thumbnails."""
        child = self.filmstrip_box.get_first_child()
        while child:
            next_child = child.get_next_sibling()
            self.filmstrip_box.remove(child)
            child = next_child

        valid_thumbs = [p for p in thumbnails if p.exists()]
        if not valid_thumbs:
            placeholder = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
            placeholder.set_hexpand(True)
            placeholder.set_size_request(-1, 68)
            placeholder.add_css_class("thumbnail-placeholder")
            icon = Gtk.Image.new_from_icon_name("video-x-generic-symbolic")
            icon.set_pixel_size(24)
            placeholder.append(icon)
            self.filmstrip_box.append(placeholder)
            return

        for thumb_path in valid_thumbs:
            pic = Gtk.Picture.new_for_filename(str(thumb_path))
            pic.set_can_shrink(True)
            pic.set_hexpand(True)
            pic.set_content_fit(Gtk.ContentFit.COVER)
            pic.set_size_request(-1, 68)
            self.filmstrip_box.append(pic)

    def set_needle_ratio(self, ratio: Optional[float]) -> None:
        """Move playhead needle to ratio (0.0 to 1.0) or hide it if None."""
        if ratio is None:
            self._cancel_popover_timer()
            self.hide_split_pill()
            if hasattr(self, "split_popover"):
                self.split_popover.popdown()
            self.needle.set_visible(False)
            return

        width = self.filmstrip_box.get_width()
        if width <= 0:
            width = self.get_width() - 14
        if width <= 0:
            width = 100
        # When ratio = 0.0, margin_x = 0, placing the 2px center of needle at X = 7 (first pixel column of image)
        # and the 14px ball spanning X = 0 to 14 (left 7px extends to the left of the image).
        margin_x = int(round(clamp(ratio, 0.0, 1.0) * width))
        self.needle.set_margin_start(margin_x)
        self.needle.set_visible(True)

        needle_center_x = margin_x + 7
        pill_w = 50
        # Always perfectly centered over the cursor needle, never constrained by segment borders
        pill_x = int(round(needle_center_x - (pill_w / 2.0)))
        self._split_pill_x = pill_x

        if self._timeline_widget is not None and self._timeline_widget.split_pill.get_visible():
            self._timeline_widget._update_split_pill_position()

    def set_selected(self, selected: bool) -> None:
        if self._is_selected != selected:
            self._is_selected = selected
            if selected:
                self.add_css_class("segment-selected")
            else:
                self.remove_css_class("segment-selected")

    def _scrub_at_x(self, x: float) -> None:
        width = self.filmstrip_box.get_width()
        if width <= 0:
            width = self.get_width() - 14
        if width <= 0:
            width = 100
        rel_x = x - 7.0
        ratio = rel_x / float(width)
        scrub_ts = self.segment.start_time + ratio * self.segment.duration
        if self.on_scrub_to_source_time:
            self.on_scrub_to_source_time(self.segment.id, scrub_ts)

    def _on_filmstrip_click(self, gesture, n_press: int, x: float, y: float) -> None:
        self._cancel_popover_timer()
        if hasattr(self, "split_popover"):
            self.split_popover.popdown()
        self.on_select(self.segment.id)
        if self.on_scrub_start:
            self.on_scrub_start(self, x, y)
        elif self.on_scrub_to_source_time:
            self._scrub_at_x(x)
        if self.needle.get_visible() and not self._is_drag_scrubbing:
            self.show_split_pill()

    def _on_filmstrip_release(self, gesture, n_press: int, x: float, y: float) -> None:
        self._is_drag_scrubbing = False
        if self.on_scrub_end:
            self.on_scrub_end()
        if self.needle.get_visible() and not self._is_drag_scrubbing:
            self.show_split_pill()

    def _on_filmstrip_drag_begin(self, gesture, start_x: float, start_y: float) -> None:
        self._drag_start_x = start_x
        self._drag_start_y = start_y
        self._is_drag_scrubbing = True
        self._cancel_popover_timer()
        self.hide_split_pill()
        if hasattr(self, "split_popover"):
            self.split_popover.popdown()
        self.on_select(self.segment.id)
        if self.on_scrub_start:
            self.on_scrub_start(self, start_x, start_y)
        elif self.on_scrub_to_source_time:
            self._scrub_at_x(start_x)

    def _on_filmstrip_drag_update(self, gesture, offset_x: float, offset_y: float) -> None:
        start_x = getattr(self, "_drag_start_x", 0.0)
        start_y = getattr(self, "_drag_start_y", 0.0)
        res = gesture.get_start_point()
        if res and res[0]:
            start_x = res[1]
            start_y = res[2]
            cur_x = start_x + offset_x
            cur_y = start_y + offset_y
            if self.on_scrub_update:
                self.on_scrub_update(self, cur_x, cur_y)
            elif self.on_scrub_to_source_time:
                self._scrub_at_x(cur_x)

    def _on_filmstrip_drag_end(self, gesture, offset_x: float, offset_y: float) -> None:
        self._is_drag_scrubbing = False
        if self.on_scrub_end:
            self.on_scrub_end()
        if self.needle.get_visible():
            self.show_split_pill()

    def _create_drag_preview_texture(self) -> Optional[Gdk.MemoryTexture]:
        """Generate a sleek miniature preview card for drag-and-drop feedback."""
        try:
            from PIL import Image, ImageDraw

            width, height = 120, 68
            img = Image.new("RGBA", (width, height), (30, 30, 32, 245))

            # 1. Paste first thumbnail if available
            valid_thumbs = [p for p in self.segment.thumbnails if p.exists()]
            if valid_thumbs:
                try:
                    thumb = Image.open(valid_thumbs[0]).convert("RGBA")
                    thumb = thumb.resize((width, height))
                    img.paste(thumb, (0, 0))
                except Exception:
                    pass

            draw = ImageDraw.Draw(img)

            # 2. Dark badge overlay at bottom
            banner_h = 22
            draw.rectangle([(0, height - banner_h), (width, height)], fill=(0, 0, 0, 195))

            # 3. Text: Clip index and duration
            dur_str = format_time(self.segment.duration, show_millis=False)
            text = f"#{self.index + 1} • {dur_str}"
            draw.text((6, height - banner_h + 4), text, fill=(255, 255, 255, 255))

            # 4. Orange accent border
            draw.rectangle([(0, 0), (width - 1, height - 1)], outline=(255, 85, 0, 240), width=2)

            raw_bytes = img.tobytes("raw", "RGBA")
            glib_bytes = GLib.Bytes.new(raw_bytes)
            return Gdk.MemoryTexture.new(width, height, Gdk.MemoryFormat.R8G8B8A8, glib_bytes, width * 4)
        except Exception as e:
            print(f"Error creating drag preview texture: {e}")
            return None

    def _setup_drag_and_drop(self) -> None:
        # Drag source for reordering attached to header ONLY
        # This completely prevents DND from cancelling needle/filmstrip scrubbing!
        drag_source = Gtk.DragSource.new()
        drag_source.set_actions(Gdk.DragAction.MOVE)
        drag_source.connect("prepare", self._on_drag_prepare)
        drag_source.connect("drag-begin", self._on_drag_begin)
        drag_source.connect("drag-end", self._on_drag_end)
        drag_source.connect("drag-cancel", self._on_drag_cancel)
        self.header.add_controller(drag_source)

        # Drop target to accept reordering anywhere on the card
        drop_target = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.MOVE)
        drop_target.connect("enter", self._on_drop_enter)
        drop_target.connect("leave", self._on_drop_leave)
        drop_target.connect("drop", self._on_drop)
        self.add_controller(drop_target)

    def _on_drag_prepare(self, source, x: float, y: float):
        # Pre-set icon on drag source
        tex = self._create_drag_preview_texture()
        if tex:
            try:
                source.set_icon(tex, 60, 34)
            except Exception:
                pass

        val = GObject.Value(GObject.TYPE_STRING, self.segment.id)
        return Gdk.ContentProvider.new_for_value(val)

    def _on_drag_begin(self, source, drag):
        if hasattr(self, "split_pill"):
            self.split_pill.set_visible(False)
        self.add_css_class("segment-dragging")
        # Replace the default string label with our custom miniature preview card
        tex = self._create_drag_preview_texture()
        if tex:
            try:
                Gtk.DragIcon.set_from_paintable(drag, tex, 60, 34)
            except Exception as e:
                print(f"Error in DragIcon.set_from_paintable: {e}")
            try:
                icon = Gtk.DragIcon.get_for_drag(drag)
                if icon:
                    pic = Gtk.Picture.new_for_paintable(tex)
                    icon.set_child(pic)
            except Exception as e:
                print(f"Error setting DragIcon child: {e}")

    def _on_drag_end(self, source, drag, delete_data):
        self.remove_css_class("segment-dragging")

    def _on_drag_cancel(self, source, drag, reason):
        self.remove_css_class("segment-dragging")
        return False

    def _on_drop_enter(self, target, x: float, y: float) -> Gdk.DragAction:
        self.add_css_class("drop-target-hover")
        return Gdk.DragAction.MOVE

    def _on_drop_leave(self, target) -> None:
        self.remove_css_class("drop-target-hover")

    def _on_drop(self, target, value, x: float, y: float) -> bool:
        self.remove_css_class("drop-target-hover")
        if isinstance(value, str):
            dragged_id = value
            if dragged_id != self.segment.id:
                self.on_drop_reorder(dragged_id, self.segment.id)
                return True
        return False
