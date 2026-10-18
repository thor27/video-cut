"""Unit tests for ImageService."""

import subprocess
from pathlib import Path
import pytest

from video_cut.services.image_service import ImageService, SUPPORTED_IMAGE_EXTENSIONS
from video_cut.services.probe_service import ProbeService


def test_is_image():
    # Valid extensions
    for ext in SUPPORTED_IMAGE_EXTENSIONS:
        assert ImageService.is_image(Path(f"sample{ext}")) is True
        assert ImageService.is_image(Path(f"sample{ext.upper()}")) is True

    # Invalid extensions
    for ext in [".mp4", ".mkv", ".mov", ".avi", ".webm", ".txt", ".py", ".json"]:
        assert ImageService.is_image(Path(f"sample{ext}")) is False
        assert ImageService.is_image(Path(f"sample{ext.upper()}")) is False


def test_convert_image_to_video(tmp_path):
    img_path = tmp_path / "test_photo.png"
    # Create a test image using ffmpeg
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi",
            "-i", "color=c=red:s=320x240:d=1",
            "-frames:v", "1",
            str(img_path)
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    assert img_path.exists()

    cache_dir = tmp_path / "cache"
    video_path = ImageService.convert_image_to_video(img_path, cache_dir=cache_dir, duration=1.0)

    assert video_path.exists()
    assert video_path.suffix == ".mp4"

    # Probe the converted video
    meta = ProbeService.probe(video_path)
    assert meta is not None
    assert abs(meta.duration - 1.0) < 0.1
    assert meta.width % 2 == 0
    assert meta.height % 2 == 0

    # Test caching: second call returns the same file without re-generating
    mtime1 = video_path.stat().st_mtime
    video_path2 = ImageService.convert_image_to_video(img_path, cache_dir=cache_dir, duration=1.0)
    assert video_path == video_path2
    assert video_path.stat().st_mtime == mtime1


def test_convert_image_with_odd_dimensions(tmp_path):
    img_path = tmp_path / "odd_photo.jpg"
    # Create an image with odd dimensions (301x201)
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi",
            "-i", "color=c=green:s=301x201:d=1",
            "-frames:v", "1",
            str(img_path)
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    assert img_path.exists()

    cache_dir = tmp_path / "cache"
    video_path = ImageService.convert_image_to_video(img_path, cache_dir=cache_dir, duration=1.0)

    assert video_path.exists()
    meta = ProbeService.probe(video_path)
    assert meta is not None
    assert abs(meta.duration - 1.0) < 0.1
    assert meta.width % 2 == 0
    assert meta.height % 2 == 0


def test_convert_image_with_soundtrack(tmp_path):
    img_path = tmp_path / "soundtrack_photo.png"
    audio_path = tmp_path / "soundtrack.wav"

    # Create test image
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=blue:s=320x240:d=1", "-frames:v", "1", str(img_path)],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # Create 5-second sine wave audio
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=500:duration=5", str(audio_path)],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    cache_dir = tmp_path / "cache"
    # Convert image with 3.5s duration and offset 1.0s
    video_path = ImageService.convert_image_to_video(
        img_path,
        cache_dir=cache_dir,
        duration=3.5,
        audio_path=audio_path,
        audio_start_time=1.0,
    )

    assert video_path.exists()
    assert video_path.suffix == ".mp4"

    meta = ProbeService.probe(video_path)
    assert meta is not None
    assert abs(meta.duration - 3.5) < 0.2

    # Verify audio duration with probe_duration
    aud_dur = ProbeService.probe_duration(video_path)
    assert aud_dur is not None
    assert abs(aud_dur - 3.5) < 0.2
