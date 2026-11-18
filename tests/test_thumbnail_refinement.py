"""Tests for dynamic thumbnail refinement when smaller segments are enlarged (Issue #32)."""

from pathlib import Path
from unittest.mock import MagicMock

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk

from video_cut.models import Segment
from video_cut.segment_card import SegmentCard
from video_cut.services.thumbnail_service import ThumbnailService
from video_cut.window import MainWindow


def _drain_glib_events(condition=None, max_iterations=200):
    ctx = GLib.MainContext.default()
    for _ in range(max_iterations):
        had_events = ctx.iteration(False)
        if condition and condition():
            break
        if not had_events and not condition:
            break


def test_thumbnail_refinement_triggers_when_thumbs_repeated(sample_video, tmp_path):
    """When a segment filmstrip contains repeated thumbnails, background refinement extracts unique frames."""
    ts = ThumbnailService(cache_dir=tmp_path)
    key = ts._file_key(sample_video)

    # Simulate a coarsely sampled video cache: only a single frame at 0.0s
    initial_thumb = tmp_path / "thumb_initial_0.jpg"
    initial_thumb.write_bytes(b"dummy_jpeg_data")
    with ts._cache_lock:
        ts._cache[key] = [(0.0, initial_thumb)]
        ts._completed_keys.add(key)

    listener_called = []
    ts.add_cache_updated_listener(lambda p: listener_called.append(p))

    # Request 4 thumbnails for a 2.0s duration (start=0.0, end=2.0)
    # Target times will be roughly 0.0, 0.67, 1.33, 1.96s
    initial_results = ts.get_thumbnails_for_segment(sample_video, start_time=0.0, end_time=2.0, max_thumbs=4)
    assert len(initial_results) == 4
    # All 4 slots initially point to the only cached frame (repeated thumb)
    assert initial_results[0] == initial_thumb
    assert initial_results[1] == initial_thumb
    assert initial_results[2] == initial_thumb
    assert initial_results[3] == initial_thumb

    # Wait for background refinement to finish extracting the missing frames
    ts.wait_idle()
    _drain_glib_events(condition=lambda: len(listener_called) >= 1)

    # Cache should now contain the refined frames
    with ts._cache_lock:
        cached_count = len(ts._cache.get(key, []))
    assert cached_count >= 3

    # Subsequent call to get_thumbnails_for_segment should now return distinct thumbnails
    refined_results = ts.get_thumbnails_for_segment(sample_video, start_time=0.0, end_time=2.0, max_thumbs=4)
    assert len(refined_results) == 4
    valid_refined = [p for p in refined_results if p is not None]
    # No longer all identical!
    assert len(set(valid_refined)) > 1
    # Listener was notified of cache update
    assert len(listener_called) >= 1
    assert listener_called[0] == sample_video


def test_thumbnail_refinement_skips_when_no_repeats(sample_video, tmp_path):
    """When all thumbnail slots already resolve to distinct frames, refinement is not triggered."""
    ts = ThumbnailService(cache_dir=tmp_path)
    key = ts._file_key(sample_video)

    # Populate cache with 5 distinct thumbnails evenly spaced
    frames = []
    for i in range(5):
        fp = tmp_path / f"thumb_{i}.jpg"
        fp.write_bytes(b"frame")
        frames.append((float(i * 0.5), fp))

    with ts._cache_lock:
        ts._cache[key] = frames
        ts._completed_keys.add(key)

    mock_refine = MagicMock()
    ts.refine_segment_thumbnails_async = mock_refine

    results = ts.get_thumbnails_for_segment(sample_video, start_time=0.0, end_time=2.0, max_thumbs=5)
    assert len(results) == 5
    assert len(set(results)) == 5
    assert not mock_refine.called


