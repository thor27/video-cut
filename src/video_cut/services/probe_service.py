"""Probe service to extract metadata from video files using ffprobe."""

import bisect
import json
import subprocess
import threading
import time
from collections.abc import Callable
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
    _in_flight_kfs: dict[str, list[Callable[[list[float]], None]]] = {}
    _lock = threading.Lock()

    @classmethod
    def clear_cache(cls) -> None:
        with cls._lock:
            cls._cache.clear()
            cls._in_flight_kfs.clear()

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
    get_duration = probe_duration

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
    def is_keyframes_cached(cls, file_path: Path | str) -> bool:
        """Check if keyframe timestamps for file_path are already probed and cached."""
        path = Path(file_path)
        key = cls._key("keyframes", path)
        if key is None:
            return False
        hit, _ = cls._lookup(key)
        return hit

    @classmethod
    def prefetch_keyframes_async(
        cls,
        file_path: Path | str,
        on_done: Callable[[list[float]], None] | None = None,
    ) -> None:
        """Prefetch and cache keyframe timestamps in a background thread with deduplication."""
        path = Path(file_path)
        try:
            real = str(path.resolve())
        except OSError:
            real = str(path)

        if cls.is_keyframes_cached(path):
            if on_done:
                from gi.repository import GLib
                kfs = cls.get_keyframe_timestamps(path)
                GLib.idle_add(on_done, kfs)
            return

        with cls._lock:
            if real in cls._in_flight_kfs:
                if on_done:
                    cls._in_flight_kfs[real].append(on_done)
                return
            cls._in_flight_kfs[real] = [on_done] if on_done else []

        def worker():
            kfs = cls.probe_keyframes_streaming(path) or [0.0]
            with cls._lock:
                callbacks = cls._in_flight_kfs.pop(real, [])
            if callbacks:
                from gi.repository import GLib
                for cb in callbacks:
                    GLib.idle_add(cb, kfs)

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

    @classmethod
    def probe_keyframes_streaming(
        cls,
        file_path: Path | str,
        duration: float | None = None,
        on_progress: Callable[[float, float], None] | None = None,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> list[float] | None:
        """Stream keyframe timestamps using fast packet probing first, falling back to frame probing."""
        path = Path(file_path)
        key = cls._key("keyframes", path)
        if key is None:
            return None

        hit, value = cls._lookup(key)
        if hit:
            if on_progress and duration and duration > 0:
                on_progress(1.0, duration)
            return value

        if duration is None or duration <= 0:
            duration = cls.probe_duration(path) or 0.0

        keyframes: list[float] = []
        last_progress_time = 0.0

        # Tier 1: Fast Packet Index Probing (reads container packet headers in <0.1s without decoding)
        cmd_pkt = [
            "ffprobe",
            "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "packet=pts_time,flags",
            "-of", "csv=p=0",
            str(path),
        ]
        try:
            proc_pkt = subprocess.Popen(
                cmd_pkt,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=65536,
            )
            assert proc_pkt.stdout is not None
            for line in proc_pkt.stdout:
                if is_cancelled and is_cancelled():
                    proc_pkt.terminate()
                    try:
                        proc_pkt.wait(timeout=1.0)
                    except subprocess.TimeoutExpired:
                        proc_pkt.kill()
                    return None

                line = line.strip()
                if not line:
                    continue
                parts = line.split(",")
                if len(parts) >= 2 and any("K" in p for p in parts[1:]):
                    for p in parts[:2]:
                        p = p.strip()
                        if p and p != "N/A" and "K" not in p:
                            try:
                                val = float(p)
                                if not keyframes or val > keyframes[-1] + 1e-4:
                                    keyframes.append(val)
                                break
                            except (ValueError, TypeError):
                                continue

                if on_progress and duration > 0:
                    now = time.time()
                    if now - last_progress_time >= 0.08:
                        last_progress_time = now
                        try:
                            pts = float(parts[0])
                            frac = min(0.99, max(0.0, pts / duration))
                            on_progress(frac, pts)
                        except (ValueError, IndexError):
                            pass

            proc_pkt.wait()
        except Exception:
            if proc_pkt.poll() is None:
                proc_pkt.kill()

        if is_cancelled and is_cancelled():
            return None

        # Check if Tier 1 successfully found keyframes across the file
        if len(keyframes) > 1 or (duration <= 2.0 and len(keyframes) >= 1):
            if keyframes[0] > 0.05:
                keyframes.insert(0, 0.0)
            if on_progress and duration > 0:
                on_progress(1.0, duration)
            return cls._store(key, keyframes)

        # Tier 2: Accurate Frame-Level Probing Fallback (if packet probe returned <= 1 keyframe)
        keyframes.clear()
        cmd_frm = [
            "ffprobe",
            "-v", "error",
            "-threads", "0",
            "-select_streams", "v:0",
            "-skip_frame", "nokey",
            "-show_entries", "frame=pts_time",
            "-of", "csv=p=0",
            str(path),
        ]
        try:
            proc_frm = subprocess.Popen(
                cmd_frm,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=65536,
            )
            assert proc_frm.stdout is not None
            for line in proc_frm.stdout:
                if is_cancelled and is_cancelled():
                    proc_frm.terminate()
                    try:
                        proc_frm.wait(timeout=1.0)
                    except subprocess.TimeoutExpired:
                        proc_frm.kill()
                    return None

                line = line.strip().rstrip(",")
                if not line:
                    continue
                try:
                    val = float(line)
                    if not keyframes or val > keyframes[-1] + 1e-4:
                        keyframes.append(val)
                    if on_progress and duration > 0:
                        now = time.time()
                        if now - last_progress_time >= 0.08 or val >= duration - 0.1:
                            last_progress_time = now
                            frac = min(0.99, max(0.0, val / duration))
                            on_progress(frac, val)
                except ValueError:
                    continue

            proc_frm.wait()
        except Exception:
            if proc_frm.poll() is None:
                proc_frm.kill()
            return None

        if is_cancelled and is_cancelled():
            return None

        if proc_frm.returncode != 0 and not keyframes:
            return None

        if not keyframes:
            keyframes = [0.0]
        elif keyframes[0] > 0.05:
            keyframes.insert(0, 0.0)

        if on_progress and duration > 0:
            on_progress(1.0, duration)

        return cls._store(key, keyframes)

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

        kfs = cls.probe_keyframes_streaming(path)
        if kfs:
            return kfs
        return [0.0]

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
