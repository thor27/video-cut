"""Utility functions for time formatting and calculations."""

import math
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

# Subprocess creation flag: hide console window on Windows GUI executions
SUBPROCESS_FLAGS: int = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def get_binary_path(name: str) -> str:
    """Find binary executable in PyInstaller bundle, local directory, or system PATH."""
    candidates: list[Path] = []
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        meipass = Path(sys._MEIPASS)
        candidates.extend([
            meipass / name,
            meipass / f"{name}.exe",
            meipass / "bin" / name,
            meipass / "bin" / f"{name}.exe",
        ])
    exe_dir = Path(sys.executable).parent
    candidates.extend([
        exe_dir / name,
        exe_dir / f"{name}.exe",
        exe_dir / "bin" / name,
        exe_dir / "bin" / f"{name}.exe",
    ])
    for c in candidates:
        if c.is_file():
            return str(c)

    which = shutil.which(name)
    if which:
        return which
    return name


def get_ffmpeg_path() -> str:
    """Return resolved path to ffmpeg executable."""
    return get_binary_path("ffmpeg")


def get_ffprobe_path() -> str:
    """Return resolved path to ffprobe executable."""
    return get_binary_path("ffprobe")

SUBPROCESS_FLAGS: int = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def format_time(seconds: float, show_millis: bool = True) -> str:
    """Format seconds into HH:MM:SS.ss or MM:SS.ss string."""
    if seconds < 0 or math.isnan(seconds):
        seconds = 0.0

    total_seconds = int(seconds)
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    secs = total_seconds % 60
    millis = int(round((seconds - total_seconds) * 100))

    if hours > 0:
        base = f"{hours:02d}:{minutes:02d}:{secs:02d}"
    else:
        base = f"{minutes:02d}:{secs:02d}"

    if show_millis:
        return f"{base}.{millis:02d}"
    return base


def clamp(val: float, min_val: float, max_val: float) -> float:
    """Clamp a value between min_val and max_val."""
    return max(min_val, min(val, max_val))


def generate_id() -> str:
    """Generate a short unique identifier for segments."""
    return uuid.uuid4().hex[:8]
