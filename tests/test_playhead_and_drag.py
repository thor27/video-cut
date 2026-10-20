"""Unit tests for SegmentCard needle positioning, filmstrip margins, and scrub drag handling."""

from pathlib import Path
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib

from video_cut.models import Segment, ProjectTimeline
from video_cut.segment_card import SegmentCard
from video_cut.timeline_widget import TimelineWidget


def test_segment_card_needle_and_margins():
    app = Adw.Application(application_id="io.github.thomaz.videocut.test.needle")
    err = []

    def run_test(app):
        try:
            seg = Segment(
                id="seg-1",
                source_path=Path("/tmp/test_video.mp4"),
                source_duration=10.0,
                start_time=0.0,
                end_time=10.0,
            )

            scrubbed_times = []

            card = SegmentCard(
                segment=seg,
                index=0,
                is_selected=False,
                on_select=lambda _: None,
                on_scrub_to_source_time=lambda s_id, t: scrubbed_times.append((s_id, t)),
                on_move_left=lambda _: None,
                on_move_right=lambda _: None,
                on_split=lambda _: None,
                on_delete=lambda _: None,
                on_drop_reorder=lambda _a, _b: None,
            )

            # 1. Verify filmstrip margins
            assert card.filmstrip_box.get_margin_start() == 7
            assert card.filmstrip_box.get_margin_end() == 7
            assert card.filmstrip_box.get_margin_top() == 7

            # 2. Verify DND is attached to card.header, NOT card
            card_controllers = card.observe_controllers()
            card_controller_types = [
                card_controllers.get_item(i).__class__.__name__
                for i in range(card_controllers.get_n_items())
            ]
            # DragSource should NOT be on card!
            assert "DragSource" not in card_controller_types

            header_controllers = card.header.observe_controllers()
            header_controller_types = [
                header_controllers.get_item(i).__class__.__name__
                for i in range(header_controllers.get_n_items())
            ]
            assert "DragSource" in header_controller_types

            # 3. Verify needle positioning at ratio = 0.0
            card.set_needle_ratio(0.0)
            assert card.needle.get_visible() is True
            # At ratio 0.0, margin_start must be 0 so center of 14px needle sits at X = 7 (first pixel column of image)
            assert card.needle.get_margin_start() == 0

            # 4. Verify needle positioning at ratio = 0.5
            width = card.filmstrip_box.get_width()
            if width <= 0:
                width = 100
            card.set_needle_ratio(0.5)
            assert card.needle.get_margin_start() == int(round(0.5 * width))

            # 5. Verify needle hidden when ratio is None
            card.set_needle_ratio(None)
            assert card.needle.get_visible() is False

            # 6. Verify scrub calculations: click and drag
            # If x = 7 (first pixel of image), scrub time should be segment.start_time (0.0)
            card._scrub_at_x(7.0)
            assert len(scrubbed_times) == 1
            assert abs(scrubbed_times[-1][1] - 0.0) < 0.001

            # If x = 7 + width (last pixel of image), scrub time should be segment.end_time (10.0)
            card._scrub_at_x(7.0 + width)
            assert abs(scrubbed_times[-1][1] - 10.0) < 0.001

            # If dragged beyond boundaries (e.g. offset = 500px), continues scrubbing without error
            card._scrub_at_x(7.0 + width + 50)
            assert scrubbed_times[-1][1] > 10.0

            # 7. Verify knob, needle, and overlay cursors are ew-resize for scrubbing and drag is unobstructed
            assert card.needle_knob.get_cursor().get_name() == "ew-resize"
            assert card.needle.get_cursor().get_name() == "ew-resize"
            assert card.overlay.get_cursor().get_name() == "ew-resize"
            assert card.needle_knob.observe_controllers().get_n_items() == 0
            assert "Drag to scrub" in card.needle_knob.get_tooltip_text()

            # Test direct drag from knob position
            card.set_needle_ratio(0.5)
            knob_x = card.needle.get_margin_start() + 7.0
            card._on_filmstrip_drag_begin(None, knob_x, 7.0)
            assert len(scrubbed_times) >= 2
            # Drag 30px to the right
            card._on_filmstrip_drag_update(None, 30.0, 0.0)
            assert scrubbed_times[-1][1] > 5.0
            card._on_filmstrip_drag_end(None, 30.0, 0.0)

            # 8. Verify split button has clean "Split" text without confusing "s" badge
            child = card.btn_split_popover.get_child().get_first_child()
            labels = []
            while child:
                if isinstance(child, Gtk.Label):
                    labels.append(child.get_label())
                child = child.get_next_sibling()
            assert labels == ["Split"], f"Expected only ['Split'] but got {labels}"

            # 9. Verify right-click context menu has Split Clip item
            assert hasattr(card, "menu_split")
            assert card.menu_split.get_sensitive() is True

        except Exception as e:
            err.append(e)
        finally:
            app.quit()

    app.connect("activate", run_test)
    app.run(["test"])
    if err:
        raise err[0]


