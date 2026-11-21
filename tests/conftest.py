import os
import subprocess
from pathlib import Path

import pytest

from video_cut.utils import get_ffmpeg_path

# GTK's GL renderer aborts inside libepoxy on displays without GL, such as
# Xvfb in CI; the Cairo renderer is enough for the widget tests.
os.environ.setdefault("GSK_RENDERER", "cairo")


@pytest.fixture(autouse=True)
def isolate_test_config(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch):
    """Isolate configuration files and invalidate ffmpeg cache for every test."""
    cfg_dir = tmp_path_factory.mktemp("videocut_cfg")
    monkeypatch.setenv("VIDEOCUT_CONFIG_DIR", str(cfg_dir))
    from video_cut.utils import invalidate_ffmpeg_cache

    invalidate_ffmpeg_cache()
    yield
    invalidate_ffmpeg_cache()


def _generate_sample_video(path: Path, duration: float) -> Path:
    subprocess.run(
        [
            get_ffmpeg_path(), "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc=size=640x360:rate=30",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
            "-t", str(duration),
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            str(path),
        ],
        check=True,
    )
    return path


@pytest.fixture(scope="session")
def sample_video(tmp_path_factory) -> Path:
    """Five second 640x360 H.264 clip with a sine audio track."""
    return _generate_sample_video(tmp_path_factory.mktemp("media") / "test_video.mp4", 5.0)


@pytest.fixture(scope="session")
def sample_video2(tmp_path_factory) -> Path:
    """Three second clip for tests that need a second, shorter source."""
    return _generate_sample_video(tmp_path_factory.mktemp("media") / "test_video2.mp4", 3.0)
