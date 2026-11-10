"""Clipboard controller for system clipboard integration and internal segment copying."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import unquote, urlparse

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gdk, Gio, GLib, GObject

from .i18n import _
from .models import Segment
from .utils import clamp

if TYPE_CHECKING:
    from .window import MainWindow


def parse_uri_list(text: str) -> list[Path]:
    """Parse text/uri-list, nautilus clipboard, or newline-separated paths into local Paths."""
    paths: list[Path] = []
    if not text:
        return paths
    for line in text.strip().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("x-special/") or line in ("copy", "cut"):
            continue
        if line.startswith("file://"):
            try:
                parsed = urlparse(line)
                p = Path(unquote(parsed.path))
                if p.exists():
                    paths.append(p)
            except Exception:
                pass
        elif line.startswith("/"):
            try:
                p = Path(line)
                if p.exists():
                    paths.append(p)
            except Exception:
                pass
    return paths


class ClipboardController:
    """Manages clipboard interactions, including internal segment and system clipboard copy/cut/paste."""

    def __init__(self, window: MainWindow) -> None:
        self.window = window
        self.internal_segment: Segment | None = None
        self.internal_segments: list[Segment] = []

    def setup_listener(self) -> None:
        """Listen for system clipboard updates to update paste button sensitivity."""
        try:
            display = Gdk.Display.get_default()
            if display:
                cb = display.get_clipboard()
                cb.connect("changed", self._on_clipboard_changed)
        except Exception as e:
            print(f"Could not connect clipboard listener: {e}")

    def _on_clipboard_changed(self, _clipboard: Gdk.Clipboard) -> None:
        self.window._update_ui_state()

    def has_pasteable_content(self) -> bool:
        """Check if internal clipboard or desktop clipboard contains pasteable media."""
        if self.internal_segments or self.internal_segment is not None:
            return True
        try:
            display = Gdk.Display.get_default()
            if display:
                cb = display.get_clipboard()
                fmts = cb.get_formats()
                if (
                    fmts.contain_gtype(Gdk.Texture)
                    or fmts.contain_gtype(Gdk.FileList)
                    or fmts.contain_mime_type("text/uri-list")
                    or fmts.contain_mime_type("image/png")
                    or fmts.contain_mime_type("image/jpeg")
                ):
                    return True
        except Exception:
            pass
        return False

    def copy_to_system_clipboard(self, source_path: Path) -> None:
        """Export local media file to desktop system clipboard."""
        self.copy_to_system_clipboard_multiple([source_path])

    def copy_to_system_clipboard_multiple(self, source_paths: list[Path]) -> None:
        """Export multiple local media files to desktop system clipboard."""
        try:
            display = Gdk.Display.get_default()
            if not display:
                return
            cb = display.get_clipboard()
            gfiles = [Gio.File.new_for_path(str(p.resolve())) for p in source_paths if p.exists()]
            if gfiles:
                fl = Gdk.FileList.new_from_list(gfiles)
                cp = Gdk.ContentProvider.new_for_value(GObject.Value(Gdk.FileList, fl))
                cb.set_content(cp)
        except Exception as e:
            print(f"Could not copy files to system clipboard: {e}")

    def copy_selected(self) -> None:
        """Copy currently selected segment(s) to internal clipboard and system clipboard."""
        win = self.window
        selected_ids = win.timeline.selected_segment_ids
        if not selected_ids:
            return
        selected = [s.copy() for s in win.timeline.segments if s.id in selected_ids]
        if not selected:
            return
        self.internal_segments = selected
        self.internal_segment = selected[0]
        self.copy_to_system_clipboard_multiple([s.source_path for s in selected])
        win._update_ui_state()

    def cut_selected(self) -> None:
        """Cut currently selected segment(s) to internal clipboard and system clipboard."""
        win = self.window
        selected_ids = set(win.timeline.selected_segment_ids)
        if not selected_ids:
            return
        selected = [s.copy() for s in win.timeline.segments if s.id in selected_ids]
        if not selected:
            return

        self.internal_segments = selected
        self.internal_segment = selected[0]
        self.copy_to_system_clipboard_multiple([s.source_path for s in selected])
        win.timeline.save_state(win.current_global_time)
        if len(selected_ids) == 1:
            win.timeline.delete_segment(selected[0].id)
        else:
            win.timeline.delete_segments(selected_ids)
        win.current_global_time = clamp(win.current_global_time, 0.0, win.timeline.total_duration)
        win.player.sync_timeline_change(win.current_global_time)
        win.timeline_widget.rebuild_filmstrip()
        win._update_ui_state()

    def paste_internal_segment(self) -> None:
        """Paste segments from internal clipboard after current selection."""
        segments_to_paste = (
            self.internal_segments
            if self.internal_segments
            else ([self.internal_segment] if self.internal_segment else [])
        )
        if not segments_to_paste:
            return
        win = self.window
        win.timeline.save_state(win.current_global_time)
        pasted = win.timeline.paste_segments(
            segments_to_paste,
            after_segment_id=win.timeline.selected_segment_id,
        )
        if pasted:
            new_idx = win.timeline.get_segment_index(pasted[0].id)
            win.current_global_time = win.timeline.get_segment_global_start(new_idx)
            win.player.sync_timeline_change(win.current_global_time)
            win.timeline_widget.rebuild_filmstrip()
            win._update_ui_state()

    def paste(self) -> None:
        """Universal paste: paste internal segment, clipboard image, or clipboard files/URIs."""
        display = Gdk.Display.get_default()
        cb = display.get_clipboard() if display else None

        # 1. If internal segment is available and clipboard was set locally
        has_internal = bool(self.internal_segments or self.internal_segment)
        if has_internal and (cb is None or cb.is_local()):
            self.paste_internal_segment()
            return
            self.paste_internal_segment()
            return

        # 2. If system clipboard has content
        if cb is not None:
            fmts = cb.get_formats()

            # A. Texture / Image
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

            # C. Text / URI list
            if (
                fmts.contain_gtype(str)
                or fmts.contain_mime_type("text/uri-list")
                or fmts.contain_mime_type("text/plain")
            ):
                cb.read_text_async(None, self._on_clipboard_text_read)
                return

        # 3. Fallback to internal segment if available
        if self.internal_segments or self.internal_segment:
            self.paste_internal_segment()
            return

        # 4. Nothing on clipboard
        self.window._show_toast(_("Clipboard does not contain any video or image"))

    def _on_clipboard_texture_read(self, cb: Gdk.Clipboard, res: Gio.AsyncResult) -> None:
        try:
            texture = cb.read_texture_finish(res)
            if texture:
                temp_path = self.window._save_texture_to_temp_file(texture)
                self.window.import_media_files([temp_path], replace=self.window.timeline.is_empty)
                return
        except Exception as e:
            print(f"Error reading texture from clipboard: {e}")
        self.window._show_toast(_("Failed to read image from clipboard"))

    def _on_clipboard_file_list_read(self, cb: Gdk.Clipboard, res: Gio.AsyncResult) -> None:
        try:
            val = cb.read_value_finish(res)
            if val:
                paths = []
                for gfile in val.get_files():
                    p = gfile.get_path()
                    if p:
                        paths.append(Path(p))
                if paths:
                    self.window.import_media_files(paths, replace=self.window.timeline.is_empty)
                    return
        except Exception as e:
            print(f"Error reading file list from clipboard: {e}")
        if self.internal_segments or self.internal_segment:
            self.paste_internal_segment()
        else:
            self.window._show_toast(_("Clipboard does not contain any video or image"))

    def _on_clipboard_text_read(self, cb: Gdk.Clipboard, res: Gio.AsyncResult) -> None:
        try:
            text = cb.read_text_finish(res)
            if text:
                paths = parse_uri_list(text)
                if paths:
                    self.window.import_media_files(paths, replace=self.window.timeline.is_empty)
                    return
        except Exception as e:
            print(f"Error reading text/URIs from clipboard: {e}")
        if self.internal_segments or self.internal_segment:
            self.paste_internal_segment()
        else:
            self.window._show_toast(_("Clipboard does not contain any video or image"))
