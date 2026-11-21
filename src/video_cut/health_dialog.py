"""System health and diagnostics dialog with automated repair actions."""

from __future__ import annotations

import threading
from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk

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
        self.set_default_size(780, 620)
        self._on_status_changed = on_status_changed
        self._items: list[HealthCheckItem] = []

        # Dynamic subrow references
        self._row_ffmpeg_cmd: Adw.ActionRow | None = None
        self._row_ffmpeg_web: Adw.ActionRow | None = None
        self._row_hwaccel_table: Adw.PreferencesRow | None = None
        self._row_codecs_table: Adw.PreferencesRow | None = None
        self._row_codecs_cmd: Adw.ActionRow | None = None

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
        btn_refresh.connect("clicked", lambda _btn: self.refresh_checks())
        header_bar.pack_start(btn_refresh)

        btn_close = Gtk.Button(label=_("Close"))
        btn_close.add_css_class("suggested-action")
        btn_close.connect("clicked", lambda _btn: self.close())
        header_bar.pack_end(btn_close)
        toolbar_view.add_top_bar(header_bar)

        self.toast_overlay = Adw.ToastOverlay()
        self.pref_page = Adw.PreferencesPage()
        self.toast_overlay.set_child(self.pref_page)
        toolbar_view.set_content(self.toast_overlay)

        # 1. Overall Status Group
        self.grp_status = Adw.PreferencesGroup()
        self.pref_page.add(self.grp_status)

        self.row_overall = Adw.ActionRow()
        self.grp_status.add(self.row_overall)

        # 2. FFmpeg Engine Group
        self.grp_ffmpeg = Adw.PreferencesGroup(title=_("FFmpeg Multimedia Engine"))
        self.pref_page.add(self.grp_ffmpeg)

        self.row_ffmpeg = Adw.ActionRow(title=_("FFmpeg Tools"))
        self.grp_ffmpeg.add(self.row_ffmpeg)

        # 3. Hardware Acceleration Group
        self.grp_hwaccel = Adw.PreferencesGroup(
            title=_("Hardware Acceleration"),
            description=_("GPU hardware encoding and decoding support in FFmpeg:"),
        )
        self.pref_page.add(self.grp_hwaccel)

        self.row_hwaccel_summary = Adw.ActionRow(title=_("GPU Acceleration"))
        self.grp_hwaccel.add(self.row_hwaccel_summary)

        # 4. Multimedia Codecs Group
        self.grp_codecs = Adw.PreferencesGroup(
            title=_("Multimedia Codecs"),
            description=_("Status of important video and audio encoders in FFmpeg:"),
        )
        self.pref_page.add(self.grp_codecs)

        self.row_codecs_summary = Adw.ActionRow(title=_("Codec Support"))
        self.grp_codecs.add(self.row_codecs_summary)

        # 5. File Association Group
        self.grp_association = Adw.PreferencesGroup(title=_(".vcut Project File Association"))
        self.pref_page.add(self.grp_association)

        self.row_association = Adw.ActionRow(title=_("Desktop File Association"))
        self.grp_association.add(self.row_association)

        # 6. Preferences Group
        self.grp_preferences = Adw.PreferencesGroup(title=_("Startup Diagnostics"))
        self.pref_page.add(self.grp_preferences)

        self.row_startup_check = Adw.SwitchRow(
            title=_("Run health check during app startup"),
            subtitle=_("Automatically check for required tools and associations when launching Video Cut"),
        )
        self.row_startup_check.set_active(HealthService.is_check_on_startup_enabled())
        self.row_startup_check.connect("notify::active", self._on_startup_check_toggled)
        self.grp_preferences.add(self.row_startup_check)

    def _on_startup_check_toggled(self, row: Adw.SwitchRow, _param: object) -> None:
        HealthService.set_check_on_startup_enabled(row.get_active())

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

        def on_copy_details(_btn: Gtk.Button) -> None:
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

    def _create_command_row(
        self,
        title: str,
        subtitle: str,
        command_text: str,
    ) -> Adw.ActionRow:
        """Create a dedicated ActionRow containing a monospace entry and copy button."""
        row = Adw.ActionRow(title=title, subtitle=subtitle)
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        box.set_valign(Gtk.Align.CENTER)

        entry = Gtk.Entry()
        entry.set_text(command_text)
        entry.set_editable(False)
        entry.set_can_focus(True)
        entry.add_css_class("monospace")
        entry.set_width_chars(28)
        entry.set_hexpand(True)
        box.append(entry)

        btn_copy = Gtk.Button()
        content = Adw.ButtonContent(
            icon_name="edit-copy-symbolic",
            label=_("Copy"),
        )
        btn_copy.set_child(content)
        btn_copy.set_tooltip_text(_("Copy command to clipboard"))

        def on_copy_cmd(_btn: Gtk.Button) -> None:
            self._copy_to_clipboard(command_text)
            content.set_icon_name("object-select-symbolic")
            content.set_label(_("Copied!"))
            btn_copy.add_css_class("suggested-action")
            GLib.timeout_add(2000, lambda: (
                content.set_icon_name("edit-copy-symbolic"),
                content.set_label(_("Copy")),
                btn_copy.remove_css_class("suggested-action")
            ) and False)
            self._show_toast(_("Command copied to clipboard"))

        btn_copy.connect("clicked", on_copy_cmd)
        box.append(btn_copy)

        row._btn_copy = btn_copy
        row._entry = entry
        row.add_suffix(box)
        return row

    def _create_website_row(
        self,
        title: str,
        subtitle: str,
        url: str,
        button_label: str = _("FFmpeg Website"),
    ) -> Adw.ActionRow:
        """Create a dedicated ActionRow with an external website link."""
        row = Adw.ActionRow(title=title, subtitle=subtitle)
        btn_web = Gtk.Button()
        content = Adw.ButtonContent(
            icon_name="web-browser-symbolic",
            label=button_label,
        )
        btn_web.set_child(content)
        btn_web.set_valign(Gtk.Align.CENTER)
        btn_web.set_tooltip_text(url)
        btn_web.connect("clicked", lambda _btn: Gio.AppInfo.launch_default_for_uri(url, None))
        row.add_suffix(btn_web)
        return row

    def refresh_checks(self) -> None:
        """Run diagnostics in background thread and update UI on completion."""
        self.row_overall.set_title(_("Running system health check..."))
        self.row_overall.set_subtitle(_("Probing FFmpeg, hardware acceleration, and codecs..."))
        icon_spin = Gtk.Spinner()
        icon_spin.start()
        self._set_overall_prefix(icon_spin)

        def worker() -> None:
            items = HealthService.run_all_checks()
            GLib.idle_add(self._apply_results, items)

        threading.Thread(target=worker, daemon=True).start()

    def _apply_results(self, items: list[HealthCheckItem]) -> None:
        self._items = items
        ffmpeg_item = next((it for it in items if it.id == "ffmpeg"), None)
        hwaccel_item = next((it for it in items if it.id == "hwaccel"), None)
        codecs_item = next((it for it in items if it.id == "codecs"), None)
        assoc_item = next((it for it in items if it.id == "file_association"), None)

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
            self.row_overall.set_subtitle(_("Video Cut is operational, but optional features or associations can be improved."))
            icon = Gtk.Image.new_from_icon_name("dialog-warning-symbolic")
            icon.add_css_class("warning")
            self._set_overall_prefix(icon)
        else:
            self.row_overall.set_title(_("All Systems Operational"))
            self.row_overall.set_subtitle(_("FFmpeg, hardware acceleration, and file associations are ready."))
            icon = Gtk.Image.new_from_icon_name("object-select-symbolic")
            icon.add_css_class("success")
            self._set_overall_prefix(icon)

        ffmpeg_ok = bool(ffmpeg_item and ffmpeg_item.status == "ok")

        if ffmpeg_item:
            self._update_ffmpeg_row(ffmpeg_item)
        if hwaccel_item:
            self._update_hwaccel_group(hwaccel_item, ffmpeg_ok=ffmpeg_ok)
        if codecs_item:
            self._update_codecs_group(codecs_item, ffmpeg_ok=ffmpeg_ok)
        if assoc_item:
            self._update_association_row(assoc_item)

        # Show first the ones with problems (error, warning), then the ones that are all green (ok)
        def status_priority(item: HealthCheckItem | None) -> int:
            if not item:
                return 2
            if item.status == "error":
                return 0
            if item.status == "warning":
                return 1
            return 2

        check_groups = [
            (self.grp_ffmpeg, ffmpeg_item),
            (self.grp_hwaccel, hwaccel_item),
            (self.grp_codecs, codecs_item),
            (self.grp_association, assoc_item),
        ]
        sorted_groups = sorted(check_groups, key=lambda pair: status_priority(pair[1]))

        self.pref_page.remove(self.grp_preferences)
        for grp, _it in check_groups:
            self.pref_page.remove(grp)
        for grp, _it in sorted_groups:
            self.pref_page.add(grp)
        self.pref_page.add(self.grp_preferences)

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
        btn_info = self._create_info_button(item)
        suffix_box.append(btn_info)

        self._suffix_ffmpeg = suffix_box
        self.row_ffmpeg.add_suffix(suffix_box)

        # Clear old dynamic action rows bellow
        if self._row_ffmpeg_cmd:
            self.grp_ffmpeg.remove(self._row_ffmpeg_cmd)
            self._row_ffmpeg_cmd = None
        if self._row_ffmpeg_web:
            self.grp_ffmpeg.remove(self._row_ffmpeg_web)
            self._row_ffmpeg_web = None

        # When FFmpeg is missing, place the actions below in separate, spacious rows
        if item.status != "ok":
            if item.fix_command:
                self._row_ffmpeg_cmd = self._create_command_row(
                    title=_("Install Command"),
                    subtitle=_("Run this command in your terminal:"),
                    command_text=item.fix_command,
                )
                self.grp_ffmpeg.add(self._row_ffmpeg_cmd)

            if item.fix_url:
                self._row_ffmpeg_web = self._create_website_row(
                    title=_("Official Website"),
                    subtitle=_("Download binaries or source:"),
                    url=item.fix_url,
                    button_label=_("FFmpeg Website"),
                )
                self.grp_ffmpeg.add(self._row_ffmpeg_web)

    def _update_hwaccel_group(self, item: HealthCheckItem, ffmpeg_ok: bool) -> None:
        """Populate the hardware acceleration section with GPU framework details."""
        if self._row_hwaccel_table:
            self.grp_hwaccel.remove(self._row_hwaccel_table)
            self._row_hwaccel_table = None

        if not ffmpeg_ok:
            self.row_hwaccel_summary.set_subtitle(_("Install FFmpeg to inspect and enable GPU hardware acceleration."))
            icon = Gtk.Image.new_from_icon_name("dialog-warning-symbolic")
            icon.add_css_class("warning")
            self._set_row_prefix(self.row_hwaccel_summary, "_prefix_hwaccel", icon)
            return

        methods = HealthService.get_hwaccel_overview()
        available_count = sum(1 for m in methods if m.is_available)

        if available_count > 0:
            active_names = [m.name.split(" ")[0] for m in methods if m.is_available]
            self.row_hwaccel_summary.set_subtitle(
                _("GPU hardware acceleration active ({active}).").format(active=", ".join(active_names))
            )
            icon = Gtk.Image.new_from_icon_name("object-select-symbolic")
            icon.add_css_class("success")
        else:
            self.row_hwaccel_summary.set_subtitle(_("Software encoding active (CPU). No compatible GPU encoder detected."))
            icon = Gtk.Image.new_from_icon_name("dialog-information-symbolic")
            icon.add_css_class("accent")
        self._set_row_prefix(self.row_hwaccel_summary, "_prefix_hwaccel", icon)

        # Build clean 3-column table
        pref_row = Adw.PreferencesRow()
        grid = Gtk.Grid()
        grid.set_column_spacing(24)
        grid.set_row_spacing(10)
        grid.set_margin_top(12)
        grid.set_margin_bottom(12)
        grid.set_margin_start(16)
        grid.set_margin_end(16)
        grid.set_hexpand(True)

        headers = [_("Framework"), _("Hardware Encoders"), _("Status")]
        for col_idx, h in enumerate(headers):
            lbl = Gtk.Label(label=h)
            lbl.add_css_class("heading")
            lbl.add_css_class("dim-label")
            lbl.set_xalign(0.0)
            grid.attach(lbl, col_idx, 0, 1, 1)

        sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        sep.set_hexpand(True)
        grid.attach(sep, 0, 1, 3, 1)

        for i, m in enumerate(methods):
            r = i + 2
            # Col 0: Framework name
            lbl_name = Gtk.Label(label=m.name)
            lbl_name.set_xalign(0.0)
            grid.attach(lbl_name, 0, r, 1, 1)

            # Col 1: Hardware encoders
            enc_text = ", ".join(m.encoders) if m.encoders else _("None")
            lbl_enc = Gtk.Label(label=enc_text)
            lbl_enc.add_css_class("monospace" if m.encoders else "dim-label")
            lbl_enc.set_xalign(0.0)
            grid.attach(lbl_enc, 1, r, 1, 1)

            # Col 2: Status with icon and text
            st_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            st_icon = Gtk.Image.new_from_icon_name(
                "object-select-symbolic" if m.is_available else "window-close-symbolic"
            )
            st_icon.add_css_class("success" if m.is_available else "dim-label")
            st_box.append(st_icon)

            lbl_st = Gtk.Label(label=_("Available") if m.is_available else _("Not Detected"))
            lbl_st.add_css_class("success" if m.is_available else "dim-label")
            st_box.append(lbl_st)
            grid.attach(st_box, 2, r, 1, 1)

        pref_row.set_child(grid)
        self._row_hwaccel_table = pref_row
        self.grp_hwaccel.add(pref_row)

    def _update_codecs_group(self, item: HealthCheckItem, ffmpeg_ok: bool) -> None:
        """Populate the codecs group with a concise 3-column table: Codec, Encoder, Status."""
        if self._row_codecs_table:
            self.grp_codecs.remove(self._row_codecs_table)
            self._row_codecs_table = None
        if self._row_codecs_cmd:
            self.grp_codecs.remove(self._row_codecs_cmd)
            self._row_codecs_cmd = None

        if not ffmpeg_ok:
            self.row_codecs_summary.set_subtitle(_("Install FFmpeg to inspect and enable multimedia codecs."))
            icon = Gtk.Image.new_from_icon_name("dialog-warning-symbolic")
            icon.add_css_class("warning")
            self._set_row_prefix(self.row_codecs_summary, "_prefix_codecs", icon)
            return

        codecs = HealthService.get_codecs_overview()
        available_count = sum(1 for c in codecs if c.is_available)
        total_count = len(codecs)

        self.row_codecs_summary.set_subtitle(
            _("{available} of {total} important codecs available.").format(
                available=available_count,
                total=total_count,
            )
        )
        icon_name = "object-select-symbolic" if item.status == "ok" else "dialog-warning-symbolic"
        icon = Gtk.Image.new_from_icon_name(icon_name)
        icon.add_css_class("success" if item.status == "ok" else "warning")
        self._set_row_prefix(self.row_codecs_summary, "_prefix_codecs", icon)

        # Build clean 3-column table: Codec, Encoder, Status
        pref_row = Adw.PreferencesRow()
        grid = Gtk.Grid()
        grid.set_column_spacing(24)
        grid.set_row_spacing(10)
        grid.set_margin_top(12)
        grid.set_margin_bottom(12)
        grid.set_margin_start(16)
        grid.set_margin_end(16)
        grid.set_hexpand(True)

        headers = [_("Codec"), _("Encoder"), _("Status")]
        for col_idx, h in enumerate(headers):
            lbl = Gtk.Label(label=h)
            lbl.add_css_class("heading")
            lbl.add_css_class("dim-label")
            lbl.set_xalign(0.0)
            grid.attach(lbl, col_idx, 0, 1, 1)

        sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        sep.set_hexpand(True)
        grid.attach(sep, 0, 1, 3, 1)

        for i, c in enumerate(codecs):
            r = i + 2
            # Col 0: Codec Name
            lbl_name = Gtk.Label(label=c.name)
            lbl_name.set_xalign(0.0)
            grid.attach(lbl_name, 0, r, 1, 1)

            # Col 1: Encoder
            lbl_enc = Gtk.Label(label=c.encoder)
            lbl_enc.add_css_class("monospace")
            lbl_enc.set_xalign(0.0)
            grid.attach(lbl_enc, 1, r, 1, 1)

            # Col 2: Status with icon and text
            st_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            st_icon = Gtk.Image.new_from_icon_name(
                "object-select-symbolic" if c.is_available else "window-close-symbolic"
            )
            st_color = "success" if c.is_available else ("error" if c.is_essential else "warning")
            st_icon.add_css_class(st_color)
            st_box.append(st_icon)

            lbl_st = Gtk.Label(label=_("Available") if c.is_available else _("Not Available"))
            lbl_st.add_css_class(st_color)
            st_box.append(lbl_st)
            grid.attach(st_box, 2, r, 1, 1)

        pref_row.set_child(grid)
        self._row_codecs_table = pref_row
        self.grp_codecs.add(pref_row)

        if item.fix_command and item.status != "ok":
            self._row_codecs_cmd = self._create_command_row(
                title=_("Install Extra Codecs"),
                subtitle=_("Run this command in your terminal:"),
                command_text=item.fix_command,
            )
            self.grp_codecs.add(self._row_codecs_cmd)

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
        else:
            btn_remove = Gtk.Button(label=_("Remove Association"))
            btn_remove.connect("clicked", self._on_remove_association_clicked)
            suffix_box.append(btn_remove)

        btn_info = self._create_info_button(item)
        suffix_box.append(btn_info)

        self._suffix_association = suffix_box
        self.row_association.add_suffix(suffix_box)

    def _on_fix_association_clicked(self, _btn: Gtk.Button) -> None:
        _ok, msg = HealthService.fix_file_association()
        self._show_toast(msg)
        self.refresh_checks()

    def _on_remove_association_clicked(self, _btn: Gtk.Button) -> None:
        _ok, msg = HealthService.remove_file_association()
        self._show_toast(msg)
        self.refresh_checks()


