"""Service to export concatenated segments using FFmpeg filter_complex with real-time progress."""

import os
import re
import subprocess
import threading
from pathlib import Path
from typing import Callable, List, Optional
import gi

gi.require_version("GLib", "2.0")
from gi.repository import GLib

from ..models import Segment
from .probe_service import ProbeService


class ExportService:
    """Manages video concatenation and export via FFmpeg."""

    def __init__(self) -> None:
        self._process: Optional[subprocess.Popen] = None
        self._cancelled = False
        self._thread: Optional[threading.Thread] = None

    def build_ffmpeg_command(
        self,
        segments: List[Segment],
        output_path: Path | str,
        target_width: Optional[int] = None,
        target_height: Optional[int] = None,
    ) -> List[str]:
        """Construct the FFmpeg command line for concatenating segments."""
        if not segments:
            raise ValueError("No segments to export")

        out_path = Path(output_path)

        # 1. Determine unique inputs
        unique_paths: List[Path] = []
        for seg in segments:
            if seg.source_path not in unique_paths:
                unique_paths.append(seg.source_path)

        path_to_idx = {p: i for i, p in enumerate(unique_paths)}

        # 2. Determine target dimensions if not provided
        if target_width is None or target_height is None:
            meta = ProbeService.probe(segments[0].source_path)
            if meta:
                target_width = meta.width
                target_height = meta.height
            else:
                target_width, target_height = 1920, 1080

        # Make dimensions even (required by x264)
        target_width = target_width - (target_width % 2)
        target_height = target_height - (target_height % 2)

        # 3. Assemble command inputs
        cmd = ["ffmpeg", "-y"]
        for p in unique_paths:
            cmd.extend(["-i", str(p)])

        # 4. Assemble filter_complex
        filter_parts: List[str] = []
        concat_inputs: List[str] = []

        for j, seg in enumerate(segments):
            in_idx = path_to_idx[seg.source_path]
            v_label = f"v{j}"
            a_label = f"a{j}"

            v_filter = (
                f"[{in_idx}:v]trim=start={seg.start_time:.3f}:end={seg.end_time:.3f},"
                f"setpts=PTS-STARTPTS,"
                f"scale={target_width}:{target_height}:force_original_aspect_ratio=decrease,"
                f"pad={target_width}:{target_height}:(ow-iw)/2:(oh-ih)/2,"
                f"setsar=1[{v_label}]"
            )
            a_filter = (
                f"[{in_idx}:a]atrim=start={seg.start_time:.3f}:end={seg.end_time:.3f},"
                f"asetpts=PTS-STARTPTS,"
                f"aresample=48000,aformat=channel_layouts=stereo[{a_label}]"
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
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "19",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-progress",
            "pipe:1",
            str(out_path),
        ])

        return cmd

    def export_async(
        self,
        segments: List[Segment],
        output_path: Path | str,
        on_progress: Callable[[float, float], None],
        on_done: Callable[[bool, Optional[str]], None],
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
                cmd = self.build_ffmpeg_command(segments, out_path)
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
                )

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
                if rc == 0 and not self._cancelled:
                    GLib.idle_add(on_progress, 1.0, total_duration)
                    GLib.idle_add(on_done, True, None)
                else:
                    stderr_msg = ""
                    if self._process.stderr:
                        stderr_msg = self._process.stderr.read()[-500:]
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
