"""Main application window using Libadwaita."""

import subprocess
from pathlib import Path
from typing import Optional
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Gdk, Adw, GLib, Gio

from .models import ProjectTimeline, Segment, ExportSettings
from .player import VideoPlayerController
from .timeline_widget import TimelineWidget
from .services.probe_service import ProbeService
from .services.thumbnail_service import ThumbnailService
from .services.export_service import ExportService
from .services.image_service import ImageService
from .image_import_dialog import ImageImportDialog
from .crop_rotate_dialog import CropRotateDialog
from .advanced_export_dialog import AdvancedExportDialog
from .utils import format_time, clamp
from .i18n import _


class MainWindow(Adw.ApplicationWindow):
    """Main window for Video Cut application."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)

        self.set_default_size(1050, 750)
        self.set_title("Video Cut")
        self.set_visible(False)

        # Models & Services
        self.timeline = ProjectTimeline()
        self.thumbnail_service = ThumbnailService()
        self.export_service = ExportService()
        self.current_global_time: float = 0.0
        self._clipboard_segment: Optional[Segment] = None
        self._thumb_toast: Optional[Adw.Toast] = None
        self._thumb_toast_label: Optional[Gtk.Label] = None

        # Build UI layout
        self._build_ui()
        self._setup_shortcuts()

    def _build_ui(self) -> None:
        # Load CSS and icons
        css_provider = Gtk.CssProvider()
        css_path = Path(__file__).parent / "style.css"
        if css_path.exists():
            css_provider.load_from_path(str(css_path))
            Gtk.StyleContext.add_provider_for_display(
                Gdk.Display.get_default(),
                css_provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
            )

        res_dir = Path(__file__).parent / "resources"
        if res_dir.exists():
            icon_theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
            icon_theme.add_search_path(str(res_dir))
        Gtk.Window.set_default_icon_name("io.github.thomaz.videocut")
        self.set_icon_name("io.github.thomaz.videocut")

        # ToolbarView layout
        self.toolbar_view = Adw.ToolbarView()
        self.set_content(self.toolbar_view)

        # 1. HeaderBar
        self.header_bar = Adw.HeaderBar()
        self.window_title = Adw.WindowTitle(title=_("Video Cut"), subtitle=_("Video Editor"))
        self.header_bar.set_title_widget(self.window_title)

        # Left controls: App Icon (About) and Open
        btn_app_icon = Gtk.Button()
        btn_app_icon.add_css_class("flat")
        btn_app_icon.set_tooltip_text(_("About Video Cut"))
        btn_app_icon.set_focusable(False)
        app_icon_img = Gtk.Image.new_from_icon_name("io.github.thomaz.videocut")
        app_icon_img.set_pixel_size(24)
        btn_app_icon.set_child(app_icon_img)
        btn_app_icon.connect("clicked", lambda _: self._show_about_dialog())
        self.header_bar.pack_start(btn_app_icon)

        btn_open = Gtk.Button()
        open_content = Adw.ButtonContent(icon_name="document-open-symbolic", label=_("Open"))
        btn_open.set_child(open_content)
        btn_open.set_tooltip_text(_("Open video or image (Ctrl+O)"))
        btn_open.set_focusable(False)
        btn_open.connect("clicked", lambda _: self._open_video_dialog())
        self.header_bar.pack_start(btn_open)

        # Right controls: Export (SplitButton with dropdown for Advanced Export)
        self.btn_export = Adw.SplitButton()
        export_content = Adw.ButtonContent(icon_name="document-save-symbolic", label=_("Export"))
        self.btn_export.set_child(export_content)
        self.btn_export.set_tooltip_text(_("Export combined video (Ctrl+E)"))
        self.btn_export.set_dropdown_tooltip(_("Export options"))
        self.btn_export.set_focusable(False)
        self.btn_export.add_css_class("suggested-action")
        self.btn_export.set_sensitive(False)
        self.btn_export.connect("clicked", lambda _: self._export_video_dialog())

        # Square right-click style menu popover
        self.export_popover = Gtk.Popover()
        self.export_popover.set_has_arrow(False)
        self.export_popover.set_autohide(True)
        self.export_popover.set_position(Gtk.PositionType.BOTTOM)
        self.export_popover.set_halign(Gtk.Align.END)
        self.export_popover.add_css_class("menu")
        self.export_popover.add_css_class("square-menu")

        pop_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        pop_box.set_margin_top(4)
        pop_box.set_margin_bottom(4)
        pop_box.set_margin_start(4)
        pop_box.set_margin_end(4)

        btn_options = Gtk.Button()
        btn_options.add_css_class("flat")
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        icon = Gtk.Image.new_from_icon_name("preferences-system-symbolic")
        icon.set_pixel_size(16)
        row.append(icon)
        lbl = Gtk.Label(label=_("Export options..."), xalign=0)
        lbl.set_hexpand(True)
        row.append(lbl)
        btn_options.set_child(row)

        def on_options_clicked(_):
            self.export_popover.popdown()
            self._open_advanced_export_dialog()

        btn_options.connect("clicked", on_options_clicked)
        pop_box.append(btn_options)

        self.export_popover.set_child(pop_box)
        self.btn_export.set_popover(self.export_popover)

        self.action_adv_export = Gio.SimpleAction.new("advanced_export", None)
        self.action_adv_export.connect("activate", lambda *_: self._open_advanced_export_dialog())
        self.action_adv_export.set_enabled(False)
        self.add_action(self.action_adv_export)

        self.header_bar.pack_end(self.btn_export)

        self.toolbar_view.add_top_bar(self.header_bar)

        # 2. Main content area: Video area and bottom timeline
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.toolbar_view.set_content(main_box)

        # Video Player area using Gtk.Picture (Zero hover controls) with Empty State
        player_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        player_container.add_css_class("video-container")
        player_container.set_vexpand(True)
        player_container.set_hexpand(True)

        self.player_stack = Gtk.Stack()
        self.player_stack.set_vexpand(True)
        self.player_stack.set_hexpand(True)
        self.player_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)

        # Empty state page
        self.empty_status = Adw.StatusPage()
        self.empty_status.set_icon_name("video-x-generic-symbolic")
        self.empty_status.set_title(_("No Media Loaded"))
        self.empty_status.set_description(_("Open a video or image file to begin cutting and arranging clips"))
        btn_open_empty = Gtk.Button(label=_("Open Video or Image"))
        btn_open_empty.add_css_class("suggested-action")
        btn_open_empty.add_css_class("pill")
        btn_open_empty.set_halign(Gtk.Align.CENTER)
        btn_open_empty.connect("clicked", lambda _: self._open_video_dialog())
        self.empty_status.set_child(btn_open_empty)
        self.player_stack.add_named(self.empty_status, "empty")

        # Video Picture page
        self.video_picture = Gtk.Picture.new()
        self.video_picture.set_content_fit(Gtk.ContentFit.CONTAIN)
        self.video_picture.set_vexpand(True)
        self.video_picture.set_hexpand(True)
        self.player_stack.add_named(self.video_picture, "player")

        player_container.append(self.player_stack)
        main_box.append(player_container)

        # 3. Storyboard Timeline (with integrated transport controls and edit buttons)
        self.timeline_widget = TimelineWidget(
            timeline=self.timeline,
            on_seek=self._on_timeline_seek,
            on_split_requested=self.split_at_cursor,
            on_delete_requested=self.delete_segment,
            on_copy_requested=self._on_segment_copy_clicked,
            on_cut_requested=self._on_segment_cut_clicked,
            on_paste_requested=self._on_segment_paste_clicked,
            on_add_video_requested=self._add_video_dialog,
            on_selection_changed=self._on_segment_selection_changed,
            on_play_pause=self._on_toggle_play,
            on_step_1s=lambda d: self.player.step(d * 1.0, self.current_global_time),
            on_step_frame=lambda d: self.player.step_frame(d),
            on_jump_segment=self._on_jump_segment_requested,
            on_structure_changed=self._on_timeline_structure_changed,
            get_segment_thumbnails=self._get_segment_thumbnails,
            on_undo_requested=self.undo,
            on_redo_requested=self.redo,
            on_cut_selection_requested=self.cut_selected_segment,
            on_copy_selection_requested=self.copy_selected_segment,
            on_paste_selection_requested=self.paste_segment,
            on_delete_selection_requested=self.delete_selected_segment,
            on_crop_rotate_requested=self.open_crop_rotate_dialog,
            on_crop_rotate_selection_requested=self.open_crop_rotate_selected_segment,
        )
        self.btn_undo = self.timeline_widget.btn_undo
        self.btn_redo = self.timeline_widget.btn_redo
        self.btn_cut = self.timeline_widget.btn_cut
        self.btn_copy = self.timeline_widget.btn_copy
        self.btn_paste = self.timeline_widget.btn_paste
        self.btn_crop_rotate = self.timeline_widget.btn_crop_rotate
        self.btn_delete = self.timeline_widget.btn_delete
        main_box.append(self.timeline_widget)

        # 4. Player Controller
        self.player = VideoPlayerController(
            timeline=self.timeline,
            video_picture=self.video_picture,
            on_time_update=self._on_player_time_update,
            on_playback_state_changed=self._on_playback_state_changed,
        )

        # Toast overlay for notifications
        self.toast_overlay = Adw.ToastOverlay()
        self.set_content(self.toast_overlay)
        self.toast_overlay.set_child(self.toolbar_view)

        # Listen for window resize to update proportional timeline layout
        def on_realize(widget):
            native = self.get_native()
            surface = native.get_surface() if native else None
            if surface:
                def on_surface_width_changed(surf, pspec):
                    w = surf.get_width()
                    if not hasattr(self, "_last_surface_width") or abs(w - self._last_surface_width) >= 2:
                        self._last_surface_width = w
                        GLib.idle_add(self.timeline_widget.recalculate_layout)

                surface.connect("notify::width", on_surface_width_changed)

        self.connect("realize", on_realize)

        def on_window_close_request(*_):
            if hasattr(self, "player") and self.player:
                self.player.clear()
            return False

        self.connect("close-request", on_window_close_request)

    def _get_segment_thumbnails(self, seg: Segment, target_count: int) -> list[Optional[Path]]:
        return self.thumbnail_service.get_thumbnails_for_segment(
            seg.source_path, seg.start_time, seg.end_time, max_thumbs=target_count
        )

    def _on_toggle_play(self) -> None:
        self.player.toggle_playback()

    def _setup_shortcuts(self) -> None:
        key_ctrl = Gtk.EventControllerKey.new()
        key_ctrl.connect("key-pressed", self._on_key_pressed)
        self.add_controller(key_ctrl)

    def _on_key_pressed(self, controller, keyval, keycode, state) -> bool:
        ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
        alt = bool(state & Gdk.ModifierType.ALT_MASK)
        shift = bool(state & Gdk.ModifierType.SHIFT_MASK)

        # Undo: Ctrl+Z (without shift)
        if ctrl and not shift and keyval in (Gdk.KEY_z, Gdk.KEY_Z):
            self.undo()
            return True

        # Redo: Ctrl+Shift+Z or Ctrl+Y
        if (ctrl and shift and keyval in (Gdk.KEY_z, Gdk.KEY_Z)) or (ctrl and keyval in (Gdk.KEY_y, Gdk.KEY_Y)):
            self.redo()
            return True

        # Space: Play/Pause
        if keyval == Gdk.KEY_space:
            self.player.toggle_playback()
            return True

        # Single frame back: Comma (,)
        if keyval in (Gdk.KEY_comma, Gdk.KEY_less):
            self.player.step_frame(-1)
            return True

        # Single frame forward: Period (.)
        if keyval in (Gdk.KEY_period, Gdk.KEY_greater):
            self.player.step_frame(1)
            return True

        # Jump prev segment: Home or Page_Up
        if keyval in (Gdk.KEY_Home, Gdk.KEY_Page_Up):
            self._on_jump_segment_requested(-1)
            return True

        # Jump next segment: End or Page_Down
        if keyval in (Gdk.KEY_End, Gdk.KEY_Page_Down):
            self._on_jump_segment_requested(1)
            return True

        # Copy: Ctrl+C
        if ctrl and not shift and not alt and keyval in (Gdk.KEY_c, Gdk.KEY_C):
            self.copy_selected_segment()
            return True

        # Cut: Ctrl+X
        if ctrl and not shift and not alt and keyval in (Gdk.KEY_x, Gdk.KEY_X):
            self.cut_selected_segment()
            return True

        # Paste: Ctrl+V
        if ctrl and not shift and not alt and keyval in (Gdk.KEY_v, Gdk.KEY_V):
            self.paste_segment()
            return True

        # Left Arrow: Step back 1s, or Alt+Left: Move clip left
        if keyval == Gdk.KEY_Left:
            if alt and self.timeline.selected_segment_id:
                self.timeline.save_state(self.current_global_time)
                if self.timeline.move_left(self.timeline.selected_segment_id):
                    self.timeline_widget.rebuild_filmstrip()
                    self._update_ui_state()
                return True
            self.player.step(-1.0, self.current_global_time)
            return True

        # Right Arrow: Step forward 1s, or Alt+Right: Move clip right
        if keyval == Gdk.KEY_Right:
            if alt and self.timeline.selected_segment_id:
                self.timeline.save_state(self.current_global_time)
                if self.timeline.move_right(self.timeline.selected_segment_id):
                    self.timeline_widget.rebuild_filmstrip()
                    self._update_ui_state()
                return True
            self.player.step(1.0, self.current_global_time)
            return True

        # Split: S
        if keyval in (Gdk.KEY_s, Gdk.KEY_S) and not ctrl:
            self.split_at_cursor()
            return True

        # Crop & Rotate: C
        if keyval in (Gdk.KEY_c, Gdk.KEY_C) and not ctrl and not alt:
            self.open_crop_rotate_selected_segment()
            return True

        # Delete: Delete or BackSpace
        if keyval in (Gdk.KEY_Delete, Gdk.KEY_BackSpace):
            self.delete_selected_segment()
            return True

        # Ctrl+O: Open
        if ctrl and keyval in (Gdk.KEY_o, Gdk.KEY_O):
            self._open_video_dialog()
            return True

        # Ctrl+E: Export
        if ctrl and keyval in (Gdk.KEY_e, Gdk.KEY_E):
            self._export_video_dialog()
            return True

        return False

    def undo(self) -> None:
        """Undo last timeline change."""
        restored = self.timeline.undo(self.current_global_time)
        if restored is not None:
            self.current_global_time = restored
            self.player.sync_timeline_change(self.current_global_time)
            self.timeline_widget.rebuild_filmstrip()
            self._update_ui_state()

    def redo(self) -> None:
        """Redo undone timeline change."""
        restored = self.timeline.redo(self.current_global_time)
        if restored is not None:
            self.current_global_time = restored
            self.player.sync_timeline_change(self.current_global_time)
            self.timeline_widget.rebuild_filmstrip()
            self._update_ui_state()

    def _on_playback_state_changed(self, is_playing: bool) -> None:
        self.timeline_widget.set_playing_icon(is_playing)

    def _on_player_time_update(self, global_time: float) -> None:
        self.current_global_time = global_time
        self.timeline_widget.update_playhead(global_time)

    def _on_timeline_seek(self, global_time: float) -> None:
        if self.player.is_playing:
            self.player.pause()
        self.current_global_time = global_time
        self.player.seek_to_global_time(global_time)
        self.timeline_widget.update_playhead(global_time)

    def _on_jump_segment_requested(self, direction: int) -> None:
        self.timeline_widget._is_scrubbing = False
        if direction > 0:
            self.player.jump_to_next_segment()
        else:
            self.player.jump_to_prev_segment()
        self.current_global_time = self.player.current_global_time
        try:
            _, act_seg, _ = self.timeline.map_global_time(self.current_global_time)
            self.timeline.selected_segment_id = act_seg.id
        except Exception:
            pass
        self.timeline_widget.update_playhead(self.current_global_time)
        self._update_ui_state()

    def _on_segment_copy_clicked(self, segment_id: str) -> None:
        self.timeline.selected_segment_id = segment_id
        self.copy_selected_segment()

    def _on_segment_cut_clicked(self, segment_id: str) -> None:
        self.timeline.selected_segment_id = segment_id
        self.cut_selected_segment()

    def _on_segment_paste_clicked(self, segment_id: str) -> None:
        self.timeline.selected_segment_id = segment_id
        self.paste_segment()

    def _on_segment_selection_changed(self, segment_id: Optional[str]) -> None:
        self._update_ui_state()

    def _update_ui_state(self) -> None:
        has_video = not self.timeline.is_empty
        has_selection = bool(
            self.timeline.selected_segment_id
            and self.timeline.get_segment(self.timeline.selected_segment_id)
        )
        can_cut = has_selection and len(self.timeline.segments) > 1
        can_copy = has_selection
        can_paste = self._clipboard_segment is not None
        can_delete = has_selection and len(self.timeline.segments) > 1

        self.btn_export.set_sensitive(has_video)
        if hasattr(self, "action_adv_export"):
            self.action_adv_export.set_enabled(has_video)
        self.btn_undo.set_sensitive(self.timeline.can_undo)
        self.btn_redo.set_sensitive(self.timeline.can_redo)
        self.btn_cut.set_sensitive(can_cut)
        self.btn_copy.set_sensitive(can_copy)
        self.btn_paste.set_sensitive(can_paste)
        self.btn_crop_rotate.set_sensitive(has_selection)
        self.btn_delete.set_sensitive(can_delete)
        self.timeline_widget.set_can_paste(can_paste)

        if has_video:
            self.player_stack.set_visible_child_name("player")
            first_seg = self.timeline.segments[0]
            count = len(self.timeline.segments)
            self.window_title.set_title(first_seg.source_name)
            self.window_title.set_subtitle(f"{count} segment{'s' if count > 1 else ''} • Total {format_time(self.timeline.total_duration)}")
        else:
            self.player_stack.set_visible_child_name("empty")
            self.player.clear()
            self.window_title.set_title(_("Video Cut"))
            self.window_title.set_subtitle(_("Video Editor"))

    def _create_media_file_filters(self) -> Gio.ListStore:
        filters = Gio.ListStore.new(Gtk.FileFilter)

        # All supported media
        filter_all = Gtk.FileFilter.new()
        filter_all.set_name(_("All Supported Media (Videos & Images)"))
        filter_all.add_mime_type("video/*")
        filter_all.add_mime_type("image/*")
        for ext in ("*.mp4", "*.mkv", "*.mov", "*.webm", "*.avi", "*.png", "*.jpg", "*.jpeg", "*.webp", "*.bmp", "*.tiff", "*.gif"):
            filter_all.add_pattern(ext)
        filters.append(filter_all)

        # Videos only
        filter_video = Gtk.FileFilter.new()
        filter_video.set_name(_("Video Files"))
        filter_video.add_mime_type("video/*")
        for ext in ("*.mp4", "*.mkv", "*.mov", "*.webm", "*.avi"):
            filter_video.add_pattern(ext)
        filters.append(filter_video)

        # Images only
        filter_image = Gtk.FileFilter.new()
        filter_image.set_name(_("Image Files"))
        filter_image.add_mime_type("image/*")
        for ext in ("*.png", "*.jpg", "*.jpeg", "*.webp", "*.bmp", "*.tiff", "*.gif"):
            filter_image.add_pattern(ext)
        filters.append(filter_image)

        return filters

    def _open_video_dialog(self) -> None:
        dialog = Gtk.FileDialog.new()
        dialog.set_title(_("Open Video or Image"))
        dialog.set_filters(self._create_media_file_filters())

        def on_open_finish(dlg, res):
            try:
                gfile = dlg.open_finish(res)
                if gfile:
                    media_path = Path(gfile.get_path())
                    if ImageService.is_image(media_path):
                        self._show_image_import_dialog(media_path, replace=True)
                    else:
                        self.load_video(media_path, replace=True)
            except Exception as e:
                print(f"Error opening media: {e}")

        dialog.open(self, None, on_open_finish)

    def _add_video_dialog(self) -> None:
        if self.timeline.is_empty:
            self._open_video_dialog()
            return

        dialog = Gtk.FileDialog.new()
        dialog.set_title(_("Add Video or Image as Segment"))
        dialog.set_filters(self._create_media_file_filters())

        def on_add_finish(dlg, res):
            try:
                gfile = dlg.open_finish(res)
                if gfile:
                    media_path = Path(gfile.get_path())
                    if ImageService.is_image(media_path):
                        self._show_image_import_dialog(media_path, replace=False)
                    else:
                        self.load_video(media_path, replace=False)
            except Exception as e:
                print(f"Error adding media: {e}")

        dialog.open(self, None, on_add_finish)

    def _show_image_import_dialog(self, image_path: Path, replace: bool) -> None:
        def on_confirm(duration: float, audio_path: Optional[Path], audio_start: float):
            self.load_video(
                image_path,
                replace=replace,
                duration=duration,
                audio_path=audio_path,
                audio_start_time=audio_start,
            )

        dialog = ImageImportDialog(
            parent=self,
            image_path=image_path,
            is_replace=replace,
            on_confirm=on_confirm,
        )
        dialog.present()

    def load_video(
        self,
        video_path: Path,
        replace: bool = True,
        duration: float = 1.0,
        audio_path: Optional[Path | str] = None,
        audio_start_time: float = 0.0,
    ) -> None:
        """Inspect media metadata and add it to the timeline."""
        display_name = None
        orig_image_path = None
        if ImageService.is_image(video_path):
            orig_image_path = video_path
            display_name = video_path.name
            try:
                converted_video = ImageService.convert_image_to_video(
                    video_path,
                    cache_dir=self.thumbnail_service.cache_dir,
                    duration=duration,
                    audio_path=audio_path,
                    audio_start_time=audio_start_time,
                )
            except Exception as e:
                self._show_toast(_("Failed to convert image: {error}").format(error=e))
                return
            video_path = converted_video

        meta = ProbeService.probe(video_path)
        if not meta or meta.duration <= 0:
            self._show_toast(_("Failed to load media: invalid or unsupported file"))
            return

        if replace:
            self.player.pause()
            self.timeline.clear()
            self.current_global_time = 0.0
        else:
            self.timeline.save_state(self.current_global_time)

        seg = self.timeline.add_video(
            source_path=video_path,
            source_duration=meta.duration,
            insert_after_id=self.timeline.selected_segment_id if not replace else None,
            display_name=display_name,
        )

        if orig_image_path:
            self.thumbnail_service.register_image_thumbnail(video_path, orig_image_path, meta.duration)
            seg.thumbnails = [orig_image_path]
        else:
            def on_first_thumb(path: Path, first_thumb_path: Path):
                self.timeline_widget.recalculate_layout(force=True)

            def on_thumb_progress(completed: int, total: int, thumb_p: Path):
                self.show_thumbnail_progress(completed, total)
                self.timeline_widget.recalculate_layout(force=True)

            if not self.thumbnail_service.is_cached(video_path):
                # Show initial progress immediately
                self.show_thumbnail_progress(0, 0)

            # Trigger background thumbnail generation
            self.thumbnail_service.generate_thumbnails_async(
                source_path=video_path,
                duration=meta.duration,
                on_done=self._on_thumbnails_ready,
                on_first_thumb=on_first_thumb,
                on_progress=on_thumb_progress,
            )

        self.timeline_widget.rebuild_filmstrip()
        self._update_ui_state()

        if replace:
            self.player.load_initial()

    def _on_thumbnails_ready(self, path: Path, items: list) -> None:
        """Called when full thumbnails for a video are ready in cache."""
        if not self.thumbnail_service.has_pending_jobs():
            self.hide_thumbnail_progress()
        self.timeline_widget.recalculate_layout(force=True)

    def split_at_cursor(self) -> None:
        """Split the segment under the playhead cursor."""
        if self.timeline.is_empty:
            return

        # Save state before splitting for Undo
        self.timeline.save_state(self.current_global_time)

        result = self.timeline.split_at(self.current_global_time)
        if result is None:
            # Revert the save if split was rejected
            if self.timeline.can_undo:
                self.timeline.undo_stack.pop()
            self._show_toast(_("Cannot split too close to clip boundary"))
            return

        left, right = result

        # Request extraction of the exact split cut frame if not yet cached
        split_source_time = left.end_time

        def on_split_thumb_ready(thumb_path: Path):
            self.timeline_widget.recalculate_layout(force=True)

        self.thumbnail_service.extract_exact_thumbnail_async(
            left.source_path,
            split_source_time,
            on_ready=on_split_thumb_ready,
        )

        self.player.sync_timeline_change(self.current_global_time)
        self.timeline_widget.rebuild_filmstrip()
        self._update_ui_state()

    def delete_selected_segment(self) -> None:
        if len(self.timeline.segments) <= 1:
            self._show_toast(_("Cannot delete the only remaining segment"))
            return
        if self.timeline.selected_segment_id:
            self.delete_segment(self.timeline.selected_segment_id)

    def delete_segment(self, segment_id: str) -> None:
        if len(self.timeline.segments) <= 1:
            self._show_toast(_("Cannot delete the only remaining segment"))
            return
        self.timeline.save_state(self.current_global_time)
        if self.timeline.delete_segment(segment_id):
            self.current_global_time = clamp(self.current_global_time, 0.0, self.timeline.total_duration)
            self.player.sync_timeline_change(self.current_global_time)
            self.timeline_widget.rebuild_filmstrip()
            self._update_ui_state()

    def copy_selected_segment(self) -> None:
        """Copy the currently selected segment to internal clipboard."""
        if not self.timeline.selected_segment_id:
            return
        seg = self.timeline.get_segment(self.timeline.selected_segment_id)
        if not seg:
            return
        self._clipboard_segment = seg.copy()
        self._update_ui_state()

    def cut_selected_segment(self) -> None:
        """Cut the currently selected segment to internal clipboard."""
        if not self.timeline.selected_segment_id:
            return
        seg = self.timeline.get_segment(self.timeline.selected_segment_id)
        if not seg:
            return
        if len(self.timeline.segments) <= 1:
            self._show_toast(_("Cannot cut the only remaining segment"))
            return

        self._clipboard_segment = seg.copy()
        self.timeline.save_state(self.current_global_time)
        if self.timeline.delete_segment(seg.id):
            self.current_global_time = clamp(self.current_global_time, 0.0, self.timeline.total_duration)
            self.player.sync_timeline_change(self.current_global_time)
            self.timeline_widget.rebuild_filmstrip()
            self._update_ui_state()

    def paste_segment(self) -> None:
        """Paste segment from internal clipboard after the current selection."""
        if not self._clipboard_segment:
            return

        self.timeline.save_state(self.current_global_time)
        pasted = self.timeline.paste_segment(
            self._clipboard_segment,
            after_segment_id=self.timeline.selected_segment_id,
        )
        new_idx = self.timeline.get_segment_index(pasted.id)
        # Position playhead at the start of the newly pasted segment
        self.current_global_time = self.timeline.get_segment_global_start(new_idx)
        self.player.sync_timeline_change(self.current_global_time)
        self.timeline_widget.rebuild_filmstrip()
        self._update_ui_state()

    def open_crop_rotate_selected_segment(self) -> None:
        """Open Crop & Rotate modal dialog for the selected segment."""
        if not self.timeline.selected_segment_id:
            return
        self.open_crop_rotate_dialog(self.timeline.selected_segment_id)

    def open_crop_rotate_dialog(self, segment_id: Optional[str] = None) -> None:
        """Open Crop & Rotate modal dialog for the given segment ID or selected segment."""
        target_id = segment_id or self.timeline.selected_segment_id
        if not target_id:
            return

        seg = self.timeline.get_segment(target_id)
        if not seg:
            return

        if self.player.is_playing:
            self.player.pause()

        def on_apply(crop, rotation, hflip, vflip) -> None:
            self.timeline.save_state(self.current_global_time)
            seg.crop = crop
            seg.rotation = rotation
            seg.hflip = hflip
            seg.vflip = vflip
            self.player.refresh_current_transform()
            self.timeline_widget.rebuild_filmstrip()
            self._update_ui_state()
            self._show_toast(_("Crop & Rotate applied to {name}").format(name=seg.source_name))

        dialog = CropRotateDialog(
            parent=self,
            segment=seg,
            on_apply=on_apply,
            current_time=self.current_global_time,
        )
        dialog.present()

    def _on_timeline_structure_changed(self) -> None:
        self.player.sync_timeline_change(self.current_global_time)
        self._update_ui_state()

    def _open_advanced_export_dialog(self) -> None:
        if self.timeline.is_empty:
            return

        src_w, src_h, src_fps = None, None, None
        try:
            first_seg = self.timeline.segments[0]
            meta = ProbeService.probe(first_seg.source_path)
            if meta:
                src_w, src_h, src_fps = meta.width, meta.height, meta.fps
        except Exception:
            pass

        def on_export(settings: ExportSettings) -> None:
            self._export_video_dialog(settings=settings)

        dialog = AdvancedExportDialog(
            parent=self,
            on_export=on_export,
            source_width=src_w,
            source_height=src_h,
            source_fps=src_fps,
        )
        dialog.present()

    def _export_video_dialog(self, settings: Optional[ExportSettings] = None) -> None:
        if self.timeline.is_empty:
            return

        dialog = Gtk.FileDialog.new()
        dialog.set_title(_("Export Combined Video"))

        ext = "mp4"
        if settings:
            ext = settings.container
            if ext == "matroska":
                ext = "mkv"

        # Name based on original video name with appropriate extension
        first_seg = self.timeline.segments[0]
        stem = first_seg.source_path.stem
        dialog.set_initial_name(f"{stem}_edited.{ext}")

        filters = Gio.ListStore.new(Gtk.FileFilter)
        filter_cur = Gtk.FileFilter.new()
        if ext == "mp4":
            filter_cur.set_name(_("MP4 Video (*.mp4)"))
            filter_cur.add_pattern("*.mp4")
        elif ext == "mkv":
            filter_cur.set_name(_("Matroska Video (*.mkv)"))
            filter_cur.add_pattern("*.mkv")
        elif ext == "webm":
            filter_cur.set_name(_("WebM Video (*.webm)"))
            filter_cur.add_pattern("*.webm")
        elif ext == "mov":
            filter_cur.set_name(_("QuickTime Video (*.mov)"))
            filter_cur.add_pattern("*.mov")
        else:
            filter_cur.set_name(f"{ext.upper()} (*.{ext})")
            filter_cur.add_pattern(f"*.{ext}")
        filters.append(filter_cur)

        filter_all = Gtk.FileFilter.new()
        filter_all.set_name(_("All Files"))
        filter_all.add_pattern("*")
        filters.append(filter_all)

        dialog.set_filters(filters)

        def on_save_finish(dlg, res):
            try:
                gfile = dlg.save_finish(res)
                if gfile:
                    dest = Path(gfile.get_path())
                    if not dest.suffix:
                        dest = dest.with_suffix(f".{ext}")
                    self._start_export(dest, settings=settings)
            except Exception as e:
                print(f"Export file selection cancelled or error: {e}")

        dialog.save(self, None, on_save_finish)

    def _start_export(self, destination: Path, settings: Optional[ExportSettings] = None) -> None:
        self.player.pause()

        # Build Export Dialog Modal (non-closable during export: must wait or click cancel)
        export_dialog = Adw.Window(transient_for=self, modal=True)
        export_dialog.set_default_size(420, 200)
        export_dialog.set_title(_("Exporting Video"))
        export_dialog.set_deletable(False)

        can_close = False

        def on_close_request(win):
            if not can_close:
                return True
            return False

        export_dialog.connect("close-request", on_close_request)

        toolbar_view = Adw.ToolbarView()
        export_dialog.set_content(toolbar_view)

        h_bar = Adw.HeaderBar()
        h_bar.set_show_title(True)
        h_bar.set_show_end_title_buttons(False)
        h_bar.set_show_start_title_buttons(False)
        toolbar_view.add_top_bar(h_bar)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        box.set_margin_top(20)
        box.set_margin_bottom(20)
        box.set_margin_start(24)
        box.set_margin_end(24)
        toolbar_view.set_content(box)

        # Status header with active spinner
        status_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        status_box.set_halign(Gtk.Align.CENTER)
        spinner = Gtk.Spinner()
        spinner.set_size_request(22, 22)
        spinner.start()
        status_box.append(spinner)

        lbl_status = Gtk.Label(label=_("Initializing export pipeline..."))
        lbl_status.add_css_class("heading")
        status_box.append(lbl_status)
        box.append(status_box)

        progress_bar = Gtk.ProgressBar()
        progress_bar.set_show_text(True)
        progress_bar.set_fraction(0.0)
        progress_bar.set_text(_("Starting..."))
        box.append(progress_bar)

        lbl_details = Gtk.Label(label=_("Preparing streams for {name}...").format(name=destination.name))
        lbl_details.add_css_class("caption")
        box.append(lbl_details)

        # Pulse timer while waiting for the first progress percentage
        pulse_timer_id: Optional[int] = None
        has_received_progress = False

        def on_pulse() -> bool:
            if not has_received_progress:
                progress_bar.pulse()
                return True
            return False

        pulse_timer_id = GLib.timeout_add(100, on_pulse)

        def stop_pulse():
            nonlocal pulse_timer_id
            if pulse_timer_id is not None:
                GLib.source_remove(pulse_timer_id)
                pulse_timer_id = None

        btn_cancel = Gtk.Button(label=_("Cancel"))
        btn_cancel.set_halign(Gtk.Align.CENTER)
        btn_cancel.add_css_class("destructive-action")
        btn_cancel.set_focusable(False)
        box.append(btn_cancel)

        def on_cancel_clicked(_):
            nonlocal can_close
            can_close = True
            stop_pulse()
            self.export_service.cancel()
            export_dialog.close()

        btn_cancel.connect("clicked", on_cancel_clicked)

        export_dialog.present()

        def on_progress(fraction: float, cur_s: float) -> None:
            nonlocal has_received_progress
            if fraction > 0.001 and not has_received_progress:
                has_received_progress = True
                stop_pulse()

            if has_received_progress:
                progress_bar.set_fraction(fraction)
                percent = int(fraction * 100)
                progress_bar.set_text(f"{percent}%")
                lbl_status.set_label(_("Rendering video ({percent}%)...").format(percent=percent))
                lbl_details.set_label(_("Processed: {processed} / {total}").format(
                    processed=format_time(cur_s),
                    total=format_time(self.timeline.total_duration),
                ))

        def on_done(success: bool, error: Optional[str]) -> None:
            nonlocal can_close
            can_close = True
            stop_pulse()
            spinner.stop()
            export_dialog.set_deletable(True)
            export_dialog.close()
            if success:
                self._show_toast(_("Export complete: {name}").format(name=destination.name))
                self._open_in_default_player(destination)
            else:
                self._show_toast(_("Export failed: {error}").format(error=error or _("Unknown error")))

        self.export_service.export_async(
            segments=list(self.timeline.segments),
            output_path=destination,
            on_progress=on_progress,
            on_done=on_done,
            settings=settings,
        )

    def _open_in_default_player(self, file_path: Path) -> None:
        """Open the exported video file in the system default media player."""
        # 1. Try GTK FileLauncher (desktop portal integrated)
        try:
            gfile = Gio.File.new_for_path(str(file_path.resolve()))
            launcher = Gtk.FileLauncher.new(gfile)
            launcher.launch(self, None, None)
            return
        except Exception:
            pass

        # 2. Try GIO default URI launcher
        try:
            uri = file_path.resolve().as_uri()
            Gio.AppInfo.launch_default_for_uri(uri, None)
            return
        except Exception:
            pass

        # 3. Fallback to subprocess xdg-open
        try:
            subprocess.Popen(["xdg-open", str(file_path.resolve())])
        except Exception as e:
            print(f"Failed to open video in default player: {e}")

    def _show_about_dialog(self) -> None:
        about = Adw.AboutWindow(
            transient_for=self,
            modal=True,
            application_name=_("Video Cut"),
            application_icon="io.github.thomaz.videocut",
            developer_name="Thomaz",
            version="0.1.0",
            comments=_("A simple, fast, and modern video cutting and segment arranging tool"),
            issue_url="https://github.com/thor27/video-cut/issues",
            license_type=Gtk.License.GPL_3_0,
        )
        about.present()

    def _show_toast(self, text: str, timeout: int = 2) -> None:
        toast = Adw.Toast.new(text)
        toast.set_use_markup(False)
        toast.set_timeout(timeout)
        self.toast_overlay.add_toast(toast)

    def _hide_toast_close_button(self) -> bool:
        """Hide the circular close button specifically on the thumbnail progress toast."""
        if not hasattr(self, "_thumb_toast") or self._thumb_toast is None:
            return False
        child = self.toast_overlay.get_first_child()
        while child:
            if child.get_css_name() == "toast" and getattr(child.props, "toast", None) == self._thumb_toast:
                sub = child.get_first_child()
                while sub:
                    if isinstance(sub, Gtk.Button) and "circular" in sub.get_css_classes():
                        sub.set_visible(False)
                    sub = sub.get_next_sibling()
            child = child.get_next_sibling()
        return False

    def show_thumbnail_progress(self, completed: int, total: int) -> None:
        """Show and update floating progress toast for thumbnail generation without close button."""
        if total > 0:
            text = _("Generating thumbnails ({completed}/{total})...").format(completed=completed, total=total)
        else:
            text = _("Generating thumbnails...")

        if not hasattr(self, "_thumb_toast") or self._thumb_toast is None:
            toast = Adw.Toast.new("")
            toast.set_timeout(0)  # Keep visible while generating

            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            spinner = Gtk.Spinner()
            spinner.set_spinning(True)
            box.append(spinner)

            lbl = Gtk.Label(label=text)
            box.append(lbl)
            toast.set_custom_title(box)

            def on_dismissed(*args):
                self._thumb_toast = None
                self._thumb_toast_label = None

            toast.connect("dismissed", on_dismissed)
            self._thumb_toast = toast
            self._thumb_toast_label = lbl

            self.toast_overlay.add_toast(toast)
            self._hide_toast_close_button()
            GLib.idle_add(self._hide_toast_close_button)
        else:
            if hasattr(self, "_thumb_toast_label") and self._thumb_toast_label is not None:
                self._thumb_toast_label.set_text(text)
            self._hide_toast_close_button()

    def hide_thumbnail_progress(self) -> None:
        """Dismiss floating thumbnail progress toast when generation completes."""
        if hasattr(self, "_thumb_toast") and self._thumb_toast is not None:
            self._thumb_toast.dismiss()
            self._thumb_toast = None
            self._thumb_toast_label = None

