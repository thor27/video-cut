"""Smoke test for UI components and MainWindow operations."""

import sys
from pathlib import Path
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib

from video_cut.window import MainWindow


def test_ui_smoke():
    app = Adw.Application(application_id="io.github.thomaz.videocut.test")

    def run_test(app):
        win = None
        try:
            # When initialized, empty status page is displayed
            win = MainWindow(application=app)
            assert win.player_stack.get_visible_child_name() == "empty"
            assert win.timeline.is_empty is True

            # Verify top bar contains ONLY Open and Export buttons
            header_buttons = []
            child = win.header_bar.get_first_child()
            while child:
                def collect_buttons(w):
                    if isinstance(w, Gtk.Button):
                        header_buttons.append(w)
                    c = w.get_first_child() if hasattr(w, "get_first_child") else None
                    while c:
                        collect_buttons(c)
                        c = c.get_next_sibling()
                collect_buttons(child)
                child = child.get_next_sibling()

            action_buttons = [b for b in header_buttons if b in (win.btn_export,) or "open" in (b.get_tooltip_text() or "").lower()]
            assert len(action_buttons) == 2, f"Expected only Open and Export buttons in header, found: {action_buttons}"

            # Verify bottom timeline bar has the edit buttons and NO 'Timeline' label
            time_bar_labels = []
            def collect_labels(w):
                if isinstance(w, Gtk.Label):
                    time_bar_labels.append(w.get_label())
                c = w.get_first_child() if hasattr(w, "get_first_child") else None
                while c:
                    collect_labels(c)
                    c = c.get_next_sibling()
            collect_labels(win.timeline_widget)
            assert "Timeline" not in time_bar_labels, "Text 'Timeline' should be removed"

            # Verify edit buttons are hosted in timeline_widget
            assert win.timeline_widget.btn_undo == win.btn_undo
            assert win.timeline_widget.btn_redo == win.btn_redo
            assert win.timeline_widget.btn_cut == win.btn_cut
            assert win.timeline_widget.btn_copy == win.btn_copy
            assert win.timeline_widget.btn_paste == win.btn_paste
            assert win.timeline_widget.btn_delete == win.btn_delete

            win.load_video(Path("/tmp/test_video.mp4"))
            assert win.player_stack.get_visible_child_name() == "player"

            # Check that 1 segment is loaded
            assert len(win.timeline.segments) == 1
            assert abs(win.timeline.total_duration - 5.0) < 0.1

            # Verify single segment CANNOT be deleted!
            win.timeline.selected_segment_id = win.timeline.segments[0].id
            win.delete_selected_segment()
            assert len(win.timeline.segments) == 1

            # Simulate moving playhead to 2.0s and splitting
            win.current_global_time = 2.0
            win.split_at_cursor()

            # Should now have 2 segments
            assert len(win.timeline.segments) == 2
            assert abs(win.timeline.segments[0].duration - 2.0) < 0.1
            assert abs(win.timeline.segments[1].duration - 3.0) < 0.1

            # Add second video as segment
            win.load_video(Path("/tmp/test_video2.mp4"), replace=False)
            assert len(win.timeline.segments) == 3

            # Reorder segment
            s3 = win.timeline.segments[2]
            win.timeline.move_left(s3.id)
            assert win.timeline.segments[1].id == s3.id

            # Delete segment (from 3 down to 2)
            win.timeline.selected_segment_id = s3.id
            win.delete_selected_segment()
            assert len(win.timeline.segments) == 2

            # Undo delete!
            assert win.timeline.can_undo is True
            win.undo()
            assert len(win.timeline.segments) == 3

            # Redo delete!
            assert win.timeline.can_redo is True
            win.redo()
            assert len(win.timeline.segments) == 2

            # Delete another segment down to 1
            rem_id = win.timeline.segments[0].id
            win.delete_segment(rem_id)
            assert len(win.timeline.segments) == 1

            # Verify deleting the last remaining segment is blocked
            last_id = win.timeline.segments[0].id
            win.delete_segment(last_id)
            assert len(win.timeline.segments) == 1
        finally:
            if win:
                win.close()
            app.quit()

    app.connect("activate", run_test)
    app.run(["test"])


def test_export_dialog_non_closable():
    app = Adw.Application(application_id="io.github.thomaz.videocut.test.exportclose")

    def run_test(app):
        win = MainWindow(application=app)
        win.load_video(Path("/tmp/test_video.mp4"))

        dialog_holder = []
        orig_present = Adw.Window.present

        def mock_present(self):
            dialog_holder.append(self)

        Adw.Window.present = mock_present

        try:
            win._start_export(Path("/tmp/test_out.mp4"))
            assert len(dialog_holder) == 1
            dialog = dialog_holder[0]

            # 1. Dialog must not have title close buttons or be deletable
            assert dialog.get_deletable() is False

            # 2. close-request signal must be blocked during export
            blocked = dialog.emit("close-request")
            assert blocked is True

            # 3. Find Cancel button and click it to cancel and close
            btn_cancel = None

            def find_btn(w):
                nonlocal btn_cancel
                if isinstance(w, Gtk.Button) and w.get_label() == "Cancel":
                    btn_cancel = w
                child = w.get_first_child() if hasattr(w, "get_first_child") else None
                while child:
                    find_btn(child)
                    child = child.get_next_sibling()

            find_btn(dialog)
            assert btn_cancel is not None

            btn_cancel.emit("clicked")

            # 4. After clicking Cancel, close-request is permitted
            assert dialog.emit("close-request") is False
        finally:
            Adw.Window.present = orig_present
            win.close()
            app.quit()

    app.connect("activate", run_test)
    app.run(["test"])


def test_export_opens_in_default_player(tmp_path):
    app = Adw.Application(application_id="io.github.thomaz.videocut.test.openplayer")

    def run_test(app):
        win = MainWindow(application=app)
        win.load_video(Path("/tmp/test_video.mp4"))

        opened_files = []
        orig_open_player = win._open_in_default_player

        def mock_open(path):
            opened_files.append(path)

        win._open_in_default_player = mock_open

        # Mock export_async to immediately succeed
        def mock_export_async(segments, output_path, on_progress, on_done):
            on_progress(1.0, 5.0)
            on_done(True, None)

        win.export_service.export_async = mock_export_async

        try:
            target_out = tmp_path / "result_video.mp4"
            win._start_export(target_out)

            assert len(opened_files) == 1
            assert opened_files[0] == target_out

            # Test real _open_in_default_player method runs safely
            win._open_in_default_player = orig_open_player
            # Call with a real path - should not raise
            win._open_in_default_player(Path("/tmp/test_video.mp4"))
        finally:
            win.close()
            app.quit()

    app.connect("activate", run_test)
    app.run(["test"])