def test_timeline_scrub_clamp():
    app = Adw.Application(application_id="io.github.thomaz.videocut.test.timeline")
    err = []

    def run_test(app):
        try:
            timeline = ProjectTimeline()
            seg1 = Segment(
                id="s1",
                source_path=Path("/tmp/test_video.mp4"),
                source_duration=5.0,
                start_time=0.0,
                end_time=5.0,
            )
            seg2 = Segment(
                id="s2",
                source_path=Path("/tmp/test_video2.mp4"),
                source_duration=5.0,
                start_time=0.0,
                end_time=5.0,
            )
            timeline.segments.append(seg1)
            timeline.segments.append(seg2)

            seeks = []
            tw = TimelineWidget(
                timeline=timeline,
                on_seek=lambda t: seeks.append(t),
                on_split_requested=lambda: None,
                on_delete_requested=lambda _: None,
                on_add_video_requested=lambda: None,
                on_selection_changed=lambda _: None,
                on_play_pause=lambda: None,
                on_step_1s=lambda _: None,
                on_step_frame=lambda _: None,
                on_jump_segment=lambda _: None,
            )

            # Scrub in seg1
            tw._on_card_scrub("s1", 2.5)
            assert len(seeks) == 1
            assert abs(seeks[-1] - 2.5) < 0.001

            # Scrub in seg2
            tw._on_card_scrub("s2", 1.0)
            assert abs(seeks[-1] - 6.0) < 0.001

            # Scrub out of bounds (negative)
            tw._on_card_scrub("s1", -10.0)
            assert abs(seeks[-1] - 0.0) < 0.001

            # Scrub out of bounds (past total_duration 10.0)
            tw._on_card_scrub("s2", 20.0)
            assert abs(seeks[-1] - 10.0) < 0.001

        except Exception as e:
            err.append(e)
        finally:
            app.quit()

    app.connect("activate", run_test)
    app.run(["test"])
    if err:
        raise err[0]


