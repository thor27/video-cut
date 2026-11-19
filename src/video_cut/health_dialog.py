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
        self.set_default_size(720, 560)
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

        self.pref_page = Adw.PreferencesPage()
        toolbar_view.set_content(self.pref_page)

        # Overall Status Group
        self.grp_status = Adw.PreferencesGroup()
        self.pref_page.add(self.grp_status)

        self.row_overall = Adw.ActionRow()
        self.grp_status.add(self.row_overall)

        # Components Group
        self.grp_components = Adw.PreferencesGroup(title=_("System Components and Configuration"))
        self.pref_page.add(self.grp_components)

        self.row_ffmpeg = Adw.ActionRow(title=_("FFmpeg Multimedia Engine"))
        self.grp_components.add(self.row_ffmpeg)

        self.row_association = Adw.ActionRow(title=_(".vcut Project File Association"))
        self.grp_components.add(self.row_association)

        self.row_codecs = Adw.ActionRow(title=_("Multimedia Video and Audio Codecs"))
        self.grp_components.add(self.row_codecs)

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

    def refresh_checks(self) -> None:
        """Run diagnostics in background thread and update UI on completion."""
        self.row_overall.set_title(_("Running system health check..."))
        self.row_overall.set_subtitle(_("Probing FFmpeg, file associations, and multimedia codecs..."))
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
            self.row_overall.set_subtitle(_("Video Cut is operational, but some features or associations can be improved."))
            icon = Gtk.Image.new_from_icon_name("dialog-warning-symbolic")
            icon.add_css_class("warning")
            self._set_overall_prefix(icon)
        else:
            self.row_overall.set_title(_("All Systems Operational"))
            self.row_overall.set_subtitle(_("FFmpeg, .vcut file associations, and multimedia codecs are ready."))
            icon = Gtk.Image.new_from_icon_name("object-select-symbolic")
            icon.add_css_class("success")
            self._set_overall_prefix(icon)

        # Update individual rows
        for item in items:
            if item.id == "ffmpeg":
                self._update_ffmpeg_row(item)
            elif item.id == "file_association":
                self._update_association_row(item)
            elif item.id == "codecs":
                self._update_codecs_row(item)

        if self._on_status_changed:
            self._on_status_changed()

    def _clear_suffixes(self, row: Adw.ActionRow) -> None:
        # ActionRow doesn't have clear_suffixes, but we can manage a container
        pass

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
                btn_copy = Gtk.Button(label=_("Copy Install Command"))
                btn_copy.set_icon_name("edit-copy-symbolic")
                btn_copy.connect("clicked", lambda _: self._copy_to_clipboard(item.fix_command or ""))
                suffix_box.append(btn_copy)

            if item.fix_url:
                btn_web = Gtk.Button(label=_("Download Site"))
                btn_web.set_icon_name("web-browser-symbolic")
                btn_web.connect("clicked", lambda _: Gio.AppInfo.launch_default_for_uri(item.fix_url or "", None))
                suffix_box.append(btn_web)
        else:
            btn_info = Gtk.Button()
            btn_info.set_icon_name("dialog-information-symbolic")
            btn_info.set_tooltip_text(item.details)
            btn_info.add_css_class("flat")
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
            btn_fix = Gtk.Button(label=_("Register Association"))
            btn_fix.add_css_class("suggested-action")
            btn_fix.connect("clicked", self._on_fix_association_clicked)
            suffix_box.append(btn_fix)
        else:
            btn_info = Gtk.Button()
            btn_info.set_icon_name("dialog-information-symbolic")
            btn_info.set_tooltip_text(item.details)
            btn_info.add_css_class("flat")
            suffix_box.append(btn_info)

        self._suffix_association = suffix_box
        self.row_association.add_suffix(suffix_box)

    def _update_codecs_row(self, item: HealthCheckItem) -> None:
        self.row_codecs.set_subtitle(item.summary)
        icon_name = "object-select-symbolic" if item.status == "ok" else "dialog-information-symbolic"
        icon = Gtk.Image.new_from_icon_name(icon_name)
        icon.add_css_class("success" if item.status == "ok" else "accent")
        self._set_row_prefix(self.row_codecs, "_prefix_codecs", icon)

        if hasattr(self, "_suffix_codecs") and self._suffix_codecs:
            self.row_codecs.remove(self._suffix_codecs)
            self._suffix_codecs = None

        suffix_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        suffix_box.set_valign(Gtk.Align.CENTER)

        if item.fix_command:
            btn_copy = Gtk.Button(label=_("Copy Codec Command"))
            btn_copy.set_icon_name("edit-copy-symbolic")
            btn_copy.connect("clicked", lambda _: self._copy_to_clipboard(item.fix_command or ""))
            suffix_box.append(btn_copy)

        btn_info = Gtk.Button()
        btn_info.set_icon_name("dialog-information-symbolic")
        btn_info.set_tooltip_text(item.details)
        btn_info.add_css_class("flat")
        suffix_box.append(btn_info)

        self._suffix_codecs = suffix_box
        self.row_codecs.add_suffix(suffix_box)

    def _on_fix_association_clicked(self, btn: Gtk.Button) -> None:
        btn.set_sensitive(False)
        btn.set_label(_("Registering..."))

        def worker() -> None:
            ok, msg = HealthService.fix_file_association()
            def done():
                self.refresh_checks()
            GLib.idle_add(done)

        threading.Thread(target=worker, daemon=True).start()

    def _copy_to_clipboard(self, text: str) -> None:
        display = self.get_display()
        if display:
            clipboard = display.get_clipboard()
            clipboard.set(text)
