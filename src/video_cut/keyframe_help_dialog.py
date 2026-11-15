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
        self.set_default_size(520, 560)
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

        clamp = Adw.Clamp(maximum_size=480)
        clamp.set_margin_start(16)
        clamp.set_margin_end(16)
        clamp.set_margin_top(12)
        clamp.set_margin_bottom(20)
        scrolled.set_child(clamp)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        clamp.set_child(box)

        # 1. What are Keyframes?
        grp_concept = Adw.PreferencesGroup()
        grp_concept.set_title(_("What is a Keyframe?"))
        grp_concept.set_description(
            _(
                "Modern video formats (H.264, HEVC, VP9, AV1) compress video by storing only the "
                "differences between frames. A Keyframe (or I-frame) is a complete, self-contained "
                "image that does not depend on any other frames to decode."
            )
        )
        box.append(grp_concept)

        # 2. Advantages of Lossless Cutting
        grp_pros = Adw.PreferencesGroup()
        grp_pros.set_title(_("Advantages of Lossless Cutting"))
        box.append(grp_pros)

        row_speed = Adw.ActionRow()
        row_speed.set_title(_("Blazing Fast Speed"))
        row_speed.set_subtitle(_("Exports take seconds because video and audio streams are copied directly without re-encoding."))
        row_speed.add_prefix(Gtk.Image.new_from_icon_name("media-flash-symbolic"))
        grp_pros.add(row_speed)

        row_quality = Adw.ActionRow()
        row_quality.set_title(_("100% Quality Preserved"))
        row_quality.set_subtitle(_("Zero compression artifacts or generation loss. Every pixel and audio sample remains identical."))
        row_quality.add_prefix(Gtk.Image.new_from_icon_name("starred-symbolic"))
        grp_pros.add(row_quality)

        row_battery = Adw.ActionRow()
        row_battery.set_title(_("Minimal CPU and Battery Usage"))
        row_battery.set_subtitle(_("No heavy CPU or GPU rendering. Extremely battery-friendly for laptop editing."))
        row_battery.add_prefix(Gtk.Image.new_from_icon_name("battery-good-charging-symbolic"))
        grp_pros.add(row_battery)

        # 3. Why cuts must align to keyframes
        grp_restrictions = Adw.PreferencesGroup()
        grp_restrictions.set_title(_("Why Cuts Must Align to Keyframes"))
        box.append(grp_restrictions)

        row_boundary = Adw.ActionRow()
        row_boundary.set_title(_("Keyframe Boundary Requirement"))
        row_boundary.set_subtitle(
            _(
                "Because intermediate frames depend on previous images, a cut cannot begin mid-group "
                "without re-encoding. Snapping cuts to keyframes guarantees every clip starts cleanly "
                "with no black frames or audio sync issues."
            )
        )
        row_boundary.add_prefix(Gtk.Image.new_from_icon_name("edit-cut-symbolic"))
        grp_restrictions.add(row_boundary)

        row_filters = Adw.ActionRow()
        row_filters.set_title(_("No Re-encoding Filters"))
        row_filters.set_subtitle(
            _(
                "Transformations like rotation, cropping, and graphic or text overlays require decoding "
                "and re-encoding the video, and cannot be applied in stream-copy mode."
            )
        )
        row_filters.add_prefix(Gtk.Image.new_from_icon_name("dialog-warning-symbolic"))
        grp_restrictions.add(row_filters)
