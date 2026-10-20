"""Modal dialog to preview an image, set segment duration, attach optional soundtrack,
slice audio offset, and preview playback before importing into the timeline.
"""

from pathlib import Path
from typing import Callable, Optional
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gst", "1.0")
from gi.repository import Gtk, Adw, GLib, Gio, Gst

try:
    Gst.init(None)
except Exception:
    pass

from .services.probe_service import ProbeService
from .utils import format_time, clamp
from .i18n import _


class AudioPreviewPlayer:
    """Safe, lightweight audio player using GStreamer playbin in pure audio mode.

    Explicitly disables video/cover art streams (GST_PLAY_FLAG_AUDIO = 0x02) to avoid
    upstream GStreamer decodebin3 crashes on MP3 files with embedded album artwork.
    """

    def __init__(self) -> None:
        self.player = Gst.ElementFactory.make("playbin", None)
        if self.player:
            # GST_PLAY_FLAG_AUDIO = 0x02 (audio only, ignores video/subtitles/artwork)
            self.player.set_property("flags", 0x02)
        self._is_playing: bool = False

    def load(self, path: Path) -> None:
        if not self.player:
            return
        self.player.set_state(Gst.State.NULL)
        uri = path.resolve().as_uri()
        self.player.set_property("uri", uri)
        self.player.set_state(Gst.State.PAUSED)
        self.player.get_state(100 * Gst.MSECOND)

    def play(self, start_sec: float) -> None:
        if not self.player:
            return
        self.player.set_state(Gst.State.PAUSED)
        self.player.get_state(100 * Gst.MSECOND)
        seek_ns = int(max(0.0, start_sec) * Gst.SECOND)
        self.player.seek_simple(
            Gst.Format.TIME,
            Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT,
            seek_ns,
        )
        self.player.set_state(Gst.State.PLAYING)
        self._is_playing = True

    def pause(self) -> None:
        if not self.player:
            return
        self.player.set_state(Gst.State.PAUSED)
        self._is_playing = False

    def get_position(self) -> float:
        if not self.player:
            return 0.0
        ok, pos = self.player.query_position(Gst.Format.TIME)
        return (pos / Gst.SECOND) if ok else 0.0

    def cleanup(self) -> None:
        self._is_playing = False
        if self.player:
            self.player.set_state(Gst.State.NULL)
            self.player = None


