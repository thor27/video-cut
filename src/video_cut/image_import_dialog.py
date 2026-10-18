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

        self.set_title("Open Image" if is_replace else "Import Image as Segment")
        self.connect("close-request", self._on_close_request)

        self._build_ui()

    @property
    def media_file(self):
        """Backwards compatibility for tests expecting media_file attribute."""
        return self.audio_player

    @media_file.setter
    def media_file(self, val):
        self.audio_player = val

    def _build_ui(self) -> None:
        toolbar_view = Adw.ToolbarView()
        self.set_content(toolbar_view)

        # Header Bar without duplicate window close buttons
        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(False)
        header.set_show_start_title_buttons(False)

        btn_cancel = Gtk.Button(label="Cancel")
        btn_cancel.connect("clicked", lambda _: self.close())
        header.pack_start(btn_cancel)

        confirm_label = "Open Image" if self.is_replace else "Add Segment"
        self.btn_confirm = Gtk.Button(label=confirm_label)
        self.btn_confirm.add_css_class("suggested-action")
        self.btn_confirm.connect("clicked", self._on_confirm_clicked)
        header.pack_end(self.btn_confirm)

        window_title = Adw.WindowTitle(
            title="Import Image Segment",
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
        group.set_title("Segment Duration")
        group.set_description("Specify how long the image appears in the timeline")

        # Row 1: Duration input & formatted time badge
        self.row_duration = Adw.ActionRow()
        self.row_duration.set_title("Duration")
        self.row_duration.set_subtitle("Time in seconds (0.5s – 300.0s)")

        icon = Gtk.Image.new_from_icon_name("preferences-system-time-symbolic")
        self.row_duration.add_prefix(icon)

        dur_controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        dur_controls.set_valign(Gtk.Align.CENTER)

        self.spin_duration = Gtk.SpinButton.new_with_range(0.5, 300.0, 0.5)
        self.spin_duration.set_value(self.duration)
        self.spin_duration.set_digits(1)
        self.spin_duration.connect("value-changed", self._on_duration_changed)
        dur_controls.append(self.spin_duration)

        self.badge_duration = Gtk.Label(label=format_time(self.duration, show_millis=True))
        self.badge_duration.add_css_class("time-badge")
        dur_controls.append(self.badge_duration)

        self.row_duration.add_suffix(dur_controls)
        group.add(self.row_duration)

        # Row 2: Quick duration preset buttons
        self.row_presets = Adw.ActionRow()
        self.row_presets.set_title("Quick Presets")
        self.row_presets.set_subtitle("Select a standard duration")

        icon_presets = Gtk.Image.new_from_icon_name("starred-symbolic")
        self.row_presets.add_prefix(icon_presets)

        presets_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        presets_box.add_css_class("linked")
        presets_box.set_valign(Gtk.Align.CENTER)
        for val in (1.0, 3.0, 5.0, 10.0):
            btn = Gtk.Button(label=f"{int(val)}s")
            btn.connect("clicked", lambda _, v=val: self.spin_duration.set_value(v))
            presets_box.append(btn)
        self.row_presets.add_suffix(presets_box)
        group.add(self.row_presets)

        container.append(group)

    def _build_soundtrack_section(self, container: Gtk.Box) -> None:
        self.soundtrack_group = Adw.PreferencesGroup()
        self.soundtrack_group.set_title("Soundtrack (Optional)")
        self.soundtrack_group.set_description(
            "Attach music or voiceover. If selected, you can slice the exact segment to play."
        )

        # Row 1: When no audio selected
        self.row_no_audio = Adw.ActionRow()
        self.row_no_audio.set_title("No Soundtrack Selected")
        self.row_no_audio.set_subtitle("Video will be generated with silent audio")
        icon_music = Gtk.Image.new_from_icon_name("audio-x-generic-symbolic")
        self.row_no_audio.add_prefix(icon_music)

        btn_choose = Gtk.Button(label="Choose Audio...")
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

        btn_change = Gtk.Button(label="Change...")
        btn_change.connect("clicked", lambda _: self._open_audio_chooser())
        audio_suffix_box.append(btn_change)

        btn_remove = Gtk.Button()
        btn_remove.set_icon_name("edit-delete-symbolic")
        btn_remove.set_tooltip_text("Remove soundtrack")
        btn_remove.add_css_class("flat")
        btn_remove.connect("clicked", lambda _: self._remove_audio())
        audio_suffix_box.append(btn_remove)

        self.row_audio_info.add_suffix(audio_suffix_box)
        self.soundtrack_group.add(self.row_audio_info)

        # Row 3: Audio Slice Window (Full-width slider inside PreferencesRow)
        self.row_audio_slice = Adw.PreferencesRow()
        self.row_audio_slice.set_visible(False)

        slice_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        slice_box.set_margin_top(12)
        slice_box.set_margin_bottom(12)
        slice_box.set_margin_start(16)
        slice_box.set_margin_end(16)

        slice_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        lbl_slice_title = Gtk.Label(label="Start Offset:")
        lbl_slice_title.add_css_class("heading")
        slice_header.append(lbl_slice_title)

        self.lbl_offset_badge = Gtk.Label(label="00:00.00")
        self.lbl_offset_badge.add_css_class("time-badge")
        slice_header.append(self.lbl_offset_badge)

        self.lbl_slice_range = Gtk.Label()
        self.lbl_slice_range.set_hexpand(True)
        self.lbl_slice_range.set_halign(Gtk.Align.END)
        self.lbl_slice_range.add_css_class("dim-label")
        self.lbl_slice_range.add_css_class("caption")
        slice_header.append(self.lbl_slice_range)
        slice_box.append(slice_header)

        self.scale_offset = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0.0, 100.0, 0.1)
        self.scale_offset.set_hexpand(True)
        self.scale_offset.set_draw_value(False)
        self.scale_offset.connect("value-changed", self._on_offset_changed)
        slice_box.append(self.scale_offset)

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

        lbl_prev_title = Gtk.Label(label="Preview Audio Slice")
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
        dialog.set_title("Select Soundtrack Audio")

        filters = Gio.ListStore.new(Gtk.FileFilter)
        filter_audio = Gtk.FileFilter.new()
        filter_audio.set_name("Audio Files (*.mp3, *.wav, *.aac, *.m4a, *.ogg, *.flac, *.opus)")
        filter_audio.add_mime_type("audio/*")
        for ext in ("*.mp3", "*.wav", "*.aac", "*.m4a", "*.ogg", "*.flac", "*.opus", "*.wma"):
            filter_audio.add_pattern(ext)
        filters.append(filter_audio)

        filter_all = Gtk.FileFilter.new()
        filter_all.set_name("All Files")
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
        self.row_audio_info.set_subtitle(f"Total track duration: {format_time(dur, show_millis=False)}")

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
        self.lbl_offset_badge.set_label(format_time(start, show_millis=True))

        if self.audio_duration < self.duration - 0.05:
            desc = (
                f"Plays {format_time(start)} → {format_time(self.audio_duration)} "
                f"(padded with silence to {format_time(self.duration)})"
            )
        else:
            end = start + self.duration
            desc = f"Plays {format_time(start)} → {format_time(end)} ({self.duration:.1f}s)"

        self.lbl_slice_range.set_label(desc)
        self.lbl_preview_time.set_label(f"00:00.00 / {format_time(self.duration)}")

    def _on_duration_changed(self, spin: Gtk.SpinButton) -> None:
        self.duration = spin.get_value()
        self.badge_duration.set_label(format_time(self.duration, show_millis=True))

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