def test_thumbnail_refinement_skips_images(tmp_path):
    """Image files naturally repeat the same image frame and should never trigger refinement."""
    ts = ThumbnailService(cache_dir=tmp_path)
    img_path = tmp_path / "photo.png"
    img_path.write_bytes(b"dummy_png")

    ts.register_image_thumbnail(img_path, img_path, duration=5.0)

    mock_refine = MagicMock()
    ts.refine_segment_thumbnails_async = mock_refine

    results = ts.get_thumbnails_for_segment(img_path, start_time=0.0, end_time=5.0, max_thumbs=6)
    assert len(results) == 6
    assert all(r == img_path for r in results)
    assert not mock_refine.called


def test_thumbnail_refinement_skips_single_thumb(sample_video, tmp_path):
    """When only 1 thumbnail is requested, refinement is not triggered."""
    ts = ThumbnailService(cache_dir=tmp_path)
    key = ts._file_key(sample_video)
    initial_thumb = tmp_path / "thumb_0.jpg"
    initial_thumb.write_bytes(b"data")
    with ts._cache_lock:
        ts._cache[key] = [(0.0, initial_thumb)]
        ts._completed_keys.add(key)

    mock_refine = MagicMock()
    ts.refine_segment_thumbnails_async = mock_refine

    results = ts.get_thumbnails_for_segment(sample_video, start_time=0.0, end_time=2.0, max_thumbs=1)
    assert len(results) == 1
    assert not mock_refine.called


def test_thumbnail_refinement_deduplicates_inflight(sample_video, tmp_path):
    """Repeated calls to get_thumbnails_for_segment while refinement is in progress do not spawn duplicate tasks."""
    ts = ThumbnailService(cache_dir=tmp_path)
    key = ts._file_key(sample_video)
    initial_thumb = tmp_path / "thumb_0.jpg"
    initial_thumb.write_bytes(b"data")
    with ts._cache_lock:
        ts._cache[key] = [(0.0, initial_thumb)]
        ts._completed_keys.add(key)

    spawned = []
    orig_submit = ts.executor.submit

    def counting_submit(fn, *args, **kwargs):
        spawned.append(fn)
        return orig_submit(fn, *args, **kwargs)

    ts.executor.submit = counting_submit

    # Multiple rapid calls for the same segment (e.g. during layout resize)
    for _ in range(5):
        ts.get_thumbnails_for_segment(sample_video, start_time=0.0, end_time=2.0, max_thumbs=4)

    # Should only spawn 1 refinement task, not 5
    assert len(spawned) == 1
    ts.wait_idle()


def test_thumbnail_cache_updated_listeners(tmp_path):
    """Verify add/remove cache updated listeners and debounced dispatch."""
    ts = ThumbnailService(cache_dir=tmp_path)
    calls = []

    def listener(p: Path):
        calls.append(p)

    ts.add_cache_updated_listener(listener)
    sample_p = Path("/tmp/video.mp4")

    # Trigger notification
    ts._notify_cache_updated(sample_p)
    ts._notify_cache_updated(sample_p)

    _drain_glib_events(condition=lambda: len(calls) == 1)

    # Debounced: single dispatch
    assert len(calls) == 1
    assert calls[0] == sample_p

    # Remove listener
    ts.remove_cache_updated_listener(listener)
    ts._notify_cache_updated(sample_p)
    _drain_glib_events()

    assert len(calls) == 1


def test_thumbnail_refinement_integration_no_toast(sample_video):
    """Verify MainWindow integrates thumbnail refinement and updates layout without showing toasts."""
    app = Adw.Application(application_id="io.github.thor27.video-cut.test.refinement")
    test_error = None

    def run_test(app):
        nonlocal test_error
        win = None
        try:
            win = MainWindow(application=app)
            # Create a timeline with a short segment
            seg = Segment(id="seg1", source_path=sample_video, start_time=0.0, end_time=2.0, source_duration=2.0)
            win.timeline.segments = [seg]
            win.timeline_widget.rebuild_filmstrip()

            # Ensure has_pending_jobs() remains false during refinement
            assert not win.thumbnail_service.has_pending_jobs()

            # Verify no progress toast was created
            assert win._thumb_toast is None
        except BaseException as e:
            test_error = e
        finally:
            if win:
                win.close()
            app.quit()

    app.connect("activate", run_test)
    app.run(["test"])
    if test_error:
        raise test_error


