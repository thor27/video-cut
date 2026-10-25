"""Service to export concatenated segments using FFmpeg filter_complex with real-time progress."""

import re
import subprocess
import threading
from collections.abc import Callable
from pathlib import Path

import gi

gi.require_version("GLib", "2.0")
from gi.repository import GLib

from ..i18n import _
from ..models import ExportSettings, Segment
from ..utils import SUBPROCESS_FLAGS, get_ffmpeg_path
from .probe_service import ProbeService


class FFmpegCapabilities:
    """Detects available video encoders, audio encoders, and muxers in installed FFmpeg."""

    _encoders: set[str] | None = None
    _muxers: set[str] | None = None

    @classmethod
    def get_encoders(cls) -> set[str]:
        if cls._encoders is None:
            try:
                res = subprocess.run(
                    [get_ffmpeg_path(), "-encoders"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                    creationflags=SUBPROCESS_FLAGS,
                )
                encoders = set()
                for line in res.stdout.splitlines():
                    parts = line.strip().split()
                    if len(parts) >= 2 and len(parts[0]) >= 6:
                        encoders.add(parts[1])
                cls._encoders = encoders
            except Exception:
                cls._encoders = {"libx264", "aac"}
        return cls._encoders

    @classmethod
    def get_muxers(cls) -> set[str]:
        if cls._muxers is None:
            try:
                res = subprocess.run(
                    [get_ffmpeg_path(), "-muxers"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                    creationflags=SUBPROCESS_FLAGS,
                )
                muxers = set()
                for line in res.stdout.splitlines():
                    parts = line.strip().split()
                    if len(parts) >= 2 and "E" in parts[0]:
                        muxers.add(parts[1])
                cls._muxers = muxers
            except Exception:
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


class ExportService:
    """Manages video concatenation and export via FFmpeg."""

    def __init__(self) -> None:
        self._process: subprocess.Popen | None = None
        self._cancelled = False
        self._thread: threading.Thread | None = None

    def build_ffmpeg_command(
        self,
        segments: list[Segment],
        output_path: Path | str,
        target_width: int | None = None,
        target_height: int | None = None,
        settings: ExportSettings | None = None,
        fast_seek: bool = False,
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
                target_width = meta.width
                target_height = meta.height
            else:
                target_width, target_height = 1920, 1080

        # Make dimensions even (required by most encoders)
        target_width = target_width - (target_width % 2)
        target_height = target_height - (target_height % 2)

        # 3. Assemble command inputs
        cmd = [get_ffmpeg_path(), "-y"]
        for p in unique_paths:
            min_s = path_to_min_start[p]
            if min_s > 0.0:
                cmd.extend(["-ss", f"{min_s:.3f}"])
            cmd.extend(["-i", str(p)])

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

            v_chain.extend([
                f"scale={target_width}:{target_height}:force_original_aspect_ratio=decrease",
                f"pad={target_width}:{target_height}:(ow-iw)/2:(oh-ih)/2",
                "setsar=1",
            ])

            if settings and settings.fps is not None and settings.fps > 0:
                v_chain.append(f"fps={settings.fps:.3f}")

            v_filter = f"[{in_idx}:v]{','.join(v_chain)}[{v_label}]"
            if path_has_audio.get(seg.source_path, True):
                a_filter = (
                    f"[{in_idx}:a]atrim=start={trimmed_start:.3f}:end={trimmed_end:.3f},"
                    f"asetpts=PTS-STARTPTS,"
                    f"aresample=48000,aformat=channel_layouts=stereo[{a_label}]"
                )
            else:
                a_filter = (
                    f"anullsrc=r=48000:cl=stereo,atrim=duration={seg.duration:.3f}[{a_label}]"
                )

            filter_parts.append(v_filter)
            filter_parts.append(a_filter)
            concat_inputs.append(f"[{v_label}][{a_label}]")

        n_segs = len(segments)
        concat_filter = f"{''.join(concat_inputs)}concat=n={n_segs}:v=1:a=1[outv][outa]"
        filter_parts.append(concat_filter)

        filter_graph = ";".join(filter_parts)

        cmd.extend([
            "-filter_complex",
            filter_graph,
            "-map",
            "[outv]",
            "-map",
            "[outa]",
        ])

        # Video encoding settings
        v_codec = settings.video_codec if settings else "libx264"
        crf_val = str(settings.crf) if settings else "19"
        preset_val = settings.preset if settings else "veryfast"
        a_codec = settings.audio_codec if settings else "aac"
        a_bitrate = settings.audio_bitrate if settings else "192k"

        if v_codec == "prores_ks":
            cmd.extend(["-c:v", "prores_ks", "-profile:v", "3", "-vendor", "apl0", "-pix_fmt", "yuv422p10le"])
        elif v_codec == "libvpx-vp9":
            vp9_cpu = {"ultrafast": "8", "veryfast": "5", "fast": "3", "medium": "2", "slow": "1"}.get(preset_val, "5")
            cmd.extend(["-c:v", "libvpx-vp9", "-crf", crf_val, "-b:v", "0", "-pix_fmt", "yuv420p", "-cpu-used", vp9_cpu, "-row-mt", "1"])
        elif v_codec == "libsvtav1":
            svt_preset = {"ultrafast": "11", "veryfast": "10", "fast": "8", "medium": "6", "slow": "4"}.get(preset_val, "10")
            cmd.extend(["-c:v", "libsvtav1", "-crf", crf_val, "-preset", svt_preset, "-pix_fmt", "yuv420p"])
        elif v_codec == "libaom-av1":
            aom_cpu = {"ultrafast": "8", "veryfast": "6", "fast": "4", "medium": "2", "slow": "1"}.get(preset_val, "6")
            cmd.extend(["-c:v", "libaom-av1", "-crf", crf_val, "-b:v", "0", "-cpu-used", aom_cpu, "-pix_fmt", "yuv420p"])
        elif v_codec == "libx265":
            cmd.extend(["-c:v", "libx265", "-preset", preset_val, "-crf", crf_val, "-tag:v", "hvc1"])
        else:
            cmd.extend(["-c:v", "libx264", "-preset", preset_val, "-crf", crf_val, "-pix_fmt", "yuv420p"])

        # Audio encoding settings
        if a_codec == "flac":
            cmd.extend(["-c:a", "flac"])
        elif a_codec == "copy":
            cmd.extend(["-c:a", "copy"])
        else:
            cmd.extend(["-c:a", a_codec, "-b:a", a_bitrate])

        cmd.extend([
            "-progress",
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

        def worker() -> None:
            try:
                cmd = self.build_ffmpeg_command(segments, out_path, settings=settings, fast_seek=True)
            except Exception as e:
                GLib.idle_add(on_done, False, str(e))
                return

            try:
                self._process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=1,
                    creationflags=SUBPROCESS_FLAGS,
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
                    GLib.idle_add(on_progress, 1.0, total_duration)
                    GLib.idle_add(on_done, True, None)
                else:
                    stderr_msg = "".join(stderr_lines)[-500:]
                    GLib.idle_add(on_done, False, f"FFmpeg failed (code {rc}): {stderr_msg}")
            except Exception as e:
                GLib.idle_add(on_done, False, str(e))
            finally:
                self._process = None

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
