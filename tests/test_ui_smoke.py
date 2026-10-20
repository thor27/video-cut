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

            # Verify single segment CAN be deleted, returning to empty state!
            win.timeline.selected_segment_id = win.timeline.segments[0].id
            win.delete_selected_segment()
            assert len(win.timeline.segments) == 0
            assert win.timeline.is_empty is True
            assert win.player_stack.get_visible_child_name() == "empty"

            # Undo restores the single segment
            assert win.timeline.can_undo is True
            win.undo()
            assert len(win.timeline.segments) == 1
            assert win.player_stack.get_visible_child_name() == "player"

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

            # Verify deleting the last remaining segment transitions to empty state
            last_id = win.timeline.segments[0].id
            win.delete_segment(last_id)
            assert len(win.timeline.segments) == 0
            assert win.timeline.is_empty is True
            assert win.player_stack.get_visible_child_name() == "empty"

            # Undo restores it back
            win.undo()
            assert len(win.timeline.segments) == 1
            assert win.player_stack.get_visible_child_name() == "player"
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

            # Test real _open_in_default_player method runs safely without spawning external app
            win._open_in_default_player = orig_open_player
            import unittest.mock
            with unittest.mock.patch.object(Gtk.FileLauncher, "launch") as mock_launch, \
                 unittest.mock.patch.object(Gio.AppInfo, "launch_default_for_uri") as mock_gio, \
                 unittest.mock.patch("subprocess.Popen") as mock_popen:
                win._open_in_default_player(Path("/tmp/test_video.mp4"))
                assert mock_launch.called or mock_gio.called or mock_popen.called
        finally:
            win.close()
            app.quit()

    app.connect("activate", run_test)
    app.run(["test"])


def test_thumbnail_progress_toast():
    app = Adw.Application(application_id="io.github.thomaz.videocut.test_toast")

    def run_test(app):
        win = MainWindow(application=app)
        try:
            # Initially no toast
            assert win._thumb_toast is None

            # Show initial progress
            win.show_thumbnail_progress(0, 0)
            assert win._thumb_toast is not None
            assert win._thumb_toast.get_timeout() == 0

            # Verify circular close button is hidden
            circular_btns = []
            child = win.toast_overlay.get_first_child()
            while child:
                if child.get_css_name() == "toast" and getattr(child.props, "toast", None) == win._thumb_toast:
                    sub = child.get_first_child()
                    while sub:
                        if isinstance(sub, Gtk.Button) and "circular" in sub.get_css_classes():
                            circular_btns.append(sub)
                        sub = sub.get_next_sibling()
                child = child.get_next_sibling()

            assert len(circular_btns) == 1
            assert circular_btns[0].get_visible() is False

            # Update progress
            win.show_thumbnail_progress(4, 16)
            assert "4/16" in win._thumb_toast_label.get_text()
            assert circular_btns[0].get_visible() is False

            # Hide progress
            win.hide_thumbnail_progress()
            assert win._thumb_toast is None

            # Redundant hide is safe
            win.hide_thumbnail_progress()
        finally:
            if win is not None:
                win.close()
            app.quit()

    app.connect("activate", run_test)
    app.run(["test"])


def test_player_controls_margin_at_minimum_window_size():
    app = Adw.Application(application_id="io.github.thomaz.videocut.test_margin")

    def run_test(app):
        win = MainWindow(application=app)
        try:
            sample_video = Path("/tmp/test_video.mp4")
            if sample_video.exists():
                win.load_video(sample_video, replace=True)

            min_w, _, _, _ = win.measure(Gtk.Orientation.HORIZONTAL, -1)
            min_h, _, _, _ = win.measure(Gtk.Orientation.VERTICAL, -1)
            win.set_default_size(min_w, min_h)
            win.present()

            time_bar = win.timeline_widget.get_first_child()
            start_w = time_bar.get_start_widget()
            center_w = time_bar.get_center_widget()
            end_w = time_bar.get_end_widget()

            # Pump iterations so layout completes
            ctx = GLib.MainContext.default()
            for _ in range(20):
                ctx.iteration(False)

            _, b_start = start_w.compute_bounds(time_bar)
            _, b_center = center_w.compute_bounds(time_bar)
            _, b_end = end_w.compute_bounds(time_bar)

            gap_left = b_center.get_x() - (b_start.get_x() + b_start.get_width())
            gap_right = b_end.get_x() - (b_center.get_x() + b_center.get_width())

            assert gap_left >= 10.0, f"Expected gap_left >= 10px, got {gap_left}px"
            assert gap_right >= 10.0, f"Expected gap_right >= 10px, got {gap_right}px"
        finally:
            if win is not None:
                win.close()
            app.quit()

    app.connect("activate", run_test)
    app.run(["test"])