def test_thumbnail_refinement_on_segment_deletion(sample_video):
    """Verify deleting other segments enlarges a small segment and triggers refinement to replace repeated thumbs."""
    app = Adw.Application(application_id="io.github.thor27.video-cut.test.refinement.delete")
    test_error = None

    def run_test(app):
        nonlocal test_error
        win = None
        try:
            win = MainWindow(application=app)
            # Pre-generate initial thumbnails
            win.thumbnail_service.generate_thumbnails_async(sample_video, 2.0, on_done=lambda p, r: None)
            win.thumbnail_service.wait_idle()
            _drain_glib_events()

            # Segment 1 is small (0.0 to 0.4s), Segment 2 is larger (0.4 to 2.0s)
            seg1 = Segment(id="seg1", source_path=sample_video, start_time=0.0, end_time=0.4, source_duration=2.0)
            seg2 = Segment(id="seg2", source_path=sample_video, start_time=0.4, end_time=2.0, source_duration=2.0)
            win.timeline.segments = [seg1, seg2]
            win.timeline_widget.rebuild_filmstrip()

            card1 = win.timeline_widget._cards.get("seg1")
            initial_count = len(card1._current_thumb_list)

            # Delete seg2 so seg1 expands proportionally to fill the timeline
            win.delete_segment("seg2")

            card1_enlarged = win.timeline_widget._cards.get("seg1")
            enlarged_count = len(card1_enlarged._current_thumb_list)
            assert enlarged_count > initial_count

            # Wait for background refinement to extract new frames and update filmstrip
            win.thumbnail_service.wait_idle()
            _drain_glib_events()

            # Filmstrip should now contain distinct thumbnails without window resize
            refined_thumbs = card1_enlarged._current_thumb_list
            assert len(refined_thumbs) == enlarged_count
            assert len(set(refined_thumbs)) > 1
        except BaseException as e:
            test_error = e
        finally:
            if win:
                win.close()
            app.quit()

    app.connect("activate", run_test)
    app.run(["test"])
    if test_error:
        raise test_error


def test_segment_card_duplicates_thumbs_and_has_no_spinners(sample_video, tmp_path):
    """Verify SegmentCard never creates Gtk.Spinner and duplicates fast initial thumb across slots."""
    seg = Segment(id="s_test", source_path=sample_video, start_time=0.0, end_time=2.0, source_duration=2.0)
    card = SegmentCard(
        segment=seg,
        index=0,
        is_selected=False,
        on_select=lambda _: None,
    )

    # 1. When empty, no Gtk.Spinner in filmstrip
    card.update_thumbnails([])
    spinners = []
    child = card.filmstrip_box.get_first_child()
    while child:
        if isinstance(child, Gtk.Spinner):
            spinners.append(child)
        sub = child.get_first_child() if hasattr(child, "get_first_child") else None
        while sub:
            if isinstance(sub, Gtk.Spinner):
                spinners.append(sub)
            sub = sub.get_next_sibling()
        child = child.get_next_sibling()
    assert len(spinners) == 0

    # 2. When only 1 thumb is provided but 4 slots requested (e.g. [thumb1, None, None, None])
    thumb1 = tmp_path / "t1.jpg"
    thumb1.write_bytes(b"dummy")
    card.update_thumbnails([thumb1, None, None, None])

    # All 4 slots should be populated with Gtk.Picture using thumb1 as duplicate fallback, 0 spinners
    pics = []
    child = card.filmstrip_box.get_first_child()
    while child:
        if isinstance(child, Gtk.Picture):
            pics.append(child)
        if isinstance(child, Gtk.Spinner):
            spinners.append(child)
        sub = child.get_first_child() if hasattr(child, "get_first_child") else None
        while sub:
            if isinstance(sub, Gtk.Spinner):
                spinners.append(sub)
            sub = sub.get_next_sibling()
        child = child.get_next_sibling()

    assert len(spinners) == 0
    assert len(pics) == 4


