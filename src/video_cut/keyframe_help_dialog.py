"""Help dialog explaining keyframes, I-frames, and lossless stream-copy conversion."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk

from .i18n import _


class KeyframeHelpDialog(Adw.Window):
    """Informational modal dialog explaining keyframes and lossless cutting."""

    def __init__(self, parent: Gtk.Window) -> None:
        super().__init__(transient_for=parent, modal=True)
        self.set_title(_("Keyframes & Lossless Cutting"))
        self.set_default_size(860, 740)
        self._build_ui()

    def _build_ui(self) -> None:
        toolbar_view = Adw.ToolbarView()
        self.set_content(toolbar_view)

        header_bar = Adw.HeaderBar()
        header_bar.set_show_title(True)
        header_bar.set_show_end_title_buttons(False)
        header_bar.set_show_start_title_buttons(False)

        btn_close = Gtk.Button(label=_("Close"))
        btn_close.add_css_class("suggested-action")
        btn_close.connect("clicked", lambda _: self.close())
        header_bar.pack_end(btn_close)
        toolbar_view.add_top_bar(header_bar)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_propagate_natural_height(True)
        toolbar_view.set_content(scrolled)

        clamp = Adw.Clamp(maximum_size=820)
        clamp.set_margin_start(20)
        clamp.set_margin_end(20)
        clamp.set_margin_top(16)
        clamp.set_margin_bottom(24)
        scrolled.set_child(clamp)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        clamp.set_child(box)

        # 1. What is Lossless Cutting?
        grp_lossless = Adw.PreferencesGroup()
        grp_lossless.set_title(_("What is Lossless Cutting?"))
        grp_lossless.set_description(
            _(
                "Lossless cutting (stream copying) trims and combines videos by copying the original "
                "compressed data packets directly into the new file without decoding or re-encoding. "
                "Because no re-compression takes place, exports are virtually instantaneous and preserve "
                "100% of the original video and audio quality."
            )
        )
        box.append(grp_lossless)

        # 2. What is a Keyframe?
        grp_keyframe = Adw.PreferencesGroup()
        grp_keyframe.set_title(_("What is a Keyframe?"))
        grp_keyframe.set_description(
            _(
                "Modern video formats (such as H.264, HEVC, VP9, and AV1) compress video by storing full "
                "images only occasionally (Keyframes or I-frames), while intermediate frames only record "
                "differences between frames. A keyframe is a complete, standalone picture that can be decoded "
                "independently."
            )
        )
        box.append(grp_keyframe)

        # 3. Advantages & Disadvantages Side by Side
        columns_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        columns_box.set_homogeneous(True)
        box.append(columns_box)

        # Left Column: Advantages of Lossless Cutting
        grp_pros = Adw.PreferencesGroup()
        grp_pros.set_title(_("Advantages of Lossless Cutting"))
        columns_box.append(grp_pros)

        row_speed = Adw.ActionRow()
        row_speed.set_title(_("Blazing Fast Speed"))
        row_speed.set_subtitle(
            _("Exports complete in seconds because video and audio packets are copied directly without re-encoding.")
        )
        row_speed.set_subtitle_lines(0)
        row_speed.add_prefix(Gtk.Image.new_from_icon_name("media-flash-symbolic"))
        grp_pros.add(row_speed)

        row_quality = Adw.ActionRow()
        row_quality.set_title(_("100% Quality Preserved"))
        row_quality.set_subtitle(
            _("Zero generation loss or compression artifacts. Every pixel and audio sample remains untouched.")
        )
        row_quality.set_subtitle_lines(0)
        row_quality.add_prefix(Gtk.Image.new_from_icon_name("starred-symbolic"))
        grp_pros.add(row_quality)

        row_battery = Adw.ActionRow()
        row_battery.set_title(_("Minimal CPU and Battery Usage"))
        row_battery.set_subtitle(
            _("Bypasses intensive CPU/GPU rendering, keeping your computer cool and preserving laptop battery.")
        )
        row_battery.set_subtitle_lines(0)
        row_battery.add_prefix(Gtk.Image.new_from_icon_name("battery-good-charging-symbolic"))
        grp_pros.add(row_battery)

        # Right Column: Disadvantages of Lossless Cutting
        grp_cons = Adw.PreferencesGroup()
        grp_cons.set_title(_("Disadvantages of Lossless Cutting"))
        columns_box.append(grp_cons)

        row_boundary = Adw.ActionRow()
        row_boundary.set_title(_("Cuts Must Align to Keyframes"))
        row_boundary.set_subtitle(
            _(
                "Intermediate frames depend on surrounding images and cannot be split mid-stream "
                "without re-encoding. All cuts snap to the nearest keyframe."
            )
        )
        row_boundary.set_subtitle_lines(0)
        row_boundary.add_prefix(Gtk.Image.new_from_icon_name("edit-cut-symbolic"))
        grp_cons.add(row_boundary)

        row_filters = Adw.ActionRow()
        row_filters.set_title(_("No Re-encoding Filters"))
        row_filters.set_subtitle(
            _(
                "Transformations that alter pixels—such as rotation, cropping, flipping, and text or "
                "graphic overlays—require re-encoding and cannot be applied."
            )
        )
        row_filters.set_subtitle_lines(0)
        row_filters.add_prefix(Gtk.Image.new_from_icon_name("dialog-warning-symbolic"))
        grp_cons.add(row_filters)

        row_codecs = Adw.ActionRow()
        row_codecs.set_title(_("Matching Codecs Required"))
        row_codecs.set_subtitle(
            _(
                "When combining multiple clips, all segments must share compatible video and audio "
                "formats, codecs, and dimensions."
            )
        )
        row_codecs.set_subtitle_lines(0)
        row_codecs.add_prefix(Gtk.Image.new_from_icon_name("preferences-system-symbolic"))
        grp_cons.add(row_codecs)