def test_header_fullscreen_and_sandwich_menu():
    app = Adw.Application(application_id="io.github.thomaz.videocut.test_header")

    def run_test(app):
        win = MainWindow(application=app)
        try:
            # 1. Fullscreen button and actions
            assert isinstance(win.btn_fullscreen, Gtk.Button)
            assert win.fullscreen_icon.get_icon_name() == "view-fullscreen-symbolic"
            assert win.lookup_action("fullscreen") is not None

            # Toggle fullscreen state simulation
            win.toggle_fullscreen()
            # Test _on_fullscreened_changed updates UI
            # When fullscreened is True:
            win.is_fullscreen = lambda: True
            win._on_fullscreened_changed()
            assert win.fullscreen_icon.get_icon_name() == "view-restore-symbolic"

            # When fullscreened is False:
            win.is_fullscreen = lambda: False
            win._on_fullscreened_changed()
            assert win.fullscreen_icon.get_icon_name() == "view-fullscreen-symbolic"

            # 2. Sandwich menu button
            assert isinstance(win.btn_menu, Gtk.MenuButton)
            assert win.btn_menu.get_icon_name() == "open-menu-symbolic"
            menu_model = win.btn_menu.get_menu_model()
            assert menu_model is not None
            assert menu_model.get_n_items() == 3

            sec_file = menu_model.get_item_link(0, "section")
            assert sec_file.get_n_items() == 2
            assert sec_file.get_item_attribute_value(0, "action").get_string() == "win.new_window"
            assert sec_file.get_item_attribute_value(1, "action").get_string() == "win.open"

            sec_export = menu_model.get_item_link(1, "section")
            assert sec_export.get_n_items() == 1
            assert sec_export.get_item_attribute_value(0, "action").get_string() == "win.advanced_export"

            sec_about = menu_model.get_item_link(2, "section")
            assert sec_about.get_n_items() == 1
            assert sec_about.get_item_attribute_value(0, "action").get_string() == "win.about"

            # 3. Create new window
            new_win = win._create_new_window()
            assert isinstance(new_win, MainWindow)
            assert new_win.get_application() == app
            new_win.close()

            # 4. Keyboard shortcut handling
            from gi.repository import Gdk
            # F11 toggles fullscreen
            fullscreen_toggled = []
            win.toggle_fullscreen = lambda: fullscreen_toggled.append(True)
            handled = win._on_key_pressed(None, Gdk.KEY_F11, 0, 0)
            assert handled is True
            assert len(fullscreen_toggled) == 1

            # Esc exits fullscreen when fullscreened
            unfullscreen_called = []
            win.unfullscreen = lambda: unfullscreen_called.append(True)
            win.is_fullscreen = lambda: True
            handled = win._on_key_pressed(None, Gdk.KEY_Escape, 0, 0)
            assert handled is True
            assert len(unfullscreen_called) == 1

            # Esc does not consume when not fullscreened
            win.is_fullscreen = lambda: False
            handled = win._on_key_pressed(None, Gdk.KEY_Escape, 0, 0)
            assert handled is False

            # Ctrl+N creates new window
            new_wins = []
            win._create_new_window = lambda: (new_wins.append(True), win)[1]
            handled = win._on_key_pressed(None, Gdk.KEY_n, 0, Gdk.ModifierType.CONTROL_MASK)
            assert handled is True
            assert len(new_wins) == 1
        finally:
            if win is not None:
                win.close()
            app.quit()

    app.connect("activate", run_test)
    app.run(["test"])