def test_drag_preview_and_single_segment_delete_prevention():
    app = Adw.Application(application_id="io.github.thomaz.videocut.test.dragpreview")
    err = []

    def run_test(app):
        try:
            seg = Segment(
                id="seg-1",
                source_path=Path("/tmp/test_video.mp4"),
                source_duration=10.0,
                start_time=0.0,
                end_time=10.0,
            )

            # 1. Card with can_delete=False
            card = SegmentCard(
                segment=seg,
                index=0,
                is_selected=False,
                on_select=lambda _: None,
                on_scrub_to_source_time=lambda _s, _t: None,
                on_move_left=lambda _: None,
                on_move_right=lambda _: None,
                on_split=lambda _: None,
                on_delete=lambda _: None,
                on_drop_reorder=lambda _a, _b: None,
                can_delete=False,
            )

            # Verify filmstrip, overlay, and needle have permanent ew-resize cursors
            assert card.filmstrip_box.get_cursor().get_name() == "ew-resize"
            assert card.overlay.get_cursor().get_name() == "ew-resize"
            assert card.needle.get_cursor().get_name() == "ew-resize"

            # Verify context menu items reflect can_delete=False, then can_delete=True
            assert card.menu_delete.get_sensitive() is False
            assert card.menu_cut.get_sensitive() is False
            card.set_can_delete(True)
            assert card.menu_delete.get_sensitive() is True
            assert card.menu_cut.get_sensitive() is True

            # 2. Test drag preview texture generation
            tex = card._create_drag_preview_texture()
            assert tex is not None
            assert tex.get_width() == 120
            assert tex.get_height() == 68

            # 3. Test _on_drag_prepare sets icon and doesn't crash
            ds = Gtk.DragSource.new()
            cp = card._on_drag_prepare(ds, 10.0, 10.0)
            assert cp is not None

            # 4. Test timeline deletion of single segment
            timeline = ProjectTimeline()
            timeline.segments.append(seg)
            assert len(timeline.segments) == 1
            assert timeline.delete_segment(seg.id, allow_last=False) is False
            assert len(timeline.segments) == 1
            assert timeline.delete_segment(seg.id) is True
            assert len(timeline.segments) == 0
            assert timeline.is_empty is True

            # Re-add segment for subsequent steps
            timeline.segments.append(seg)

            # 5. Test scrubbing cursor locking on TimelineWidget
            structure_changed = []
            tw = TimelineWidget(
                timeline=timeline,
                on_seek=lambda _: None,
                on_split_requested=lambda: None,
                on_delete_requested=lambda _: None,
                on_add_video_requested=lambda: None,
                on_selection_changed=lambda _: None,
                on_play_pause=lambda: None,
                on_step_1s=lambda _: None,
                on_step_frame=lambda _: None,
                on_jump_segment=lambda _: None,
                on_structure_changed=lambda: structure_changed.append(True),
            )
            tw.rebuild_filmstrip()

            tw._set_scrubbing_cursor(True)
            assert tw.get_cursor().get_name() == "ew-resize"
            assert tw.cards_box.get_cursor().get_name() == "ew-resize"
            assert tw.scrolled_window.get_cursor().get_name() == "ew-resize"
            for c in tw._cards.values():
                assert c.get_cursor().get_name() == "ew-resize"
                assert c.header.get_cursor().get_name() == "ew-resize"

            tw._set_scrubbing_cursor(False)
            assert tw.get_cursor() is None
            assert tw.cards_box.get_cursor() is None
            assert tw.scrolled_window.get_cursor() is None
            for c in tw._cards.values():
                assert c.get_cursor() is None
                assert c.header.get_cursor().get_name() == "grab"

            # 6. Test jump_to_next_segment and jump_to_prev_segment when video is paused
            seg2 = Segment(
                id="seg-2",
                source_path=Path("/tmp/test_video2.mp4"),
                source_duration=5.0,
                start_time=0.0,
                end_time=5.0,
            )
            timeline.segments.append(seg2)
            tw.rebuild_filmstrip()
            assert len(timeline.segments) == 2

            from video_cut.player import VideoPlayerController
            pic = Gtk.Picture.new()
            time_updates = []
            player = VideoPlayerController(
                timeline=timeline,
                video_picture=pic,
                on_time_update=lambda t: (time_updates.append(t), tw.update_playhead(t)),
                on_playback_state_changed=lambda _: None,
            )

            # Start paused at 0.0s (seg-1)
            player.seek_to_global_time(0.0)
            assert tw._cards["seg-1"].needle.get_visible() is True
            assert tw._cards["seg-2"].needle.get_visible() is False

            # Jump to next segment -> should move orange needle to seg-2 (t=10.0)
            player.jump_to_next_segment()
            assert player.current_global_time == 10.0
            assert tw._cards["seg-1"].needle.get_visible() is False
            assert tw._cards["seg-2"].needle.get_visible() is True
            assert tw._cards["seg-2"].needle.get_margin_start() == 0

            # Jump to previous segment -> should move orange needle to seg-1 (t=0.0)
            player.jump_to_prev_segment()
            assert player.current_global_time == 0.0
            assert tw._cards["seg-1"].needle.get_visible() is True
            assert tw._cards["seg-1"].needle.get_margin_start() == 0
            assert tw._cards["seg-2"].needle.get_visible() is False

        except Exception as e:
            err.append(e)
        finally:
            app.quit()

    app.connect("activate", run_test)
    app.run(["test"])
    if err:
        raise err[0]


