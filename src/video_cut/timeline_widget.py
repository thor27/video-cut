"""Storyboard timeline widget managing the filmstrip and playhead needles."""

from pathlib import Path
from typing import Callable, Dict, Optional
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Gdk, Adw, GLib

from .models import ProjectTimeline, Segment
from .segment_card import SegmentCard
from .utils import format_time, clamp
from .i18n import _


def compute_segment_widths(
    segments: list[Segment],
    available_width: int,
    min_width: int = 50,
) -> dict[str, int]:
    """
    Distribute available_width among segments proportionally based on their duration.
    Clamps narrow segments to min_width and redistributes remaining width.
    Ensures the sum of widths equals available_width exactly without rounding errors.
    """
    if not segments or available_width <= 0:
        return {}

    total_duration = sum(seg.duration for seg in segments)
    if total_duration <= 0:
        w = max(min_width, available_width // len(segments))
        return {seg.id: w for seg in segments}

    fixed: dict[str, int] = {}
    remaining = list(segments)
    rem_width = available_width

    while remaining:
        rem_dur = sum(s.duration for s in remaining)
        clamped = False
        for s in list(remaining):
            prop_w = (s.duration / rem_dur) * rem_width if rem_dur > 0 else 0
            if prop_w < min_width and rem_width >= min_width:
                fixed[s.id] = min_width
                rem_width -= min_width
                remaining.remove(s)
                clamped = True
                break
        if not clamped:
            for s in remaining:
                prop_w = (s.duration / rem_dur) * rem_width
                fixed[s.id] = int(round(prop_w))
            break

    diff = available_width - sum(fixed.values())
    if diff != 0 and segments:
        largest_id = max(segments, key=lambda s: s.duration).id
        fixed[largest_id] = max(min_width, fixed[largest_id] + diff)

    return fixed


class TimelineWidget(Gtk.Box):
    """Timeline container featuring the storyboard filmstrip of clips and exact time readout."""

    __gtype_name__ = "TimelineWidget"

    def __init__(
        self,
        timeline: ProjectTimeline,
        on_seek: Callable[[float], None],
        on_split_requested: Callable[[], None],
        on_delete_requested: Callable[[str], None],
        on_add_video_requested: Callable[[], None],
        on_selection_changed: Callable[[Optional[str]], None],
        on_play_pause: Callable[[], None],
        on_step_1s: Callable[[int], None],
        on_step_frame: Callable[[int], None],
        on_jump_segment: Callable[[int], None],
        on_structure_changed: Callable[[], None] = lambda: None,
        get_segment_thumbnails: Optional[Callable[[Segment, int], list[Path]]] = None,
        on_copy_requested: Optional[Callable[[str], None]] = None,
        on_cut_requested: Optional[Callable[[str], None]] = None,
        on_paste_requested: Optional[Callable[[str], None]] = None,
        on_undo_requested: Optional[Callable[[], None]] = None,
        on_redo_requested: Optional[Callable[[], None]] = None,
        on_cut_selection_requested: Optional[Callable[[], None]] = None,
        on_copy_selection_requested: Optional[Callable[[], None]] = None,
        on_paste_selection_requested: Optional[Callable[[], None]] = None,
        on_delete_selection_requested: Optional[Callable[[], None]] = None,
        on_crop_rotate_requested: Optional[Callable[[str], None]] = None,
        on_crop_rotate_selection_requested: Optional[Callable[[], None]] = None,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.timeline = timeline
        self.on_seek = on_seek
        self.on_split_requested = on_split_requested
        self.on_delete_requested = on_delete_requested
        self.on_copy_requested = on_copy_requested
        self.on_cut_requested = on_cut_requested
        self.on_paste_requested = on_paste_requested
        self.on_undo_requested = on_undo_requested
        self.on_redo_requested = on_redo_requested
        self.on_cut_selection_requested = on_cut_selection_requested
        self.on_copy_selection_requested = on_copy_selection_requested
        self.on_paste_selection_requested = on_paste_selection_requested
        self.on_delete_selection_requested = on_delete_selection_requested
        self.on_crop_rotate_requested = on_crop_rotate_requested
        self.on_crop_rotate_selection_requested = on_crop_rotate_selection_requested
        self.on_add_video_requested = on_add_video_requested
        self.on_selection_changed = on_selection_changed
        self.on_play_pause = on_play_pause
        self.on_step_1s = on_step_1s
        self.on_step_frame = on_step_frame
        self.on_jump_segment = on_jump_segment
        self.on_structure_changed = on_structure_changed
        self.get_segment_thumbnails = get_segment_thumbnails
        self._can_paste = False
        self.btn_add: Optional[Gtk.Button] = None
        self._is_drop_active: bool = False

        self._cards: Dict[str, SegmentCard] = {}
        self._current_time = 0.0
        self._is_scrubbing = False
        self._is_playing = False
        self._active_scrub_seg_id: Optional[str] = None

        self.add_css_class("timeline-container")

        # 1. Exact Time readout, transport controls, and edit buttons bar
        self._build_time_bar()

        # 2. Scrollable storyboard filmstrip
        self._build_filmstrip_container()

    def _build_time_bar(self) -> None:
        time_bar = Gtk.CenterBox()
        time_bar.add_css_class("time-bar")

        # 1. Left (Start): Edit controls (Undo, Redo, Cut, Copy, Paste, Delete)
        edit_controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        edit_controls.set_halign(Gtk.Align.START)
        edit_controls.set_valign(Gtk.Align.CENTER)

        # Undo / Redo group
        undo_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        undo_box.add_css_class("linked")

        self.btn_undo = Gtk.Button.new_from_icon_name("edit-undo-symbolic")
        self.btn_undo.set_tooltip_text(_("Undo (Ctrl+Z)"))
        self.btn_undo.set_focusable(False)
        self.btn_undo.set_sensitive(False)
        if self.on_undo_requested:
            self.btn_undo.connect("clicked", lambda _: self.on_undo_requested())
        undo_box.append(self.btn_undo)

        self.btn_redo = Gtk.Button.new_from_icon_name("edit-redo-symbolic")
        self.btn_redo.set_tooltip_text(_("Redo (Ctrl+Shift+Z or Ctrl+Y)"))
        self.btn_redo.set_focusable(False)
        self.btn_redo.set_sensitive(False)
        if self.on_redo_requested:
            self.btn_redo.connect("clicked", lambda _: self.on_redo_requested())
        undo_box.append(self.btn_redo)

        edit_controls.append(undo_box)

        # Cut, Copy, Paste, Delete group
        clipboard_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        clipboard_box.add_css_class("linked")

        self.btn_cut = Gtk.Button.new_from_icon_name("edit-cut-symbolic")
        self.btn_cut.set_tooltip_text(_("Cut clip (Ctrl+X)"))
        self.btn_cut.set_focusable(False)
        self.btn_cut.set_sensitive(False)
        if self.on_cut_selection_requested:
            self.btn_cut.connect("clicked", lambda _: self.on_cut_selection_requested())
        clipboard_box.append(self.btn_cut)

        self.btn_copy = Gtk.Button.new_from_icon_name("edit-copy-symbolic")
        self.btn_copy.set_tooltip_text(_("Copy clip (Ctrl+C)"))
        self.btn_copy.set_focusable(False)
        self.btn_copy.set_sensitive(False)
        if self.on_copy_selection_requested:
            self.btn_copy.connect("clicked", lambda _: self.on_copy_selection_requested())
        clipboard_box.append(self.btn_copy)

        self.btn_paste = Gtk.Button.new_from_icon_name("edit-paste-symbolic")
        self.btn_paste.set_tooltip_text(_("Paste clip (Ctrl+V)"))
        self.btn_paste.set_focusable(False)
        self.btn_paste.set_sensitive(False)
        if self.on_paste_selection_requested:
            self.btn_paste.connect("clicked", lambda _: self.on_paste_selection_requested())
        clipboard_box.append(self.btn_paste)

        self.btn_crop_rotate = Gtk.Button.new_from_icon_name("crop-symbolic")
        self.btn_crop_rotate.set_tooltip_text(_("Crop & Rotate (C)"))
        self.btn_crop_rotate.set_focusable(False)
        self.btn_crop_rotate.set_sensitive(False)
        if self.on_crop_rotate_selection_requested:
            self.btn_crop_rotate.connect("clicked", lambda _: self.on_crop_rotate_selection_requested())
        clipboard_box.append(self.btn_crop_rotate)

        self.btn_delete = Gtk.Button.new_from_icon_name("user-trash-symbolic")
        self.btn_delete.set_tooltip_text(_("Delete clip (Delete)"))
        self.btn_delete.set_focusable(False)
        self.btn_delete.set_sensitive(False)
        self.btn_delete.add_css_class("destructive-action")
        if self.on_delete_selection_requested:
            self.btn_delete.connect("clicked", lambda _: self.on_delete_selection_requested())
        clipboard_box.append(self.btn_delete)

        edit_controls.append(clipboard_box)

        time_bar.set_start_widget(edit_controls)

        # 2. Center: Compact transport controls bar
        transport_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        transport_box.add_css_class("transport-box")
        transport_box.set_halign(Gtk.Align.CENTER)
        transport_box.set_valign(Gtk.Align.CENTER)

        # 1. Skip to prev segment
        btn_prev_seg = Gtk.Button.new_from_icon_name("media-skip-backward-symbolic")
        btn_prev_seg.set_tooltip_text(_("Previous segment (Home)"))
        btn_prev_seg.set_focusable(False)
        btn_prev_seg.add_css_class("flat")
        btn_prev_seg.add_css_class("circular")
        btn_prev_seg.connect("clicked", lambda _: self.on_jump_segment(-1))
        transport_box.append(btn_prev_seg)

        # 2. Step back 1s (double arrow)
        btn_back_1s = Gtk.Button.new_from_icon_name("media-seek-backward-symbolic")
        btn_back_1s.set_tooltip_text(_("Step back 1s (Left Arrow)"))
        btn_back_1s.set_focusable(False)
        btn_back_1s.add_css_class("flat")
        btn_back_1s.add_css_class("circular")
        btn_back_1s.connect("clicked", lambda _: self.on_step_1s(-1))
        transport_box.append(btn_back_1s)

        # 3. Previous single frame (single arrow)
        btn_prev_frame = Gtk.Button.new_from_icon_name("go-previous-symbolic")
        btn_prev_frame.set_tooltip_text(_("Previous frame (,)"))
        btn_prev_frame.set_focusable(False)
        btn_prev_frame.add_css_class("flat")
        btn_prev_frame.add_css_class("circular")
        btn_prev_frame.connect("clicked", lambda _: self.on_step_frame(-1))
        transport_box.append(btn_prev_frame)

        # 4. Play / Pause
        self.btn_play_pause = Gtk.Button.new_from_icon_name("media-playback-start-symbolic")
        self.btn_play_pause.set_tooltip_text(_("Play / Pause (Space)"))
        self.btn_play_pause.set_focusable(False)
        self.btn_play_pause.add_css_class("circular")
        self.btn_play_pause.add_css_class("suggested-action")
        self.btn_play_pause.connect("clicked", lambda _: self.on_play_pause())
        transport_box.append(self.btn_play_pause)

        # 5. Next single frame (single arrow)
        btn_next_frame = Gtk.Button.new_from_icon_name("go-next-symbolic")
        btn_next_frame.set_tooltip_text(_("Next frame (.)"))
        btn_next_frame.set_focusable(False)
        btn_next_frame.add_css_class("flat")
        btn_next_frame.add_css_class("circular")
        btn_next_frame.connect("clicked", lambda _: self.on_step_frame(1))
        transport_box.append(btn_next_frame)

        # 6. Step forward 1s (double arrow)
        btn_fwd_1s = Gtk.Button.new_from_icon_name("media-seek-forward-symbolic")
        btn_fwd_1s.set_tooltip_text(_("Step forward 1s (Right Arrow)"))
        btn_fwd_1s.set_focusable(False)
        btn_fwd_1s.add_css_class("flat")
        btn_fwd_1s.add_css_class("circular")
        btn_fwd_1s.connect("clicked", lambda _: self.on_step_1s(1))
        transport_box.append(btn_fwd_1s)

        # 7. Skip to next segment
        btn_next_seg = Gtk.Button.new_from_icon_name("media-skip-forward-symbolic")
        btn_next_seg.set_tooltip_text(_("Next segment (End)"))
        btn_next_seg.set_focusable(False)
        btn_next_seg.add_css_class("flat")
        btn_next_seg.add_css_class("circular")
        btn_next_seg.connect("clicked", lambda _: self.on_jump_segment(1))
        transport_box.append(btn_next_seg)

        time_bar.set_center_widget(transport_box)

        # 3. Right (End): Current time / Total time badge
        end_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        end_box.set_halign(Gtk.Align.END)
        end_box.set_valign(Gtk.Align.CENTER)

        self.thumb_progress_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.thumb_spinner = Gtk.Spinner()
        self.lbl_thumb_progress = Gtk.Label(label=_("Generating thumbnails..."))

        self.lbl_time_display = Gtk.Label(label="00:00.00 / 00:00.00")
        self.lbl_time_display.add_css_class("time-badge")
        self.lbl_time_display.set_halign(Gtk.Align.END)
        self.lbl_time_display.set_valign(Gtk.Align.CENTER)
        end_box.append(self.lbl_time_display)

        time_bar.set_end_widget(end_box)

        self.append(time_bar)

    def show_thumbnail_progress(self, completed: int, total: int) -> None:
        """Deprecated: Progress is now shown via floating toast notification in MainWindow."""
        pass

    def hide_thumbnail_progress(self) -> None:
        """Deprecated: Progress is now shown via floating toast notification in MainWindow."""
        pass

    def set_playing_icon(self, is_playing: bool) -> None:
        """Update play/pause icon in transport bar."""
        self._is_playing = is_playing
        icon_name = "media-playback-pause-symbolic" if is_playing else "media-playback-start-symbolic"
        self.btn_play_pause.set_icon_name(icon_name)

    def set_can_paste(self, can_paste: bool) -> None:
        """Update paste availability across all segment cards and toolbar."""
        self._can_paste = can_paste
        self.btn_paste.set_sensitive(can_paste)
        for card in self._cards.values():
            if hasattr(card, "btn_paste"):
                card.btn_paste.set_sensitive(can_paste)

    def _build_filmstrip_container(self) -> None:
        self.scrolled_window = Gtk.ScrolledWindow()
        self.scrolled_window.set_policy(Gtk.PolicyType.EXTERNAL, Gtk.PolicyType.NEVER)
        self.scrolled_window.set_overlay_scrolling(True)
        self.scrolled_window.set_min_content_height(115)
        self.scrolled_window.add_css_class("filmstrip-scroll")
        self.scrolled_window.set_overflow(Gtk.Overflow.VISIBLE)

        self.cards_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.cards_box.add_css_class("cards-box")
        self.cards_box.set_valign(Gtk.Align.CENTER)
        self.cards_box.set_overflow(Gtk.Overflow.VISIBLE)
        self.cards_box.set_margin_start(0)
        self.cards_box.set_margin_end(0)

        self.scrolled_window.set_child(self.cards_box)

        # Global filmstrip overlay hosting the floating split pill above all cards
        self.filmstrip_overlay = Gtk.Overlay()
        self.filmstrip_overlay.set_overflow(Gtk.Overflow.VISIBLE)
        self.filmstrip_overlay.set_child(self.scrolled_window)

        # Floating Split Pill Button positioned above all cards in filmstrip_overlay
        self.split_pill = Gtk.Button()
        self.split_pill.add_css_class("playhead-split-btn")
        self.split_pill.set_cursor_from_name("pointer")
        self.split_pill.set_tooltip_text(_("Split clip at cursor"))
        self.split_pill.set_focusable(False)
        self.split_pill.set_visible(False)
        self.split_pill.set_halign(Gtk.Align.START)
        self.split_pill.set_valign(Gtk.Align.START)
        self.split_pill.set_size_request(-1, 24)

        pill_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        split_icon = Gtk.Image.new_from_icon_name("video-split-symbolic")
        split_icon.set_pixel_size(12)
        split_icon.add_css_class("split-pill-icon")
        split_lbl = Gtk.Label(label=_("Split"))
        split_lbl.add_css_class("split-pill-text")
        pill_box.append(split_icon)
        pill_box.append(split_lbl)
        self.split_pill.set_child(pill_box)
        self.split_pill.connect("clicked", lambda _: self._on_split_pill_clicked())

        self._split_pill_motion = Gtk.EventControllerMotion.new()
        self._split_pill_motion.connect("enter", lambda *_: self._cancel_split_timer())
        self._split_pill_motion.connect("motion", lambda *_: self._cancel_split_timer())
        self._split_pill_motion.connect("leave", lambda *_: self._schedule_split_close())
        self.split_pill.add_controller(self._split_pill_motion)

        self._split_timer: Optional[int] = None
        self._split_pill_x: int = 0
        self.split_pill.get_margin_start = lambda: self._split_pill_x

        self.filmstrip_overlay.add_overlay(self.split_pill)
        self.append(self.filmstrip_overlay)

        hadj = self.scrolled_window.get_hadjustment()
        if hadj:
            def _on_page_size_changed(*_):
                self.recalculate_layout()
                return False
            hadj.connect("notify::page-size", lambda *_: GLib.idle_add(_on_page_size_changed))

    def recalculate_layout(self, force: bool = False) -> None:
        """Dynamically compute and apply proportional widths and thumbnail counts to all segment cards."""
        if self.timeline.is_empty:
            return

        hadj = self.scrolled_window.get_hadjustment()
        if hadj and hadj.get_page_size() > 0:
            container_w = int(hadj.get_page_size())
        else:
            container_w = self.scrolled_window.get_width()

        if container_w <= 0:
            root = self.get_root()
            if root and root.get_width() > 0:
                container_w = root.get_width() - 32
            else:
                container_w = 980

        if not force and hasattr(self, "_last_layout_width") and abs(container_w - self._last_layout_width) < 2:
            return
        self._last_layout_width = container_w

        add_btn = self.cards_box.get_last_child()
        if add_btn:
            add_btn_width = max(89, add_btn.measure(Gtk.Orientation.HORIZONTAL, -1)[0])
        else:
            add_btn_width = 89

        num_segs = len(self.timeline.segments)
        total_gaps = num_segs * self.cards_box.get_spacing()
        min_card_width = 50
        cards_margin = self.cards_box.get_margin_start() + self.cards_box.get_margin_end()
        available_width = max(min_card_width * num_segs, container_w - add_btn_width - total_gaps - cards_margin)

        widths = compute_segment_widths(self.timeline.segments, available_width, min_width=min_card_width)

        for seg in self.timeline.segments:
            card = self._cards.get(seg.id)
            if card:
                w = widths.get(seg.id, min_card_width)
                card.set_card_width(w)

                # Dynamically update thumbnail count based on card width
                # Enforce a minimum thumbnail width (80px) so small segments show only 1 thumb
                # and thumbnails are never squeezed into tiny, illegible vertical slivers
                filmstrip_w = max(1, w - 14)
                target_thumbs = max(1, filmstrip_w // 80)
                try:
                    if self.get_segment_thumbnails:
                        thumbs = self.get_segment_thumbnails(seg, target_thumbs)
                        if thumbs:
                            card.update_thumbnails(thumbs)
                except Exception:
                    pass

        if hadj and hadj.get_value() != 0:
            hadj.set_value(0)

        if not self._is_scrubbing:
            self.update_playhead(self._current_time)

    def rebuild_filmstrip(self) -> None:
        """Recreate segment cards to reflect timeline changes."""
        child = self.cards_box.get_first_child()
        while child:
            next_child = child.get_next_sibling()
            self.cards_box.remove(child)
            child = next_child

        self._cards.clear()

        # Update time display
        total = self.timeline.total_duration
        self._update_time_label()

        can_delete = len(self.timeline.segments) > 0
        total_segments = len(self.timeline.segments)
        for idx, seg in enumerate(self.timeline.segments):
            card = SegmentCard(
                segment=seg,
                index=idx,
                is_selected=(seg.id == self.timeline.selected_segment_id),
                on_select=self._on_segment_selected,
                on_scrub_to_source_time=self._on_card_scrub,
                on_move_left=self._on_card_move_left,
                on_move_right=self._on_card_move_right,
                on_split=lambda seg_id: self.on_split_requested(),
                on_delete=self._on_card_delete,
                on_copy=self._on_card_copy,
                on_cut=self._on_card_cut,
                on_paste=self._on_card_paste,
                on_crop_rotate=self._on_card_crop_rotate,
                on_drop_reorder=self._on_card_drop_reorder,
                can_delete=can_delete,
                can_paste=self._can_paste,
                can_move_left=(idx > 0),
                can_move_right=(idx < total_segments - 1),
                on_scrub_start=self._on_scrub_start,
                on_scrub_update=self._on_scrub_update,
                on_scrub_end=self._on_scrub_end,
                timeline_widget=self,
            )
            self._cards[seg.id] = card
            self.cards_box.append(card)

        # Append "+ Add Video or Image" button card at the end when timeline has segments
        if not self.timeline.is_empty:
            self.btn_add = Gtk.Button()
            self.btn_add.set_tooltip_text(_("Add Video or Image as Segment"))
            self.btn_add.set_focusable(False)
            self.btn_add.add_css_class("card")
            self.btn_add.add_css_class("add-segment-button")
            if self._is_drop_active:
                self.btn_add.add_css_class("drop-target-active")
            self.btn_add.set_size_request(85, -1)
            self.btn_add.set_hexpand(False)
            btn_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            btn_box.set_valign(Gtk.Align.CENTER)
            btn_box.set_halign(Gtk.Align.CENTER)
            icon = Gtk.Image.new_from_icon_name("list-add-symbolic")
            icon.set_pixel_size(24)
            lbl = Gtk.Label(label=_("Add Video\nor Image"))
            lbl.set_justify(Gtk.Justification.CENTER)
            lbl.add_css_class("caption")
            btn_box.append(icon)
            btn_box.append(lbl)
            self.btn_add.set_child(btn_box)
            self.btn_add.connect("clicked", lambda _: self.on_add_video_requested())
            self.cards_box.append(self.btn_add)
        else:
            self.btn_add = None
        self._last_layout_width = -1
        self.recalculate_layout(force=True)
        def _do_idle_layout():
            self.recalculate_layout(force=True)
            return False
        GLib.idle_add(_do_idle_layout)
        self.update_playhead(self._current_time)

    def _update_time_label(self) -> None:
        cur_str = format_time(self._current_time)
        tot_str = format_time(self.timeline.total_duration)
        self.lbl_time_display.set_label(f"{cur_str} / {tot_str}")

    def update_playhead(self, global_time: float) -> None:
        """Update playhead time display and move the red line on the active clip."""
        if self._is_scrubbing:
            # During interactive drag scrubbing, needle is explicitly managed by _handle_scrub
            return

        self._current_time = clamp(global_time, 0.0, self.timeline.total_duration)
        self._update_time_label()

        if self.timeline.is_empty:
            return

        try:
            active_idx, active_seg, active_source_ts = self.timeline.map_global_time(self._current_time)
        except ValueError:
            return

        # If playing, or if there is no current selection, or selected segment no longer exists:
        # update selection to the active segment
        if (
            self._is_playing
            or not self.timeline.selected_segment_id
            or not self.timeline.get_segment(self.timeline.selected_segment_id)
        ):
            self.timeline.selected_segment_id = active_seg.id

        selected_id = self.timeline.selected_segment_id
        for seg_id, card in self._cards.items():
            card.set_selected(seg_id == selected_id)
            if seg_id == active_seg.id and active_seg.duration > 0:
                offset = active_source_ts - active_seg.start_time
                ratio = clamp(offset / active_seg.duration, 0.0, 1.0)
                card.set_needle_ratio(ratio)
            else:
                card.set_needle_ratio(None)

        if hasattr(self, "split_pill") and self.split_pill.get_visible():
            self._update_split_pill_position()

    def _cancel_split_timer(self) -> None:
        if self._split_timer is not None:
            GLib.source_remove(self._split_timer)
            self._split_timer = None
        for card in self._cards.values():
            if hasattr(card, "_popover_close_timer") and card._popover_close_timer is not None:
                GLib.source_remove(card._popover_close_timer)
                card._popover_close_timer = None

    def _schedule_split_close(self) -> None:
        self._cancel_split_timer()
        self._split_timer = GLib.timeout_add(300, self._do_close_split_pill)

    def _do_close_split_pill(self) -> bool:
        self._split_timer = None
        self.split_pill.set_visible(False)
        return GLib.SOURCE_REMOVE

    def show_split_pill(self) -> None:
        self._cancel_split_timer()
        if not self.split_pill.get_visible():
            self.split_pill.set_visible(True)
        self._update_split_pill_position()

    def hide_split_pill(self) -> None:
        self._cancel_split_timer()
        self.split_pill.set_visible(False)

    def _on_split_pill_clicked(self) -> None:
        self._cancel_split_timer()
        self.split_pill.set_visible(False)
        self.on_split_requested()

    def _get_split_pill_dimensions(self) -> tuple[int, int]:
        """Measure natural dimensions of the split pill button dynamically based on localized text and font size."""
        child = self.split_pill.get_child()
        if child is not None:
            _, box_w, _, _ = child.measure(Gtk.Orientation.HORIZONTAL, -1)
            _, box_h, _, _ = child.measure(Gtk.Orientation.VERTICAL, -1)
            # playhead-split-btn CSS has padding: 1px 6px, border: 1px -> 14px horizontal, 4px vertical
            # and min-height: 20px / target height: 24px
            return (max(50, box_w + 14), max(24, box_h + 4))
        return (50, 24)

    def _update_split_pill_position(self) -> None:
        active_card = None
        for card in self._cards.values():
            if card.needle.get_visible():
                active_card = card
                break

        if not active_card:
            self.split_pill.set_visible(False)
            return

        res, rect = active_card.overlay.compute_bounds(self.filmstrip_overlay)
        if res and rect.get_width() > 0:
            fs_x = rect.get_x()
            fs_y = rect.get_y()
        else:
            fs_x = self.cards_box.get_margin_start()
            for seg in self.timeline.segments:
                if seg.id == active_card.segment.id:
                    break
                c = self._cards.get(seg.id)
                w = c.get_width() if c and c.get_width() > 0 else (getattr(c, "_target_width", 100))
                fs_x += w + self.cards_box.get_spacing()
            fs_x += 6  # card border (2px) + padding (4px)
            fs_y = 54.0

        needle_x = fs_x + active_card.needle.get_margin_start()
        needle_center_x = needle_x + 7

        pill_w, pill_h = self._get_split_pill_dimensions()

        pill_x = max(0, int(round(needle_center_x - (pill_w / 2.0))))
        pill_y = max(4, int(round(fs_y - pill_h - 4)))

        self._split_pill_x = pill_x
        self.split_pill.set_margin_start(pill_x)
        self.split_pill.set_margin_top(pill_y)

    def _set_scrubbing_cursor(self, active: bool) -> None:
        """Keep the two-arrows mouse cursor active across the entire window, timeline, and cards while dragging."""
        root = self.get_root()
        if root:
            native = root.get_native()
            if native:
                surface = native.get_surface()
                if surface:
                    c = Gdk.Cursor.new_from_name("ew-resize", None) if active else None
                    surface.set_cursor(c)
            if active:
                root.set_cursor_from_name("ew-resize")
            else:
                root.set_cursor(None)

        if active:
            self.set_cursor_from_name("ew-resize")
            self.scrolled_window.set_cursor_from_name("ew-resize")
            self.cards_box.set_cursor_from_name("ew-resize")
            for card in self._cards.values():
                card.set_cursor_from_name("ew-resize")
                card.header.set_cursor_from_name("ew-resize")
        else:
            self.set_cursor(None)
            self.scrolled_window.set_cursor(None)
            self.cards_box.set_cursor(None)
            for card in self._cards.values():
                card.set_cursor(None)
                card.header.set_cursor_from_name("grab")

    def _on_scrub_start(self, source_card: SegmentCard, x: float, y: float) -> None:
        self._is_scrubbing = True
        self._active_scrub_seg_id = source_card.segment.id
        self._set_scrubbing_cursor(True)
        self._handle_scrub(source_card, x, y)

    def _on_scrub_update(self, source_card: SegmentCard, cur_x: float, cur_y: float) -> None:
        if not self._is_scrubbing:
            self._is_scrubbing = True
            self._set_scrubbing_cursor(True)
        else:
            root = self.get_root()
            if root:
                native = root.get_native()
                if native:
                    surface = native.get_surface()
                    if surface and surface.get_cursor() is None:
                        surface.set_cursor(Gdk.Cursor.new_from_name("ew-resize", None))
        self._handle_scrub(source_card, cur_x, cur_y)

    def _on_scrub_end(self) -> None:
        self._is_scrubbing = False
        self._active_scrub_seg_id = None
        self._set_scrubbing_cursor(False)

    def _handle_scrub(self, source_card: SegmentCard, cur_x: float, cur_y: float) -> None:
        if self.timeline.is_empty:
            return

        target_seg_id = None
        target_ratio = 0.0

        # 1. Check if the mouse is physically inside any segment card's filmstrip image
        for seg in self.timeline.segments:
            card = self._cards.get(seg.id)
            if not card or not card.filmstrip_box.get_mapped():
                continue

            coords = source_card.overlay.translate_coordinates(card.filmstrip_box, cur_x, cur_y)
            if coords is not None:
                fs_x = coords[0]
                w = card.filmstrip_box.get_width()
                if w <= 0:
                    w = card.get_width() - 14
                if w <= 0:
                    w = 100
                if 0.0 <= fs_x <= float(w):
                    target_seg_id = seg.id
                    target_ratio = clamp(fs_x / float(w), 0.0, 1.0)
                    break

        if target_seg_id is not None:
            # Mouse has reached a segment's image -> jump needle directly under mouse on that segment!
            self._active_scrub_seg_id = target_seg_id
            idx = self.timeline.get_segment_index(target_seg_id)
            seg = self.timeline.segments[idx]
            global_start = self.timeline.get_segment_global_start(idx)
            global_time = global_start + target_ratio * seg.duration
            self._seek_and_position_needle(global_time, target_seg_id, target_ratio)
            return

        # 2. Mouse is in the gap between cards or outside:
        # Keep the needle on the segment the user came from until the mouse physically enters another card
        active_id = self._active_scrub_seg_id or source_card.segment.id
        active_card = self._cards.get(active_id)
        if active_card and active_card.filmstrip_box.get_mapped():
            idx = self.timeline.get_segment_index(active_id)
            if idx != -1:
                seg = self.timeline.segments[idx]
                global_start = self.timeline.get_segment_global_start(idx)
                w = active_card.filmstrip_box.get_width()
                if w <= 0:
                    w = active_card.get_width() - 14
                if w <= 0:
                    w = 100

                coords = source_card.overlay.translate_coordinates(active_card.filmstrip_box, cur_x, cur_y)
                if coords is not None:
                    fs_x = coords[0]
                    if fs_x > w:
                        # To the right of segment image (in gap) -> needle stays at end
                        ratio = 1.0
                        global_time = global_start + seg.duration
                    else:
                        # To the left of segment image (in gap) -> needle stays at start
                        ratio = 0.0
                        global_time = global_start
                else:
                    ratio = 0.0
                    global_time = global_start

                self._seek_and_position_needle(global_time, active_id, ratio)

    def _seek_and_position_needle(self, global_time: float, active_seg_id: str, ratio: float) -> None:
        self._current_time = clamp(global_time, 0.0, self.timeline.total_duration)
        self._update_time_label()

        for s_id, card in self._cards.items():
            if s_id == active_seg_id:
                card.set_needle_ratio(ratio)
            else:
                card.set_needle_ratio(None)

        self.on_seek(self._current_time)

    def _on_segment_selected(self, segment_id: str) -> None:
        self.timeline.selected_segment_id = segment_id
        for s_id, card in self._cards.items():
            card.set_selected(s_id == segment_id)
        self.on_selection_changed(segment_id)

    def _on_card_scrub(self, segment_id: str, source_ts: float) -> None:
        idx = self.timeline.get_segment_index(segment_id)
        if idx != -1:
            seg = self.timeline.segments[idx]
            global_start = self.timeline.get_segment_global_start(idx)
            global_time = clamp(global_start + (source_ts - seg.start_time), 0.0, self.timeline.total_duration)
            self.on_seek(global_time)

    def _on_card_move_left(self, segment_id: str) -> None:
        self.timeline.save_state(self._current_time)
        if self.timeline.move_left(segment_id):
            self.rebuild_filmstrip()
            self.on_structure_changed()

    def _on_card_move_right(self, segment_id: str) -> None:
        self.timeline.save_state(self._current_time)
        if self.timeline.move_right(segment_id):
            self.rebuild_filmstrip()
            self.on_structure_changed()

    def _on_card_delete(self, segment_id: str) -> None:
        self.on_delete_requested(segment_id)

    def _on_card_copy(self, segment_id: str) -> None:
        if self.on_copy_requested:
            self.on_copy_requested(segment_id)

    def _on_card_cut(self, segment_id: str) -> None:
        if self.on_cut_requested:
            self.on_cut_requested(segment_id)

    def _on_card_paste(self, segment_id: str) -> None:
        if self.on_paste_requested:
            self.on_paste_requested(segment_id)

    def _on_card_crop_rotate(self, segment_id: str) -> None:
        if self.on_crop_rotate_requested:
            self.on_crop_rotate_requested(segment_id)

    def _on_card_drop_reorder(self, dragged_id: str, target_id: str) -> None:
        from_idx = self.timeline.get_segment_index(dragged_id)
        to_idx = self.timeline.get_segment_index(target_id)
        if from_idx != -1 and to_idx != -1 and from_idx != to_idx:
            self.timeline.save_state(self._current_time)
            if self.timeline.reorder(from_idx, to_idx):
                self.rebuild_filmstrip()
                self.on_structure_changed()

    def set_drop_active(self, active: bool) -> None:
        """Mark or unmark the '+ Add Video or Image' card as an active drop target."""
        self._is_drop_active = active
        if hasattr(self, "btn_add") and self.btn_add:
            if active:
                self.btn_add.add_css_class("drop-target-active")
            else:
                self.btn_add.remove_css_class("drop-target-active")