def test_placeholder_thumbnail_loading_class(sample_video, tmp_path):
    """Verify placeholder thumbnails receive thumbnail-loading CSS class and lose it when refined."""
    ts = ThumbnailService(cache_dir=tmp_path)
    key = ts._file_key(sample_video)

    thumb1 = tmp_path / "t1.jpg"
    thumb1.write_bytes(b"dummy1")

    with ts._cache_lock:
        ts._cache[key] = [(0.0, thumb1)]
        ts._completed_keys.add(key)

    items = ts.get_segment_thumbnail_items(sample_video, start_time=0.0, end_time=2.0, max_thumbs=4)
    assert len(items) == 4
    # First slot is at ~0.0s, matches closest timestamp within tolerance -> is_placeholder is False
    assert items[0] == (thumb1, False)
    # Remaining slots are distant borrowed frames -> is_placeholder is True
    assert items[1] == (thumb1, True)
    assert items[2] == (thumb1, True)
    assert items[3] == (thumb1, True)

    seg = Segment(id="s_load", source_path=sample_video, start_time=0.0, end_time=2.0, source_duration=2.0)
    card = SegmentCard(
        segment=seg,
        index=0,
        is_selected=False,
        on_select=lambda _: None,
    )
    card.update_thumbnails(items)

    pics: list[Gtk.Picture] = []
    child = card.filmstrip_box.get_first_child()
    while child:
        if isinstance(child, Gtk.Picture):
            pics.append(child)
        child = child.get_next_sibling()

    assert len(pics) == 4
    assert not pics[0].has_css_class("thumbnail-loading")
    assert pics[1].has_css_class("thumbnail-loading")
    assert pics[2].has_css_class("thumbnail-loading")
    assert pics[3].has_css_class("thumbnail-loading")

    # Now simulate when refined thumbnails are extracted and all are real
    thumb2 = tmp_path / "t2.jpg"
    thumb3 = tmp_path / "t3.jpg"
    thumb4 = tmp_path / "t4.jpg"
    thumb2.write_bytes(b"dummy2")
    thumb3.write_bytes(b"dummy3")
    thumb4.write_bytes(b"dummy4")
    refined_items = [(thumb1, False), (thumb2, False), (thumb3, False), (thumb4, False)]
    card.update_thumbnails(refined_items)

    pics = []
    child = card.filmstrip_box.get_first_child()
    while child:
        if isinstance(child, Gtk.Picture):
            pics.append(child)
        child = child.get_next_sibling()

    assert len(pics) == 4
    assert not any(p.has_css_class("thumbnail-loading") for p in pics)


def test_completed_thumbnails_never_pulsate(sample_video, tmp_path):
    """Verify that after full thumbnail generation completes, no thumbnails have is_placeholder=True or thumbnail-loading."""
    ts = ThumbnailService(cache_dir=tmp_path)
    ts.generate_thumbnails_async(sample_video, 2.0, on_done=lambda *_: None)
    ts.wait_idle()
    _drain_glib_events()

    items = ts.get_segment_thumbnail_items(sample_video, start_time=0.0, end_time=2.0, max_thumbs=6)
    assert len(items) == 6
    # All slots have real thumbnails loaded; none are placeholders!
    assert all(is_placeholder is False for _, is_placeholder in items)

    seg = Segment(id="s_done", source_path=sample_video, start_time=0.0, end_time=2.0, source_duration=2.0)
    card = SegmentCard(
        segment=seg,
        index=0,
        is_selected=False,
        on_select=lambda _: None,
    )
    card.update_thumbnails(items)

    pics: list[Gtk.Picture] = []
    child = card.filmstrip_box.get_first_child()
    while child:
        if isinstance(child, Gtk.Picture):
            pics.append(child)
        child = child.get_next_sibling()

    assert len(pics) == 6
    # None of the pictures have the thumbnail-loading animation!
    assert not any(p.has_css_class("thumbnail-loading") for p in pics)
