"""Service to generate and cache video thumbnails asynchronously and ultra-fast."""

import hashlib
import json
import subprocess
import threading
from bisect import bisect_left
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

import gi

gi.require_version("GLib", "2.0")
from gi.repository import GLib

from ..utils import SUBPROCESS_FLAGS, get_ffmpeg_path
from .ffmpeg_service import (
    FLAG_FILTER_VIDEO,
    FLAG_FRAMES_VIDEO,
    FLAG_INPUT,
    FLAG_MAP,
    FLAG_NO_AUDIO,
    FLAG_NO_DATA,
    FLAG_NO_STDIN,
    FLAG_NO_SUBTITLES,
    FLAG_OVERWRITE,
    FLAG_QUALITY_VIDEO,
    FLAG_SEEK,
    FLAG_SKIP_FRAME,
)
from .image_service import ImageService
from .private_dir import private_dir


class ThumbnailService:
    """Manages asynchronous extraction and caching of video thumbnails."""

    def __init__(self, cache_dir: Path | None = None, max_workers: int = 4) -> None:
        if cache_dir is None:
            self.cache_dir = private_dir("video_cut_cache")
        else:
            self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.executor = ThreadPoolExecutor(max_workers=max_workers)
        # Mapping from file key to list of (timestamp, image_path)
        self._cache: dict[str, list[tuple[float, Path]]] = {}
        # Mapping from file key to list of (timestamp, fullsize_preview_path)
        self._preview_cache: dict[str, list[tuple[float, Path]]] = {}
        self._image_keys: set[str] = set()
        self._cache_lock = threading.Lock()
        self._completed_keys: set[str] = set()
        self._in_progress_keys: set[str] = set()
        self._active_jobs: int = 0
        self._active_threads: list[threading.Thread] = []
        self._active_futures: list[Future] = []
        self._cache_updated_listeners: list[Callable[[Path], None]] = []
        self._in_flight_refinement_timestamps: set[tuple[str, int]] = set()
        self._cache_update_idle_id: int | None = None
        self._pending_updated_paths: set[Path] = set()

    def has_pending_jobs(self) -> bool:
        """Check if any thumbnail generation tasks are currently active."""
        with self._cache_lock:
            return self._active_jobs > 0

    def add_cache_updated_listener(self, callback: Callable[[Path], None]) -> None:
        """Register a callback to be notified when new thumbnails are added to cache."""
        with self._cache_lock:
            if callback not in self._cache_updated_listeners:
                self._cache_updated_listeners.append(callback)

    def remove_cache_updated_listener(self, callback: Callable[[Path], None]) -> None:
        """Unregister a previously added cache update listener."""
        with self._cache_lock:
            if callback in self._cache_updated_listeners:
                self._cache_updated_listeners.remove(callback)

    def _notify_cache_updated(self, source_path: Path) -> None:
        """Schedule a debounced notification for cache update listeners on GLib idle."""
        with self._cache_lock:
            self._pending_updated_paths.add(Path(source_path))
            if self._cache_update_idle_id is None:
                self._cache_update_idle_id = GLib.idle_add(self._dispatch_cache_updated)

    def _dispatch_cache_updated(self) -> bool:
        with self._cache_lock:
            self._cache_update_idle_id = None
            paths = list(self._pending_updated_paths)
            self._pending_updated_paths.clear()
            listeners = list(self._cache_updated_listeners)

        for p in paths:
            for cb in listeners:
                try:
                    cb(p)
                except Exception:
                    pass
        return False

    def _file_key(self, path: Path) -> str:
        try:
            stat = path.stat()
            raw = f"{path.resolve()}_{stat.st_size}_{stat.st_mtime}"
        except OSError:
            raw = str(path)
        return hashlib.md5(raw.encode("utf-8"), usedforsecurity=False).hexdigest()

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

    def register_image_thumbnail(
        self,
        video_path: Path,
        image_path: Path,
        duration: float = 1.0,
        full_image_path: Path | None = None,
    ) -> None:
        """Register thumbnail for a converted image video without needing FFmpeg extraction."""
        key = self._file_key(video_path)
        with self._cache_lock:
            self._image_keys.add(key)
            self._cache[key] = [(0.0, image_path), (duration, image_path)]
            preview_p = full_image_path if full_image_path else image_path
            self._preview_cache[key] = [(0.0, preview_p), (duration, preview_p)]
            self._completed_keys.add(key)

    def get_preview_keyframe(
        self,
        source_path: Path,
        target_time: float | None = None,
    ) -> Path | None:
        """Get or extract a high-quality full-size keyframe thumbnail for preview/crop-rotate dialog.

        If target_time is provided, returns or extracts the keyframe nearest to target_time.
        Otherwise returns the cached midpoint keyframe.
        """
        path = Path(source_path)
        key = self._file_key(path)
        video_cache_dir = self.cache_dir / key
        video_cache_dir.mkdir(parents=True, exist_ok=True)

        with self._cache_lock:
            # 1. Fast path: If this is an image-converted video, any timestamp maps to the original image!
            if key in self._image_keys:
                img_items = list(self._preview_cache.get(key, []))
                if img_items and img_items[0][1].exists():
                    return img_items[0][1]

            items = list(self._preview_cache.get(key, []))

        # Check in-memory preview cache
        if items:
            if target_time is None:
                best = items[len(items) // 2]
                if best[1].exists():
                    return best[1]
            else:
                best = min(items, key=lambda x: abs(x[0] - target_time))
                if best[1].exists():
                    return best[1]

        # Check disk cache: preview_keyframe.jpg or any preview_mid_*.jpg
        default_preview = video_cache_dir / "preview_keyframe.jpg"
        if target_time is not None:
            ts_ms = int(round(target_time * 1000))
            exact_p = video_cache_dir / f"preview_mid_{ts_ms}.jpg"
            if exact_p.exists():
                with self._cache_lock:
                    if key not in self._preview_cache:
                        self._preview_cache[key] = []
                    self._preview_cache[key].append((target_time, exact_p))
                return exact_p
        elif default_preview.exists():
            return default_preview

        # If not cached yet, extract synchronously at target_time
        t = target_time if target_time is not None else 0.0
        ts_ms = int(round(t * 1000))
        out_p = video_cache_dir / f"preview_mid_{ts_ms}.jpg"
        if not out_p.exists():
            cmd = [
                get_ffmpeg_path(),
                FLAG_OVERWRITE,
                FLAG_NO_STDIN,
                FLAG_SEEK,
                f"{t:.3f}",
                FLAG_INPUT,
                str(path),
                FLAG_NO_AUDIO,
                FLAG_NO_SUBTITLES,
                FLAG_NO_DATA,
                FLAG_FRAMES_VIDEO,
                "1",
                FLAG_FILTER_VIDEO,
                "scale=trunc(iw/2)*2:trunc(ih/2)*2",
                FLAG_QUALITY_VIDEO,
                "2",
                str(out_p),
            ]
            try:
                subprocess.run(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=True,
                    creationflags=SUBPROCESS_FLAGS,
                )
            except Exception:
                pass

        if out_p.exists():
            with self._cache_lock:
                if key not in self._preview_cache:
                    self._preview_cache[key] = []
                self._preview_cache[key].append((t, out_p))
            if not default_preview.exists():
                try:
                    import shutil

                    shutil.copyfile(out_p, default_preview)
                except Exception:
                    pass
            return out_p

        if default_preview.exists():
            return default_preview

        # If file does not exist or ffmpeg extraction fails, fallback to best item in _cache
        with self._cache_lock:
            normal_items = list(self._cache.get(key, []))
        if normal_items:
            best_normal = (
                min(normal_items, key=lambda x: abs(x[0] - target_time))
                if target_time is not None
                else normal_items[len(normal_items) // 2]
            )
            if best_normal[1].exists():
                return best_normal[1]

        return None

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
        auto_refine: bool = True,
    ) -> list[Path | None]:
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
            first_t = 10.0 if (start_time == 0.0 and dur > 60.0) else start_time
            for i in range(count):
                ratio = i / float(count - 1)
                if i == 0:
                    t = first_t
                else:
                    t = start_time + ratio * dur
                if i == count - 1 and dur > 0.08:
                    t = min(t, end_time - 0.04)
                target_times.append(t)

        if not items:
            return [None] * count

        results: list[Path | None] = []
        dt = self._calculate_dt(dur)
        if count == 1:
            max_diff = max(1.5, dur)
            step = dur
        else:
            step = dur / float(count - 1)
            # While in progress, use strict tolerance so unreached positions show placeholders
            max_diff = max(dt, step * 0.65)

        n_items = len(items)
        for t in target_times:
            # Binary search for closest timestamp in sorted thumbnail cache
            idx = bisect_left(items, t, key=lambda x: x[0])
            if idx == 0:
                closest_ts, closest_path = items[0]
            elif idx >= n_items:
                closest_ts, closest_path = items[-1]
            else:
                before = items[idx - 1]
                after = items[idx]
                if (after[0] - t) < (t - before[0]):
                    closest_ts, closest_path = after
                else:
                    closest_ts, closest_path = before
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

        if auto_refine and count > 1 and dur > 0.05:
            with self._cache_lock:
                is_img = key in self._image_keys
                is_in_progress = key in self._in_progress_keys
            if not is_img and not is_in_progress and not ImageService.is_image(source_path):
                valid_results = [p for p in results if p is not None]
                if len(valid_results) > 1 and len(set(valid_results)) < len(valid_results):
                    step = dur / float(count - 1)
                    tolerance = min(0.15, step * 0.4)
                    missing_times: list[float] = []
                    for t in target_times:
                        if not items or not any(abs(c_ts - t) <= tolerance for c_ts, _ in items):
                            missing_times.append(t)
                    if missing_times:
                        self.refine_segment_thumbnails_async(source_path, missing_times)

        return results

    def refine_segment_thumbnails_async(
        self,
        source_path: Path,
        target_times: list[float],
        on_done: Callable[[], None] | None = None,
    ) -> None:
        """Extract missing thumbnails for specific target timestamps in the background."""
        if getattr(self.executor, "_shutdown", False):
            return

        path = Path(source_path)
        key = self._file_key(path)

        with self._cache_lock:
            if key in self._image_keys:
                return
        if ImageService.is_image(path):
            return

        needed_times: list[float] = []
        with self._cache_lock:
            cached_items = list(self._cache.get(key, []))
            for t in target_times:
                ts_ms = int(round(t * 1000))
                item_key = (key, ts_ms)
                if item_key in self._in_flight_refinement_timestamps:
                    continue
                # Also skip if an in-flight timestamp for this key is within 15ms
                if any(
                    abs(inflight_ts - ts_ms) <= 15
                    for k, inflight_ts in self._in_flight_refinement_timestamps
                    if k == key
                ):
                    continue
                # Skip if cache already has a thumbnail very close to t (<= 0.015s)
                if any(abs(c_ts - t) <= 0.015 for c_ts, _ in cached_items):
                    continue
                self._in_flight_refinement_timestamps.add(item_key)
                needed_times.append(t)

        if not needed_times:
            if on_done:
                GLib.idle_add(on_done)
            return

        def worker() -> None:
            video_cache_dir = self.cache_dir / key
            video_cache_dir.mkdir(parents=True, exist_ok=True)
            vf = "scale='min(960,iw)':'min(540,ih)':force_original_aspect_ratio=decrease,scale=trunc(iw/2)*2:trunc(ih/2)*2"
            ffmpeg_bin = get_ffmpeg_path()

            batch_items: list[tuple[float, Path, int]] = []
            for t in needed_times:
                ts_ms = int(round(t * 1000))
                out_p = video_cache_dir / f"thumb_refined_{ts_ms}.jpg"
                batch_items.append((t, out_p, ts_ms))

            try:
                to_extract = [(t, out_p) for t, out_p, _ in batch_items if not out_p.exists()]
                if to_extract:
                    cmd = [ffmpeg_bin, FLAG_OVERWRITE, FLAG_NO_STDIN]
                    for t, _out_p in to_extract:
                        cmd.extend([FLAG_SKIP_FRAME, "bidir", FLAG_SEEK, f"{t:.3f}", FLAG_INPUT, str(path)])
                    for local_i, (_t, out_p) in enumerate(to_extract):
                        cmd.extend([
                            FLAG_MAP, f"{local_i}:v:0",
                            FLAG_FRAMES_VIDEO, "1",
                            FLAG_NO_AUDIO, FLAG_NO_SUBTITLES, FLAG_NO_DATA,
                            FLAG_FILTER_VIDEO, vf,
                            FLAG_QUALITY_VIDEO, "2",
                            str(out_p),
                        ])
                    res = subprocess.run(
                        cmd,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        creationflags=SUBPROCESS_FLAGS,
                    )
                    if res.returncode != 0:
                        # Fallback to individual seeks if multi-input fails
                        for t, out_p in to_extract:
                            if not out_p.exists():
                                fb_cmd = [
                                    ffmpeg_bin, FLAG_OVERWRITE, FLAG_NO_STDIN,
                                    FLAG_SEEK, f"{t:.3f}",
                                    FLAG_INPUT, str(path),
                                    FLAG_NO_AUDIO, FLAG_NO_SUBTITLES, FLAG_NO_DATA,
                                    FLAG_FRAMES_VIDEO, "1",
                                    FLAG_FILTER_VIDEO, vf,
                                    FLAG_QUALITY_VIDEO, "2",
                                    str(out_p),
                                ]
                                try:
                                    subprocess.run(
                                        fb_cmd,
                                        stdout=subprocess.DEVNULL,
                                        stderr=subprocess.DEVNULL,
                                        creationflags=SUBPROCESS_FLAGS,
                                    )
                                except Exception:
                                    pass

                newly_added = 0
                with self._cache_lock:
                    if key not in self._cache:
                        self._cache[key] = []
                    for t, out_p, _ in batch_items:
                        if out_p.exists():
                            if not any(abs(existing_ts - t) < 0.01 for existing_ts, _ in self._cache[key]):
                                self._cache[key].append((t, out_p))
                                newly_added += 1
                    if newly_added > 0:
                        self._cache[key].sort(key=lambda x: x[0])
                        try:
                            index_file = video_cache_dir / "index.json"
                            data = [[ts, str(p)] for ts, p in self._cache[key]]
                            index_file.write_text(json.dumps(data), encoding="utf-8")
                        except Exception:
                            pass

                if newly_added > 0:
                    self._notify_cache_updated(path)
            finally:
                with self._cache_lock:
                    for _, _, ts_ms in batch_items:
                        self._in_flight_refinement_timestamps.discard((key, ts_ms))

            if on_done:
                GLib.idle_add(on_done)

        try:
            future = self.executor.submit(worker)
            with self._cache_lock:
                self._active_futures.append(future)
        except RuntimeError:
            with self._cache_lock:
                for t in needed_times:
                    ts_ms = int(round(t * 1000))
                    self._in_flight_refinement_timestamps.discard((key, ts_ms))
            if on_done:
                GLib.idle_add(on_done)

    def extract_exact_thumbnail_async(
        self,
        source_path: Path,
        target_time: float,
        on_ready: Callable[[Path], None] | None = None,
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
                vf = "scale='min(960,iw)':'min(540,ih)':force_original_aspect_ratio=decrease,scale=trunc(iw/2)*2:trunc(ih/2)*2"
                cmd = [
                    get_ffmpeg_path(),
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
                    vf,
                    "-q:v",
                    "2",
                    str(out_p),
                ]
                try:
                    subprocess.run(
                        cmd,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        check=True,
                        creationflags=SUBPROCESS_FLAGS,
                    )
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

        try:
            future = self.executor.submit(worker)
            with self._cache_lock:
                self._active_futures.append(future)
        except RuntimeError:
            pass

    def generate_thumbnails_async(
        self,
        source_path: Path | str,
        duration: float,
        on_done: Callable[[Path, list[tuple[float, Path]]], None],
        on_first_thumb: Callable[[Path, Path], None] | None = None,
        on_progress: Callable[[int, int, Path], None] | None = None,
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
                    loaded.sort(key=lambda x: x[0])
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
            start_t = 10.0 if duration > 60.0 else 0.0
            step = (duration - start_t) / float(num_samples - 1)
            raw_times = [start_t + i * step for i in range(num_samples)]
            raw_times[-1] = max(start_t, duration - 0.05)
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

        vf = "scale='min(960,iw)':'min(540,ih)':force_original_aspect_ratio=decrease,scale=trunc(iw/2)*2:trunc(ih/2)*2"

        items: list[tuple[int, float, Path]] = []
        for idx, t in enumerate(times):
            ts_ms = int(round(t * 1000))
            out_p = video_cache_dir / f"thumb_{idx:03d}_{ts_ms}.jpg"
            items.append((idx, t, out_p))

        def extract_batch(batch_items: list[tuple[int, float, Path]]) -> None:
            to_extract = [item for item in batch_items if not item[2].exists()]
            if to_extract:
                ffmpeg_bin = get_ffmpeg_path()
                cmd = [ffmpeg_bin, FLAG_OVERWRITE, FLAG_NO_STDIN]
                for _idx, t, _out_p in to_extract:
                    cmd.extend([FLAG_SKIP_FRAME, "bidir", FLAG_SEEK, f"{t:.3f}", FLAG_INPUT, str(path)])
                for local_i, (_idx, _t, out_p) in enumerate(to_extract):
                    cmd.extend([
                        FLAG_MAP, f"{local_i}:v:0",
                        FLAG_FRAMES_VIDEO, "1",
                        FLAG_NO_AUDIO, FLAG_NO_SUBTITLES, FLAG_NO_DATA,
                        FLAG_FILTER_VIDEO, vf,
                        FLAG_QUALITY_VIDEO, "2",
                        str(out_p),
                    ])
                res = subprocess.run(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=SUBPROCESS_FLAGS,
                )
                if res.returncode != 0:
                    # Fallback to individual extraction if batched multi-input fails
                    for _idx, t, out_p in to_extract:
                        if not out_p.exists():
                            fb_cmd = [
                                ffmpeg_bin, FLAG_OVERWRITE, FLAG_NO_STDIN,
                                FLAG_SEEK, f"{t:.3f}",
                                FLAG_INPUT, str(path),
                                FLAG_NO_AUDIO, FLAG_NO_SUBTITLES, FLAG_NO_DATA,
                                FLAG_FRAMES_VIDEO, "1",
                                FLAG_FILTER_VIDEO, vf,
                                FLAG_QUALITY_VIDEO, "2",
                                str(out_p),
                            ]
                            try:
                                subprocess.run(
                                    fb_cmd,
                                    stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL,
                                    creationflags=SUBPROCESS_FLAGS,
                                )
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

        # Submit special full-size midpoint keyframe extraction for dialog preview
        mid_time = duration / 2.0
        mid_ms = int(round(mid_time * 1000))
        preview_mid_p = video_cache_dir / f"preview_mid_{mid_ms}.jpg"
        default_preview_p = video_cache_dir / "preview_keyframe.jpg"

        def extract_preview() -> None:
            if not preview_mid_p.exists() and not default_preview_p.exists():
                cmd = [
                    get_ffmpeg_path(),
                    "-y",
                    "-nostdin",
                    "-ss",
                    f"{mid_time:.3f}",
                    "-i",
                    str(path),
                    "-an",
                    "-sn",
                    "-dn",
                    "-frames:v",
                    "1",
                    "-vf",
                    "scale=trunc(iw/2)*2:trunc(ih/2)*2",
                    "-q:v",
                    "2",
                    str(preview_mid_p),
                ]
                try:
                    subprocess.run(
                        cmd,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        creationflags=SUBPROCESS_FLAGS,
                    )
                except Exception:
                    pass

            target_p = (
                preview_mid_p
                if preview_mid_p.exists()
                else (default_preview_p if default_preview_p.exists() else None)
            )
            if target_p:
                if not default_preview_p.exists() and preview_mid_p.exists():
                    try:
                        import shutil

                        shutil.copyfile(preview_mid_p, default_preview_p)
                    except Exception:
                        pass
                with self._cache_lock:
                    if key not in self._preview_cache:
                        self._preview_cache[key] = []
                    self._preview_cache[key].append((mid_time, target_p))

        futures = [self.executor.submit(extract_preview)]
        futures.extend([self.executor.submit(extract_batch, b) for b in batches])

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
        """Wait for all active background threads and futures to finish."""
        for t in list(self._active_threads):
            if t.is_alive():
                t.join(timeout=10.0)
        with self._cache_lock:
            futures = list(self._active_futures)
        for f in futures:
            try:
                f.result(timeout=10.0)
            except Exception:
                pass
        with self._cache_lock:
            self._active_futures = [f for f in self._active_futures if not f.done()]
