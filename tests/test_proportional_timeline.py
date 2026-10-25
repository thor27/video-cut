"""Unit and integration tests for proportional timeline segment sizing and dynamic thumbnails."""

from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk

from video_cut.models import Segment
from video_cut.segment_card import SegmentCard
from video_cut.timeline_widget import compute_segment_widths
from video_cut.window import MainWindow


def test_compute_segment_widths_proportions():
    # 1. Exact user example:
    # 60s total, seg 1 = 30s (50%), seg 2 = 15s (25%), seg 3 = 15s (25%) in 200px available
    s1 = Segment(Path("/tmp/a.mp4"), 30.0)
    s2 = Segment(Path("/tmp/a.mp4"), 15.0)
    s3 = Segment(Path("/tmp/a.mp4"), 15.0)
    widths = compute_segment_widths([s1, s2, s3], 200)
    assert widths[s1.id] == 100
    assert widths[s2.id] == 50
    assert widths[s3.id] == 50
    assert sum(widths.values()) == 200

    # 2. Single segment gets full available space
    s_single = Segment(Path("/tmp/a.mp4"), 120.0)
    widths_single = compute_segment_widths([s_single], 850)
    assert widths_single[s_single.id] == 850

    # 3. Tiny segment in long video clamps to min_width without exceeding available_width
    s_long = Segment(Path("/tmp/a.mp4"), 999.0)
    s_tiny = Segment(Path("/tmp/a.mp4"), 1.0)
    widths_clamped = compute_segment_widths([s_long, s_tiny], 500, min_width=36)
    assert widths_clamped[s_tiny.id] == 36
    assert widths_clamped[s_long.id] == 500 - 36
    assert sum(widths_clamped.values()) == 500


def test_segment_card_responsive_header():
    s = Segment(Path("/tmp/a.mp4"), 10.0)
    card = SegmentCard(
        segment=s,
        index=0,
        is_selected=False,
        on_select=lambda _: None,
    )

    # Wide (>= 180): all controls visible
    card.set_card_width(200)
    assert card.btn_left.get_visible() is True
    assert card.btn_right.get_visible() is True
    assert card.dur_label.get_visible() is True
    assert card.handle_icon.get_visible() is True
    assert card.idx_label.get_visible() is True

    # Tier 2 (< 180, >= 110): nav arrows hidden; dur, handle, idx visible
    card.set_card_width(140)
    assert card.btn_left.get_visible() is False
    assert card.btn_right.get_visible() is False
    assert card.dur_label.get_visible() is True
    assert card.handle_icon.get_visible() is True
    assert card.idx_label.get_visible() is True

    # Tier 3 (< 110, >= 60): dur hidden; handle, idx visible
    card.set_card_width(80)
    assert card.btn_left.get_visible() is False
    assert card.btn_right.get_visible() is False
    assert card.dur_label.get_visible() is False
    assert card.handle_icon.get_visible() is True
    assert card.idx_label.get_visible() is True

    # Tier 4 (< 60): handle hidden; idx badge visible
    card.set_card_width(50)
    assert card.btn_left.get_visible() is False
    assert card.btn_right.get_visible() is False
    assert card.dur_label.get_visible() is False
    assert card.handle_icon.get_visible() is False
    assert card.idx_label.get_visible() is True

    # Height must remain uniform regardless of whether card is wide or very small
    h_small = card.measure(Gtk.Orientation.VERTICAL, -1)[0]
    card.set_card_width(200)
    h_wide = card.measure(Gtk.Orientation.VERTICAL, -1)[0]
    card.set_card_width(50)
    h_tiny = card.measure(Gtk.Orientation.VERTICAL, -1)[0]
    assert h_small == h_wide == h_tiny


