"""Automated tests for multi-segment selection, bulk transformations, batch clipboard, and batch deletion."""

from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk

from video_cut.models import OverlayItem, ProjectTimeline
from video_cut.window import MainWindow


def test_timeline_multi_selection_model():
    timeline = ProjectTimeline()
    s1 = timeline.add_video(Path("/tmp/clip1.mp4"), 10.0)
    s2 = timeline.add_video(Path("/tmp/clip2.mp4"), 8.0)
    s3 = timeline.add_video(Path("/tmp/clip3.mp4"), 12.0)

    # Initial selection defaults to single selected segment
    assert timeline.selected_segment_id == s3.id
    assert timeline.selected_segment_ids == {s3.id}

    # Set multi-selection
    timeline.selected_segment_ids = {s1.id, s2.id}
    assert timeline.selected_segment_ids == {s1.id, s2.id}
    assert timeline.selected_segment_id in {s1.id, s2.id}

    # Clear selection
    timeline.selected_segment_ids = set()
    assert timeline.selected_segment_ids == set()
    assert timeline.selected_segment_id is None


def test_timeline_batch_delete_and_undo():
    timeline = ProjectTimeline()
    s1 = timeline.add_video(Path("/tmp/clip1.mp4"), 10.0)
    s2 = timeline.add_video(Path("/tmp/clip2.mp4"), 8.0)
    s3 = timeline.add_video(Path("/tmp/clip3.mp4"), 12.0)
    s4 = timeline.add_video(Path("/tmp/clip4.mp4"), 15.0)

    timeline.selected_segment_ids = {s2.id, s3.id}
    timeline.save_state(15.0)

    count = timeline.delete_segments({s2.id, s3.id})
    assert count == 2
    assert len(timeline.segments) == 2
    assert [s.id for s in timeline.segments] == [s1.id, s4.id]
    assert s2.id not in timeline.selected_segment_ids
    assert s3.id not in timeline.selected_segment_ids

    # Undo restores both segments and the multi-selection state in one step
    timeline.undo(5.0)
    assert len(timeline.segments) == 4
    assert [s.id for s in timeline.segments] == [s1.id, s2.id, s3.id, s4.id]
    assert timeline.selected_segment_ids == {s2.id, s3.id}

    # Redo re-deletes them
    timeline.redo(15.0)
    assert len(timeline.segments) == 2
    assert [s.id for s in timeline.segments] == [s1.id, s4.id]


def test_timeline_batch_paste_and_undo():
    timeline = ProjectTimeline()
    s1 = timeline.add_video(Path("/tmp/clip1.mp4"), 10.0)
    s2 = timeline.add_video(Path("/tmp/clip2.mp4"), 8.0)

    # Clone segments to paste
    to_paste = [s1.copy(), s2.copy()]
    timeline.save_state(5.0)

    pasted = timeline.paste_segments(to_paste, after_segment_id=s1.id)
    assert len(pasted) == 2
    assert len(timeline.segments) == 4
    # Expected order: s1, pasted[0], pasted[1], s2
    assert timeline.segments[0].id == s1.id
    assert timeline.segments[1].id == pasted[0].id
    assert timeline.segments[2].id == pasted[1].id
    assert timeline.segments[3].id == s2.id
    # New unique IDs assigned
    assert pasted[0].id != s1.id
    assert pasted[1].id != s2.id
    # Pasted segments become active selection
    assert timeline.selected_segment_ids == {pasted[0].id, pasted[1].id}

    # Undo removes pasted segments
    timeline.undo(12.0)
    assert len(timeline.segments) == 2
    assert [s.id for s in timeline.segments] == [s1.id, s2.id]


