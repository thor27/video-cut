"""Reusable modal dialog for long-running operations with progress reporting."""

from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk, Pango

from .i18n import _


class ProgressModal(Adw.Window):
    """Reusable modal window with an active spinner, progress bar, status labels, and cancel button."""

    def __init__(
        self,
        parent: Gtk.Window,
        title: str,
        initial_status: str,
        initial_details: str = "",
        on_cancel: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(transient_for=parent, modal=True)
        self.set_default_size(420, 200)
        self.set_title(title)
        self.set_deletable(False)

        self._can_close = False
        self._has_received_progress = False
        self._on_cancel_callback = on_cancel
        self._pulse_timer_id: int | None = None

        self.connect("close-request", self._on_close_request)

        toolbar_view = Adw.ToolbarView()
        self.set_content(toolbar_view)

        h_bar = Adw.HeaderBar()
        h_bar.set_show_title(True)
        h_bar.set_show_end_title_buttons(False)
        h_bar.set_show_start_title_buttons(False)
        toolbar_view.add_top_bar(h_bar)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        box.set_margin_top(20)
        box.set_margin_bottom(20)
        box.set_margin_start(24)
        box.set_margin_end(24)
        toolbar_view.set_content(box)

        # Status header with active spinner
        status_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        status_box.set_halign(Gtk.Align.CENTER)

        self.spinner = Gtk.Spinner()
        self.spinner.set_size_request(22, 22)
        self.spinner.start()
        status_box.append(self.spinner)

        self.lbl_status = Gtk.Label(label=initial_status)
        self.lbl_status.add_css_class("heading")
        status_box.append(self.lbl_status)
        box.append(status_box)

        self.progress_bar = Gtk.ProgressBar()
        self.progress_bar.set_show_text(True)
        self.progress_bar.set_fraction(0.0)
        self.progress_bar.set_text("")
        box.append(self.progress_bar)

        self.lbl_details = Gtk.Label(label=initial_details)
        self.lbl_details.add_css_class("caption")
        self.lbl_details.set_ellipsize(Pango.EllipsizeMode.END)
        box.append(self.lbl_details)

        self.btn_cancel = Gtk.Button(label=_("Cancel"))
        self.btn_cancel.set_halign(Gtk.Align.CENTER)
        self.btn_cancel.add_css_class("destructive-action")
        self.btn_cancel.set_focusable(False)
        self.btn_cancel.connect("clicked", self._on_cancel_clicked)
        box.append(self.btn_cancel)

        # Pulse timer while waiting for initial progress update
        self._pulse_timer_id = GLib.timeout_add(100, self._on_pulse_tick)

    def _on_close_request(self, *_) -> bool:
        return not self._can_close

    def _on_pulse_tick(self) -> bool:
        if not self._has_received_progress:
            self.progress_bar.pulse()
            return True
        return False

    def _stop_pulse(self) -> None:
        if self._pulse_timer_id is not None:
            GLib.source_remove(self._pulse_timer_id)
            self._pulse_timer_id = None

    def _on_cancel_clicked(self, *_) -> None:
        self.cancel()

    def update_progress(
        self,
        fraction: float,
        status_text: str | None = None,
        details_text: str | None = None,
    ) -> None:
        """Update progress fraction, percentage text, and optional labels."""
        if not self._has_received_progress:
            self._has_received_progress = True
            self._stop_pulse()

        clamped = max(0.0, min(1.0, fraction))
        self.progress_bar.set_fraction(clamped)
        percent = int(clamped * 100)
        self.progress_bar.set_text(f"{percent}%")

        if status_text is not None:
            self.lbl_status.set_label(status_text)
        if details_text is not None:
            self.lbl_details.set_label(details_text)

    def set_status(self, text: str) -> None:
        self.lbl_status.set_label(text)

    def set_details(self, text: str) -> None:
        self.lbl_details.set_label(text)

    def finish(self) -> None:
        """Stop spinner and timer, allow closing, and close the dialog."""
        self._stop_pulse()
        self.spinner.stop()
        self._can_close = True
        self.set_deletable(True)
        self.close()

    def cancel(self) -> None:
        """Trigger cancellation callback and close dialog."""
        self._stop_pulse()
        self.spinner.stop()
        self._can_close = True
        self.set_deletable(True)
        if self._on_cancel_callback:
            try:
                self._on_cancel_callback()
            except Exception:
                pass
        self.close()
