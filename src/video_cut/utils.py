"""Utility functions for time formatting and calculations."""

import math
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

SUBPROCESS_FLAGS: int = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def get_ffmpeg_path() -> str:
    """Return resolved path to ffmpeg executable."""
    if "SNAP" in os.environ:
        snap_bin = Path(os.environ["SNAP"]) / "usr" / "bin" / "ffmpeg"
        if snap_bin.is_file() and os.access(snap_bin, os.X_OK):
            return str(snap_bin)
    if "APPDIR" in os.environ:
        appdir_bin = Path(os.environ["APPDIR"]) / "usr" / "bin" / "ffmpeg"
        if appdir_bin.is_file() and os.access(appdir_bin, os.X_OK):
            return str(appdir_bin)

    # Check PyInstaller frozen bundle locations (macOS / Windows / Linux)
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            for cand in [Path(meipass) / "bin" / "ffmpeg", Path(meipass) / "ffmpeg"]:
                if cand.is_file() and os.access(cand, os.X_OK):
                    return str(cand)
        exe_dir = Path(sys.executable).resolve().parent
        for cand in [exe_dir / "bin" / "ffmpeg", exe_dir / "ffmpeg", exe_dir.parent / "Resources" / "bin" / "ffmpeg"]:
            if cand.is_file() and os.access(cand, os.X_OK):
                return str(cand)

    # Standard macOS Homebrew locations
    if sys.platform == "darwin":
        for brew_bin in [Path("/opt/homebrew/bin/ffmpeg"), Path("/usr/local/bin/ffmpeg")]:
            if brew_bin.is_file() and os.access(brew_bin, os.X_OK):
                return str(brew_bin)

    return shutil.which("ffmpeg") or "ffmpeg"


def get_ffprobe_path() -> str:
    """Return resolved path to ffprobe executable."""
    if "SNAP" in os.environ:
        snap_bin = Path(os.environ["SNAP"]) / "usr" / "bin" / "ffprobe"
        if snap_bin.is_file() and os.access(snap_bin, os.X_OK):
            return str(snap_bin)
    if "APPDIR" in os.environ:
        appdir_bin = Path(os.environ["APPDIR"]) / "usr" / "bin" / "ffprobe"
        if appdir_bin.is_file() and os.access(appdir_bin, os.X_OK):
            return str(appdir_bin)

    # Check PyInstaller frozen bundle locations (macOS / Windows / Linux)
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            for cand in [Path(meipass) / "bin" / "ffprobe", Path(meipass) / "ffprobe"]:
                if cand.is_file() and os.access(cand, os.X_OK):
                    return str(cand)
        exe_dir = Path(sys.executable).resolve().parent
        for cand in [exe_dir / "bin" / "ffprobe", exe_dir / "ffprobe", exe_dir.parent / "Resources" / "bin" / "ffprobe"]:
            if cand.is_file() and os.access(cand, os.X_OK):
                return str(cand)

    # Standard macOS Homebrew locations
    if sys.platform == "darwin":
        for brew_bin in [Path("/opt/homebrew/bin/ffprobe"), Path("/usr/local/bin/ffprobe")]:
            if brew_bin.is_file() and os.access(brew_bin, os.X_OK):
                return str(brew_bin)

    return shutil.which("ffprobe") or "ffprobe"


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
