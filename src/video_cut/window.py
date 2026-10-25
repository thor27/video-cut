import os
import subprocess
from collections.abc import Callable
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from . import __version__
from .advanced_export_dialog import AdvancedExportDialog
from .clipboard_controller import ClipboardController, parse_uri_list
from .crop_rotate_dialog import CropRotateDialog
from .i18n import _
from .image_import_dialog import ImageImportDialog
from .models import ExportSettings, ProjectTimeline, Segment
from .player import VideoPlayerController
from .progress_modal import ProgressModal
from .services.export_service import ExportService
from .services.image_service import ImageService
from .services.probe_service import ProbeService, VideoMetadata
from .services.thumbnail_service import ThumbnailService
from .timeline_widget import TimelineWidget
from .utils import clamp, format_time


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
        self.clipboard_controller = ClipboardController(self)
        self.current_global_time: float = 0.0
        self._thumb_toast: Adw.Toast | None = None
        self._thumb_toast_label: Gtk.Label | None = None

        # Build UI layout
        self._build_ui()
        self._setup_shortcuts()
        self._setup_window_drag_and_drop()
        self._setup_clipboard_listener()
        self._update_ui_state()

    @property
    def _clipboard_segment(self) -> Segment | None:
        return self.clipboard_controller.internal_segment

    @_clipboard_segment.setter
    def _clipboard_segment(self, value: Segment | None) -> None:
        self.clipboard_controller.internal_segment = value

    def _build_ui(self) -> None:
        # Load CSS and icons
        css_provider = Gtk.CssProvider()
        css_path = Path(__file__).parent / "style.css"
        if not css_path.exists():
            snap_dir = os.environ.get("SNAP")
            for candidate in [
                Path(snap_dir) / "usr" / "share" / "video-cut" / "style.css" if snap_dir else None,
                Path(snap_dir) / "share" / "video-cut" / "style.css" if snap_dir else None,
                Path("/usr/share/video-cut/style.css"),
            ]:
                if candidate and candidate.exists():
                    css_path = candidate
                    break

        if css_path and css_path.exists():
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

        # Left controls: Fullscreen toggle and Open
        self.btn_fullscreen = Gtk.Button()
        self.btn_fullscreen.add_css_class("flat")
        self.btn_fullscreen.set_tooltip_text(_("Toggle Fullscreen (F11)"))
        self.btn_fullscreen.set_focusable(False)
        self.fullscreen_icon = Gtk.Image.new_from_icon_name("view-fullscreen-symbolic")
        self.btn_fullscreen.set_child(self.fullscreen_icon)
        self.btn_fullscreen.connect("clicked", lambda _: self.toggle_fullscreen())
        self.header_bar.pack_start(self.btn_fullscreen)
        self.connect("notify::fullscreened", self._on_fullscreened_changed)

        # Empty state top actions: Undo, Redo, Paste in linked button group
        self.top_actions_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.top_actions_box.add_css_class("linked")

        self.top_btn_undo = Gtk.Button.new_from_icon_name("edit-undo-symbolic")
        self.top_btn_undo.set_tooltip_text(_("Undo (Ctrl+Z)"))
        self.top_btn_undo.set_focusable(False)
        self.top_btn_undo.set_sensitive(False)
        self.top_btn_undo.connect("clicked", lambda _: self.undo())
        self.top_actions_box.append(self.top_btn_undo)

        self.top_btn_redo = Gtk.Button.new_from_icon_name("edit-redo-symbolic")
        self.top_btn_redo.set_tooltip_text(_("Redo (Ctrl+Shift+Z or Ctrl+Y)"))
        self.top_btn_redo.set_focusable(False)
        self.top_btn_redo.set_sensitive(False)
        self.top_btn_redo.connect("clicked", lambda _: self.redo())
        self.top_actions_box.append(self.top_btn_redo)

        self.top_btn_paste = Gtk.Button.new_from_icon_name("edit-paste-symbolic")
        self.top_btn_paste.set_tooltip_text(_("Paste clip (Ctrl+V)"))
        self.top_btn_paste.set_focusable(False)
        self.top_btn_paste.set_sensitive(False)
        self.top_btn_paste.connect("clicked", lambda _: self.paste_segment())
        self.top_actions_box.append(self.top_btn_paste)

        self.header_bar.pack_start(self.top_actions_box)

        # Actions for sandwich menu
        self.action_new_window = Gio.SimpleAction.new("new_window", None)
        self.action_new_window.connect("activate", lambda *_: self._create_new_window())
        self.add_action(self.action_new_window)

        self.action_open = Gio.SimpleAction.new("open", None)
        self.action_open.connect("activate", lambda *_: self._open_video_dialog())
        self.add_action(self.action_open)

        self.action_adv_export = Gio.SimpleAction.new("advanced_export", None)
        self.action_adv_export.connect("activate", lambda *_: self._open_advanced_export_dialog())
        self.action_adv_export.set_enabled(False)
        self.add_action(self.action_adv_export)

        self.action_about = Gio.SimpleAction.new("about", None)
        self.action_about.connect("activate", lambda *_: self._show_about_dialog())
        self.add_action(self.action_about)

        self.action_fullscreen = Gio.SimpleAction.new("fullscreen", None)
        self.action_fullscreen.connect("activate", lambda *_: self.toggle_fullscreen())
        self.add_action(self.action_fullscreen)

        # Right controls: Sandwich Menu and Export
        menu = Gio.Menu.new()
        file_section = Gio.Menu.new()
        file_section.append(_("New Window"), "win.new_window")
        file_section.append(_("Open..."), "win.open")
        menu.append_section(None, file_section)

        export_section = Gio.Menu.new()
        export_section.append(_("Export Options..."), "win.advanced_export")
        menu.append_section(None, export_section)

        about_section = Gio.Menu.new()
        about_section.append(_("About Video Cut"), "win.about")
        menu.append_section(None, about_section)

        self.btn_menu = Gtk.MenuButton()
        self.btn_menu.set_icon_name("open-menu-symbolic")
        self.btn_menu.set_tooltip_text(_("Main Menu"))
        self.btn_menu.set_menu_model(menu)
        self.btn_menu.set_focusable(False)
        self.btn_menu.add_css_class("flat")

        # Export button (suggested action)
        self.btn_export = Gtk.Button(label=_("Export..."))
        self.btn_export.set_tooltip_text(_("Export with default options (Ctrl+E)"))
        self.btn_export.set_focusable(False)
        self.btn_export.add_css_class("suggested-action")
        self.btn_export.set_sensitive(False)
        self.btn_export.connect("clicked", lambda _: self._export_video_dialog())

        # In header_bar pack_end, widgets are packed from right to left
        self.header_bar.pack_end(self.btn_menu)
        self.header_bar.pack_end(self.btn_export)

        self.toolbar_view.add_top_bar(self.header_bar)

        # 2. Main content area: Video area and bottom timeline
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.toolbar_view.set_content(main_box)

        # Video Player area using Gtk.Picture (Zero hover controls) with Empty State
        self.player_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.player_container.add_css_class("video-container")
        self.player_container.add_css_class("empty-state")
        self.player_container.set_vexpand(True)
        self.player_container.set_hexpand(True)

        self.player_stack = Gtk.Stack()
        self.player_stack.set_vexpand(True)
        self.player_stack.set_hexpand(True)
        self.player_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)

        # Empty state page
        self.empty_status = Adw.StatusPage()
        self.empty_status.set_icon_name("video-x-generic-symbolic")
        self.empty_status.set_title(_("No Media Loaded"))
        self.empty_status.set_description(
            _("Drag and drop videos or images here, paste from clipboard (Ctrl+V), or click to open")
        )
        self.btn_open_empty = Gtk.Button(label=_("Open Video or Image"))
        self.btn_open_empty.add_css_class("suggested-action")
        self.btn_open_empty.add_css_class("pill")
        self.btn_open_empty.set_halign(Gtk.Align.CENTER)
        self.btn_open_empty.connect("clicked", lambda _: self._open_video_dialog())
        self.empty_status.set_child(self.btn_open_empty)
        self.player_stack.add_named(self.empty_status, "empty")

        # Video Picture page
        self.video_picture = Gtk.Picture.new()
        self.video_picture.set_content_fit(Gtk.ContentFit.CONTAIN)
        self.video_picture.set_vexpand(True)
        self.video_picture.set_hexpand(True)
        self.player_stack.add_named(self.video_picture, "player")

        self.player_container.append(self.player_stack)
        main_box.append(self.player_container)

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

    def _get_segment_thumbnails(self, seg: Segment, target_count: int) -> list[Path | None]:
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

        # Ctrl+N: New Window
        if ctrl and not shift and not alt and keyval in (Gdk.KEY_n, Gdk.KEY_N):
            self._create_new_window()
            return True

        # F11: Toggle Fullscreen
        if keyval == Gdk.KEY_F11:
            self.toggle_fullscreen()
            return True

        # Esc: Exit Fullscreen if in fullscreen mode
        if keyval == Gdk.KEY_Escape and self.is_fullscreen():
            self.unfullscreen()
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

    def _on_segment_selection_changed(self, segment_id: str | None) -> None:
        self._update_ui_state()

    def _setup_clipboard_listener(self) -> None:
        """Listen for system clipboard updates to update paste button sensitivity."""
        self.clipboard_controller.setup_listener()

    def _on_clipboard_changed(self, clipboard: Gdk.Clipboard) -> None:
        self._update_ui_state()

    def _has_pasteable_content(self) -> bool:
        """Check if internal clipboard or desktop clipboard contains pasteable media."""
        return self.clipboard_controller.has_pasteable_content()

    def _update_ui_state(self) -> None:
        has_video = not self.timeline.is_empty
        has_selection = bool(
            self.timeline.selected_segment_id
            and self.timeline.get_segment(self.timeline.selected_segment_id)
        )
        can_cut = has_selection
        can_copy = has_selection
        can_paste = self._has_pasteable_content()
        can_delete = has_selection

        self.btn_export.set_sensitive(has_video)
        self.btn_export.set_visible(has_video)
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

        if hasattr(self, "top_actions_box"):
            self.top_actions_box.set_visible(not has_video)
        if hasattr(self, "top_btn_undo"):
            self.top_btn_undo.set_sensitive(self.timeline.can_undo)
        if hasattr(self, "top_btn_redo"):
            self.top_btn_redo.set_sensitive(self.timeline.can_redo)
        if hasattr(self, "top_btn_paste"):
            self.top_btn_paste.set_sensitive(can_paste)

        self.timeline_widget.set_visible(has_video)

        if has_video:
            self.player_container.remove_css_class("empty-state")
            self.player_stack.set_visible_child_name("player")
            first_seg = self.timeline.segments[0]
            count = len(self.timeline.segments)
            self.window_title.set_title(first_seg.source_name)
            self.window_title.set_subtitle(f"{count} segment{'s' if count > 1 else ''} • Total {format_time(self.timeline.total_duration)}")
        else:
            self.player_container.add_css_class("empty-state")
            self.player_stack.set_visible_child_name("empty")
            self.player.clear()
            self.window_title.set_title(_("Video Cut"))
            self.window_title.set_subtitle(_("Video Editor"))
            self.hide_thumbnail_progress()

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
                    self.import_media_files([media_path], replace=True)
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
                    self.import_media_files([media_path], replace=False)
            except Exception as e:
                print(f"Error adding media: {e}")

        dialog.open(self, None, on_add_finish)

    def _save_texture_to_temp_file(self, texture: Gdk.Texture) -> Path:
        """Save a Gdk.Texture to a temporary PNG file in the project cache directory."""
        cache_dir = self.thumbnail_service.cache_dir / "pasted"
        cache_dir.mkdir(parents=True, exist_ok=True)
        temp_path = cache_dir / f"pasted_image_{int(GLib.get_real_time())}.png"
        texture.save_to_png(str(temp_path))
        return temp_path

    def import_media_files(self, paths: list[Path], replace: bool | None = None) -> None:
        """Import a list of video or image paths, opening if empty or appending if active."""
        if not paths:
            return

        valid_paths = [p for p in paths if p.exists()]
        if not valid_paths:
            self._show_toast(_("No valid media files found"))
            return

        if replace is None:
            replace = self.timeline.is_empty

        # Single image import -> prompt duration & soundtrack dialog
        if len(valid_paths) == 1 and ImageService.is_image(valid_paths[0]):
            self._show_image_import_dialog(valid_paths[0], replace=replace)
            return

        # Sequential batch import
        def import_next(idx: int, count: int) -> None:
            if idx >= len(valid_paths):
                if count == 1:
                    self._show_toast(_("Added '{name}' to timeline").format(name=valid_paths[0].name))
                elif count > 1:
                    self._show_toast(_("Added {count} media clips to timeline").format(count=count))
                return

            media_path = valid_paths[idx]
            is_first = (idx == 0 and replace)

            def on_item_done():
                import_next(idx + 1, count + 1)

            try:
                if ImageService.is_image(media_path):
                    self.load_video(media_path, replace=is_first, duration=3.0, on_done=on_item_done)
                else:
                    self.load_video(media_path, replace=is_first, on_done=on_item_done)
            except Exception as e:
                print(f"Error importing {media_path}: {e}")
                import_next(idx + 1, count)

        import_next(0, 0)

    def _setup_window_drag_and_drop(self) -> None:
        """Enable dragging and dropping video/image files onto the window."""
        drop_target = Gtk.DropTarget()
        drop_target.set_gtypes([Gdk.FileList, Gio.File, Gdk.Texture, str])
        drop_target.set_actions(Gdk.DragAction.COPY)
        drop_target.connect("enter", self._on_window_drag_enter)
        drop_target.connect("leave", self._on_window_drag_leave)
        drop_target.connect("drop", self._on_window_drop)
        self.add_controller(drop_target)

    def _set_drop_highlight(self, active: bool) -> None:
        """Mark or unmark the '+ Add Video or Image' box when dragging media over the window."""
        if hasattr(self, "timeline_widget") and self.timeline_widget:
            self.timeline_widget.set_drop_active(active)
        if hasattr(self, "btn_open_empty") and self.btn_open_empty:
            if active:
                self.btn_open_empty.add_css_class("drop-target-active")
            else:
                self.btn_open_empty.remove_css_class("drop-target-active")

    def _on_window_drag_enter(self, target: Gtk.DropTarget, x: float, y: float) -> Gdk.DragAction:
        self._set_drop_highlight(True)
        return Gdk.DragAction.COPY

    def _on_window_drag_leave(self, target: Gtk.DropTarget) -> None:
        self._set_drop_highlight(False)

    def _on_window_drop(self, target: Gtk.DropTarget, value, x: float, y: float) -> bool:
        self._set_drop_highlight(False)
        paths: list[Path] = []

        if isinstance(value, Gdk.FileList):
            for gfile in value.get_files():
                p = gfile.get_path()
                if p:
                    paths.append(Path(p))
        elif isinstance(value, Gio.File):
            p = value.get_path()
            if p:
                paths.append(Path(p))
        elif isinstance(value, Gdk.Texture):
            try:
                temp_img = self._save_texture_to_temp_file(value)
                paths.append(temp_img)
            except Exception as e:
                print(f"Error saving dropped texture: {e}")
        elif isinstance(value, str):
            # Ignore internal segment reordering drag
            if value.startswith("video-cut-segment:") or (hasattr(self, "timeline") and self.timeline.get_segment(value) is not None):
                return False
            paths = parse_uri_list(value)

        if not paths:
            return False

        self.import_media_files(paths, replace=self.timeline.is_empty)
        return True

    def _show_image_import_dialog(self, image_path: Path, replace: bool) -> None:
        def on_confirm(duration: float, audio_path: Path | None, audio_start: float):
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
        audio_path: Path | str | None = None,
        audio_start_time: float = 0.0,
        on_done: Callable[[], None] | None = None,
    ) -> None:
        """Inspect media metadata and add it to the timeline."""
        if ImageService.is_image(video_path):
            self._load_image_as_video(
                image_path=video_path,
                replace=replace,
                duration=duration,
                audio_path=audio_path,
                audio_start_time=audio_start_time,
                on_done_callback=on_done,
            )
            return

        self._finish_load_video(
            video_path=video_path,
            replace=replace,
            orig_image_path=None,
            display_name=None,
        )
        if on_done:
            on_done()

    def _load_image_as_video(
        self,
        image_path: Path,
        replace: bool = True,
        duration: float = 1.0,
        audio_path: Path | str | None = None,
        audio_start_time: float = 0.0,
        on_done_callback: Callable[[], None] | None = None,
    ) -> None:
        """Convert image to video asynchronously with real progress reporting."""
        display_name = image_path.name

        # If already cached, load immediately without modal
        if ImageService.is_cached(
            image_path=image_path,
            cache_dir=self.thumbnail_service.cache_dir,
            duration=duration,
            audio_path=audio_path,
            audio_start_time=audio_start_time,
        ):
            try:
                converted_video = ImageService.convert_image_to_video(
                    image_path=image_path,
                    cache_dir=self.thumbnail_service.cache_dir,
                    duration=duration,
                    audio_path=audio_path,
                    audio_start_time=audio_start_time,
                )
                thumb_path = ImageService.create_thumbnail(
                    image_path=image_path,
                    thumb_path=converted_video.with_suffix(".thumb.jpg"),
                )
                self._finish_load_video(
                    video_path=converted_video,
                    replace=replace,
                    orig_image_path=thumb_path or image_path,
                    full_image_path=image_path,
                    display_name=display_name,
                )
                if on_done_callback:
                    on_done_callback()
            except Exception as e:
                self._show_toast(_("Failed to convert image: {error}").format(error=e))
                if on_done_callback:
                    on_done_callback()
            return

        cancel_fn: Callable[[], None] | None = None

        modal = ProgressModal(
            parent=self,
            title=_("Importing Image"),
            initial_status=_("Importing image..."),
            initial_details=_("Converting '{name}' into video segment...").format(name=image_path.name),
            on_cancel=lambda: cancel_fn() if cancel_fn else None,
        )
        modal.present()

        def on_progress(fraction: float, cur_s: float) -> None:
            percent = int(fraction * 100)
            if percent >= 100:
                modal.update_progress(
                    1.0,
                    status_text=_("Finalizing clip..."),
                    details_text=_("Preparing timeline and media player..."),
                )
            else:
                modal.update_progress(
                    fraction,
                    status_text=_("Importing image ({percent}%)...").format(percent=percent),
                    details_text=_("Converting frame: {processed} / {total}").format(
                        processed=format_time(cur_s),
                        total=format_time(duration),
                    ),
                )

        def on_done(
            converted_video: Path | None,
            error: Exception | None,
            meta: VideoMetadata | None = None,
            thumb_path: Path | None = None,
        ) -> None:
            if error or not converted_video:
                modal.finish()
                if str(error) != "Conversion cancelled":
                    self._show_toast(_("Failed to convert image: {error}").format(error=error or _("Unknown error")))
                if on_done_callback:
                    on_done_callback()
                return

            modal.update_progress(
                1.0,
                status_text=_("Finalizing clip..."),
                details_text=_("Preparing timeline and media player..."),
            )

            try:
                self._finish_load_video(
                    video_path=converted_video,
                    replace=replace,
                    orig_image_path=thumb_path or image_path,
                    full_image_path=image_path,
                    display_name=display_name,
                    preprobed_meta=meta,
                )
            finally:
                def _do_finish():
                    modal.finish()
                    if on_done_callback:
                        on_done_callback()
                    return False

                GLib.idle_add(_do_finish)

        cancel_fn = ImageService.convert_image_to_video_async(
            image_path=image_path,
            cache_dir=self.thumbnail_service.cache_dir,
            duration=duration,
            audio_path=audio_path,
            audio_start_time=audio_start_time,
            on_progress=on_progress,
            on_done=on_done,
        )

    def _finish_load_video(
        self,
        video_path: Path,
        replace: bool = True,
        orig_image_path: Path | None = None,
        full_image_path: Path | None = None,
        display_name: str | None = None,
        preprobed_meta: VideoMetadata | None = None,
    ) -> None:
        """Inspect media metadata, add to timeline, and start thumbnail generation."""
        meta = preprobed_meta if preprobed_meta is not None else ProbeService.probe(video_path)
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
            self.thumbnail_service.register_image_thumbnail(
                video_path,
                orig_image_path,
                meta.duration,
                full_image_path=full_image_path,
            )
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
        self.timeline_widget.hide_split_pill()
        self.timeline_widget.rebuild_filmstrip()
        self._update_ui_state()

    def delete_selected_segment(self) -> None:
        if self.timeline.selected_segment_id:
            self.delete_segment(self.timeline.selected_segment_id)

    def delete_segment(self, segment_id: str) -> None:
        self.timeline.save_state(self.current_global_time)
        if self.timeline.delete_segment(segment_id):
            self.current_global_time = clamp(self.current_global_time, 0.0, self.timeline.total_duration)
            self.player.sync_timeline_change(self.current_global_time)
            self.timeline_widget.rebuild_filmstrip()
            self._update_ui_state()

    def _copy_to_system_clipboard(self, source_path: Path) -> None:
        """Export local media file to desktop system clipboard."""
        self.clipboard_controller.copy_to_system_clipboard(source_path)

    def copy_selected_segment(self) -> None:
        """Copy currently selected segment to internal clipboard and system clipboard."""
        self.clipboard_controller.copy_selected()

    def cut_selected_segment(self) -> None:
        """Cut currently selected segment to internal clipboard and system clipboard."""
        self.clipboard_controller.cut_selected()

    def paste_segment(self) -> None:
        """Universal paste: paste internal segment, clipboard image, or clipboard files/URIs."""
        self.clipboard_controller.paste()

    def open_crop_rotate_selected_segment(self) -> None:
        """Open Crop & Rotate modal dialog for the selected segment."""
        if not self.timeline.selected_segment_id:
            return
        self.open_crop_rotate_dialog(self.timeline.selected_segment_id)

    def open_crop_rotate_dialog(self, segment_id: str | None = None) -> None:
        """Open Crop & Rotate modal dialog for the given segment ID or selected segment."""
        target_id = segment_id or self.timeline.selected_segment_id
        if not target_id:
            return

        seg = self.timeline.get_segment(target_id)
        if not seg:
            return

        if self.player.is_playing:
            self.player.pause()

        # Check if playhead cursor is within the selected segment
        seg_idx = self.timeline.get_segment_index(seg.id)
        seg_global_start = self.timeline.get_segment_global_start(seg_idx)
        seg_global_end = seg_global_start + seg.duration
        is_cursor_in_segment = (
            seg_global_start - 0.05 <= self.current_global_time <= seg_global_end + 0.05
        )

        player_frame = None
        if is_cursor_in_segment:
            try:
                player_frame = self.player.get_current_frame_pixbuf(self.get_renderer())
            except Exception:
                player_frame = None

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
            thumbnail_service=self.thumbnail_service,
            initial_pixbuf=player_frame,
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

    def _export_video_dialog(self, settings: ExportSettings | None = None) -> None:
        if self.timeline.is_empty:
            return

        dialog = Gtk.FileDialog.new()
        if settings:
            dialog.set_title(_("Export Video"))
        else:
            dialog.set_title(_("Export with Default Options"))

        ext = "mp4"
        if settings:
            ext = settings.container
            if ext == "matroska":
                ext = "mkv"

        # Name based on original video or image name with appropriate extension
        first_seg = self.timeline.segments[0]
        stem = first_seg.stem
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

    def _start_export(self, destination: Path, settings: ExportSettings | None = None) -> None:
        self.player.pause()

        modal = ProgressModal(
            parent=self,
            title=_("Exporting Video"),
            initial_status=_("Rendering video..."),
            initial_details=_("Exporting to {name}...").format(name=destination.name),
            on_cancel=lambda: self.export_service.cancel(),
        )
        modal.present()

        def on_progress(fraction: float, cur_s: float) -> None:
            percent = int(fraction * 100)
            modal.update_progress(
                fraction,
                status_text=_("Rendering video ({percent}%)...").format(percent=percent),
                details_text=_("Processed: {processed} / {total}").format(
                    processed=format_time(cur_s),
                    total=format_time(self.timeline.total_duration),
                ),
            )

        def on_done(success: bool, error: str | None) -> None:
            modal.finish()
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

    def _create_new_window(self) -> "MainWindow":
        """Create and present a new application window."""
        app = self.get_application()
        if app:
            win = MainWindow(application=app)
        else:
            win = MainWindow()
        win.present()
        return win

    def toggle_fullscreen(self) -> None:
        """Toggle fullscreen mode for the window."""
        if self.is_fullscreen():
            self.unfullscreen()
        else:
            self.fullscreen()

    def _on_fullscreened_changed(self, *_) -> None:
        """Update fullscreen button icon and tooltip when window state changes."""
        if self.is_fullscreen():
            self.fullscreen_icon.set_from_icon_name("view-restore-symbolic")
            self.btn_fullscreen.set_tooltip_text(_("Exit Fullscreen (F11 or Esc)"))
        else:
            self.fullscreen_icon.set_from_icon_name("view-fullscreen-symbolic")
            self.btn_fullscreen.set_tooltip_text(_("Toggle Fullscreen (F11)"))

    def _show_about_dialog(self) -> None:
        about = Adw.AboutWindow(
            transient_for=self,
            modal=True,
            application_name=_("Video Cut"),
            application_icon="io.github.thomaz.videocut",
            developer_name="Thomaz Reis",
            version=__version__,
            comments=_(
                "An easy-to-use video editor to cut and merge videos, add images, crop, and rotate"
            ),
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

