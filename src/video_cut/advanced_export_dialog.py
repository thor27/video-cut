"""Advanced Export dialog for configuring export settings (containers, codecs, quality, resolution)."""

from __future__ import annotations

from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk

from .i18n import _
from .models import ExportSettings
from .services.export_service import FFmpegCapabilities


class AdvancedExportDialog(Adw.Window):
    """Modal dialog allowing users to customize export container, codecs, quality, and resolution."""

    def __init__(
        self,
        parent: Gtk.Window,
        on_export: Callable[[ExportSettings], None],
        source_width: int | None = None,
        source_height: int | None = None,
        source_fps: float | None = None,
        initial_lossless: bool = False,
    ) -> None:
        super().__init__(transient_for=parent, modal=True)
        self.set_title(_("Export Options"))
        self.set_default_size(540, 700)
        self.on_export = on_export

        self.source_width = source_width
        self.source_height = source_height
        self.source_fps = source_fps

        self.containers = FFmpegCapabilities.get_available_containers()
        if not self.containers:
            self.containers = [("mp4", "MP4 (.mp4)", "mp4")]

        self._current_video_codecs: list[tuple[str, str]] = []
        self._current_audio_codecs: list[tuple[str, str]] = []
        self._updating_codecs = False

        self._build_ui()
        if initial_lossless:
            self.row_lossless.set_active(True)
        self._update_codecs_for_container()

    def _build_ui(self) -> None:
        toolbar_view = Adw.ToolbarView()
        self.set_content(toolbar_view)

        header_bar = self._create_header_bar()
        toolbar_view.add_top_bar(header_bar)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_propagate_natural_height(True)
        scrolled.set_propagate_natural_width(True)
        toolbar_view.set_content(scrolled)

        clamp = Adw.Clamp(maximum_size=520)
        scrolled.set_child(clamp)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_margin_start(16)
        box.set_margin_end(16)
        box.set_margin_top(8)
        box.set_margin_bottom(12)
        clamp.set_child(box)

        box.append(self._create_lossless_group())
        self.grp_video = self._create_video_group()
        box.append(self.grp_video)
        self.grp_audio = self._create_audio_group()
        box.append(self.grp_audio)

    def _create_lossless_group(self) -> Adw.PreferencesGroup:
        """Build the Lossless stream-copy preference group with question mark help button."""
        grp = Adw.PreferencesGroup(title=_("Export Mode"))
        self.row_lossless = Adw.SwitchRow(title=_("Lossless (Stream Copy)"))
        self.row_lossless.set_subtitle(_("Direct stream copying without re-encoding (instant export)"))
        self.row_lossless.set_active(False)
        self.row_lossless.connect("notify::active", self._on_lossless_toggled)

        self.btn_lossless_help = Gtk.Button.new_from_icon_name("dialog-question-symbolic")
        self.btn_lossless_help.add_css_class("flat")
        self.btn_lossless_help.add_css_class("circular")
        self.btn_lossless_help.set_valign(Gtk.Align.CENTER)
        self.btn_lossless_help.set_tooltip_text(_("Lossless cutting & keyframe explanation"))
        self.btn_lossless_help.connect("clicked", lambda _: self._show_lossless_help())
        self.row_lossless.add_suffix(self.btn_lossless_help)

        grp.add(self.row_lossless)
        return grp

    def _show_lossless_help(self) -> None:
        from .keyframe_help_dialog import KeyframeHelpDialog

        dialog = KeyframeHelpDialog(parent=self)
        dialog.present()

    def _on_lossless_toggled(self, row: Adw.SwitchRow, _param) -> None:
        is_lossless = row.get_active()
        self.grp_video.set_sensitive(not is_lossless)
        self.grp_audio.set_sensitive(not is_lossless)

    def _create_header_bar(self) -> Adw.HeaderBar:
        """Build the top header bar with Cancel and Export action buttons."""
        header_bar = Adw.HeaderBar()
        header_bar.set_show_title(True)
        header_bar.set_show_end_title_buttons(False)
        header_bar.set_show_start_title_buttons(False)

        btn_cancel = Gtk.Button(label=_("Cancel"))
        btn_cancel.connect("clicked", lambda _: self.close())
        header_bar.pack_start(btn_cancel)

        btn_export = Gtk.Button(label=_("Export…"))
        btn_export.add_css_class("suggested-action")
        btn_export.connect("clicked", lambda _: self._on_export_clicked())
        header_bar.pack_end(btn_export)

        return header_bar

    def _create_video_group(self) -> Adw.PreferencesGroup:
        """Build the Video preference group with container, resolution, fps, codec, quality, and preset."""
        grp_video = Adw.PreferencesGroup(title=_("Video Re-encoding Options"))
        grp_video.add(self._create_container_row())
        grp_video.add(self._create_resolution_row())
        grp_video.add(self._create_fps_row())
        grp_video.add(self._create_video_codec_row())
        grp_video.add(self._create_quality_row())
        grp_video.add(self._create_preset_row())
        return grp_video

    def _create_container_row(self) -> Adw.ComboRow:
        self.row_container = Adw.ComboRow(title=_("Container Format"))
        container_labels = [c[1] for c in self.containers]
        self.row_container.set_model(Gtk.StringList.new(container_labels))
        self.row_container.set_selected(0)
        self.row_container.connect("notify::selected", lambda *_: self._update_codecs_for_container())
        return self.row_container

    def _create_resolution_row(self) -> Adw.ComboRow:
        self.row_resolution = Adw.ComboRow(title=_("Resolution"))
        match_res_label = (
            _("Match Source ({w}×{h})").format(w=self.source_width, h=self.source_height)
            if self.source_width and self.source_height
            else _("Match Source")
        )
        self.resolutions = [
            ((None, None), match_res_label),
            ((3840, 2160), _("4K UHD (3840×2160)")),
            ((2560, 1440), _("2K QHD (2560×1440)")),
            ((1920, 1080), _("1080p FHD (1920×1080)")),
            ((1280, 720), _("720p HD (1280×720)")),
            ((854, 480), _("480p SD (854×480)")),
        ]
        self.row_resolution.set_model(Gtk.StringList.new([r[1] for r in self.resolutions]))
        self.row_resolution.set_selected(0)
        return self.row_resolution

    def _create_fps_row(self) -> Adw.ComboRow:
        self.row_fps = Adw.ComboRow(title=_("Frame Rate"))
        match_fps_label = (
            _("Match Source ({fps:.2f} fps)").format(fps=self.source_fps)
            if self.source_fps and self.source_fps > 0
            else _("Match Source")
        )
        self.fps_options = [
            (None, match_fps_label),
            (60.0, _("60 fps")),
            (30.0, _("30 fps")),
            (24.0, _("24 fps")),
        ]
        self.row_fps.set_model(Gtk.StringList.new([f[1] for f in self.fps_options]))
        self.row_fps.set_selected(0)
        return self.row_fps

    def _create_video_codec_row(self) -> Adw.ComboRow:
        self.row_video_codec = Adw.ComboRow(title=_("Video Codec"))
        self.row_video_codec.connect("notify::selected", lambda *_: self._on_video_codec_changed())
        return self.row_video_codec

    def _create_quality_row(self) -> Adw.ComboRow:
        self.row_quality = Adw.ComboRow(title=_("Quality"))
        self.quality_options = [
            (21, _("Balanced (CRF 21)")),
            (18, _("High Quality (CRF 18)")),
            (26, _("Smaller Size (CRF 26)")),
        ]
        self.row_quality.set_model(Gtk.StringList.new([q[1] for q in self.quality_options]))
        self.row_quality.set_selected(0)
        return self.row_quality

    def _create_preset_row(self) -> Adw.ComboRow:
        self.row_preset = Adw.ComboRow(title=_("Encoding Speed"))
        self.preset_options = [
            ("veryfast", _("Very Fast (Default)")),
            ("ultrafast", _("Ultrafast")),
            ("fast", _("Fast")),
            ("medium", _("Medium")),
            ("slow", _("Slow")),
        ]
        self.row_preset.set_model(Gtk.StringList.new([p[1] for p in self.preset_options]))
        self.row_preset.set_selected(0)
        return self.row_preset

    def _create_audio_group(self) -> Adw.PreferencesGroup:
        """Build the Audio preference group with codec and bitrate rows."""
        grp_audio = Adw.PreferencesGroup(title=_("Audio"))
        grp_audio.add(self._create_audio_codec_row())
        grp_audio.add(self._create_audio_bitrate_row())
        return grp_audio

    def _create_audio_codec_row(self) -> Adw.ComboRow:
        self.row_audio_codec = Adw.ComboRow(title=_("Audio Codec"))
        self.row_audio_codec.connect("notify::selected", lambda *_: self._on_audio_codec_changed())
        return self.row_audio_codec

    def _create_audio_bitrate_row(self) -> Adw.ComboRow:
        self.row_audio_bitrate = Adw.ComboRow(title=_("Audio Bitrate"))
        self.bitrate_options = [
            ("192k", _("192 kbps (Default)")),
            ("128k", _("128 kbps")),
            ("256k", _("256 kbps")),
            ("320k", _("320 kbps")),
        ]
        self.row_audio_bitrate.set_model(Gtk.StringList.new([b[1] for b in self.bitrate_options]))
        self.row_audio_bitrate.set_selected(0)
        return self.row_audio_bitrate

    def _get_current_container_key(self) -> str:
        idx = self.row_container.get_selected()
        if 0 <= idx < len(self.containers):
            return self.containers[idx][0]
        return "mp4"

    def _update_codecs_for_container(self) -> None:
        self._updating_codecs = True
        container = self._get_current_container_key()

        # Update Video Codecs
        prev_v_codec = None
        if self._current_video_codecs:
            v_idx = self.row_video_codec.get_selected()
            if 0 <= v_idx < len(self._current_video_codecs):
                prev_v_codec = self._current_video_codecs[v_idx][0]

        self._current_video_codecs = FFmpegCapabilities.get_compatible_video_codecs(container)
        v_labels = [c[1] for c in self._current_video_codecs]
        self.row_video_codec.set_model(Gtk.StringList.new(v_labels))

        new_v_idx = 0
        if prev_v_codec:
            for i, (cid, _) in enumerate(self._current_video_codecs):
                if cid == prev_v_codec:
                    new_v_idx = i
                    break
        self.row_video_codec.set_selected(new_v_idx)

        # Update Audio Codecs
        prev_a_codec = None
        if self._current_audio_codecs:
            a_idx = self.row_audio_codec.get_selected()
            if 0 <= a_idx < len(self._current_audio_codecs):
                prev_a_codec = self._current_audio_codecs[a_idx][0]

        self._current_audio_codecs = FFmpegCapabilities.get_compatible_audio_codecs(container)
        a_labels = [c[1] for c in self._current_audio_codecs]
        self.row_audio_codec.set_model(Gtk.StringList.new(a_labels))

        new_a_idx = 0
        if prev_a_codec:
            for i, (cid, _) in enumerate(self._current_audio_codecs):
                if cid == prev_a_codec:
                    new_a_idx = i
                    break
        self.row_audio_codec.set_selected(new_a_idx)

        self._updating_codecs = False
        self._on_video_codec_changed()
        self._on_audio_codec_changed()

    def _on_video_codec_changed(self) -> None:
        if self._updating_codecs:
            return
        idx = self.row_video_codec.get_selected()
        if 0 <= idx < len(self._current_video_codecs):
            codec_id = self._current_video_codecs[idx][0]
            # ProRes does not use CRF or standard preset
            is_prores = (codec_id == "prores_ks")
            self.row_quality.set_sensitive(not is_prores)
            self.row_preset.set_sensitive(not is_prores)

    def _on_audio_codec_changed(self) -> None:
        if self._updating_codecs:
            return
        idx = self.row_audio_codec.get_selected()
        if 0 <= idx < len(self._current_audio_codecs):
            codec_id = self._current_audio_codecs[idx][0]
            # FLAC is lossless, bitrate option is not applicable
            is_flac = (codec_id == "flac")
            self.row_audio_bitrate.set_sensitive(not is_flac)

    def _on_export_clicked(self) -> None:
        container = self._get_current_container_key()

        # Video Codec
        v_idx = self.row_video_codec.get_selected()
        v_codec = self._current_video_codecs[v_idx][0] if 0 <= v_idx < len(self._current_video_codecs) else "libx264"

        # Resolution
        res_idx = self.row_resolution.get_selected()
        target_w, target_h = self.resolutions[res_idx][0] if 0 <= res_idx < len(self.resolutions) else (None, None)

        # FPS
        fps_idx = self.row_fps.get_selected()
        fps_val = self.fps_options[fps_idx][0] if 0 <= fps_idx < len(self.fps_options) else None

        # Quality (CRF)
        q_idx = self.row_quality.get_selected()
        crf_val = self.quality_options[q_idx][0] if 0 <= q_idx < len(self.quality_options) else 21

        # Preset
        p_idx = self.row_preset.get_selected()
        preset_val = self.preset_options[p_idx][0] if 0 <= p_idx < len(self.preset_options) else "veryfast"

        # Audio Codec
        a_idx = self.row_audio_codec.get_selected()
        a_codec = self._current_audio_codecs[a_idx][0] if 0 <= a_idx < len(self._current_audio_codecs) else "aac"

        # Audio Bitrate
        b_idx = self.row_audio_bitrate.get_selected()
        bitrate_val = self.bitrate_options[b_idx][0] if 0 <= b_idx < len(self.bitrate_options) else "192k"

        if self.row_lossless.get_active():
            settings = ExportSettings(
                container=container,
                lossless=True,
            )
            self.close()
            self.on_export(settings)
            return

        settings = ExportSettings(
            container=container,
            video_codec=v_codec,
            crf=crf_val,
            preset=preset_val,
            target_width=target_w,
            target_height=target_h,
            fps=fps_val,
            audio_codec=a_codec,
            audio_bitrate=bitrate_val,
            lossless=False,
        )

        self.close()
        self.on_export(settings)