def test_timeline_widget_ctrl_shift_and_select_all(sample_video):
    app = Adw.Application(application_id="io.github.thor27.video-cut.test.multiselect.widget")

    def run_test(app):
        win = None
        try:
            win = MainWindow(application=app)
            win.load_video(sample_video)

            # Split twice to create 3 segments
            win.current_global_time = 0.5
            win.split_at_cursor()
            win.current_global_time = 1.0
            win.split_at_cursor()
            assert len(win.timeline.segments) == 3

            seg0, seg1, seg2 = win.timeline.segments[0], win.timeline.segments[1], win.timeline.segments[2]
            tw = win.timeline_widget

            # 1. Normal click selects seg0
            tw._on_segment_selected(seg0.id, ctrl=False, shift=False)
            assert win.timeline.selected_segment_ids == {seg0.id}
            assert tw._cards[seg0.id]._is_selected is True
            assert tw._cards[seg1.id]._is_selected is False
            assert tw._cards[seg2.id]._is_selected is False

            # 2. Ctrl+Click on seg2 adds seg2 without unselecting seg0
            tw._on_segment_selected(seg2.id, ctrl=True, shift=False)
            assert win.timeline.selected_segment_ids == {seg0.id, seg2.id}
            assert tw._cards[seg0.id]._is_selected is True
            assert tw._cards[seg1.id]._is_selected is False
            assert tw._cards[seg2.id]._is_selected is True

            # 3. Ctrl+Click again on seg0 removes seg0
            tw._on_segment_selected(seg0.id, ctrl=True, shift=False)
            assert win.timeline.selected_segment_ids == {seg2.id}
            assert tw._cards[seg0.id]._is_selected is False
            assert tw._cards[seg2.id]._is_selected is True

            # 4. Shift+Click from seg0 to seg2 selects [seg0, seg1, seg2]
            tw._on_segment_selected(seg0.id, ctrl=False, shift=False)
            tw._on_segment_selected(seg2.id, ctrl=False, shift=True)
            assert win.timeline.selected_segment_ids == {seg0.id, seg1.id, seg2.id}
            for seg in win.timeline.segments:
                assert tw._cards[seg.id]._is_selected is True

            # 5. Ctrl+A selects all
            tw._on_segment_selected(seg0.id, ctrl=False, shift=False)
            assert win.timeline.selected_segment_ids == {seg0.id}
            tw.select_all()
            assert win.timeline.selected_segment_ids == {seg0.id, seg1.id, seg2.id}

            # 6. Right-click context menu "Select" accumulates multi-selection
            tw._on_segment_selected(seg0.id, ctrl=False, shift=False)
            assert win.timeline.selected_segment_ids == {seg0.id}

            card1 = tw._cards[seg1.id]
            card1._on_right_click(None, 1, 10, 10)
            # Verify right-clicking did NOT clear seg0
            assert win.timeline.selected_segment_ids == {seg0.id}
            assert card1.menu_select._lbl.get_label() == "Select"
            # Click Select on card 1
            card1._menu_action_toggle_select()
            assert win.timeline.selected_segment_ids == {seg0.id, seg1.id}

            # Right click and Select on card 2
            card2 = tw._cards[seg2.id]
            card2._on_right_click(None, 1, 10, 10)
            assert card2.menu_select._lbl.get_label() == "Select"
            card2._menu_action_toggle_select()
            assert win.timeline.selected_segment_ids == {seg0.id, seg1.id, seg2.id}

            # Card 1 now shows "Deselect"
            card1._on_right_click(None, 1, 10, 10)
            assert card1.menu_select._lbl.get_label() == "Deselect"
            card1._menu_action_toggle_select()
            assert win.timeline.selected_segment_ids == {seg0.id, seg2.id}

            # Context menu Select All
            card1._menu_action_select_all()
            assert win.timeline.selected_segment_ids == {seg0.id, seg1.id, seg2.id}
        finally:
            if win:
                win.close()
            app.quit()

    app.connect("activate", run_test)
    app.run(["test"])


