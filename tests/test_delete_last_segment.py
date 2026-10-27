"""Unit tests for deleting the last remaining segment and returning to the empty state."""

from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw

from video_cut.window import MainWindow


def test_delete_last_segment_returns_to_empty_state_and_undo():
    app = Adw.Application(application_id="io.github.thor27.video-cut.test.deletelast")
    err = []

    def run_test(app):
        win = None
        try:
            win = MainWindow(application=app)

            # Initially empty state
            assert win.timeline.is_empty is True
            assert win.player_stack.get_visible_child_name() == "empty"
            assert win.timeline_widget.get_visible() is False
            assert win.top_actions_box.get_visible() is True
            assert win.btn_export.get_visible() is False
            assert win.btn_delete.get_sensitive() is False
            assert win.btn_cut.get_sensitive() is False
            assert win.btn_export.get_sensitive() is False

            # Load 1 video
            win.load_video(Path("/tmp/test_video.mp4"))
            assert len(win.timeline.segments) == 1
            assert win.player_stack.get_visible_child_name() == "player"
            assert win.timeline_widget.get_visible() is True
            assert win.top_actions_box.get_visible() is False
            assert win.btn_export.get_visible() is True
            assert win.btn_delete.get_sensitive() is True
            assert win.btn_cut.get_sensitive() is True
            assert win.btn_export.get_sensitive() is True

            # Delete the only/last segment
            seg_id = win.timeline.segments[0].id
            win.timeline.selected_segment_id = seg_id
            win.delete_selected_segment()

            # Verify return to empty state
            assert len(win.timeline.segments) == 0
            assert win.timeline.is_empty is True
            assert win.timeline.selected_segment_id is None
            assert win.player_stack.get_visible_child_name() == "empty"
            assert win.timeline_widget.get_visible() is False
            assert win.top_actions_box.get_visible() is True
            assert win.btn_export.get_visible() is False
            assert win.btn_delete.get_sensitive() is False
            assert win.btn_cut.get_sensitive() is False
            assert win.btn_export.get_sensitive() is False
            assert win.btn_undo.get_sensitive() is True
            assert win.top_btn_undo.get_sensitive() is True
            assert win.timeline_widget.btn_add is None
            assert win.timeline_widget.cards_box.get_first_child() is None

            # Undo restores the segment
            win.undo()
            assert len(win.timeline.segments) == 1
            assert win.timeline.is_empty is False
            assert win.player_stack.get_visible_child_name() == "player"
            assert win.timeline_widget.get_visible() is True
            assert win.top_actions_box.get_visible() is False
            assert win.btn_export.get_visible() is True
            assert win.btn_delete.get_sensitive() is True
            assert win.btn_cut.get_sensitive() is True
            assert win.btn_redo.get_sensitive() is True
            assert win.timeline_widget.btn_add is not None

            # Redo returns to empty state
            win.redo()
            assert len(win.timeline.segments) == 0
            assert win.timeline.is_empty is True
            assert win.player_stack.get_visible_child_name() == "empty"
            assert win.timeline_widget.btn_add is None
            assert win.timeline_widget.cards_box.get_first_child() is None

            # Undo again to test cut
            win.undo()
            assert len(win.timeline.segments) == 1
            assert win.player_stack.get_visible_child_name() == "player"
            assert win.timeline_widget.btn_add is not None

            # Cut the last remaining segment
            win.timeline.selected_segment_id = win.timeline.segments[0].id
            win.cut_selected_segment()
            assert len(win.timeline.segments) == 0
            assert win.timeline.is_empty is True
            assert win.player_stack.get_visible_child_name() == "empty"
            assert win._clipboard_segment is not None
            assert win.timeline_widget.btn_add is None
            assert win.timeline_widget.cards_box.get_first_child() is None

        except Exception as e:
            err.append(e)
        finally:
            if win:
                win.close()
            app.quit()

    app.connect("activate", run_test)
    app.run(["test"])
    if err:
        raise err[0]
