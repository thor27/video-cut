"""Service to detect images and convert them into MP4 video segments."""

import hashlib
import re
import subprocess
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path

from gi.repository import GLib

from ..utils import SUBPROCESS_FLAGS, get_ffmpeg_path

SUPPORTED_IMAGE_EXTENSIONS: set[str] = {
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".bmp",
    ".tiff",
    ".gif",
}


class ImageService:
    """Handles image detection and conversion into video segments."""

    @classmethod
    def is_image(cls, path: Path | str) -> bool:
        """Check if the given path is a supported image format."""
        p = Path(path)
        return p.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS

    @classmethod
    def get_output_path(
        cls,
        image_path: Path | str,
        cache_dir: Path | None = None,
        duration: float = 1.0,
        audio_path: Path | str | None = None,
        audio_start_time: float = 0.0,
    ) -> tuple[Path, Path, Path | None]:
        """Compute output path and validate input paths for image conversion."""
        img_path = Path(image_path)
        if not img_path.exists():
            raise FileNotFoundError(f"Image not found: {img_path}")

        if cache_dir is None:
            conv_dir = Path(tempfile.gettempdir()) / "video_cut_cache" / "converted_images"
        else:
            conv_dir = Path(cache_dir) / "converted_images"
        conv_dir.mkdir(parents=True, exist_ok=True)

        try:
            stat = img_path.stat()
            raw = f"{img_path.resolve()}_{stat.st_size}_{stat.st_mtime}_{duration:.3f}"
        except OSError:
            raw = f"{img_path}_{duration:.3f}"

        aud_path: Path | None = None
        if audio_path is not None:
            aud_path = Path(audio_path)
            if not aud_path.exists():
                raise FileNotFoundError(f"Audio not found: {aud_path}")
            try:
                astat = aud_path.stat()
                raw += f"_{aud_path.resolve()}_{astat.st_size}_{astat.st_mtime}_{audio_start_time:.3f}"
            except OSError:
                raw += f"_{aud_path}_{audio_start_time:.3f}"

        file_key = hashlib.md5(raw.encode("utf-8")).hexdigest()
        out_path = conv_dir / f"{file_key}.mp4"
        return img_path, out_path, aud_path

    @classmethod
    def build_conversion_command(
        cls,
        img_path: Path,
        out_path: Path,
        duration: float = 1.0,
        aud_path: Path | None = None,
        audio_start_time: float = 0.0,
        with_progress: bool = False,
    ) -> list[str]:
        """Build the FFmpeg command line for image-to-video conversion."""
        ffmpeg_bin = get_ffmpeg_path()
        if aud_path is not None:
            cmd = [
                ffmpeg_bin,
                "-y",
                "-loop",
                "1",
                "-framerate",
                "30",
                "-t",
                f"{duration:.3f}",
                "-i",
                str(img_path),
                "-ss",
                f"{audio_start_time:.3f}",
                "-t",
                f"{duration:.3f}",
                "-i",
                str(aud_path),
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-vf",
                "scale=trunc(iw/2)*2:trunc(ih/2)*2",
                "-af",
                f"aresample=44100,apad=whole_dur={duration:.3f}",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-ac",
                "2",
                "-shortest",
            ]
        else:
            cmd = [
                ffmpeg_bin,
                "-y",
                "-loop",
                "1",
                "-framerate",
                "30",
                "-t",
                f"{duration:.3f}",
                "-i",
                str(img_path),
                "-f",
                "lavfi",
                "-t",
                f"{duration:.3f}",
                "-i",
                "anullsrc=channel_layout=stereo:sample_rate=44100",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-vf",
                "scale=trunc(iw/2)*2:trunc(ih/2)*2",
                "-c:a",
                "aac",
                "-shortest",
            ]

        if with_progress:
            cmd.extend(["-progress", "pipe:1"])

        cmd.append(str(out_path))
        return cmd

    @classmethod
    def is_cached(
        cls,
        image_path: Path | str,
        cache_dir: Path | None = None,
        duration: float = 1.0,
        audio_path: Path | str | None = None,
        audio_start_time: float = 0.0,
    ) -> bool:
        """Check if a converted video for the image already exists in cache."""
        try:
            _, out_path, _ = cls.get_output_path(
                image_path=image_path,
                cache_dir=cache_dir,
                duration=duration,
                audio_path=audio_path,
                audio_start_time=audio_start_time,
            )
            return out_path.exists() and out_path.stat().st_size > 0
        except Exception:
            return False

    @classmethod
    def convert_image_to_video(
        cls,
        image_path: Path | str,
        cache_dir: Path | None = None,
        duration: float = 1.0,
        audio_path: Path | str | None = None,
        audio_start_time: float = 0.0,
    ) -> Path:
        """Synchronously convert an image into a 30fps H.264/AAC MP4 video."""
        img_path, out_path, aud_path = cls.get_output_path(
            image_path=image_path,
            cache_dir=cache_dir,
            duration=duration,
            audio_path=audio_path,
            audio_start_time=audio_start_time,
        )

        if out_path.exists() and out_path.stat().st_size > 0:
            return out_path

        cmd = cls.build_conversion_command(
            img_path=img_path,
            out_path=out_path,
            duration=duration,
            aud_path=aud_path,
            audio_start_time=audio_start_time,
            with_progress=False,
        )

        try:
            subprocess.run(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True,
                creationflags=SUBPROCESS_FLAGS,
            )
        except Exception as e:
            if out_path.exists():
                try:
                    out_path.unlink()
                except OSError:
                    pass
            raise RuntimeError(f"Failed to convert image to video: {e}") from e

        return out_path

    @classmethod
    def convert_image_to_video_async(
        cls,
        image_path: Path | str,
        cache_dir: Path | None = None,
        duration: float = 1.0,
        audio_path: Path | str | None = None,
        audio_start_time: float = 0.0,
        on_progress: Callable[[float, float], None] | None = None,
        on_done: Callable[[Path | None, Exception | None], None] | None = None,
    ) -> Callable[[], None]:
        """Asynchronously convert an image to video with real progress reporting and cancellation support.

        Returns a cancel callback.
        """
        img_path, out_path, aud_path = cls.get_output_path(
            image_path=image_path,
            cache_dir=cache_dir,
            duration=duration,
            audio_path=audio_path,
            audio_start_time=audio_start_time,
        )

        cancelled = False
        process: subprocess.Popen | None = None

        def cancel() -> None:
            nonlocal cancelled, process
            cancelled = True
            if process:
                try:
                    process.terminate()
                except Exception:
                    pass
            if out_path.exists():
                try:
                    out_path.unlink()
                except OSError:
                    pass

        # If already cached, notify immediately on idle loop
        if out_path.exists() and out_path.stat().st_size > 0:
            if on_progress:
                GLib.idle_add(on_progress, 1.0, duration)
            if on_done:
                GLib.idle_add(on_done, out_path, None)
            return cancel

        def worker() -> None:
            nonlocal process
            try:
                cmd = cls.build_conversion_command(
                    img_path=img_path,
                    out_path=out_path,
                    duration=duration,
                    aud_path=aud_path,
                    audio_start_time=audio_start_time,
                    with_progress=True,
                )

                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                    bufsize=1,
                    creationflags=SUBPROCESS_FLAGS,
                )

                time_pattern = re.compile(r"out_time_us=(\d+)")

                while True:
                    if cancelled:
                        if process:
                            process.terminate()
                        if out_path.exists():
                            try:
                                out_path.unlink()
                            except OSError:
                                pass
                        if on_done:
                            GLib.idle_add(on_done, None, RuntimeError("Conversion cancelled"))
                        return

                    line = process.stdout.readline() if process.stdout else ""
                    if not line and process.poll() is not None:
                        break

                    m = time_pattern.search(line)
                    if m and on_progress:
                        cur_us = int(m.group(1))
                        cur_s = cur_us / 1_000_000.0
                        fraction = min(1.0, max(0.0, cur_s / duration)) if duration > 0 else 1.0
                        GLib.idle_add(on_progress, fraction, cur_s)

                rc = process.wait()
                if cancelled:
                    return

                if rc == 0:
                    if on_progress:
                        GLib.idle_add(on_progress, 1.0, duration)
                    if on_done:
                        GLib.idle_add(on_done, out_path, None)
                else:
                    if out_path.exists():
                        try:
                            out_path.unlink()
                        except OSError:
                            pass
                    if on_done:
                        GLib.idle_add(
                            on_done,
                            None,
                            RuntimeError(f"FFmpeg conversion failed with code {rc}"),
                        )
            except Exception as exc:
                if out_path.exists():
                    try:
                        out_path.unlink()
                    except OSError:
                        pass
                if on_done:
                    GLib.idle_add(on_done, None, exc)

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        return cancel