def test_split_pill_hover_and_context_menu_top_left():
    app = Adw.Application(application_id="io.github.thomaz.videocut.test.split_pill")
    err = []

    def run_test(app):
        try:
            from video_cut.i18n import init_i18n
            init_i18n("en_US")
            split_called = []
            seg = Segment(
                id="seg-1",
                source_path=Path("/tmp/test_video.mp4"),
                source_duration=10.0,
                start_time=0.0,
                end_time=10.0,
            )
            card = SegmentCard(
                segment=seg,
                index=0,
                is_selected=True,
                on_select=lambda _: None,
                on_split=lambda s_id: split_called.append(s_id),
            )
            # 1. Context menu top-left alignment and item label
            assert card.context_menu.get_halign() == Gtk.Align.START
            assert card.context_menu.get_position() == Gtk.PositionType.BOTTOM
            menu_row = card.menu_split.get_child()
            menu_label = menu_row.get_first_child().get_next_sibling().get_label()
            assert menu_label in ("Split at Cursor", "Dividir no Cursor")

            # Simulate right click at (150, 40)
            card._on_right_click(None, 1, 150.0, 40.0)
            res = card.context_menu.get_pointing_to()
            pointing_to = res.rect if hasattr(res, "rect") else res[1]
            assert pointing_to.x == 150
            assert pointing_to.y == 40
            assert pointing_to.width == 1
            assert pointing_to.height == 1

            # 2. Split pill positioning and hover
            card.set_needle_ratio(0.5)
            assert card.needle.get_visible() is True
            needle_x = card.needle.get_margin_start() + 7

            # Initially hidden
            assert card.split_pill.get_visible() is False

            # Hovering near needle -> split pill appears
            card._on_filmstrip_motion(None, needle_x + 5, 20.0)
            assert card.split_pill.get_visible() is True

            # Split pill margin_start is positioned directly above the needle
            pill_x = card.split_pill.get_margin_start()
            assert abs((pill_x + 28) - needle_x) <= 35

            # Moving far away -> close scheduled
            card._on_filmstrip_motion(None, needle_x + 100, 20.0)
            assert card._popover_close_timer is not None
            card._do_close_popover()
            assert card.split_pill.get_visible() is False

            # Clicking on filmstrip moves cursor and immediately shows split pill without mouse motion
            card._on_filmstrip_click(None, 1, needle_x, 20.0)
            assert card.split_pill.get_visible() is True

            # Hovering in header above needle -> split pill appears
            card._on_header_motion(None, needle_x, 10.0)
            assert card.split_pill.get_visible() is True

            # Drag scrub begin immediately hides split pill and blocks hover
            card._on_filmstrip_drag_begin(None, needle_x, 20.0)
            assert card.split_pill.get_visible() is False
            assert card._is_drag_scrubbing is True
            # During drag scrubbing, motion does NOT show split pill
            card._on_filmstrip_motion(None, needle_x, 20.0)
            assert card.split_pill.get_visible() is False

            # Drag scrub end restores normal scrubbing state and shows split pill
            card._on_filmstrip_drag_end(None, 10.0, 0.0)
            assert card._is_drag_scrubbing is False
            assert card.split_pill.get_visible() is True

            # Clicking split pill triggers on_split
            card.split_pill.emit("clicked")
            assert split_called == ["seg-1"]

            # Verify split pill is always centered at ratio 0.0 (not clamped to card border)
            card.set_needle_ratio(0.0)
            card.show_split_pill()
            needle_x_start = card.needle.get_margin_start() + 7
            pill_x_start = card.split_pill.get_margin_start()
            child = card.split_pill.get_child()
            pill_w = max(50, child.measure(Gtk.Orientation.HORIZONTAL, -1)[1] + 14) if child else 50
            assert abs((pill_x_start + pill_w / 2.0) - needle_x_start) <= 2
            # Verify split pill container has overflow visible so it is never clipped
            assert card.split_fixed.get_overflow() == Gtk.Overflow.VISIBLE
            assert card.header_overlay.get_overflow() == Gtk.Overflow.VISIBLE
            assert card.get_overflow() == Gtk.Overflow.VISIBLE

            # Verify split pill is always centered at ratio 1.0 (not clamped to card border)
            card.set_needle_ratio(1.0)
            needle_x_end = card.needle.get_margin_start() + 7
            pill_x_end = card.split_pill.get_margin_start()
            assert abs((pill_x_end + pill_w / 2.0) - needle_x_end) <= 2

        except Exception as e:
            err.append(e)
        finally:
            app.quit()

    app.connect("activate", run_test)
    app.run(["test"])
    if err:
        raise err[0]


