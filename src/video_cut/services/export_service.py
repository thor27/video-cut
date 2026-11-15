"""Service to export concatenated segments using FFmpeg filter_complex with real-time progress."""

from __future__ import annotations

import contextlib
import math
import os
import re
import secrets
import subprocess
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import gi

gi.require_version("GLib", "2.0")
from gi.repository import GLib

from ..i18n import _
from ..models import ExportSettings, OverlayItem, Segment
from .ffmpeg_service import (
    FLAG_AUDIO_BITRATE,
    FLAG_CODEC_AUDIO,
    FLAG_CODEC_VIDEO,
    FLAG_CRF,
    FLAG_FILTER_COMPLEX,
    FLAG_INPUT,
    FLAG_MAP,
    FLAG_OVERWRITE,
    FLAG_PIX_FMT,
    FLAG_PRESET,
    FLAG_PROGRESS,
    FLAG_SEEK,
    FLAG_VIDEO_BITRATE,
    FLAG_VIDEO_TAG,
    FFmpegService,
)
from .overlay_service import OverlayRenderService
from .private_dir import private_dir
from .probe_service import ProbeService


class FFmpegCapabilities:
    """Detects available video encoders, audio encoders, and muxers in installed FFmpeg."""

    _encoders: set[str] | None = None
    _muxers: set[str] | None = None

    @classmethod
    def get_encoders(cls) -> set[str]:
        if cls._encoders is None:
            cls._encoders = FFmpegService.query_available_encoders()
            if not cls._encoders:
                cls._encoders = {"libx264", "aac"}
        return cls._encoders

    @classmethod
    def get_muxers(cls) -> set[str]:
        if cls._muxers is None:
            cls._muxers = FFmpegService.query_available_muxers()
            if not cls._muxers:
                cls._muxers = {"mp4", "matroska", "webm", "mov"}
        return cls._muxers

    @classmethod
    def get_available_containers(cls) -> list[tuple[str, str, str]]:
        """Return list of available (key, label, extension) container formats."""
        muxers = cls.get_muxers()
        containers = [
            ("mp4", _("MP4 (.mp4)"), "mp4"),
            ("matroska", _("Matroska (.mkv)"), "mkv"),
            ("webm", _("WebM (.webm)"), "webm"),
            ("mov", _("QuickTime (.mov)"), "mov"),
        ]
        res = [c for c in containers if c[0] in muxers]
        return res if res else [("mp4", _("MP4 (.mp4)"), "mp4")]

    @classmethod
    def get_compatible_video_codecs(cls, container: str) -> list[tuple[str, str]]:
        """Return list of (encoder_name, label) compatible with container and available."""
        encoders = cls.get_encoders()
        codecs = [
            ("libx264", _("H.264 / AVC (Most Compatible)"), ["mp4", "matroska", "mov"]),
            ("libx265", _("H.265 / HEVC (High Efficiency)"), ["mp4", "matroska", "mov"]),
            ("libvpx-vp9", _("VP9 (Web Open Standard)"), ["webm", "matroska", "mp4"]),
            ("libsvtav1", _("AV1 (Next-Gen Open Standard)"), ["mp4", "matroska", "webm"]),
            ("prores_ks", _("Apple ProRes (Editing Master)"), ["mov", "matroska"]),
        ]
        if "libsvtav1" not in encoders and "libaom-av1" in encoders:
            codecs = [("libaom-av1" if c[0] == "libsvtav1" else c[0], c[1], c[2]) for c in codecs]

        res = []
        for codec_id, label, allowed in codecs:
            if container in allowed and codec_id in encoders:
                res.append((codec_id, label))
        if not res and "libx264" in encoders:
            res.append(("libx264", _("H.264 / AVC")))
        return res

    @classmethod
    def get_compatible_audio_codecs(cls, container: str) -> list[tuple[str, str]]:
        """Return list of (encoder_name, label) compatible with container and available."""
        encoders = cls.get_encoders()
        codecs = [
            ("aac", _("AAC (Universal Default)"), ["mp4", "matroska", "mov"]),
            ("libopus", _("Opus (High Quality Voice & Music)"), ["webm", "matroska", "mp4"]),
            ("libmp3lame", "MP3", ["mp4", "matroska", "mov"]),
            ("flac", _("FLAC (Lossless Audio)"), ["matroska", "mov", "mp4"]),
        ]
        res = []
        for codec_id, label, allowed in codecs:
            if container in allowed and codec_id in encoders:
                res.append((codec_id, label))
        if not res and "aac" in encoders:
            res.append(("aac", _("AAC")))
        return res


