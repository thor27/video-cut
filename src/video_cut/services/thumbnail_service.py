"""Service to generate and cache video thumbnails asynchronously and ultra-fast."""

import hashlib
import re
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Dict, List, Optional
import gi

gi.require_version("GLib", "2.0")
from gi.repository import GLib


class ThumbnailService:
    """Manages asynchronous extraction and caching of video thumbnails."""

    def __init__(self, cache_dir: Optional[Path] = None, max_workers: int = 2) -> None:
        if cache_dir is None:
            self.cache_dir = Path(tempfile.gettempdir()) / "video_cut_cache"
        else:
            self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.executor = ThreadPoolExecutor(max_workers=max_workers)
        # Mapping from file key to list of (timestamp, image_path)
        self._cache: Dict[str, List[tuple[float, Path]]] = {}

    def _file_key(self, path: Path) -> str:
        try:
            stat = path.stat()
            raw = f"{path.resolve()}_{stat.st_size}_{stat.st_mtime}"
        except OSError:
            raw = str(path)
        return hashlib.md5(raw.encode("utf-8")).hexdigest()

    def _calculate_dt(self, duration: float) -> float:
        """Determine ideal sampling interval in seconds to balance resolution and speed."""
        if duration <= 10.0:
            return 0.5
        elif duration <= 60.0:
            return 1.0
        elif duration <= 300.0:
            return 2.0
        else:
            return max(2.0, duration / 100.0)

    def register_image_thumbnail(self, video_path: Path, image_path: Path, duration: float = 1.0) -> None:
        """Register thumbnail for a converted image video without needing FFmpeg extraction."""
        key = self._file_key(video_path)
        self._cache[key] = [(0.0, image_path), (duration, image_path)]

    def get_thumbnails_for_segment(
        self,
        source_path: Path,
        start_time: float,
        end_time: float,
        max_thumbs: int = 8,
    ) -> List[Optional[Path]]:
        """Return a list of thumbnail paths (or None for loading slots) of length max_thumbs.

        The first item corresponds to start_time, the last item to end_time,
        and intermediate items to evenly distributed timestamps in [start_time, end_time].
        """
        count = max(1, max_thumbs)
        key = self._file_key(source_path)
        items = self._cache.get(key, [])

        dur = max(0.001, end_time - start_time)
        if count == 1:
            target_times = [start_time + dur / 2.0]
        else:
            target_times = []
            for i in range(count):
                ratio = i / float(count - 1)
                t = start_time + ratio * dur
                if i == count - 1 and dur > 0.08:
                    t = min(t, end_time - 0.04)
                target_times.append(t)

        if not items:
            return [None] * count

        results: List[Optional[Path]] = []
        dt = self._calculate_dt(dur)
        if count == 1:
            max_diff = max(1.5, dur)
        else:
            step = dur / float(count - 1)
            max_diff = max(dt, step * 0.65)

        for t in target_times:
            closest_ts, closest_path = min(items, key=lambda x: abs(x[0] - t))
            if abs(closest_ts - t) <= max_diff and closest_path.exists():
                results.append(closest_path)
            else:
                results.append(None)

        return results

    def extract_exact_thumbnail_async(
        self,
        source_path: Path,
        target_time: float,
        on_ready: Optional[Callable[[Path], None]] = None,
    ) -> None:
        """Extract a single thumbnail at an exact timestamp asynchronously."""
        path = Path(source_path)
        key = self._file_key(path)

        def worker() -> None:
            video_cache_dir = self.cache_dir / key
            video_cache_dir.mkdir(parents=True, exist_ok=True)
            ts_ms = int(round(target_time * 1000))
            out_p = video_cache_dir / f"thumb_exact_{ts_ms}.jpg"
            if not out_p.exists():
                cmd = [
                    "ffmpeg",
                    "-y",
                    "-ss",
                    f"{target_time:.3f}",
                    "-i",
                    str(path),
                    "-frames:v",
                    "1",
                    "-vf",
                    "scale=160:90:force_original_aspect_ratio=decrease,pad=160:90:(ow-iw)/2:(oh-ih)/2",
                    "-q:v",
                    "3",
                    str(out_p),
                ]
                try:
                    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
                except Exception:
                    pass

            if out_p.exists():
                if key not in self._cache:
                    self._cache[key] = []
                self._cache[key].append((target_time, out_p))
                self._cache[key].sort(key=lambda x: x[0])
                if on_ready:
                    GLib.idle_add(on_ready, out_p)

        self.executor.submit(worker)

    def generate_thumbnails_async(
        self,
        source_path: Path | str,
        duration: float,
        on_done: Callable[[Path, List[tuple[float, Path]]], None],
        on_first_thumb: Optional[Callable[[Path, Path], None]] = None,
    ) -> None:
        """Extract thumbnails in the background, providing an instant first thumb then full set."""
        path = Path(source_path)

        def worker() -> None:
            key = self._file_key(path)
            video_cache_dir = self.cache_dir / key
            video_cache_dir.mkdir(parents=True, exist_ok=True)

            dt = self._calculate_dt(duration)

            # Check if already cached in memory
            if key in self._cache and len(self._cache[key]) > 1:
                items = self._cache[key]
                GLib.idle_add(on_done, path, items)
                return

            # Check if already cached on disk
            existing_seq = sorted(video_cache_dir.glob("thumb_seq_*.jpg"))
            if existing_seq:
                results = []
                for idx, thumb_file in enumerate(existing_seq):
                    ts = min(duration, idx * dt)
                    results.append((ts, thumb_file))
                end_thumb = video_cache_dir / "thumb_final.jpg"
                if end_thumb.exists():
                    results.append((max(0.0, duration - 0.05), end_thumb))
                results.sort(key=lambda x: x[0])
                self._cache[key] = results
                GLib.idle_add(on_done, path, results)
                return

            # Step 1: Immediate first frame (< 50ms)
            first_thumb = video_cache_dir / "thumb_0000.jpg"
            if not first_thumb.exists():
                cmd_first = [
                    "ffmpeg",
                    "-y",
                    "-ss",
                    "0",
                    "-i",
                    str(path),
                    "-frames:v",
                    "1",
                    "-vf",
                    "scale=160:90:force_original_aspect_ratio=decrease,pad=160:90:(ow-iw)/2:(oh-ih)/2",
                    "-q:v",
                    "3",
                    str(first_thumb),
                ]
                try:
                    subprocess.run(cmd_first, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
                except Exception:
                    pass

            if first_thumb.exists():
                if key not in self._cache:
                    self._cache[key] = []
                self._cache[key].append((0.0, first_thumb))
                if on_first_thumb:
                    GLib.idle_add(on_first_thumb, path, first_thumb)

            # Step 2: Fast uniform sampling
            fps_val = 1.0 / dt
            pattern = str(video_cache_dir / "thumb_seq_%04d.jpg")
            vf = f"fps=fps={fps_val:.4f},scale=160:90:force_original_aspect_ratio=decrease,pad=160:90:(ow-iw)/2:(oh-ih)/2"

            cmd_seq = [
                "ffmpeg",
                "-y",
                "-i",
                str(path),
                "-vf",
                vf,
                "-q:v",
                "4",
                pattern,
            ]
            try:
                subprocess.run(cmd_seq, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
            except Exception:
                pass

            found_files = sorted(video_cache_dir.glob("thumb_seq_*.jpg"))
            results: List[tuple[float, Path]] = []
            if found_files:
                for idx, thumb_file in enumerate(found_files):
                    ts = min(duration, idx * dt)
                    results.append((ts, thumb_file))

            # Step 3: Extract the final frame of the video
            if duration > 0.5:
                end_thumb = video_cache_dir / "thumb_final.jpg"
                if not end_thumb.exists():
                    cmd_last = [
                        "ffmpeg",
                        "-y",
                        "-ss",
                        f"{max(0.0, duration - 0.05):.3f}",
                        "-i",
                        str(path),
                        "-frames:v",
                        "1",
                        "-vf",
                        "scale=160:90:force_original_aspect_ratio=decrease,pad=160:90:(ow-iw)/2:(oh-ih)/2",
                        "-q:v",
                        "3",
                        str(end_thumb),
                    ]
                    try:
                        subprocess.run(cmd_last, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
                    except Exception:
                        pass
                if end_thumb.exists():
                    results.append((max(0.0, duration - 0.05), end_thumb))

            if not results and first_thumb.exists():
                results.append((0.0, first_thumb))

            results.sort(key=lambda x: x[0])
            self._cache[key] = results
            GLib.idle_add(on_done, path, results)

        self.executor.submit(worker)