def test_bulk_transform_and_overlays(sample_video):
    app = Adw.Application(application_id="io.github.thor27.video-cut.test.multiselect.bulk")

    def run_test(app):
        win = None
        try:
            win = MainWindow(application=app)
            win.load_video(sample_video)

            # Split into 3 segments
            win.current_global_time = 0.4
            win.split_at_cursor()
            win.current_global_time = 0.8
            win.split_at_cursor()
            assert len(win.timeline.segments) == 3

            seg0, seg1, _seg2 = win.timeline.segments[0], win.timeline.segments[1], win.timeline.segments[2]

            # Select seg0 and seg1
            win.timeline.selected_segment_ids = {seg0.id, seg1.id}
            win.timeline.selected_segment_id = seg0.id

            # Bulk Crop & Rotate
            crop_box = (0.1, 0.1, 0.8, 0.8)
            win.timeline.save_state(win.current_global_time)
            for sid in win.timeline.selected_segment_ids:
                s = win.timeline.get_segment(sid)
                s.crop = crop_box
                s.rotation = 90
                s.hflip = True

            assert seg0.crop == crop_box
            assert seg0.rotation == 90
            assert seg0.hflip is True
            assert seg1.crop == crop_box
            assert seg1.rotation == 90
            assert seg1.hflip is True
            # seg2 unaffected
            assert _seg2.crop is None
            assert _seg2.rotation == 0

            # Bulk Overlays
            overlay1 = OverlayItem(
                id="test-ov-1",
                item_type="text",
                text="Watermark",
                x=0.5,
                y=0.5,
                width=0.3,
                height=0.1,
                color="#ff0000",
                font_size=24,
            )
            for sid in win.timeline.selected_segment_ids:
                s = win.timeline.get_segment(sid)
                s.overlays = [overlay1.clone()]

            assert len(seg0.overlays) == 1
            assert len(seg1.overlays) == 1
            assert len(_seg2.overlays) == 0
            assert seg0.overlays[0].text == "Watermark"
            assert seg1.overlays[0].text == "Watermark"
            # Each overlay item has its own distinct ID
            assert seg0.overlays[0].id != seg1.overlays[0].id
        finally:
            if win:
                win.close()
            app.quit()

    app.connect("activate", run_test)
    app.run(["test"])


def test_batch_copy_paste_delete(sample_video):
    app = Adw.Application(application_id="io.github.thor27.video-cut.test.multiselect.clipboard")

    def run_test(app):
        win = None
        try:
            win = MainWindow(application=app)
            win.load_video(sample_video)

            # Split into 3 segments
            win.current_global_time = 0.3
            win.split_at_cursor()
            win.current_global_time = 0.7
            win.split_at_cursor()
            assert len(win.timeline.segments) == 3

            seg0, seg1, _seg2 = win.timeline.segments[0], win.timeline.segments[1], win.timeline.segments[2]

            # Multi-select seg0 and seg1
            win.timeline.selected_segment_ids = {seg0.id, seg1.id}
            win.timeline.selected_segment_id = seg0.id

            # Batch Copy
            win.copy_selected_segment()
            assert len(win.clipboard_controller.internal_segments) == 2
            assert [s.source_path for s in win.clipboard_controller.internal_segments] == [seg0.source_path, seg1.source_path]

            # Batch Paste
            win.paste_segment()
            # Original 3 segments + 2 pasted = 5 segments
            assert len(win.timeline.segments) == 5

            # Select the newly pasted segments and batch delete them
            pasted_ids = set(win.timeline.selected_segment_ids)
            assert len(pasted_ids) == 2
            win.delete_selected_segment()
            assert len(win.timeline.segments) == 3

            # Single Undo restores the deleted clips
            win.undo()
            assert len(win.timeline.segments) == 5
        finally:
            if win:
                win.close()
            app.quit()

    app.connect("activate", run_test)
    app.run(["test"])


def test_ctrl_a_and_delete_shortcut(sample_video):
    app = Adw.Application(application_id="io.github.thor27.video-cut.test.multiselect.shortcuts")

    def run_test(app):
        win = None
        try:
            win = MainWindow(application=app)
            win.load_video(sample_video)

            # Split into 2 segments
            win.current_global_time = 0.5
            win.split_at_cursor()
            assert len(win.timeline.segments) == 2

            # Simulate Ctrl+A keypress
            handled = win._on_key_pressed(None, Gdk.KEY_a, 0, Gdk.ModifierType.CONTROL_MASK)
            assert handled is True
            assert len(win.timeline.selected_segment_ids) == 2

            # Simulate Delete keypress on multi-selection
            handled_del = win._on_key_pressed(None, Gdk.KEY_Delete, 0, 0)
            assert handled_del is True
            # Both segments deleted, timeline is empty
            assert win.timeline.is_empty is True

            # Ctrl+Z undo restores both segments
            win.undo()
            assert len(win.timeline.segments) == 2
        finally:
            if win:
                win.close()
            app.quit()

    app.connect("activate", run_test)
    app.run(["test"])
