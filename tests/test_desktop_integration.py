"""Tests for Desktop Integration: Drag & Drop and Universal Copy & Paste."""

import tempfile
from pathlib import Path
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Gdk, Adw, Gio, GLib, GObject

from video_cut.window import MainWindow, parse_uri_list
from video_cut.models import ProjectTimeline, Segment


def test_parse_uri_list(tmp_path):
    f1 = tmp_path / "video 1.mp4"
    f2 = tmp_path / "video2.mkv"
    f1.write_text("dummy")
    f2.write_text("dummy")

    # Nautilus format
    nautilus_text = f"x-special/nautilus-clipboard\ncopy\n{f1.as_uri()}\n{f2.as_uri()}\n"
    res1 = parse_uri_list(nautilus_text)
    assert len(res1) == 2
    assert res1[0] == f1
    assert res1[1] == f2

    # Standard URI list
    uri_text = f"# comment\n{f1.as_uri()}\r\n{f2.as_uri()}\r\n"
    res2 = parse_uri_list(uri_text)
    assert len(res2) == 2
    assert res2[0] == f1
    assert res2[1] == f2

    # Plain paths
    plain_text = f"{f1}\n{f2}\n/nonexistent/file.mp4"
    res3 = parse_uri_list(plain_text)
    assert len(res3) == 2
    assert res3[0] == f1
    assert res3[1] == f2

    # Empty text
    assert parse_uri_list("") == []


def test_desktop_drag_and_drop(tmp_path):
    v1 = tmp_path / "clip1.mp4"
    v2 = tmp_path / "clip2.mp4"
    v1.write_text("dummy")
    v2.write_text("dummy")

    app = Adw.Application(application_id="io.github.thomaz.videocut.test.dnd")

    def run_test(app):
        win = None
        try:
            win = MainWindow(application=app)

            # 1. Test drag enter / leave CSS classes
            assert not win.has_css_class("window-drop-active")
            action = win._on_window_drag_enter(None, 100.0, 100.0)
            assert action == Gdk.DragAction.COPY
            assert win.has_css_class("window-drop-active")

            win._on_window_drag_leave(None)
            assert not win.has_css_class("window-drop-active")

            # 2. Test drop rejection for internal segment strings
            assert win._on_window_drop(None, "video-cut-segment:seg-1", 0, 0) is False

            # 3. Test drop URI list when timeline is empty -> opens initial video
            assert win.timeline.is_empty is True
            drop_text = f"{v1.as_uri()}\n"
            accepted = win._on_window_drop(None, drop_text, 0, 0)
            assert accepted is True
            assert len(win.timeline.segments) == 1
            assert win.timeline.segments[0].source_path == v1

            # 4. Test drop second file when timeline has active video -> appends
            accepted2 = win._on_window_drop(None, f"{v2.as_uri()}\n", 0, 0)
            assert accepted2 is True
            assert len(win.timeline.segments) == 2
            assert win.timeline.segments[1].source_path == v2

            # 5. Test drop Gdk.FileList
            gfile1 = Gio.File.new_for_path(str(v1))
            fl = Gdk.FileList.new_from_list([gfile1])
            accepted3 = win._on_window_drop(None, fl, 0, 0)
            assert accepted3 is True
            assert len(win.timeline.segments) == 3
        finally:
            if win:
                win.close()
            app.quit()

    app.connect("activate", run_test)
    app.run(["test"])


def test_universal_copy_and_paste(tmp_path):
    v1 = tmp_path / "clip1.mp4"
    v2 = tmp_path / "clip2.mp4"
    v1.write_text("dummy")
    v2.write_text("dummy")

    app = Adw.Application(application_id="io.github.thomaz.videocut.test.clipboard.universal")

    def run_test(app):
        win = None
        try:
            win = MainWindow(application=app)
            win.load_video(v1, replace=True)
            assert len(win.timeline.segments) == 1

            # Select and copy segment
            s1 = win.timeline.segments[0]
            win.timeline.selected_segment_id = s1.id
            win.copy_selected_segment()

            # Internal segment stored
            assert win._clipboard_segment is not None
            assert win._clipboard_segment.id == s1.id

            # System clipboard should now have the file URI
            display = Gdk.Display.get_default()
            cb = display.get_clipboard()
            assert cb is not None

            # Test internal paste duplicates the segment
            win.paste_segment()
            assert len(win.timeline.segments) == 2
            pasted = win.timeline.segments[1]
            assert pasted.id != s1.id
            assert pasted.source_path == s1.source_path

            # Test import_media_files batch
            win.import_media_files([v2], replace=False)
            assert len(win.timeline.segments) == 3
            assert win.timeline.segments[2].source_path == v2

            # Test has_pasteable_content
            assert win._has_pasteable_content() is True
        finally:
            if win:
                win.close()
            app.quit()

    app.connect("activate", run_test)
    app.run(["test"])
