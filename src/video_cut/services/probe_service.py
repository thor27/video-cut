"""Probe service to extract metadata from video files using ffprobe."""

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class VideoMetadata:
    path: Path
    duration: float
    width: int
    height: int
    fps: float

    @property
    def aspect_ratio(self) -> float:
        if self.height > 0:
            return self.width / self.height
        return 16.0 / 9.0


class ProbeService:
    """Wrapper around ffprobe to inspect video files."""

    @staticmethod
    def probe(file_path: Path | str) -> Optional[VideoMetadata]:
        path = Path(file_path)
        if not path.exists():
            return None

        cmd = [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,duration,r_frame_rate:format=duration",
            "-of",
            "json",
            str(path),
        ]

        try:
            result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
            data = json.loads(result.stdout)

            streams = data.get("streams", [])
            fmt = data.get("format", {})

            if not streams:
                return None

            stream = streams[0]
            width = int(stream.get("width", 1920))
            height = int(stream.get("height", 1080))

            # Duration can be in format or stream
            dur_str = fmt.get("duration") or stream.get("duration")
            if not dur_str:
                return None
            duration = float(dur_str)

            # Framerate e.g. "30/1" or "29.97"
            fps_str = stream.get("r_frame_rate", "30/1")
            if "/" in fps_str:
                num, den = fps_str.split("/", 1)
                fps = float(num) / float(den) if float(den) > 0 else 30.0
            else:
                fps = float(fps_str)

            return VideoMetadata(
                path=path,
                duration=duration,
                width=width,
                height=height,
                fps=fps,
            )
        except Exception:
            return None

    @staticmethod
    def probe_duration(file_path: Path | str) -> Optional[float]:
        """Inspect media duration using ffprobe for audio or video files."""
        path = Path(file_path)
        if not path.exists():
            return None

        cmd = [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(path),
        ]
        try:
            result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
            data = json.loads(result.stdout)
            fmt = data.get("format", {})
            dur_str = fmt.get("duration")
            if dur_str:
                return float(dur_str)
            return None
        except Exception:
            return None