def test_split_pill_left_boundary_not_clipped_in_timeline():
    """Verify that in the timeline, the split pill at ratio 0.0 has positive coordinate and is never clipped by scrolled_window."""
    from video_cut.window import MainWindow
    app = Adw.Application(application_id="io.github.thomaz.videocut.test.unclipped_pill")
    err = []

    def run_test(app):
        win = None
        try:
            win = MainWindow(application=app)
            win.load_video(Path("/tmp/test_video.mp4"))

            tw = win.timeline_widget
            assert tw.cards_box.get_margin_start() == 0, "cards_box must have margin_start == 0 for clean edge-to-edge layout"
            assert tw.cards_box.get_margin_end() == 0, "cards_box must have margin_end == 0 for clean edge-to-edge layout"
            assert tw.cards_box.get_overflow() == Gtk.Overflow.VISIBLE
            assert tw.scrolled_window.get_overflow() == Gtk.Overflow.VISIBLE

            card = list(tw._cards.values())[0]
            card.set_needle_ratio(0.0)
            tw.show_split_pill()

            # Floating split pill in filmstrip_overlay is clamped to >= 0, so it is never clipped
            assert tw.split_pill.get_margin_start() >= 0
            assert tw.split_pill.get_margin_top() > 0
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


def test_split_pill_centralization_across_languages():
    """Verify split pill is dynamically centered over the orange playhead needle regardless of language and text length."""
    import time
    from video_cut.window import MainWindow
    from video_cut.i18n import init_i18n

    for lang in ["en_US", "pt_BR", "fr"]:
        app = Adw.Application(application_id=f"io.github.thomaz.videocut.test.pill_center_{lang}")
        err = []

        def run_test(app, target_lang=lang):
            win = None
            try:
                init_i18n(target_lang)
                win = MainWindow(application=app)
                win.load_video(Path("/tmp/test_video.mp4"))
                win.present()

                ctx = GLib.MainContext.default()
                for _ in range(50):
                    ctx.iteration(False)

                tw = win.timeline_widget
                card = list(tw._cards.values())[0]
                card.set_needle_ratio(0.5)
                tw.show_split_pill()

                for _ in range(50):
                    ctx.iteration(False)

                fs_x = tw.cards_box.get_margin_start()
                for seg in tw.timeline.segments:
                    if seg.id == card.segment.id:
                        break
                    c = tw._cards.get(seg.id)
                    w = c.get_width() if c and c.get_width() > 0 else 100
                    fs_x += w + tw.cards_box.get_spacing()
                fs_x += 6
                needle_center = fs_x + card.needle.get_margin_start() + 7
                pill_w, _ = tw._get_split_pill_dimensions()
                pill_center = tw.split_pill.get_margin_start() + (pill_w / 2.0)
                diff = abs(pill_center - needle_center)

                assert diff <= 1.0, f"In {target_lang}, split pill center ({pill_center}) deviated from needle center ({needle_center}) by {diff}px"
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




