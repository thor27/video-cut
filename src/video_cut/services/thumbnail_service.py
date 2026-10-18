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
    ) -> List[Path]:
        """Return cached thumbnail paths that fall within [start_time, end_time]."""
        key = self._file_key(source_path)
        items = self._cache.get(key, [])
        if not items:
            return []

        # Find matching items
        matching = [p for ts, p in items if start_time <= ts <= end_time]
        if not matching:
            # Pick closest thumbnail
            mid = (start_time + end_time) / 2.0
            closest = min(items, key=lambda x: abs(x[0] - mid))
            return [closest[1]]

        # Subsample if more than max_thumbs
        if len(matching) > max_thumbs:
            step = len(matching) / float(max_thumbs)
            return [matching[int(i * step)] for i in range(max_thumbs)]
        return matching

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
            if key in self._cache:
                items = self._cache[key]
                GLib.idle_add(on_done, path, items)
                return

            video_cache_dir = self.cache_dir / key
            video_cache_dir.mkdir(parents=True, exist_ok=True)

            # Step 1: Immediate first frame (< 50ms)
            first_thumb = video_cache_dir / "thumb_initial.jpg"
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
                    if first_thumb.exists() and on_first_thumb:
                        GLib.idle_add(on_first_thumb, path, first_thumb)
                except Exception:
                    pass

            # Step 2: Ultra-fast keyframe extraction (decodes only I-frames at 600x realtime)
            pattern = str(video_cache_dir / "thumb_%04d.jpg")
            vf = "scale=160:90:force_original_aspect_ratio=decrease,pad=160:90:(ow-iw)/2:(oh-ih)/2"

            cmd_keyframes = [
                "ffmpeg",
                "-y",
                "-skip_frame",
                "nokey",
                "-i",
                str(path),
                "-fps_mode",
                "vfr",
                "-vf",
                vf,
                "-vframes",
                "45",
                "-q:v",
                "4",
                pattern,
            ]

            try:
                subprocess.run(cmd_keyframes, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
            except Exception:
                pass

            found_files = sorted(video_cache_dir.glob("thumb_[0-9][0-9][0-9][0-9].jpg"))

            # Step 3: If keyframes yielded fewer than 8 thumbnails (e.g. video has few keyframes),
            # supplement with fast seeks at intervals
            if len(found_files) < 8 and duration > 1.0:
                target_count = 12
                interval = duration / float(target_count)
                for idx in range(target_count):
                    t = idx * interval
                    out_p = video_cache_dir / f"thumb_seek_{idx:02d}.jpg"
                    if not out_p.exists():
                        cmd_seek = [
                            "ffmpeg",
                            "-y",
                            "-ss",
                            f"{t:.2f}",
                            "-i",
                            str(path),
                            "-frames:v",
                            "1",
                            "-vf",
                            vf,
                            "-q:v",
                            "4",
                            str(out_p),
                        ]
                        try:
                            subprocess.run(cmd_seek, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
                        except Exception:
                            pass
                found_files = sorted(video_cache_dir.glob("thumb_*.jpg"))

            # Map found files evenly across duration
            results: List[tuple[float, Path]] = []
            if found_files:
                num_files = len(found_files)
                for idx, thumb_file in enumerate(found_files):
                    ts = (idx / float(max(1, num_files - 1))) * duration
                    results.append((ts, thumb_file))
            elif first_thumb.exists():
                results.append((0.0, first_thumb))

            self._cache[key] = results
            GLib.idle_add(on_done, path, results)

        self.executor.submit(worker)