def _is_same_file(a: Path, b: Path) -> bool:
    try:
        return os.path.samefile(a, b)
    except OSError:
        pass
    try:
        return a.resolve() == b.resolve()
    except (OSError, RuntimeError):
        return False


class ExportService:
    """Manages video concatenation and export via FFmpeg."""

    def __init__(self) -> None:
        self._process: subprocess.Popen | None = None
        self._cancelled = False
        self._thread: threading.Thread | None = None

    @classmethod
    def can_lossless_export(cls, segments: list[Segment]) -> tuple[bool, str]:
        """Check whether segments can be exported losslessly via stream copying."""
        if not segments:
            return False, _("No segments to export")

        for seg in segments:
            if seg.rotation % 360 != 0 or seg.hflip or seg.vflip:
                return False, _("Rotation and flip transformations require re-encoding")
            if seg.crop is not None:
                cx, cy, cw, ch = seg.crop
                if not (abs(cx) < 1e-4 and abs(cy) < 1e-4 and abs(cw - 1.0) < 1e-4 and abs(ch - 1.0) < 1e-4):
                    return False, _("Crop transformations require re-encoding")
            if seg.overlays:
                return False, _("Graphic and text overlays require re-encoding")

        sig0 = ProbeService.get_video_codec_signature(segments[0].source_path)
        if not sig0:
            return False, _("Could not inspect source video codec")

        for s in segments[1:]:
            sig = ProbeService.get_video_codec_signature(s.source_path)
            if not sig:
                return False, _("Could not inspect source video codec")
            if (
                sig["video_codec"] != sig0["video_codec"]
                or sig["width"] != sig0["width"]
                or sig["height"] != sig0["height"]
                or sig["pix_fmt"] != sig0["pix_fmt"]
                or sig["has_audio"] != sig0["has_audio"]
            ):
                return False, _("Segments have different video codecs or dimensions")
            if sig0["has_audio"] and sig.get("audio_codec") != sig0.get("audio_codec"):
                return False, _("Segments have different audio codecs")

        return True, ""

    def build_ffmpeg_command(
        self,
        segments: list[Segment],
        output_path: Path | str,
        target_width: int | None = None,
        target_height: int | None = None,
        settings: ExportSettings | None = None,
        fast_seek: bool = False,
        overlay_temp_dir: Path | None = None,
    ) -> list[str]:
        """Construct the FFmpeg command line for concatenating segments."""
        if not segments:
            raise ValueError("No segments to export")

        out_path = Path(output_path)

        # 1. Determine unique inputs
        unique_paths: list[Path] = []
        for seg in segments:
            if seg.source_path not in unique_paths:
                unique_paths.append(seg.source_path)

        path_to_idx = {p: i for i, p in enumerate(unique_paths)}

        # Determine fast seek offset per unique input if requested
        path_to_min_start: dict[Path, float] = {}
        if fast_seek:
            for p in unique_paths:
                segs_for_path = [s for s in segments if s.source_path == p]
                min_s = min(s.start_time for s in segs_for_path)
                path_to_min_start[p] = min_s if min_s > 0.05 else 0.0
        else:
            for p in unique_paths:
                path_to_min_start[p] = 0.0

        # Check audio presence for unique paths
        path_has_audio = {p: ProbeService.has_audio(p) for p in unique_paths}

        # 2. Determine target dimensions if not provided
        if target_width is None and settings and settings.target_width is not None:
            target_width = settings.target_width
        if target_height is None and settings and settings.target_height is not None:
            target_height = settings.target_height

        if target_width is None or target_height is None:
            meta = ProbeService.probe(segments[0].source_path)
            if meta:
                rot = segments[0].rotation % 360
                ow, oh = (meta.height, meta.width) if rot in (90, 270) else (meta.width, meta.height)
                if segments[0].crop is not None:
                    cx, cy, cw, ch = segments[0].crop
                    if not (abs(cx) < 1e-4 and abs(cy) < 1e-4 and abs(cw - 1.0) < 1e-4 and abs(ch - 1.0) < 1e-4):
                        cw = max(0.01, min(1.0, cw))
                        ch = max(0.01, min(1.0, ch))
                        target_width = int(math.floor(ow * cw / 2.0) * 2)
                        target_height = int(math.floor(oh * ch / 2.0) * 2)
                    else:
                        target_width = ow
                        target_height = oh
                else:
                    target_width = ow
                    target_height = oh
            else:
                target_width, target_height = 1920, 1080

        # Make dimensions even (required by most encoders)
        target_width = max(2, target_width - (target_width % 2))
        target_height = max(2, target_height - (target_height % 2))

        def get_segment_oriented_crop_dims(s: Segment) -> tuple[int, int]:
            meta_s = ProbeService.probe(s.source_path)
            src_w = meta_s.width if meta_s else (target_width or 1920)
            src_h = meta_s.height if meta_s else (target_height or 1080)
            rot_s = s.rotation % 360
            ow_s, oh_s = (src_h, src_w) if rot_s in (90, 270) else (src_w, src_h)
            if s.crop is not None:
                cx, cy, cw, ch = s.crop
                if not (abs(cx) < 1e-4 and abs(cy) < 1e-4 and abs(cw - 1.0) < 1e-4 and abs(ch - 1.0) < 1e-4):
                    cw = max(0.01, min(1.0, cw))
                    ch = max(0.01, min(1.0, ch))
                    sw = int(math.floor(ow_s * cw / 2.0) * 2)
                    sh = int(math.floor(oh_s * ch / 2.0) * 2)
                    return max(2, sw), max(2, sh)
            sw = int(math.floor(ow_s / 2.0) * 2)
            sh = int(math.floor(oh_s / 2.0) * 2)
            return max(2, sw), max(2, sh)

        # 3. Assemble command inputs
        cmd = ["ffmpeg", FLAG_OVERWRITE]
        for p in unique_paths:
            min_s = path_to_min_start[p]
            if min_s > 0.0:
                cmd.extend([FLAG_SEEK, f"{min_s:.3f}"])
            cmd.extend([FLAG_INPUT, str(p)])

        # 3b. Overlay inputs
        # Group overlay items into static and animated layers to avoid generating thousands of PNG frames on disk
        segment_overlay_layers: dict[int, list[tuple[str, int, OverlayItem | None]]] = {}
        segment_video_audio_overlays: dict[int, list[tuple[int, OverlayItem]]] = {}
        segment_audio_overlays: dict[int, list[tuple[int, OverlayItem]]] = {}
        if overlay_temp_dir is None:
            overlay_temp_dir = private_dir("video_cut_export_overlays")
        overlay_temp_dir.mkdir(parents=True, exist_ok=True)

        current_input_idx = len(unique_paths)
        for j, seg in enumerate(segments):
            if seg.has_overlays:
                seg_w, seg_h = get_segment_oriented_crop_dims(seg)
                sorted_overlays = sorted(seg.overlays, key=lambda it: it.z_index)

                raw_layers: list[tuple[str, Any]] = []
                current_static: list[OverlayItem] = []

                for it in sorted_overlays:
                    # Audio items are handled exclusively in the audio filter pipeline
                    if it.item_type == "audio":
                        continue

                    is_anim = it.item_type in ("video", "gif") or (
                        it.media_path
                        and Path(it.media_path).suffix.lower() in (".mp4", ".webm", ".mov", ".mkv", ".avi", ".gif")
                    )
                    if is_anim and it.media_path and Path(it.media_path).is_file():
                        if current_static:
                            raw_layers.append(("static", current_static))
                            current_static = []
                        raw_layers.append(("animated", it))
                    else:
                        current_static.append(it)
                if current_static:
                    raw_layers.append(("static", current_static))

                seg_layers: list[tuple[str, int, OverlayItem | None]] = []
                single_static = (len(raw_layers) == 1 and raw_layers[0][0] == "static")

                for l_idx, (l_type, l_data) in enumerate(raw_layers):
                    if l_type == "static":
                        png_filename = f"overlay_seg_{j}.png" if single_static else f"overlay_seg_{j}_l{l_idx}.png"
                        png_path = overlay_temp_dir / png_filename
                        OverlayRenderService.render_overlays_to_png(l_data, png_path, seg_w, seg_h)
                        cmd.extend(["-loop", "1", FLAG_INPUT, str(png_path)])
                        seg_layers.append(("static", current_input_idx, None))
                        current_input_idx += 1
                    else:
                        it = l_data
                        if it.is_loop:
                            cmd.extend(["-stream_loop", "-1", FLAG_INPUT, str(it.media_path)])
                        else:
                            cmd.extend([FLAG_INPUT, str(it.media_path)])
                        seg_layers.append(("animated", current_input_idx, it))
                        if it.enable_audio and it.media_path and Path(it.media_path).is_file():
                            if j not in segment_video_audio_overlays:
                                segment_video_audio_overlays[j] = []
                            segment_video_audio_overlays[j].append((current_input_idx, it))
                        current_input_idx += 1

                segment_overlay_layers[j] = seg_layers

                # Collect standalone audio overlay items
                for it in sorted_overlays:
                    if it.item_type == "audio" and it.media_path and Path(it.media_path).is_file():
                        if it.is_loop:
                            cmd.extend(["-stream_loop", "-1", FLAG_INPUT, str(it.media_path)])
                        else:
                            cmd.extend([FLAG_INPUT, str(it.media_path)])
                        if j not in segment_audio_overlays:
                            segment_audio_overlays[j] = []
                        segment_audio_overlays[j].append((current_input_idx, it))
                        current_input_idx += 1

        # 4. Assemble filter_complex
        filter_parts: list[str] = []
        concat_inputs: list[str] = []

        for j, seg in enumerate(segments):
            in_idx = path_to_idx[seg.source_path]
            min_s = path_to_min_start[seg.source_path]
            trimmed_start = max(0.0, seg.start_time - min_s)
            trimmed_end = max(trimmed_start, seg.end_time - min_s)
            v_label = f"v{j}"
            a_label = f"a{j}"

            v_chain: list[str] = [
                f"trim=start={trimmed_start:.3f}:end={trimmed_end:.3f}",
                "setpts=PTS-STARTPTS",
            ]

            rot = seg.rotation % 360
            if rot == 90:
                v_chain.append("transpose=1")
            elif rot == 180:
                v_chain.append("transpose=1,transpose=1")
            elif rot == 270:
                v_chain.append("transpose=2")

            if seg.hflip:
                v_chain.append("hflip")
            if seg.vflip:
                v_chain.append("vflip")

            if seg.crop is not None:
                cx, cy, cw, ch = seg.crop
                if not (abs(cx) < 1e-4 and abs(cy) < 1e-4 and abs(cw - 1.0) < 1e-4 and abs(ch - 1.0) < 1e-4):
                    cw = max(0.01, min(1.0, cw))
                    ch = max(0.01, min(1.0, ch))
                    cx = max(0.0, min(1.0 - cw, cx))
                    cy = max(0.0, min(1.0 - ch, cy))
                    v_chain.append(
                        f"crop=w='floor(iw*{cw:.5f}/2)*2':h='floor(ih*{ch:.5f}/2)*2':x='floor(iw*{cx:.5f}/2)*2':y='floor(ih*{cy:.5f}/2)*2'"
                    )

            post_scale_pad = [
                f"scale={target_width}:{target_height}:force_original_aspect_ratio=decrease",
                f"pad={target_width}:{target_height}:(ow-iw)/2:(oh-ih)/2",
                "setsar=1",
            ]
            if settings and settings.fps is not None and settings.fps > 0:
                post_scale_pad.append(f"fps={settings.fps:.3f}")

            if seg.has_overlays and j in segment_overlay_layers and segment_overlay_layers[j]:
                layers = segment_overlay_layers[j]
                v_curr = f"v{j}_base"
                v_filter = f"[{in_idx}:v]{','.join(v_chain)}[{v_curr}]"
                filter_parts.append(v_filter)

                seg_w, seg_h = get_segment_oriented_crop_dims(seg)
                for idx, (l_type, in_layer_idx, it) in enumerate(layers):
                    is_last = (idx == len(layers) - 1)
                    v_next = f"v{j}_overlaid" if is_last else f"v{j}_l{idx}"

                    if l_type == "static":
                        filter_parts.append(
                            f"[{v_curr}][{in_layer_idx}:v]overlay=0:0:shortest=1:eof_action=repeat[{v_next}]"
                        )
                    else:
                        assert it is not None
                        w_px = max(2, int(round(it.width * seg_w)) & ~1)
                        h_px = max(2, int(round(it.height * seg_h)) & ~1)
                        center_x = it.x * seg_w
                        center_y = it.y * seg_h
                        anim_label = f"anim_{j}_{idx}"

                        anim_filters: list[str] = []
                        if it.start_time > 0.0:
                            anim_filters.append(f"setpts=PTS-STARTPTS+{it.start_time:.3f}/TB")
                        else:
                            anim_filters.append("setpts=PTS-STARTPTS")

                        anim_filters.append(f"scale={w_px}:{h_px}")
                        anim_filters.append("format=yuva420p")

                        if it.opacity < 0.999:
                            anim_filters.append(f"colorchannelmixer=aa={it.opacity:.3f}")

                        if abs(it.rotation % 360) > 0.01:
                            rot_rad = math.radians(it.rotation % 360)
                            anim_filters.append(
                                f"rotate='{rot_rad:.5f}':ow='rotw({rot_rad:.5f})':oh='roth({rot_rad:.5f})':c=none"
                            )

                        filter_parts.append(f"[{in_layer_idx}:v]{','.join(anim_filters)}[{anim_label}]")

                        overlay_opts = [
                            f"x='{center_x:.2f}-w/2'",
                            f"y='{center_y:.2f}-h/2'",
                            "shortest=1",
                            "eof_action=repeat",
                        ]
                        if it.start_time > 0.0 or it.end_time is not None:
                            st = max(0.0, it.start_time)
                            et = it.end_time if it.end_time is not None else seg.duration
                            overlay_opts.append(f"enable='between(t,{st:.3f},{et:.3f})'")

                        filter_parts.append(
                            f"[{v_curr}][{anim_label}]overlay={':'.join(overlay_opts)}[{v_next}]"
                        )

                    v_curr = v_next

                scale_pad_filter = f"[{v_curr}]{','.join(post_scale_pad)}[{v_label}]"
                filter_parts.append(scale_pad_filter)
            else:
                v_chain.extend(post_scale_pad)
                v_filter = f"[{in_idx}:v]{','.join(v_chain)}[{v_label}]"
                filter_parts.append(v_filter)

            extra_audio_inputs: list[str] = []

            # 1. Video overlays with enable_audio
            if j in segment_video_audio_overlays:
                for v_idx, (in_layer_idx, it) in enumerate(segment_video_audio_overlays[j]):
                    if it.media_path and ProbeService.has_audio(it.media_path):
                        a_sub_lbl = f"a{j}_sub_{v_idx}"
                        sub_st = max(0.0, it.start_time)
                        sub_dur = seg.duration - sub_st
                        if sub_dur > 0:
                            v_a_parts = [
                                f"atrim=duration={sub_dur:.3f}",
                                "asetpts=PTS-STARTPTS",
                            ]
                            if sub_st > 0.0:
                                d_ms = int(round(sub_st * 1000))
                                v_a_parts.append(f"adelay={d_ms}|{d_ms}")
                            vol = getattr(it, "audio_volume", 1.0)
                            if abs(vol - 1.0) > 0.001:
                                v_a_parts.append(f"volume={vol:.2f}")
                            v_a_parts.extend(["aresample=48000", "aformat=channel_layouts=stereo"])
                            filter_parts.append(f"[{in_layer_idx}:a]{','.join(v_a_parts)}[{a_sub_lbl}]")
                            extra_audio_inputs.append(a_sub_lbl)

            # 2. Audio overlay items
            if j in segment_audio_overlays:
                for a_idx, (in_layer_idx, it) in enumerate(segment_audio_overlays[j]):
                    a_ov_lbl = f"a{j}_ov_{a_idx}"
                    a_start = max(0.0, it.audio_start)
                    a_end = it.audio_end
                    sub_st = max(0.0, it.start_time)
                    max_dur = seg.duration - sub_st

                    ov_parts = []
                    if a_end is not None and a_end > a_start:
                        ov_parts.append(f"atrim=start={a_start:.3f}:end={a_end:.3f}")
                    elif a_start > 0.0:
                        ov_parts.append(f"atrim=start={a_start:.3f}")
                    ov_parts.append("asetpts=PTS-STARTPTS")

                    if it.is_loop:
                        ov_parts.append(f"atrim=duration={max_dur:.3f}")

                    if sub_st > 0.0:
                        d_ms = int(round(sub_st * 1000))
                        ov_parts.append(f"adelay={d_ms}|{d_ms}")

                    ov_parts.append(f"atrim=duration={seg.duration:.3f}")
                    vol = getattr(it, "audio_volume", 1.0)
                    if abs(vol - 1.0) > 0.001:
                        ov_parts.append(f"volume={vol:.2f}")
                    ov_parts.extend(["aresample=48000", "aformat=channel_layouts=stereo"])
                    filter_parts.append(f"[{in_layer_idx}:a]{','.join(ov_parts)}[{a_ov_lbl}]")
                    extra_audio_inputs.append(a_ov_lbl)

            a_base_lbl = f"a{j}_base" if extra_audio_inputs else a_label
            if path_has_audio.get(seg.source_path, True):
                a_filter = (
                    f"[{in_idx}:a]atrim=start={trimmed_start:.3f}:end={trimmed_end:.3f},"
                    f"asetpts=PTS-STARTPTS,"
                    f"aresample=48000,aformat=channel_layouts=stereo[{a_base_lbl}]"
                )
            else:
                a_filter = (
                    f"anullsrc=r=48000:cl=stereo,atrim=duration={seg.duration:.3f}[{a_base_lbl}]"
                )
            filter_parts.append(a_filter)

            if extra_audio_inputs:
                all_a = [a_base_lbl] + extra_audio_inputs
                filter_parts.append(
                    f"{''.join(f'[{lbl}]' for lbl in all_a)}amix=inputs={len(all_a)}:duration=first:dropout_transition=0[{a_label}]"
                )

            concat_inputs.append(f"[{v_label}][{a_label}]")

        n_segs = len(segments)
        concat_filter = f"{''.join(concat_inputs)}concat=n={n_segs}:v=1:a=1[outv][outa]"
        filter_parts.append(concat_filter)

        filter_graph = ";".join(filter_parts)

        cmd.extend([
            FLAG_FILTER_COMPLEX,
            filter_graph,
            FLAG_MAP,
            "[outv]",
            FLAG_MAP,
            "[outa]",
        ])

        # Video encoding settings
        v_codec = settings.video_codec if settings else "libx264"
        crf_val = str(settings.crf) if settings else "19"
        preset_val = settings.preset if settings else "veryfast"
        a_codec = settings.audio_codec if settings else "aac"
        a_bitrate = settings.audio_bitrate if settings else "192k"

        if v_codec == "prores_ks":
            cmd.extend([FLAG_CODEC_VIDEO, "prores_ks", "-profile:v", "3", "-vendor", "apl0", FLAG_PIX_FMT, "yuv422p10le"])
        elif v_codec == "libvpx-vp9":
            vp9_cpu = {"ultrafast": "8", "veryfast": "5", "fast": "3", "medium": "2", "slow": "1"}.get(preset_val, "5")
            cmd.extend([FLAG_CODEC_VIDEO, "libvpx-vp9", FLAG_CRF, crf_val, FLAG_VIDEO_BITRATE, "0", FLAG_PIX_FMT, "yuv420p", "-cpu-used", vp9_cpu, "-row-mt", "1"])
        elif v_codec == "libsvtav1":
            svt_preset = {"ultrafast": "11", "veryfast": "10", "fast": "8", "medium": "6", "slow": "4"}.get(preset_val, "10")
            cmd.extend([FLAG_CODEC_VIDEO, "libsvtav1", FLAG_CRF, crf_val, FLAG_PRESET, svt_preset, FLAG_PIX_FMT, "yuv420p"])
        elif v_codec == "libaom-av1":
            aom_cpu = {"ultrafast": "8", "veryfast": "6", "fast": "4", "medium": "2", "slow": "1"}.get(preset_val, "6")
            cmd.extend([FLAG_CODEC_VIDEO, "libaom-av1", FLAG_CRF, crf_val, FLAG_VIDEO_BITRATE, "0", "-cpu-used", aom_cpu, FLAG_PIX_FMT, "yuv420p"])
        elif v_codec == "libx265":
            cmd.extend([FLAG_CODEC_VIDEO, "libx265", FLAG_PRESET, preset_val, FLAG_CRF, crf_val, FLAG_VIDEO_TAG, "hvc1"])
        else:
            cmd.extend([FLAG_CODEC_VIDEO, "libx264", FLAG_PRESET, preset_val, FLAG_CRF, crf_val, FLAG_PIX_FMT, "yuv420p"])

        # Audio encoding settings
        if a_codec == "flac":
            cmd.extend([FLAG_CODEC_AUDIO, "flac"])
        elif a_codec == "copy":
            cmd.extend([FLAG_CODEC_AUDIO, "copy"])
        else:
            cmd.extend([FLAG_CODEC_AUDIO, a_codec, FLAG_AUDIO_BITRATE, a_bitrate])

        cmd.extend([
            FLAG_PROGRESS,
            "pipe:1",
            str(out_path),
        ])

        return cmd

    def export_async(
        self,
        segments: list[Segment],
        output_path: Path | str,
        on_progress: Callable[[float, float], None],
        on_done: Callable[[bool, str | None], None],
        settings: ExportSettings | None = None,
    ) -> None:
        """Start async export with progress and completion callbacks."""
        self._cancelled = False
        total_duration = sum(seg.duration for seg in segments)
        if total_duration <= 0.0:
            GLib.idle_add(on_done, False, "Total export duration is 0")
            return

        out_path = Path(output_path)
        part_path = out_path.with_name(f".{out_path.stem}.{secrets.token_hex(4)}.part{out_path.suffix}")

        if settings and settings.lossless:
            def worker_lossless() -> None:
                inputs = [seg.source_path for seg in segments]
                if any(_is_same_file(out_path, p) for p in inputs):
                    GLib.idle_add(on_done, False, "Destination is one of the source files")
                    return

                exported = False
                time_pattern = re.compile(r"out_time_us=(\d+)")

                try:
                    if len(segments) == 1:
                        seg = segments[0]
                        cmd = [
                            "ffmpeg", FLAG_OVERWRITE,
                            "-ss", f"{seg.start_time:.3f}",
                            "-to", f"{seg.end_time:.3f}",
                            FLAG_INPUT, str(seg.source_path),
                            "-c", "copy",
                            "-avoid_negative_ts", "make_zero",
                            FLAG_PROGRESS, "pipe:1",
                            str(part_path),
                        ]
                        self._process = subprocess.Popen(
                            cmd,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            text=True,
                            bufsize=1,
                        )
                        stderr_lines: list[str] = []
                        def drain_stderr():
                            if self._process and self._process.stderr:
                                try:
                                    for line in self._process.stderr:
                                        stderr_lines.append(line)
                                        if len(stderr_lines) > 200:
                                            stderr_lines.pop(0)
                                except Exception:
                                    pass
                        stderr_thread = threading.Thread(target=drain_stderr, daemon=True)
                        stderr_thread.start()

                        while True:
                            if self._cancelled:
                                if self._process:
                                    self._process.terminate()
                                GLib.idle_add(on_done, False, "Export cancelled by user")
                                return
                            line = self._process.stdout.readline() if self._process.stdout else ""
                            if not line and self._process.poll() is not None:
                                break
                            m = time_pattern.search(line)
                            if m:
                                cur_us = int(m.group(1))
                                cur_s = cur_us / 1_000_000.0
                                fraction = min(1.0, max(0.0, cur_s / total_duration))
                                GLib.idle_add(on_progress, fraction, cur_s)

                        rc = self._process.wait()
                        stderr_thread.join(timeout=1.0)
                        if rc == 0 and not self._cancelled:
                            os.replace(part_path, out_path)
                            exported = True
                            GLib.idle_add(on_progress, 1.0, total_duration)
                            GLib.idle_add(on_done, True, None)
                        else:
                            stderr_msg = "".join(stderr_lines)[-500:]
                            GLib.idle_add(on_done, False, f"FFmpeg failed (code {rc}): {stderr_msg}")
                    else:
                        with tempfile.TemporaryDirectory(prefix="video_cut_lossless_") as tmp_dir_str:
                            tmp_dir = Path(tmp_dir_str)
                            concat_manifest = tmp_dir / "concat_list.txt"
                            manifest_lines: list[str] = []

                            for idx, seg in enumerate(segments):
                                if self._cancelled:
                                    GLib.idle_add(on_done, False, "Export cancelled by user")
                                    return
                                part_seg = tmp_dir / f"seg_{idx:04d}{out_path.suffix}"
                                cmd_cut = [
                                    "ffmpeg", FLAG_OVERWRITE,
                                    "-ss", f"{seg.start_time:.3f}",
                                    "-to", f"{seg.end_time:.3f}",
                                    FLAG_INPUT, str(seg.source_path),
                                    "-c", "copy",
                                    "-avoid_negative_ts", "make_zero",
                                    str(part_seg),
                                ]
                                res = subprocess.run(cmd_cut, capture_output=True, text=True)
                                if res.returncode != 0:
                                    GLib.idle_add(on_done, False, f"FFmpeg cut failed on segment {idx + 1}: {res.stderr[-300:]}")
                                    return
                                manifest_lines.append(f"file '{part_seg.resolve()}'")

                            concat_manifest.write_text("\n".join(manifest_lines) + "\n", encoding="utf-8")

                            cmd_concat = [
                                "ffmpeg", FLAG_OVERWRITE,
                                "-f", "concat",
                                "-safe", "0",
                                FLAG_INPUT, str(concat_manifest),
                                "-c", "copy",
                                FLAG_PROGRESS, "pipe:1",
                                str(part_path),
                            ]
                            self._process = subprocess.Popen(
                                cmd_concat,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE,
                                text=True,
                                bufsize=1,
                            )
                            stderr_lines = []
                            def drain_stderr():
                                if self._process and self._process.stderr:
                                    try:
                                        for line in self._process.stderr:
                                            stderr_lines.append(line)
                                            if len(stderr_lines) > 200:
                                                stderr_lines.pop(0)
                                    except Exception:
                                        pass
                            stderr_thread = threading.Thread(target=drain_stderr, daemon=True)
                            stderr_thread.start()

                            while True:
                                if self._cancelled:
                                    if self._process:
                                        self._process.terminate()
                                    GLib.idle_add(on_done, False, "Export cancelled by user")
                                    return
                                line = self._process.stdout.readline() if self._process.stdout else ""
                                if not line and self._process.poll() is not None:
                                    break
                                m = time_pattern.search(line)
                                if m:
                                    cur_us = int(m.group(1))
                                    cur_s = cur_us / 1_000_000.0
                                    fraction = min(1.0, max(0.0, cur_s / total_duration))
                                    GLib.idle_add(on_progress, fraction, cur_s)

                            rc = self._process.wait()
                            stderr_thread.join(timeout=1.0)
                            if rc == 0 and not self._cancelled:
                                os.replace(part_path, out_path)
                                exported = True
                                GLib.idle_add(on_progress, 1.0, total_duration)
                                GLib.idle_add(on_done, True, None)
                            else:
                                stderr_msg = "".join(stderr_lines)[-500:]
                                GLib.idle_add(on_done, False, f"FFmpeg concat failed (code {rc}): {stderr_msg}")
                except Exception as e:
                    GLib.idle_add(on_done, False, str(e))
                finally:
                    self._process = None
                    if not exported:
                        with contextlib.suppress(OSError):
                            part_path.unlink(missing_ok=True)

            self._thread = threading.Thread(target=worker_lossless, daemon=True)
            self._thread.start()
            return

        def worker() -> None:
            with tempfile.TemporaryDirectory(prefix="video_cut_overlays_") as tmp_dir:
                try:
                    cmd = self.build_ffmpeg_command(
                        segments,
                        part_path,
                        settings=settings,
                        fast_seek=True,
                        overlay_temp_dir=Path(tmp_dir),
                    )
                except Exception as e:
                    GLib.idle_add(on_done, False, str(e))
                    return

                inputs = [Path(arg) for flag, arg in zip(cmd, cmd[1:], strict=False) if flag == "-i"]
                if any(_is_same_file(out_path, p) for p in inputs):
                    GLib.idle_add(on_done, False, "Destination is one of the source files")
                    return

                exported = False
                try:
                    self._process = subprocess.Popen(
                        cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        bufsize=1,
                    )

                    stderr_lines: list[str] = []
                    def drain_stderr():
                        if self._process and self._process.stderr:
                            try:
                                for line in self._process.stderr:
                                    stderr_lines.append(line)
                                    if len(stderr_lines) > 200:
                                        stderr_lines.pop(0)
                            except Exception:
                                pass

                    stderr_thread = threading.Thread(target=drain_stderr, daemon=True)
                    stderr_thread.start()

                    time_pattern = re.compile(r"out_time_us=(\d+)")

                    while True:
                        if self._cancelled:
                            if self._process:
                                self._process.terminate()
                            GLib.idle_add(on_done, False, "Export cancelled by user")
                            return

                        line = self._process.stdout.readline() if self._process.stdout else ""
                        if not line and self._process.poll() is not None:
                            break

                        m = time_pattern.search(line)
                        if m:
                            cur_us = int(m.group(1))
                            cur_s = cur_us / 1_000_000.0
                            fraction = min(1.0, max(0.0, cur_s / total_duration))
                            GLib.idle_add(on_progress, fraction, cur_s)

                    rc = self._process.wait()
                    stderr_thread.join(timeout=1.0)
                    if rc == 0 and not self._cancelled:
                        os.replace(part_path, out_path)
                        exported = True
                        GLib.idle_add(on_progress, 1.0, total_duration)
                        GLib.idle_add(on_done, True, None)
                    else:
                        stderr_msg = "".join(stderr_lines)[-500:]
                        GLib.idle_add(on_done, False, f"FFmpeg failed (code {rc}): {stderr_msg}")
                except Exception as e:
                    GLib.idle_add(on_done, False, str(e))
                finally:
                    self._process = None
                    if not exported:
                        with contextlib.suppress(OSError):
                            part_path.unlink(missing_ok=True)

        self._thread = threading.Thread(target=worker, daemon=True)
        self._thread.start()

    def cancel(self) -> None:
        """Cancel ongoing export."""
        self._cancelled = True
        if self._process:
            try:
                self._process.terminate()
            except Exception:
                pass
