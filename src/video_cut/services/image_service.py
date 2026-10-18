"""Service to detect images and convert them into 1-second MP4 video segments."""

import hashlib
import subprocess
import tempfile
from pathlib import Path
from typing import Optional, Set

SUPPORTED_IMAGE_EXTENSIONS: Set[str] = {
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
    def convert_image_to_video(
        cls,
        image_path: Path | str,
        cache_dir: Optional[Path] = None,
        duration: float = 1.0,
        audio_path: Optional[Path | str] = None,
        audio_start_time: float = 0.0,
    ) -> Path:
        """Convert an image into a 30fps H.264/AAC MP4 video with optional soundtrack or silent audio."""
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

        aud_path: Optional[Path] = None
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

        # Return cached version if already converted
        if out_path.exists() and out_path.stat().st_size > 0:
            return out_path

        if aud_path is not None:
            cmd = [
                "ffmpeg",
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
                str(out_path),
            ]
        else:
            # Convert image to video with silent stereo audio and even dimensions
            cmd = [
                "ffmpeg",
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
                str(out_path),
            ]

        try:
            subprocess.run(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True,
            )
        except Exception as e:
            if out_path.exists():
                try:
                    out_path.unlink()
                except OSError:
                    pass
            raise RuntimeError(f"Failed to convert image to video: {e}") from e

        return out_path