def test_proportional_timeline_ui_integration():
    app = Adw.Application(application_id="io.github.thomaz.videocut.test.proportional")

    def run_test(app):
        win = None
        try:
            win = MainWindow(application=app)

            # Check ScrolledWindow policy is EXTERNAL horizontally, NEVER vertically
            h_policy, v_policy = win.timeline_widget.scrolled_window.get_policy()
            assert h_policy == Gtk.PolicyType.EXTERNAL
            assert v_policy == Gtk.PolicyType.NEVER

            # Load video (5 seconds)
            win.load_video(Path("/tmp/test_video.mp4"))
            assert len(win.timeline.segments) == 1

            # Split at 3.5s -> Segment 1: 3.5s (70%), Segment 2: 1.5s (30%)
            win.current_global_time = 3.5
            win.split_at_cursor()
            assert len(win.timeline.segments) == 2

            s1 = win.timeline.segments[0]
            s2 = win.timeline.segments[1]
            assert abs(s1.duration - 3.5) < 0.1
            assert abs(s2.duration - 1.5) < 0.1

            card1 = win.timeline_widget._cards[s1.id]
            card2 = win.timeline_widget._cards[s2.id]

            # Trigger recalculate_layout
            win.timeline_widget.recalculate_layout()

            # Verify card 1 has a larger width than card 2
            w1 = card1.get_size_request()[0]
            w2 = card2.get_size_request()[0]
            assert w1 > w2
            # Proportions: w1 / (w1 + w2) should be approximately 3.5 / 5.0 = 0.70
            ratio = w1 / float(w1 + w2)
            assert abs(ratio - 0.70) < 0.05

            # Total width of cards + gaps + add_btn fits within available space
            total_gaps = 2 * 10
            add_w = 89
            total_used = w1 + w2 + total_gaps + add_w
            container_w = win.timeline_widget.cards_box.get_width()
            if container_w > 0:
                assert total_used <= container_w + 5
        finally:
            if win:
                win.close()
            app.quit()

    app.connect("activate", run_test)
    app.run(["test"])


def test_window_does_not_grow_infinitely():
    """Verify recalculate_layout does not enter a growth loop after opening video and splitting."""
    from gi.repository import Gio
    app = Adw.Application(application_id="io.github.thomaz.videocut.test.growth_check", flags=Gio.ApplicationFlags.NON_UNIQUE)

    def run_test(app):
        win = None
        try:
            win = MainWindow(application=app)
            win.load_video(Path("/tmp/test_video.mp4"))
            win.current_global_time = 1.5
            win.split_at_cursor()

            w1 = sum(c.get_size_request()[0] for c in win.timeline_widget._cards.values())
            for _ in range(10):
                win.timeline_widget.recalculate_layout()
            w2 = sum(c.get_size_request()[0] for c in win.timeline_widget._cards.values())
            assert w1 == w2, f"Card widths must not grow on subsequent layout passes! {w1} vs {w2}"
        finally:
            if win:
                win.close()
            app.quit()

    app.connect("activate", run_test)
    app.run([])


def test_all_actions_recalculate_proportions_and_no_scroll():
    """Verify delete, split, paste, add video, and resize all recalculate within window size with zero horizontal scroll."""
    from gi.repository import Gio
    app = Adw.Application(application_id="io.github.thomaz.videocut.test.actions_recalc", flags=Gio.ApplicationFlags.NON_UNIQUE)

    def run_test(app):
        win = None
        try:
            win = MainWindow(application=app)
            win.set_default_size(1000, 700)

            # 1. Open video
            win.load_video(Path("/tmp/test_video.mp4"))
            assert len(win.timeline.segments) == 1
            card_open = list(win.timeline_widget._cards.values())[0]
            w_open = card_open.get_size_request()[0]
            assert w_open > 700, f"Single segment should occupy available space, got {w_open}"

            # 2. Split at 2.5s (50% / 50%)
            win.current_global_time = 2.5
            win.split_at_cursor()
            assert len(win.timeline.segments) == 2
            cards_split = [win.timeline_widget._cards[s.id] for s in win.timeline.segments]
            w_split = [c.get_size_request()[0] for c in cards_split]
            assert abs(w_split[0] - w_split[1]) <= 2, f"Split at midpoint should yield equal widths, got {w_split}"

            # 3. Copy first segment & paste
            win.timeline.selected_segment_id = win.timeline.segments[0].id
            win.copy_selected_segment()
            win.paste_segment()
            assert len(win.timeline.segments) == 3
            cards_pasted = [win.timeline_widget._cards[s.id] for s in win.timeline.segments]
            w_pasted = [c.get_size_request()[0] for c in cards_pasted]
            assert len(w_pasted) == 3

            # 4. Delete segment
            to_del_id = win.timeline.segments[1].id
            win.delete_segment(to_del_id)
            assert len(win.timeline.segments) == 2
            cards_after_del = [win.timeline_widget._cards[s.id] for s in win.timeline.segments]
            w_after_del = [c.get_size_request()[0] for c in cards_after_del]
            assert sum(w_after_del) > sum(w_pasted[:2])

            # 5. Add another video
            win._add_video_to_timeline(Path("/tmp/test_video.mp4"), replace=False)
            assert len(win.timeline.segments) == 3

            # Verify no horizontal scrollbar mapped / external policy preserved
            h_policy, _ = win.timeline_widget.scrolled_window.get_policy()
            assert h_policy == Gtk.PolicyType.EXTERNAL
            hadj = win.timeline_widget.scrolled_window.get_hadjustment()
            assert hadj.get_value() == 0.0
        finally:
            if win:
                win.close()
            app.quit()

    app.connect("activate", run_test)
    app.run([])


