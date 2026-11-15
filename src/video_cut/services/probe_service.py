"""Probe service to extract metadata from video files using ffprobe."""

import bisect
import json
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROBE_TIMEOUT = 30.0

_CacheKey = tuple[str, str, int, int]


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


def _parse_video_metadata(path: Path, data: dict[str, Any]) -> VideoMetadata | None:
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

    return VideoMetadata(path=path, duration=duration, width=width, height=height, fps=fps)


class ProbeService:
    """Wrapper around ffprobe to inspect video files.

    Results are cached per (resolved path, size, mtime), because the timeline
    and the exporter probe the same file many times. Failed or timed out probes
    are not cached, so a file that is still being written is probed again.
    """

    _cache: dict[_CacheKey, Any] = {}
    _lock = threading.Lock()

    @classmethod
    def clear_cache(cls) -> None:
        with cls._lock:
            cls._cache.clear()

    @staticmethod
    def _key(kind: str, path: Path) -> _CacheKey | None:
        try:
            st = path.stat()
            real = str(path.resolve())
        except OSError:
            return None
        return (kind, real, st.st_size, st.st_mtime_ns)

    @classmethod
    def _lookup(cls, key: _CacheKey) -> tuple[bool, Any]:
        with cls._lock:
            if key in cls._cache:
                return True, cls._cache[key]
        return False, None

    @classmethod
    def _store(cls, key: _CacheKey, value: Any) -> Any:
        with cls._lock:
            cls._cache[key] = value
        return value

    @staticmethod
    def _run_ffprobe(args: list[str]) -> dict[str, Any]:
        result = subprocess.run(
            ["ffprobe", "-v", "error", *args, "-of", "json"],
            capture_output=True,
            text=True,
            check=True,
            timeout=PROBE_TIMEOUT,
        )
        return json.loads(result.stdout)

    @classmethod
    def probe(cls, file_path: Path | str) -> VideoMetadata | None:
        path = Path(file_path)
        key = cls._key("probe", path)
        if key is None:
            return None
        hit, value = cls._lookup(key)
        if hit:
            return value

        try:
            data = cls._run_ffprobe(
                [
                    "-select_streams",
                    "v:0",
                    "-show_entries",
                    "stream=width,height,duration,r_frame_rate:format=duration",
                    str(path),
                ]
            )
            meta = _parse_video_metadata(path, data)
        except Exception:
            return None
        return cls._store(key, meta)

    @classmethod
    def probe_duration(cls, file_path: Path | str) -> float | None:
        """Inspect media duration using ffprobe for audio or video files."""
        path = Path(file_path)
        key = cls._key("duration", path)
        if key is None:
            return None
        hit, value = cls._lookup(key)
        if hit:
            return value

        try:
            data = cls._run_ffprobe(["-show_entries", "format=duration", str(path)])
            dur_str = data.get("format", {}).get("duration")
            duration = float(dur_str) if dur_str else None
        except Exception:
            return None
        return cls._store(key, duration)

    @classmethod
    def has_audio(cls, file_path: Path | str) -> bool:
        """Check if media file has an audio stream."""
        path = Path(file_path)
        key = cls._key("audio", path)
        if key is None:
            return False
        hit, value = cls._lookup(key)
        if hit:
            return value

        try:
            data = cls._run_ffprobe(
                ["-select_streams", "a:0", "-show_entries", "stream=codec_type", str(path)]
            )
            has_stream = len(data.get("streams", [])) > 0
        except Exception:
            return False
        return cls._store(key, has_stream)

    @classmethod
    def get_keyframe_timestamps(cls, file_path: Path | str) -> list[float]:
        """Inspect media file and return sorted list of keyframe (I-frame) timestamps in seconds."""
        path = Path(file_path)
        key = cls._key("keyframes", path)
        if key is None:
            return [0.0]
        hit, value = cls._lookup(key)
        if hit:
            return value

        try:
            data = cls._run_ffprobe(
                [
                    "-select_streams",
                    "v:0",
                    "-skip_frame",
                    "nokey",
                    "-show_entries",
                    "frame=pts_time,pkt_pts_time",
                    str(path),
                ]
            )
            keyframes: list[float] = []
            for frame in data.get("frames", []):
                ts = frame.get("pts_time") if frame.get("pts_time") is not None else frame.get("pkt_pts_time")
                if ts is not None:
                    try:
                        val = float(ts)
                        if not keyframes or val > keyframes[-1] + 1e-4:
                            keyframes.append(val)
                    except (ValueError, TypeError):
                        continue
            if not keyframes:
                keyframes = [0.0]
            elif keyframes[0] > 0.05:
                # Always ensure start of video is reachable
                keyframes.insert(0, 0.0)
        except Exception:
            keyframes = [0.0]

        return cls._store(key, keyframes)

    @staticmethod
    def find_nearest_keyframe(keyframes: list[float], target_time: float) -> float:
        """Find the keyframe timestamp closest to target_time."""
        if not keyframes:
            return max(0.0, target_time)
        idx = bisect.bisect_left(keyframes, target_time)
        if idx == 0:
            return keyframes[0]
        if idx >= len(keyframes):
            return keyframes[-1]
        before = keyframes[idx - 1]
        after = keyframes[idx]
        return after if (after - target_time) < (target_time - before) else before

    @staticmethod
    def find_adjacent_keyframe(keyframes: list[float], current_time: float, direction: int) -> float | None:
        """Find the next (direction > 0) or previous (direction < 0) keyframe from current_time."""
        if not keyframes:
            return None
        epsilon = 0.02
        if direction > 0:
            for k in keyframes:
                if k > current_time + epsilon:
                    return k
            return None
        else:
            for k in reversed(keyframes):
                if k < current_time - epsilon:
                    return k
            return None

    @classmethod
    def get_video_codec_signature(cls, file_path: Path | str) -> dict[str, Any] | None:
        """Inspect and return codec signature for verifying lossless stream-copy compatibility."""
        path = Path(file_path)
        key = cls._key("codec_signature", path)
        if key is None:
            return None
        hit, value = cls._lookup(key)
        if hit:
            return value

        try:
            data = cls._run_ffprobe(
                [
                    "-show_entries",
                    "stream=codec_type,codec_name,width,height,pix_fmt,r_frame_rate,sample_rate,channels",
                    str(path),
                ]
            )
            v_stream = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
            a_stream = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), None)
            if not v_stream:
                return None

            sig: dict[str, Any] = {
                "video_codec": v_stream.get("codec_name"),
                "width": v_stream.get("width"),
                "height": v_stream.get("height"),
                "pix_fmt": v_stream.get("pix_fmt"),
                "fps": v_stream.get("r_frame_rate"),
                "has_audio": a_stream is not None,
                "audio_codec": a_stream.get("codec_name") if a_stream else None,
                "sample_rate": a_stream.get("sample_rate") if a_stream else None,
                "channels": a_stream.get("channels") if a_stream else None,
            }
        except Exception:
            return None

        return cls._store(key, sig)
