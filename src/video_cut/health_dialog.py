"""System health and diagnostics dialog with automated repair actions."""

from __future__ import annotations

import threading
from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk

from .i18n import _
from .services.health_service import HealthCheckItem, HealthService


class HealthCheckDialog(Adw.Window):
    """Modal dialog presenting system health status, diagnostics, and automated repairs."""

    def __init__(
        self,
        parent: Gtk.Window | None = None,
        on_status_changed: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(transient_for=parent, modal=True)
        self.set_title(_("System Health & Setup"))
        self.set_default_size(780, 500)
        self._on_status_changed = on_status_changed
        self._items: list[HealthCheckItem] = []

        self._build_ui()
        self.refresh_checks()

    def _build_ui(self) -> None:
        toolbar_view = Adw.ToolbarView()
        self.set_content(toolbar_view)

        header_bar = Adw.HeaderBar()
        header_bar.set_show_title(True)
        header_bar.set_show_end_title_buttons(False)
        header_bar.set_show_start_title_buttons(False)

        btn_refresh = Gtk.Button()
        content_refresh = Adw.ButtonContent(
            icon_name="view-refresh-symbolic",
            label=_("Re-check"),
        )
        btn_refresh.set_child(content_refresh)
        btn_refresh.connect("clicked", lambda _: self.refresh_checks())
        header_bar.pack_start(btn_refresh)

        btn_close = Gtk.Button(label=_("Close"))
        btn_close.add_css_class("suggested-action")
        btn_close.connect("clicked", lambda _: self.close())
        header_bar.pack_end(btn_close)
        toolbar_view.add_top_bar(header_bar)

        self.toast_overlay = Adw.ToastOverlay()
        self.pref_page = Adw.PreferencesPage()
        self.toast_overlay.set_child(self.pref_page)
        toolbar_view.set_content(self.toast_overlay)

        # Overall Status Group
        self.grp_status = Adw.PreferencesGroup()
        self.pref_page.add(self.grp_status)

        self.row_overall = Adw.ActionRow()
        self.grp_status.add(self.row_overall)

        # Group 1: FFmpeg Multimedia Engine
        self.grp_ffmpeg = Adw.PreferencesGroup(title=_("FFmpeg Multimedia Engine"))
        self.pref_page.add(self.grp_ffmpeg)

        self.row_ffmpeg = Adw.ActionRow(title=_("FFmpeg Tools"))
        self.grp_ffmpeg.add(self.row_ffmpeg)

        # Group 2: .vcut Project File Association
        self.grp_association = Adw.PreferencesGroup(title=_(".vcut Project File Association"))
        self.pref_page.add(self.grp_association)

        self.row_association = Adw.ActionRow(title=_("Desktop File Association"))
        self.grp_association.add(self.row_association)

    def _show_toast(self, text: str, timeout: int = 2) -> None:
        toast = Adw.Toast.new(text)
        toast.set_use_markup(False)
        toast.set_timeout(timeout)
        self.toast_overlay.add_toast(toast)

    def _copy_to_clipboard(self, text: str) -> None:
        display = self.get_display()
        if display:
            clipboard = display.get_clipboard()
            clipboard.set(text)

    def _set_overall_prefix(self, widget: Gtk.Widget) -> None:
        if hasattr(self, "_prefix_overall") and self._prefix_overall:
            self.row_overall.remove(self._prefix_overall)
            self._prefix_overall = None
        self._prefix_overall = widget
        self.row_overall.add_prefix(widget)

    def _set_row_prefix(self, row: Adw.ActionRow, attr_name: str, widget: Gtk.Widget) -> None:
        if hasattr(self, attr_name):
            old = getattr(self, attr_name)
            if old:
                row.remove(old)
                setattr(self, attr_name, None)
        setattr(self, attr_name, widget)
        row.add_prefix(widget)

    def _create_info_button(self, item: HealthCheckItem) -> Gtk.MenuButton:
        """Create an information MenuButton with a popover displaying diagnostic details."""
        btn_info = Gtk.MenuButton()
        btn_info.set_icon_name("dialog-information-symbolic")
        btn_info.set_tooltip_text(_("View component details"))
        btn_info.add_css_class("flat")

        popover = Gtk.Popover()
        popover.set_autohide(True)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_margin_top(12)
        box.set_margin_bottom(12)
        box.set_margin_start(12)
        box.set_margin_end(12)
        box.set_size_request(400, -1)

        # Header title with icon
        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        icon_name = (
            "object-select-symbolic" if item.status == "ok"
            else "dialog-error-symbolic" if item.status == "error"
            else "dialog-warning-symbolic"
        )
        icon = Gtk.Image.new_from_icon_name(icon_name)
        icon.add_css_class(
            "success" if item.status == "ok"
            else "error" if item.status == "error"
            else "warning"
        )
        header_box.append(icon)

        lbl_title = Gtk.Label(label=item.title)
        lbl_title.add_css_class("title-4")
        lbl_title.set_xalign(0.0)
        header_box.append(lbl_title)
        box.append(header_box)

        # Scrolled monospace text view for diagnostic details
        scroll = Gtk.ScrolledWindow()
        scroll.set_min_content_height(140)
        scroll.set_max_content_height(280)
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)

        txt_view = Gtk.TextView()
        txt_view.set_editable(False)
        txt_view.set_cursor_visible(False)
        txt_view.set_monospace(True)
        txt_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        txt_view.get_buffer().set_text(item.details)
        scroll.set_child(txt_view)
        box.append(scroll)

        # Copy Details button
        btn_copy = Gtk.Button()
        content = Adw.ButtonContent(
            icon_name="edit-copy-symbolic",
            label=_("Copy Details"),
        )
        btn_copy.set_child(content)
        btn_copy.set_halign(Gtk.Align.END)

        def on_copy_details(_: Gtk.Button) -> None:
            self._copy_to_clipboard(item.details)
            content.set_icon_name("object-select-symbolic")
            content.set_label(_("Copied!"))
            btn_copy.add_css_class("suggested-action")
            GLib.timeout_add(2000, lambda: (
                content.set_icon_name("edit-copy-symbolic"),
                content.set_label(_("Copy Details")),
                btn_copy.remove_css_class("suggested-action")
            ) and False)
            self._show_toast(_("Details copied to clipboard"))

        btn_copy.connect("clicked", on_copy_details)
        box.append(btn_copy)

        popover.set_child(box)
        btn_info.set_popover(popover)
        return btn_info

    def refresh_checks(self) -> None:
        """Run diagnostics in background thread and update UI on completion."""
        self.row_overall.set_title(_("Running system health check..."))
        self.row_overall.set_subtitle(_("Probing FFmpeg and file associations..."))
        icon_spin = Gtk.Spinner()
        icon_spin.start()
        self._set_overall_prefix(icon_spin)

        def worker() -> None:
            items = HealthService.run_all_checks()
            GLib.idle_add(self._apply_results, items)

        threading.Thread(target=worker, daemon=True).start()

    def _apply_results(self, items: list[HealthCheckItem]) -> None:
        self._items = items
        has_error = any(it.status == "error" for it in items)
        has_warning = any(it.status == "warning" for it in items)

        # Update Overall Status Banner
        if has_error:
            self.row_overall.set_title(_("Action Required"))
            self.row_overall.set_subtitle(_("Essential multimedia components are missing or misconfigured."))
            icon = Gtk.Image.new_from_icon_name("dialog-error-symbolic")
            icon.add_css_class("error")
            self._set_overall_prefix(icon)
        elif has_warning:
            self.row_overall.set_title(_("Configuration Recommended"))
            self.row_overall.set_subtitle(_("Video Cut is operational, but file associations can be improved."))
            icon = Gtk.Image.new_from_icon_name("dialog-warning-symbolic")
            icon.add_css_class("warning")
            self._set_overall_prefix(icon)
        else:
            self.row_overall.set_title(_("All Systems Operational"))
            self.row_overall.set_subtitle(_("FFmpeg and .vcut file associations are ready."))
            icon = Gtk.Image.new_from_icon_name("object-select-symbolic")
            icon.add_css_class("success")
            self._set_overall_prefix(icon)

        # Update individual component rows
        for item in items:
            if item.id == "ffmpeg":
                self._update_ffmpeg_row(item)
            elif item.id == "file_association":
                self._update_association_row(item)

        if self._on_status_changed:
            self._on_status_changed()

    def _update_ffmpeg_row(self, item: HealthCheckItem) -> None:
        self.row_ffmpeg.set_subtitle(item.summary)
        icon_name = "object-select-symbolic" if item.status == "ok" else "dialog-error-symbolic"
        icon = Gtk.Image.new_from_icon_name(icon_name)
        icon.add_css_class("success" if item.status == "ok" else "error")
        self._set_row_prefix(self.row_ffmpeg, "_prefix_ffmpeg", icon)

        if hasattr(self, "_suffix_ffmpeg") and self._suffix_ffmpeg:
            self.row_ffmpeg.remove(self._suffix_ffmpeg)
            self._suffix_ffmpeg = None

        suffix_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        suffix_box.set_valign(Gtk.Align.CENTER)

        if item.status != "ok":
            if item.fix_command:
                entry = Gtk.Entry()
                entry.set_text(item.fix_command)
                entry.set_editable(False)
                entry.set_can_focus(True)
                entry.add_css_class("monospace")
                entry.set_width_chars(24)
                entry.set_tooltip_text(_("Terminal install command"))
                suffix_box.append(entry)

                btn_copy = Gtk.Button()
                content_copy = Adw.ButtonContent(
                    icon_name="edit-copy-symbolic",
                    label=_("Copy"),
                )
                btn_copy.set_child(content_copy)
                btn_copy.set_tooltip_text(_("Copy command to clipboard"))

                cmd_text = item.fix_command

                def on_copy_cmd(_: Gtk.Button, cmd: str = cmd_text) -> None:
                    self._copy_to_clipboard(cmd)
                    content_copy.set_icon_name("object-select-symbolic")
                    content_copy.set_label(_("Copied!"))
                    btn_copy.add_css_class("suggested-action")
                    GLib.timeout_add(2000, lambda: (
                        content_copy.set_icon_name("edit-copy-symbolic"),
                        content_copy.set_label(_("Copy")),
                        btn_copy.remove_css_class("suggested-action")
                    ) and False)
                    self._show_toast(_("Command copied to clipboard"))

                btn_copy.connect("clicked", on_copy_cmd)
                suffix_box.append(btn_copy)

            if item.fix_url:
                btn_web = Gtk.Button()
                content_web = Adw.ButtonContent(
                    icon_name="web-browser-symbolic",
                    label=_("FFmpeg Website"),
                )
                btn_web.set_child(content_web)
                btn_web.set_tooltip_text(item.fix_url)
                target_url = item.fix_url
                btn_web.connect("clicked", lambda _: Gio.AppInfo.launch_default_for_uri(target_url, None))
                suffix_box.append(btn_web)

        btn_info = self._create_info_button(item)
        suffix_box.append(btn_info)

        self._suffix_ffmpeg = suffix_box
        self.row_ffmpeg.add_suffix(suffix_box)

    def _update_association_row(self, item: HealthCheckItem) -> None:
        self.row_association.set_subtitle(item.summary)
        icon_name = "object-select-symbolic" if item.status == "ok" else "dialog-warning-symbolic"
        icon = Gtk.Image.new_from_icon_name(icon_name)
        icon.add_css_class("success" if item.status == "ok" else "warning")
        self._set_row_prefix(self.row_association, "_prefix_association", icon)

        if hasattr(self, "_suffix_association") and self._suffix_association:
            self.row_association.remove(self._suffix_association)
            self._suffix_association = None

        suffix_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        suffix_box.set_valign(Gtk.Align.CENTER)

        if item.status != "ok":
            btn_fix = Gtk.Button(label=item.fix_label or _("Register Association"))
            btn_fix.add_css_class("suggested-action")
            btn_fix.connect("clicked", self._on_fix_association_clicked)
            suffix_box.append(btn_fix)

        btn_info = self._create_info_button(item)
        suffix_box.append(btn_info)

        self._suffix_association = suffix_box
        self.row_association.add_suffix(suffix_box)

    def _on_fix_association_clicked(self, btn: Gtk.Button) -> None:
        btn.set_sensitive(False)
        btn.set_label(_("Registering..."))

        def worker() -> None:
            ok, msg = HealthService.fix_file_association()

            def done() -> None:
                self._show_toast(msg)
                self.refresh_checks()

            GLib.idle_add(done)

        threading.Thread(target=worker, daemon=True).start()
