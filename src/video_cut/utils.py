"""Utility functions for time formatting and calculations."""

import math
import uuid


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