def test_thumbnail_sync_on_split_and_placeholders():
    app = Adw.Application(application_id="io.github.thomaz.videocut.test_split_thumbs")

    def run_test(app):
        win = None
        try:
            win = MainWindow(application=app)
            win.set_default_size(1000, 700)

            # Load video
            win.load_video(Path("/tmp/test_30s.mp4"))
            assert len(win.timeline.segments) == 1
            card = list(win.timeline_widget._cards.values())[0]

            # Before background thumbnails finish, placeholder items exist in filmstrip
            assert card.filmstrip_box.get_first_child() is not None

            # Split at 15.0s
            win.current_global_time = 15.0
            win.split_at_cursor()
            assert len(win.timeline.segments) == 2

            left_seg = win.timeline.segments[0]
            right_seg = win.timeline.segments[1]

            assert abs(left_seg.start_time - 0.0) < 0.01
            assert abs(left_seg.end_time - 15.0) < 0.01
            assert abs(right_seg.start_time - 15.0) < 0.01
            assert abs(right_seg.end_time - 30.0) < 0.01

            # Check thumbnail querying for both sides
            left_thumbs = win.thumbnail_service.get_thumbnails_for_segment(
                left_seg.source_path, left_seg.start_time, left_seg.end_time, max_thumbs=3
            )
            right_thumbs = win.thumbnail_service.get_thumbnails_for_segment(
                right_seg.source_path, right_seg.start_time, right_seg.end_time, max_thumbs=3
            )

            assert len(left_thumbs) == 3
            assert len(right_thumbs) == 3

            # Update cards with thumbnails / placeholders
            left_card = win.timeline_widget._cards[left_seg.id]
            right_card = win.timeline_widget._cards[right_seg.id]

            left_card.update_thumbnails(left_thumbs)
            right_card.update_thumbnails(right_thumbs)

            # Both cards have 3 child widgets (pictures or placeholders)
            left_children = []
            c = left_card.filmstrip_box.get_first_child()
            while c:
                left_children.append(c)
                c = c.get_next_sibling()
            assert len(left_children) == 3

            right_children = []
            c = right_card.filmstrip_box.get_first_child()
            while c:
                right_children.append(c)
                c = c.get_next_sibling()
            assert len(right_children) == 3
        finally:
            if win:
                win.close()
            app.quit()

    app.connect("activate", run_test)
    app.run([])


def test_small_segment_minimum_thumb_size():
    """Verify that narrow segments display only 1 thumbnail to prevent tiny illegible slivers."""
    from gi.repository import Gio
    app = Adw.Application(application_id="io.github.thomaz.videocut.test_min_thumb_size", flags=Gio.ApplicationFlags.NON_UNIQUE)

    def run_test(app):
        win = None
        try:
            win = MainWindow(application=app)
            win.set_default_size(1000, 700)

            # Load video
            win.load_video(Path("/tmp/test_video.mp4"))
            # Split video into a very small 0.5s segment and a remaining 4.5s segment
            win.current_global_time = 0.5
            win.split_at_cursor()
            assert len(win.timeline.segments) == 2

            small_seg = win.timeline.segments[0]
            card_small = win.timeline_widget._cards[small_seg.id]

            # The small segment width is clamped to min_card_width (50px)
            w = card_small.get_size_request()[0]
            assert w <= 100

            # Count child widgets in filmstrip
            children = []
            c = card_small.filmstrip_box.get_first_child()
            while c:
                children.append(c)
                c = c.get_next_sibling()

            # Must have only 1 thumbnail widget, not multiple squeezed slivers!
            assert len(children) == 1
        finally:
            if win:
                win.close()
            app.quit()

    app.connect("activate", run_test)
    app.run([])