class StartupHealthAlertModal(Adw.Window):
    """Modal dialog alerting user when system issues are detected on startup."""

    def __init__(
        self,
        parent: Gtk.Window | None = None,
        items: list[HealthCheckItem] | None = None,
        on_open_setup: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(transient_for=parent, modal=True)
        self.set_title(_("Video Cut"))
        self.set_default_size(520, -1)
        self.set_resizable(False)

        self._on_open_setup = on_open_setup
        self._items = items or []

        self._build_ui()

    def _build_ui(self) -> None:
        toolbar_view = Adw.ToolbarView()
        self.set_content(toolbar_view)

        header_bar = Adw.HeaderBar()
        header_bar.set_show_title(False)
        header_bar.set_show_end_title_buttons(False)
        header_bar.set_show_start_title_buttons(False)
        toolbar_view.add_top_bar(header_bar)

        # Allow dismissing with Escape key
        key_controller = Gtk.EventControllerKey()
        key_controller.connect("key-pressed", self._on_key_pressed)
        self.add_controller(key_controller)
        self.connect("close-request", self._on_close_request)

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        main_box.set_margin_start(24)
        main_box.set_margin_end(24)
        main_box.set_margin_top(16)
        main_box.set_margin_bottom(20)

        # Icon and Header
        has_error = any(it.status == "error" for it in self._items)
        icon_name = "dialog-error-symbolic" if has_error else "dialog-warning-symbolic"
        icon_color = "error" if has_error else "warning"

        icon = Gtk.Image.new_from_icon_name(icon_name)
        icon.set_pixel_size(48)
        icon.add_css_class(icon_color)
        icon.set_halign(Gtk.Align.CENTER)
        main_box.append(icon)

        title_label = Gtk.Label(label=_("System Setup Recommended"))
        title_label.add_css_class("title-2")
        title_label.set_halign(Gtk.Align.CENTER)
        main_box.append(title_label)

        desc_label = Gtk.Label(
            label=_(
                "Video Cut detected setup recommendations that may affect video processing or file associations:"
            )
        )
        desc_label.add_css_class("body")
        desc_label.add_css_class("dim-label")
        desc_label.set_wrap(True)
        desc_label.set_justify(Gtk.Justification.CENTER)
        desc_label.set_halign(Gtk.Align.CENTER)
        main_box.append(desc_label)

        # List of detected issues
        issues = [it for it in self._items if it.status in ("error", "warning")]
        if issues:
            pref_group = Adw.PreferencesGroup()
            for it in issues:
                row = Adw.ActionRow(title=it.title, subtitle=it.summary)
                row_icon = Gtk.Image.new_from_icon_name(
                    "dialog-error-symbolic" if it.status == "error" else "dialog-warning-symbolic"
                )
                row_icon.add_css_class("error" if it.status == "error" else "warning")
                row.add_prefix(row_icon)
                pref_group.add(row)
            main_box.append(pref_group)

        # "Do not show this message again in the future" CheckBox
        self.chk_dont_show = Gtk.CheckButton(
            label=_("Do not show this message again in the future")
        )
        self.chk_dont_show.set_halign(Gtk.Align.CENTER)
        self.chk_dont_show.set_margin_top(8)
        self.chk_dont_show.set_margin_bottom(4)
        main_box.append(self.chk_dont_show)

        # Action Buttons
        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        btn_box.set_halign(Gtk.Align.END)
        btn_box.set_margin_top(8)

        btn_dismiss = Gtk.Button(label=_("Dismiss"))
        btn_dismiss.connect("clicked", self._on_dismiss_clicked)
        btn_box.append(btn_dismiss)

        btn_review = Gtk.Button(label=_("Review & Fix..."))
        btn_review.add_css_class("suggested-action")
        btn_review.connect("clicked", self._on_review_clicked)
        btn_box.append(btn_review)

        main_box.append(btn_box)
        toolbar_view.set_content(main_box)

    def _save_preference_if_checked(self) -> None:
        if self.chk_dont_show.get_active():
            HealthService.set_check_on_startup_enabled(False)

    def _on_close_request(self, _win: Gtk.Window) -> bool:
        self._save_preference_if_checked()
        return False

    def _on_key_pressed(
        self,
        _controller: Gtk.EventControllerKey,
        keyval: int,
        _keycode: int,
        _state: Gdk.ModifierType,
    ) -> bool:
        if keyval == Gdk.KEY_Escape:
            self._save_preference_if_checked()
            self.destroy()
            return True
        return False

    def _on_dismiss_clicked(self, _btn: Gtk.Button) -> None:
        self._save_preference_if_checked()
        self.destroy()

    def _on_review_clicked(self, _btn: Gtk.Button) -> None:
        self._save_preference_if_checked()
        self.destroy()
        if self._on_open_setup:
            self._on_open_setup()

