"""Tests for Segment Selection, Copy/Cut/Paste, and video-split-symbolic icon."""

from pathlib import Path
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Gdk, Adw

from video_cut.models import ProjectTimeline, Segment
from video_cut.window import MainWindow
from video_cut.segment_card import SegmentCard


def test_timeline_paste_segment():
    timeline = ProjectTimeline()
    s1 = timeline.add_video(Path("/tmp/clip1.mp4"), 10.0)
    s2 = timeline.add_video(Path("/tmp/clip2.mp4"), 8.0)
    assert len(timeline.segments) == 2

    # Paste after s1
    pasted = timeline.paste_segment(s1, after_segment_id=s1.id)
    assert len(timeline.segments) == 3
    assert timeline.segments[1].id == pasted.id
    assert pasted.id != s1.id
    assert pasted.source_path == s1.source_path
    assert pasted.duration == s1.duration
    assert timeline.selected_segment_id == pasted.id

    # Paste with no after_id -> appended at end
    pasted_end = timeline.paste_segment(s2)
    assert len(timeline.segments) == 4
    assert timeline.segments[-1].id == pasted_end.id
    assert timeline.selected_segment_id == pasted_end.id


def test_clipboard_ui_and_selection():
    app = Adw.Application(application_id="io.github.thomaz.videocut.test.clipboard")

    def run_test(app):
        win = None
        try:
            win = MainWindow(application=app)

            # Check icon registration and presence
            theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
            assert theme.has_icon("video-split-symbolic")

            # Check cut, copy, paste, delete, undo, redo buttons have their symbolic icons
            assert win.btn_undo.get_icon_name() == "edit-undo-symbolic"
            assert win.btn_redo.get_icon_name() == "edit-redo-symbolic"
            assert win.btn_cut.get_icon_name() == "edit-cut-symbolic"
            assert win.btn_copy.get_icon_name() == "edit-copy-symbolic"
            assert win.btn_paste.get_icon_name() == "edit-paste-symbolic"
            assert win.btn_delete.get_icon_name() == "user-trash-symbolic"

            # Load video: initially 1 segment
            win.load_video(Path("/tmp/test_video.mp4"))
            assert len(win.timeline.segments) == 1
            s1 = win.timeline.segments[0]
            assert win.btn_delete.get_sensitive() is False, "Cannot delete only remaining segment"

            # Split into 2 segments using win.btn_split / split_at_cursor
            win.current_global_time = 2.0
            win.split_at_cursor()
            assert len(win.timeline.segments) == 2
            s1 = win.timeline.segments[0]
            s2 = win.timeline.segments[1]
            assert win.btn_delete.get_sensitive() is True, "Can delete when multiple segments exist"

            # Verify SegmentCard header contains ONLY Move Left and Move Right buttons with default cursor
            card1 = win.timeline_widget._cards[s1.id]
            child = card1.header.get_first_child()
            split_btn = None
            has_cut = False
            has_copy = False
            has_paste = False
            has_del = False
            has_move_l = False
            has_move_r = False
            while child:
                if isinstance(child, Gtk.Button):
                    assert child.get_cursor() is not None and child.get_cursor().get_name() == "default"
                    if child.get_icon_name() == "video-split-symbolic":
                        split_btn = child
                    elif child.get_icon_name() == "edit-cut-symbolic":
                        has_cut = True
                    elif child.get_icon_name() == "edit-copy-symbolic":
                        has_copy = True
                    elif child.get_icon_name() == "edit-paste-symbolic":
                        has_paste = True
                    elif child.get_icon_name() == "user-trash-symbolic":
                        has_del = True
                    elif child.get_icon_name() == "go-previous-symbolic":
                        has_move_l = True
                    elif child.get_icon_name() == "go-next-symbolic":
                        has_move_r = True
                child = child.get_next_sibling()
            assert split_btn is None, "Split button must be in HeaderBar edit box, not in SegmentCard header"
            assert not (has_cut or has_copy or has_paste or has_del), "Cut, copy, paste, delete must NOT be in header"
            assert has_move_l and has_move_r, "SegmentCard header must contain move left and move right buttons"

            # Check boundary sensitivity for nav buttons: s1 at index 0 cannot move left
            assert card1.btn_left.get_sensitive() is False
            assert card1.btn_right.get_sensitive() is True

            card2 = win.timeline_widget._cards[s2.id]
            assert card2.btn_left.get_sensitive() is True
            assert card2.btn_right.get_sensitive() is False

            # Verify right click context menu exists on card with all actions
            assert hasattr(card1, "context_menu")
            assert hasattr(card1, "menu_cut")
            assert hasattr(card1, "menu_copy")
            assert hasattr(card1, "menu_paste")
            assert hasattr(card1, "menu_move_left")
            assert hasattr(card1, "menu_move_right")
            assert hasattr(card1, "menu_delete")

            # Initially clipboard is empty, so card1.menu_paste is disabled
            card1._update_context_menu_state()
            assert card1.menu_paste.get_sensitive() is False
            assert card1.menu_cut.get_sensitive() is True
            assert card1.menu_delete.get_sensitive() is True
            assert card1.menu_move_left.get_sensitive() is False
            assert card1.menu_move_right.get_sensitive() is True

            # Verify playhead split popover exists on card and is parented to needle_knob
            assert hasattr(card1, "split_popover")
            assert hasattr(card1, "btn_split_popover")
            assert card1.split_popover.get_parent() == card1.needle_knob
            assert card1.btn_split_popover.get_cursor().get_name() == "pointer"

            # Test selection behavior:
            # When paused, selecting s1 stays selected even if playhead is updated
            win.timeline_widget._on_segment_selected(s1.id)
            assert win.timeline.selected_segment_id == s1.id
            win.timeline_widget.update_playhead(3.5)  # 3.5s is in s2!
            # Since paused, selection must remain s1
            assert win.timeline.selected_segment_id == s1.id

            # Now select s2 via right click
            card2._on_right_click(None, 1, 10.0, 10.0)
            assert win.timeline.selected_segment_id == s2.id

            # Copy s2 using card's context menu copy button!
            card2.menu_copy.emit("clicked")
            assert win._clipboard_segment is not None
            assert win._clipboard_segment.id == s2.id
            assert win.btn_paste.get_sensitive() is True
            # Segment cards now have paste enabled!
            assert card2.menu_paste.get_sensitive() is True

            # Paste clip using card2's context menu paste button: should insert after s2
            card2.menu_paste.emit("clicked")
            assert len(win.timeline.segments) == 3
            pasted = win.timeline.segments[2]
            assert pasted.id != s2.id
            assert pasted.source_path == s2.source_path
            assert win.timeline.selected_segment_id == pasted.id
            # Playhead should jump to start of pasted clip
            assert abs(win.current_global_time - win.timeline.get_segment_global_start(2)) < 0.001

            # Test splitting via card's popover split button
            win.current_global_time = 1.0
            card_s1 = win.timeline_widget._cards[s1.id]
            card_s1.btn_split_popover.emit("clicked")
            assert len(win.timeline.segments) == 4

            # Undo split & paste!
            win.undo()
            assert len(win.timeline.segments) == 3
            win.undo()
            assert len(win.timeline.segments) == 2

            # Redo paste!
            win.redo()
            assert len(win.timeline.segments) == 3

            # Test Cut using card's context menu cut button:
            win.timeline_widget._on_segment_selected(s1.id)
            card1 = win.timeline_widget._cards[s1.id]
            card1.menu_cut.emit("clicked")
            assert len(win.timeline.segments) == 2
            assert win._clipboard_segment.source_path == s1.source_path

            # Test top bar Delete button:
            sel_id = win.timeline.selected_segment_id
            win.btn_delete.emit("clicked")
            assert len(win.timeline.segments) == 1
            assert win.btn_delete.get_sensitive() is False

            # Undo delete & cut
            win.undo()
            assert len(win.timeline.segments) == 2
            win.undo()
            assert len(win.timeline.segments) == 3

            # Verify cannot cut if only 1 segment remains
            win.timeline.segments = [s1]
            win.timeline.selected_segment_id = s1.id
            win._update_ui_state()
            assert win.btn_cut.get_sensitive() is False
            assert win.btn_delete.get_sensitive() is False
            win.cut_selected_segment()
            assert len(win.timeline.segments) == 1
        finally:
            if win:
                win.close()
            app.quit()

    app.connect("activate", run_test)
    app.run(["test"])
