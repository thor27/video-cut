"""Service to generate and cache video thumbnails asynchronously and ultra-fast."""

import hashlib
import json
import re
import subprocess
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set
import gi

gi.require_version("GLib", "2.0")
from gi.repository import GLib


class ThumbnailService:
    """Manages asynchronous extraction and caching of video thumbnails."""

    def __init__(self, cache_dir: Optional[Path] = None, max_workers: int = 4) -> None:
        if cache_dir is None:
            self.cache_dir = Path(tempfile.gettempdir()) / "video_cut_cache"
        else:
            self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.executor = ThreadPoolExecutor(max_workers=max_workers)
        # Mapping from file key to list of (timestamp, image_path)
        self._cache: Dict[str, List[tuple[float, Path]]] = {}
        self._cache_lock = threading.Lock()
        self._completed_keys: Set[str] = set()
        self._in_progress_keys: Set[str] = set()
        self._active_jobs: int = 0
        self._active_threads: List[threading.Thread] = []

    def has_pending_jobs(self) -> bool:
        """Check if any thumbnail generation tasks are currently active."""
        with self._cache_lock:
            return self._active_jobs > 0

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
        with self._cache_lock:
            self._cache[key] = [(0.0, image_path), (duration, image_path)]
            self._completed_keys.add(key)

    def is_cached(self, source_path: Path) -> bool:
        """Check if thumbnails for source_path are already fully cached."""
        path = Path(source_path)
        key = self._file_key(path)
        with self._cache_lock:
            if key in self._completed_keys and key in self._cache:
                return True
        index_file = self.cache_dir / key / "index.json"
        return index_file.exists()

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
        with self._cache_lock:
            items = list(self._cache.get(key, []))
            is_completed = key in self._completed_keys

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
            step = dur
        else:
            step = dur / float(count - 1)
            # While in progress, use strict tolerance so unreached positions show placeholders
            max_diff = max(dt, step * 0.65)

        for t in target_times:
            closest_ts, closest_path = min(items, key=lambda x: abs(x[0] - t))
            if is_completed:
                # If extraction is completed, every slot must resolve to the closest frame
                if closest_path.exists():
                    results.append(closest_path)
                else:
                    results.append(None)
            else:
                # While extraction is in progress, only show frames close enough to target time
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
                    "-nostdin",
                    "-ss",
                    f"{target_time:.3f}",
                    "-i",
                    str(path),
                    "-an",
                    "-sn",
                    "-dn",
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
                with self._cache_lock:
                    if key not in self._cache:
                        self._cache[key] = []
                    if not any(abs(existing_ts - target_time) < 0.01 for existing_ts, _ in self._cache[key]):
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
        on_progress: Optional[Callable[[int, int, Path], None]] = None,
    ) -> None:
        """Extract thumbnails in the background ultra-fast using parallel targeted seeks,
        streaming progressive updates as each thumbnail completes.
        """
        path = Path(source_path)
        key = self._file_key(path)
        video_cache_dir = self.cache_dir / key
        video_cache_dir.mkdir(parents=True, exist_ok=True)
        index_file = video_cache_dir / "index.json"

        # 1. Check if already completed in memory
        with self._cache_lock:
            if key in self._completed_keys and key in self._cache:
                results = list(self._cache[key])
                GLib.idle_add(on_done, path, results)
                return
            if key in self._in_progress_keys:
                return

        # 2. Check if already cached on disk with index.json
        if index_file.exists():
            try:
                data = json.loads(index_file.read_text(encoding="utf-8"))
                loaded = [(float(ts), Path(p)) for ts, p in data if Path(p).exists()]
                if len(loaded) >= 1:
                    with self._cache_lock:
                        self._cache[key] = loaded
                        self._completed_keys.add(key)
                    GLib.idle_add(on_done, path, loaded)
                    return
            except Exception:
                pass

        # 3. Determine target sample timestamps
        if duration <= 0.6:
            times = [0.0]
        else:
            dt = self._calculate_dt(duration)
            num_samples = max(6, min(24, int(round(duration / dt)) + 1))
            step = duration / float(num_samples - 1)
            raw_times = [i * step for i in range(num_samples)]
            raw_times[-1] = max(0.0, duration - 0.05)
            times = []
            for t in raw_times:
                rt = round(max(0.0, min(duration, t)), 3)
                if not times or rt > times[-1] + 0.04:
                    times.append(rt)

        total_samples = len(times)
        completed_counter = [0]

        with self._cache_lock:
            self._active_jobs += 1
            self._in_progress_keys.add(key)

        vf = "scale=160:90:force_original_aspect_ratio=decrease,pad=160:90:(ow-iw)/2:(oh-ih)/2"

        items: list[tuple[int, float, Path]] = []
        for idx, t in enumerate(times):
            ts_ms = int(round(t * 1000))
            out_p = video_cache_dir / f"thumb_{idx:03d}_{ts_ms}.jpg"
            items.append((idx, t, out_p))

        def extract_batch(batch_items: list[tuple[int, float, Path]]) -> None:
            to_extract = [item for item in batch_items if not item[2].exists()]
            if to_extract:
                cmd = ["ffmpeg", "-y", "-nostdin"]
                for idx, t, out_p in to_extract:
                    cmd.extend(["-skip_frame", "bidir", "-ss", f"{t:.3f}", "-i", str(path)])
                for local_i, (idx, t, out_p) in enumerate(to_extract):
                    cmd.extend([
                        "-map", f"{local_i}:v:0",
                        "-frames:v", "1",
                        "-an", "-sn", "-dn",
                        "-vf", vf,
                        "-q:v", "3",
                        str(out_p),
                    ])
                res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                if res.returncode != 0:
                    # Fallback to individual extraction if batched multi-input fails
                    for idx, t, out_p in to_extract:
                        if not out_p.exists():
                            fb_cmd = [
                                "ffmpeg", "-y", "-nostdin",
                                "-ss", f"{t:.3f}",
                                "-i", str(path),
                                "-an", "-sn", "-dn",
                                "-frames:v", "1",
                                "-vf", vf,
                                "-q:v", "3",
                                str(out_p),
                            ]
                            try:
                                subprocess.run(fb_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                            except Exception:
                                pass

            for idx, t, out_p in batch_items:
                with self._cache_lock:
                    if out_p.exists():
                        if key not in self._cache:
                            self._cache[key] = []
                        if not any(abs(existing_ts - t) < 0.01 for existing_ts, _ in self._cache[key]):
                            self._cache[key].append((t, out_p))
                            self._cache[key].sort(key=lambda x: x[0])
                    completed_counter[0] += 1
                    curr_completed = completed_counter[0]
                    if curr_completed == total_samples:
                        self._completed_keys.add(key)

                if idx == 0 and out_p.exists() and on_first_thumb:
                    GLib.idle_add(on_first_thumb, path, out_p)

                if on_progress and out_p.exists():
                    GLib.idle_add(on_progress, curr_completed, total_samples, out_p)

        # Submit first thumbnail individually for instant response (< 100ms)
        # and batch remaining samples across workers for high-speed parallel seeks
        batches: list[list[tuple[int, float, Path]]] = []
        if items:
            batches.append(items[:1])
            remaining = items[1:]
            if remaining:
                mid = (len(remaining) + 1) // 2
                batches.append(remaining[:mid])
                if remaining[mid:]:
                    batches.append(remaining[mid:])

        futures = [self.executor.submit(extract_batch, b) for b in batches]

        def coordinator() -> None:
            for f in futures:
                try:
                    f.result()
                except Exception:
                    pass

            with self._cache_lock:
                self._completed_keys.add(key)
                self._in_progress_keys.discard(key)
                self._active_jobs = max(0, self._active_jobs - 1)
                final_results = list(self._cache.get(key, []))

            # Save disk index for instant reloads
            if len(final_results) >= 1:
                try:
                    data = [[ts, str(p)] for ts, p in final_results]
                    index_file.write_text(json.dumps(data), encoding="utf-8")
                except Exception:
                    pass

            GLib.idle_add(on_done, path, final_results)

        coord_thread = threading.Thread(target=coordinator, daemon=True)
        self._active_threads.append(coord_thread)
        coord_thread.start()

    def wait_idle(self) -> None:
        """Wait for all active background threads to finish."""
        for t in list(self._active_threads):
            if t.is_alive():
                t.join(timeout=10.0)
