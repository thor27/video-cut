"""Unit tests for ProgressModal."""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk

from video_cut.progress_modal import ProgressModal


def test_progress_modal_lifecycle():
    # ProgressModal requires an Adw.Application or GTK init
    _ = Adw.Application.new("io.github.thor27.video-cut.test_modal", 0)
    cancelled = False

    def on_cancel():
        nonlocal cancelled
        cancelled = True

    parent = Gtk.Window()
    modal = ProgressModal(
        parent=parent,
        title="Test Progress",
        initial_status="Starting...",
        initial_details="Processing item 1",
        on_cancel=on_cancel,
    )

    assert modal.get_title() == "Test Progress"
    assert modal.lbl_status.get_label() == "Starting..."
    assert modal.lbl_details.get_label() == "Processing item 1"
    assert modal.progress_bar.get_fraction() == 0.0

    # Pulse timer is active initially
    assert modal._pulse_timer_id is not None
    assert modal._has_received_progress is False

    # Update progress
    modal.update_progress(0.42, status_text="Working (42%)...", details_text="42 of 100 done")
    assert modal._has_received_progress is True
    assert modal._pulse_timer_id is None
    assert abs(modal.progress_bar.get_fraction() - 0.42) < 1e-4
    assert modal.progress_bar.get_text() == "42%"
    assert modal.lbl_status.get_label() == "Working (42%)..."
    assert modal.lbl_details.get_label() == "42 of 100 done"

    # Direct setters
    modal.set_status("Almost done...")
    assert modal.lbl_status.get_label() == "Almost done..."
    modal.set_details("Finalizing")
    assert modal.lbl_details.get_label() == "Finalizing"

    # Finish closes cleanly
    modal.finish()
    assert modal._can_close is True


def test_progress_modal_cancel():
    parent = Gtk.Window()
    cancelled = False

    def on_cancel():
        nonlocal cancelled
        cancelled = True

    modal = ProgressModal(
        parent=parent,
        title="Cancel Test",
        initial_status="Starting...",
        on_cancel=on_cancel,
    )

    modal.cancel()
    assert cancelled is True
    assert modal._can_close is True