class ImageImportDialog(Adw.Window):
    """Dialog allowing users to configure an image segment before adding to timeline."""

    def __init__(
        self,
        parent: Gtk.Window,
        image_path: Path,
        is_replace: bool = False,
        on_confirm: Optional[Callable[[float, Optional[Path], float], None]] = None,
    ):
        super().__init__()
        self.set_transient_for(parent)
        self.set_modal(True)
        self.set_destroy_with_parent(True)
        self.set_default_size(580, 720)

        self.image_path = Path(image_path)
        self.is_replace = is_replace
        self.on_confirm = on_confirm

        # State
        self.duration: float = 3.0
        self.audio_path: Optional[Path] = None
        self.audio_duration: float = 0.0
        self.audio_start_time: float = 0.0

        # Audio playback state using safe GStreamer engine
        self.audio_player: Optional[AudioPreviewPlayer] = None
        self._preview_timer_id: Optional[int] = None
        self._is_previewing: bool = False

        self.set_title(_("Import Image"))
        self.connect("close-request", self._on_close_request)

        self._build_ui()

    @property
    def media_file(self):
        """Backwards compatibility for tests expecting media_file attribute."""
        return self.audio_player

    @media_file.setter
    def media_file(self, val):
        self.audio_player = val

    @property
    def lbl_offset_badge(self):
        class _BadgeCompat:
            def __init__(self, entry):
                self._entry = entry
            def get_label(self):
                return self._entry.get_text()
            def set_label(self, val):
                self._entry.set_text(val)
        return _BadgeCompat(self.entry_offset)

    @property
    def badge_duration(self):
        class _BadgeCompat:
            def __init__(self, dialog):
                self._dialog = dialog
            def get_label(self):
                return format_time(self._dialog.duration, show_millis=True)
            def set_label(self, val):
                pass
        return _BadgeCompat(self)

    @property
    def lbl_slice_range(self):
        class _Compat:
            def get_label(self):
                return ""
            def set_label(self, val):
                pass
        return _Compat()

    @staticmethod
    def _parse_time(text: str) -> Optional[float]:
        """Parse time string like '12', '12.5', '12,5', '01:23.45', or '1:02:03' into seconds."""
        if not text:
            return None
        clean = text.strip().rstrip("sS").replace(",", ".").strip()
        parts = clean.split(":")
        try:
            if len(parts) == 1:
                val = float(parts[0])
                return val if val >= 0 else None
            elif len(parts) == 2:
                mins = int(parts[0])
                secs = float(parts[1])
                return mins * 60.0 + secs if mins >= 0 and secs >= 0 else None
            elif len(parts) == 3:
                hrs = int(parts[0])
                mins = int(parts[1])
                secs = float(parts[2])
                return hrs * 3600.0 + mins * 60.0 + secs if hrs >= 0 and mins >= 0 and secs >= 0 else None
        except (ValueError, TypeError):
            return None
        return None

    def _build_ui(self) -> None:
        toolbar_view = Adw.ToolbarView()
        self.set_content(toolbar_view)

        # Header Bar without duplicate window close buttons
        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(False)
        header.set_show_start_title_buttons(False)

        btn_cancel = Gtk.Button(label=_("Cancel"))
        btn_cancel.connect("clicked", lambda _: self.close())
        header.pack_start(btn_cancel)

        self.btn_confirm = Gtk.Button(label=_("Import"))
        self.btn_confirm.add_css_class("suggested-action")
        self.btn_confirm.connect("clicked", self._on_confirm_clicked)
        header.pack_end(self.btn_confirm)

        window_title = Adw.WindowTitle(
            title=_("Import Image Segment"),
            subtitle=self.image_path.name,
        )
        header.set_title_widget(window_title)
        toolbar_view.add_top_bar(header)

        # Scrolled content
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        toolbar_view.set_content(scrolled)

        clamp = Adw.Clamp(maximum_size=540, tightening_threshold=480)
        scrolled.set_child(clamp)

        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        content_box.set_margin_top(8)
        content_box.set_margin_bottom(16)
        content_box.set_margin_start(16)
        content_box.set_margin_end(16)
        clamp.set_child(content_box)

        # 1. Image Preview Section
        self._build_image_preview_section(content_box)

        # 2. Duration Settings Section
        self._build_duration_section(content_box)

        # 3. Soundtrack Section (Optional)
        self._build_soundtrack_section(content_box)

    def _build_image_preview_section(self, container: Gtk.Box) -> None:
        frame = Gtk.Frame()
        frame.add_css_class("image-import-preview-frame")

        card_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        card_box.set_margin_top(6)
        card_box.set_margin_bottom(6)
        card_box.set_margin_start(6)
        card_box.set_margin_end(6)

        # Picture widget with constrained height to preserve vertical space
        self.picture = Gtk.Picture.new_for_filename(str(self.image_path))
        self.picture.set_can_shrink(True)
        self.picture.set_content_fit(Gtk.ContentFit.CONTAIN)
        self.picture.set_size_request(-1, 140)
        self.picture.add_css_class("image-preview-pic")
        card_box.append(self.picture)

        # Metadata caption
        meta_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        meta_box.set_halign(Gtk.Align.CENTER)

        lbl_name = Gtk.Label(label=self.image_path.name)
        lbl_name.add_css_class("dim-label")
        lbl_name.add_css_class("caption")
        lbl_name.set_ellipsize(3)  # PANGO_ELLIPSIZE_END
        meta_box.append(lbl_name)

        card_box.append(meta_box)
        frame.set_child(card_box)
        container.append(frame)

    def _build_duration_section(self, container: Gtk.Box) -> None:
        group = Adw.PreferencesGroup()
        group.set_title(_("Segment Duration"))

        self.row_duration = Adw.PreferencesRow()
        self.row_presets = self.row_duration

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        box.set_margin_top(8)
        box.set_margin_bottom(8)
        box.set_margin_start(12)
        box.set_margin_end(12)
        box.set_valign(Gtk.Align.CENTER)
        box.set_hexpand(True)

        # Quick preset buttons: 1s, 3s, 5s, 10s (Left-aligned)
        self.presets_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.presets_box.add_css_class("linked")
        self.presets_box.set_valign(Gtk.Align.CENTER)
        self.presets_box.set_halign(Gtk.Align.START)
        self._preset_buttons: dict[float, Gtk.ToggleButton] = {}
        for val in (1.0, 3.0, 5.0, 10.0):
            btn = Gtk.ToggleButton(label=f"{int(val)}s")
            is_active = abs(self.duration - val) < 0.05
            btn.set_active(is_active)
            if is_active:
                btn.add_css_class("suggested-action")
            btn.connect("clicked", lambda _, v=val: self._on_preset_clicked(v))
            self._preset_buttons[val] = btn
            self.presets_box.append(btn)
        box.append(self.presets_box)

        # Spacer between left presets and right-aligned spin button
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        box.append(spacer)

        # Duration spin button (Right-aligned)
        self.spin_duration = Gtk.SpinButton.new_with_range(0.5, 300.0, 0.5)
        self.spin_duration.set_value(self.duration)
        self.spin_duration.set_digits(1)
        self.spin_duration.set_valign(Gtk.Align.CENTER)
        self.spin_duration.set_halign(Gtk.Align.END)
        self.spin_duration.set_tooltip_text(_("Time in seconds (0.5s – 300.0s)"))
        self.spin_duration.connect("value-changed", self._on_duration_changed)
        box.append(self.spin_duration)

        self.row_duration.set_child(box)
        group.add(self.row_duration)
        container.append(group)

    def _build_soundtrack_section(self, container: Gtk.Box) -> None:
        self.soundtrack_group = Adw.PreferencesGroup()
        self.soundtrack_group.set_title(_("Soundtrack (Optional)"))

        # Row 1: When no audio selected
        self.row_no_audio = Adw.ActionRow()
        self.row_no_audio.set_title(_("No Soundtrack Selected"))
        self.row_no_audio.set_subtitle(_("Video will be generated with no audio"))
        icon_music = Gtk.Image.new_from_icon_name("audio-x-generic-symbolic")
        self.row_no_audio.add_prefix(icon_music)

        btn_choose = Gtk.Button(label=_("Choose Audio..."))
        btn_choose.set_valign(Gtk.Align.CENTER)
        btn_choose.connect("clicked", lambda _: self._open_audio_chooser())
        self.row_no_audio.add_suffix(btn_choose)
        self.soundtrack_group.add(self.row_no_audio)

        # Row 2: When audio IS selected
        self.row_audio_info = Adw.ActionRow()
        self.row_audio_info.set_visible(False)
        self.row_audio_info.add_prefix(Gtk.Image.new_from_icon_name("audio-volume-high-symbolic"))

        audio_suffix_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        audio_suffix_box.set_valign(Gtk.Align.CENTER)

        btn_change = Gtk.Button(label=_("Change..."))
        btn_change.connect("clicked", lambda _: self._open_audio_chooser())
        audio_suffix_box.append(btn_change)

        btn_remove = Gtk.Button()
        btn_remove.set_icon_name("edit-delete-symbolic")
        btn_remove.set_tooltip_text(_("Remove soundtrack"))
        btn_remove.add_css_class("flat")
        btn_remove.connect("clicked", lambda _: self._remove_audio())
        audio_suffix_box.append(btn_remove)

        self.row_audio_info.add_suffix(audio_suffix_box)
        self.soundtrack_group.add(self.row_audio_info)

        # Row 3: Audio Slice Window (Single horizontal row with label, slider, and editable clock entry)
        self.row_audio_slice = Adw.PreferencesRow()
        self.row_audio_slice.set_visible(False)

        slice_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        slice_box.set_margin_top(8)
        slice_box.set_margin_bottom(8)
        slice_box.set_margin_start(16)
        slice_box.set_margin_end(16)
        slice_box.set_valign(Gtk.Align.CENTER)

        lbl_slice_title = Gtk.Label(label=_("Start Offset:"))
        lbl_slice_title.add_css_class("heading")
        lbl_slice_title.set_valign(Gtk.Align.CENTER)
        slice_box.append(lbl_slice_title)

        self.scale_offset = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0.0, 100.0, 0.1)
        self.scale_offset.set_hexpand(True)
        self.scale_offset.set_draw_value(False)
        self.scale_offset.set_valign(Gtk.Align.CENTER)
        self.scale_offset.connect("value-changed", self._on_offset_changed)
        slice_box.append(self.scale_offset)

        self.entry_offset = Gtk.Entry()
        self.entry_offset.set_text("00:00.00")
        self.entry_offset.set_max_width_chars(9)
        self.entry_offset.set_alignment(0.5)
        self.entry_offset.add_css_class("time-badge")
        self.entry_offset.add_css_class("time-entry")
        self.entry_offset.set_valign(Gtk.Align.CENTER)
        self.entry_offset.set_tooltip_text(_("Initial delay offset (e.g. 5.5 or 00:05.50)"))
        self.entry_offset.connect("activate", self._on_entry_offset_activate)

        focus_controller = Gtk.EventControllerFocus.new()
        focus_controller.connect("leave", lambda *_: self._on_entry_offset_activate())
        self.entry_offset.add_controller(focus_controller)

        slice_box.append(self.entry_offset)

        self.row_audio_slice.set_child(slice_box)
        self.soundtrack_group.add(self.row_audio_slice)

        # Row 4: Audio Preview Player (Full-width player inside PreferencesRow)
        self.row_preview = Adw.PreferencesRow()
        self.row_preview.set_visible(False)

        preview_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        preview_container.set_margin_top(12)
        preview_container.set_margin_bottom(12)
        preview_container.set_margin_start(16)
        preview_container.set_margin_end(16)

        lbl_prev_title = Gtk.Label(label=_("Preview Audio Slice"))
        lbl_prev_title.set_halign(Gtk.Align.START)
        lbl_prev_title.add_css_class("heading")
        preview_container.append(lbl_prev_title)

        player_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        player_row.set_valign(Gtk.Align.CENTER)

        self.btn_preview_play = Gtk.Button()
        self.btn_preview_play.set_icon_name("media-playback-start-symbolic")
        self.btn_preview_play.add_css_class("suggested-action")
        self.btn_preview_play.add_css_class("circular")
        self.btn_preview_play.connect("clicked", self._on_preview_play_clicked)
        player_row.append(self.btn_preview_play)

        self.preview_progress = Gtk.ProgressBar()
        self.preview_progress.set_hexpand(True)
        self.preview_progress.set_valign(Gtk.Align.CENTER)
        self.preview_progress.set_fraction(0.0)
        player_row.append(self.preview_progress)

        self.lbl_preview_time = Gtk.Label(label=f"00:00.0 / {format_time(self.duration)}")
        self.lbl_preview_time.add_css_class("time-badge")
        player_row.append(self.lbl_preview_time)

        preview_container.append(player_row)
        self.row_preview.set_child(preview_container)
        self.soundtrack_group.add(self.row_preview)

        container.append(self.soundtrack_group)

    def _open_audio_chooser(self) -> None:
        dialog = Gtk.FileDialog.new()
        dialog.set_title(_("Select Soundtrack Audio"))

        filters = Gio.ListStore.new(Gtk.FileFilter)
        filter_audio = Gtk.FileFilter.new()
        filter_audio.set_name(_("Audio Files (*.mp3, *.wav, *.aac, *.m4a, *.ogg, *.flac, *.opus)"))
        filter_audio.add_mime_type("audio/*")
        for ext in ("*.mp3", "*.wav", "*.aac", "*.m4a", "*.ogg", "*.flac", "*.opus", "*.wma"):
            filter_audio.add_pattern(ext)
        filters.append(filter_audio)

        filter_all = Gtk.FileFilter.new()
        filter_all.set_name(_("All Files"))
        filter_all.add_pattern("*")
        filters.append(filter_all)
        dialog.set_filters(filters)

        def on_finish(dlg, res):
            try:
                gfile = dlg.open_finish(res)
                if gfile:
                    self._set_audio_file(Path(gfile.get_path()))
            except Exception:
                pass

        dialog.open(self, None, on_finish)

    def _set_audio_file(self, audio_path: Path) -> None:
        dur = ProbeService.probe_duration(audio_path)
        if dur is None or dur <= 0:
            return

        self._cleanup_audio()

        self.audio_path = audio_path
        self.audio_duration = dur
        self.audio_start_time = 0.0

        # Update Audio Info Row
        self.row_audio_info.set_title(audio_path.name)
        self.row_audio_info.set_subtitle(_("Total track duration: {duration}").format(duration=format_time(dur, show_millis=False)))

        # Initialize safe GStreamer audio player
        try:
            self.audio_player = AudioPreviewPlayer()
            self.audio_player.load(audio_path)
        except Exception as e:
            print(f"Error loading audio preview: {e}")
            self.audio_player = None

        # Update slider limits and slice labels
        self._update_audio_controls()

        # Update visibility
        self.row_no_audio.set_visible(False)
        self.row_audio_info.set_visible(True)
        self.row_audio_slice.set_visible(True)
        self.row_preview.set_visible(True)

    def _remove_audio(self) -> None:
        self._cleanup_audio()
        self.audio_path = None
        self.audio_duration = 0.0
        self.audio_start_time = 0.0

        self.row_no_audio.set_visible(True)
        self.row_audio_info.set_visible(False)
        self.row_audio_slice.set_visible(False)
        self.row_preview.set_visible(False)

    def _update_audio_controls(self) -> None:
        if self.audio_path is None:
            return

        max_offset = max(0.0, self.audio_duration - self.duration)
        self.scale_offset.set_range(0.0, max(0.01, max_offset))

        if self.audio_start_time > max_offset:
            self.audio_start_time = max_offset
        self.scale_offset.set_value(self.audio_start_time)

        self._update_slice_labels()

    def _update_slice_labels(self) -> None:
        if self.audio_path is None:
            return

        start = self.audio_start_time
        formatted = format_time(start, show_millis=True)
        if hasattr(self, "entry_offset"):
            if not self.entry_offset.has_focus() or self.entry_offset.get_text() != formatted:
                self.entry_offset.set_text(formatted)

        self.lbl_preview_time.set_label(f"00:00.00 / {format_time(self.duration)}")

    def _on_entry_offset_activate(self, *_) -> None:
        if self.audio_path is None:
            return
        text = self.entry_offset.get_text()
        sec = self._parse_time(text)
        max_offset = max(0.0, self.audio_duration - self.duration)
        if sec is not None:
            clamped = clamp(sec, 0.0, max_offset)
            self.audio_start_time = clamped
            self.scale_offset.set_value(clamped)
            self.entry_offset.set_text(format_time(clamped, show_millis=True))
            self._stop_preview(reset=True)
        else:
            self.entry_offset.set_text(format_time(self.audio_start_time, show_millis=True))

    def _on_preset_clicked(self, val: float) -> None:
        self.spin_duration.set_value(val)
        self._update_preset_buttons()

    def _update_preset_buttons(self) -> None:
        if not hasattr(self, "_preset_buttons"):
            return
        curr = round(self.duration, 1)
        for val, btn in self._preset_buttons.items():
            is_active = abs(curr - val) < 0.05
            btn.set_active(is_active)
            if is_active:
                btn.add_css_class("suggested-action")
            else:
                btn.remove_css_class("suggested-action")

    def _on_duration_changed(self, spin: Gtk.SpinButton) -> None:
        self.duration = spin.get_value()
        self._update_preset_buttons()

        if self.audio_path is not None:
            self._stop_preview(reset=True)
            self._update_audio_controls()
        else:
            self.lbl_preview_time.set_label(f"00:00.00 / {format_time(self.duration)}")

    def _on_offset_changed(self, scale: Gtk.Scale) -> None:
        val = scale.get_value()
        max_offset = max(0.0, self.audio_duration - self.duration)
        self.audio_start_time = clamp(val, 0.0, max_offset)

        self._update_slice_labels()
        self._stop_preview(reset=True)

    def _on_preview_play_clicked(self, *_) -> None:
        if self._is_previewing:
            self._stop_preview(reset=False)
        else:
            self._start_preview()

    def _start_preview(self) -> None:
        if not self.audio_player or self.audio_path is None:
            return

        self._is_previewing = True
        self.btn_preview_play.set_icon_name("media-playback-pause-symbolic")

        self.audio_player.play(self.audio_start_time)

        if self._preview_timer_id is not None:
            GLib.source_remove(self._preview_timer_id)
        self._preview_timer_id = GLib.timeout_add(40, self._on_preview_tick)

    def _stop_preview(self, reset: bool = True) -> None:
        self._is_previewing = False
        if self._preview_timer_id is not None:
            GLib.source_remove(self._preview_timer_id)
            self._preview_timer_id = None

        if self.audio_player:
            self.audio_player.pause()

        self.btn_preview_play.set_icon_name("media-playback-start-symbolic")
        if reset:
            self.preview_progress.set_fraction(0.0)
            self.lbl_preview_time.set_label(f"00:00.00 / {format_time(self.duration)}")

    def _on_preview_tick(self) -> bool:
        if not self._is_previewing or not self.audio_player:
            return False

        current_sec = self.audio_player.get_position()
        elapsed = current_sec - self.audio_start_time
        elapsed = max(0.0, elapsed)

        if elapsed >= self.duration:
            self._stop_preview(reset=True)
            return False

        fraction = min(1.0, elapsed / self.duration) if self.duration > 0 else 1.0
        self.preview_progress.set_fraction(fraction)
        self.lbl_preview_time.set_label(f"{format_time(elapsed)} / {format_time(self.duration)}")
        return True

    def _cleanup_audio(self) -> None:
        self._stop_preview(reset=True)
        if self.audio_player:
            self.audio_player.cleanup()
            self.audio_player = None

    def _on_confirm_clicked(self, *_) -> None:
        duration = self.duration
        audio_path = self.audio_path
        audio_start = self.audio_start_time

        self._cleanup_audio()
        self.close()

        if self.on_confirm:
            self.on_confirm(duration, audio_path, audio_start)

    def _on_close_request(self, *_) -> bool:
        self._cleanup_audio()
        return False
