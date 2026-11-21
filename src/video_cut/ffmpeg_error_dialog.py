"""Modal dialog for displaying FFmpeg execution errors with full copyable logs."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk

from .i18n import _


class FFmpegErrorDialog(Adw.Window):
    """Modal dialog presenting FFmpeg failure information with full copyable output."""

    def __init__(
        self,
        parent: Gtk.Window | None = None,
        title: str | None = None,
        heading: str | None = None,
        message: str | None = None,
        error_details: str | None = None,
    ) -> None:
        super().__init__(transient_for=parent, modal=True)
        self.set_title(title or _("Export Failed"))
        self.set_default_size(700, 520)

        self._error_text = (error_details or _("No error details available.")).strip()
        self._build_ui(
            heading=heading or _("FFmpeg Operation Failed"),
            message=message
            or _(
                "An error occurred during video processing. The full error output is shown below:"
            ),
        )

    def _build_ui(self, heading: str, message: str) -> None:
        toolbar_view = Adw.ToolbarView()
        self.set_content(toolbar_view)

        header_bar = Adw.HeaderBar()
        header_bar.set_show_title(True)
        header_bar.set_show_end_title_buttons(False)
        header_bar.set_show_start_title_buttons(False)
        toolbar_view.add_top_bar(header_bar)

        # Allow dismissing with Escape key
        key_controller = Gtk.EventControllerKey()
        key_controller.connect("key-pressed", self._on_key_pressed)
        self.add_controller(key_controller)

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        main_box.set_margin_start(24)
        main_box.set_margin_end(24)
        main_box.set_margin_top(16)
        main_box.set_margin_bottom(20)
        main_box.set_vexpand(True)
        toolbar_view.set_content(main_box)

        # Header section with icon, title, and description
        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        main_box.append(header_box)

        icon = Gtk.Image.new_from_icon_name("dialog-error-symbolic")
        icon.set_pixel_size(48)
        icon.add_css_class("error")
        header_box.append(icon)

        text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        text_box.set_hexpand(True)
        header_box.append(text_box)

        lbl_heading = Gtk.Label(label=heading)
        lbl_heading.set_xalign(0.0)
        lbl_heading.add_css_class("title-2")
        text_box.append(lbl_heading)

        lbl_msg = Gtk.Label(label=message)
        lbl_msg.set_xalign(0.0)
        lbl_msg.set_wrap(True)
        lbl_msg.add_css_class("dim-label")
        text_box.append(lbl_msg)

        # Section label for the error log
        lbl_log_title = Gtk.Label(label=_("Error Details & Command Output"))
        lbl_log_title.set_xalign(0.0)
        lbl_log_title.add_css_class("heading")
        main_box.append(lbl_log_title)

        # Error log text box in a scrolled card
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_hexpand(True)
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scrolled.add_css_class("card")
        main_box.append(scrolled)

        text_view = Gtk.TextView()
        text_view.set_editable(False)
        text_view.set_cursor_visible(True)
        text_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        text_view.set_left_margin(12)
        text_view.set_right_margin(12)
        text_view.set_top_margin(12)
        text_view.set_bottom_margin(12)
        text_view.add_css_class("monospace")
        text_view.get_buffer().set_text(self._error_text)
        scrolled.set_child(text_view)

        # Bottom action area with single Copy button and Close button
        bottom_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        bottom_box.set_halign(Gtk.Align.END)
        main_box.append(bottom_box)

        self._btn_copy = Gtk.Button()
        self._copy_btn_content = Adw.ButtonContent(
            icon_name="edit-copy-symbolic",
            label=_("Copy Details"),
        )
        self._btn_copy.set_child(self._copy_btn_content)
        self._btn_copy.set_tooltip_text(_("Copy full error details to clipboard"))
        self._btn_copy.connect("clicked", self._on_copy_clicked)
        bottom_box.append(self._btn_copy)

        btn_close = Gtk.Button(label=_("Close"))
        btn_close.add_css_class("suggested-action")
        btn_close.connect("clicked", lambda _: self.close())
        bottom_box.append(btn_close)

    def _on_key_pressed(self, _controller, keyval, _keycode, _state) -> bool:
        if keyval == Gdk.KEY_Escape:
            self.close()
            return True
        return False

    def _on_copy_clicked(self, _btn: Gtk.Button) -> None:
        display = self.get_display()
        if display:
            clipboard = display.get_clipboard()
            clipboard.set(self._error_text)

        self._copy_btn_content.set_label(_("Copied!"))
        self._copy_btn_content.set_icon_name("object-select-symbolic")
        self._btn_copy.add_css_class("success")

        def _reset_btn():
            self._copy_btn_content.set_label(_("Copy Details"))
            self._copy_btn_content.set_icon_name("edit-copy-symbolic")
            self._btn_copy.remove_css_class("success")
            return GLib.SOURCE_REMOVE

        GLib.timeout_add_seconds(2, _reset_btn)
