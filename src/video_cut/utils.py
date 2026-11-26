import contextlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

SUBPROCESS_FLAGS: int = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


from typing import Any

MIN_FFMPEG_VERSION: tuple[int, int] = (4, 3)
ESSENTIAL_ENCODERS: set[str] = {"libx264", "aac"}
HW_ENCODERS: set[str] = {
    "h264_vaapi",
    "hevc_vaapi",
    "vp9_vaapi",
    "av1_vaapi",
    "h264_nvenc",
    "hevc_nvenc",
    "av1_nvenc",
    "h264_qsv",
    "hevc_qsv",
    "vp9_qsv",
    "av1_qsv",
    "h264_amf",
    "hevc_amf",
    "av1_amf",
    "h264_vulkan",
    "hevc_vulkan",
}

_RESOLVED_CACHE: tuple[str, str, str, str | None] | None = None
_CACHE_KEY: tuple[Any, ...] | None = None


def invalidate_ffmpeg_cache() -> None:
    """Invalidate cached resolved ffmpeg binaries."""
    global _RESOLVED_CACHE, _CACHE_KEY
    _RESOLVED_CACHE = None
    _CACHE_KEY = None


def get_healthcheck_config_dir() -> Path:
    """Return configuration directory path, respecting VIDEOCUT_CONFIG_DIR if set."""
    override = os.environ.get("VIDEOCUT_CONFIG_DIR")
    if override:
        p = Path(override)
        p.mkdir(parents=True, exist_ok=True)
        return p
    try:
        from gi.repository import GLib

        cfg_dir = Path(GLib.get_user_config_dir()) / "video-cut"
    except Exception:
        cfg_dir = Path.home() / ".config" / "video-cut"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    return cfg_dir


def get_healthcheck_config() -> dict[str, Any]:
    """Read stored healthcheck preferences from user configuration."""
    cfg = get_healthcheck_config_dir() / "healthcheck.json"
    if cfg.exists():
        try:
            return json.loads(cfg.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def set_healthcheck_config_keys(**kwargs: Any) -> None:
    """Update keys in user configuration file."""
    cfg = get_healthcheck_config_dir() / "healthcheck.json"
    data: dict[str, Any] = {}
    if cfg.exists():
        with contextlib.suppress(Exception):
            data = json.loads(cfg.read_text(encoding="utf-8"))
    data.update(kwargs)
    cfg.write_text(json.dumps(data, indent=2), encoding="utf-8")


def get_ffmpeg_source_setting() -> str:
    """Return configured source mode: 'auto', 'container', 'system', 'manual'."""
    cfg = get_healthcheck_config()
    source = str(cfg.get("ffmpeg_source", "auto")).strip().lower()
    return source if source in ("auto", "container", "system", "manual") else "auto"


def get_custom_ffmpeg_path_setting() -> str:
    """Return user-configured custom ffmpeg binary path, if any."""
    cfg = get_healthcheck_config()
    return str(cfg.get("ffmpeg_custom_path", "")).strip()


def parse_ffmpeg_version(version_line: str) -> tuple[int, int] | None:
    """Parse major and minor version numbers from ffmpeg -version first line.

    Examples:
        'ffmpeg version 6.1.1 ...' -> (6, 1)
        'ffmpeg version n5.1.2 ...' -> (5, 1)
        'ffmpeg version 4.4-static ...' -> (4, 4)
        'ffmpeg version 2024-05-02-git...' -> (2024, 5) (modern git build)
        'ffmpeg version N-112345-gabcdef' -> (99, 0)
    """
    if not version_line:
        return None
    m = re.search(r"version\s+n?(\d+)[.-](\d+)", version_line, re.IGNORECASE)
    if m:
        return int(m.group(1)), int(m.group(2))
    if re.search(r"version\s+N-\d+", version_line, re.IGNORECASE):
        return (99, 0)
    return None


def probe_binary_encoders(binary_path: str) -> set[str]:
    """Return the set of encoder names supported by a specific ffmpeg binary."""
    if not binary_path:
        return set()
    try:
        res = subprocess.run(
            [binary_path, "-encoders"],
            capture_output=True,
            text=True,
            timeout=3.0,
            creationflags=SUBPROCESS_FLAGS,
        )
        if res.returncode != 0:
            return set()
        encoders = set()
        for line in res.stdout.splitlines():
            parts = line.strip().split()
            if len(parts) >= 2 and len(parts[0]) == 6:
                encoders.add(parts[1])
        return encoders
    except Exception:
        return set()


def is_flatpak() -> bool:
    """Return True if running inside a Flatpak sandbox."""
    return bool(os.environ.get("FLATPAK_ID") or Path("/.flatpak-info").exists())


def get_container_ffmpeg_path() -> str | None:
    """Return path to container/bundled ffmpeg if available."""
    if is_flatpak():
        for cand in ("/app/bin/ffmpeg", "/usr/bin/ffmpeg"):
            p = Path(cand)
            if p.is_file() and os.access(p, os.X_OK):
                return str(p)
    if "SNAP" in os.environ:
        snap_bin = Path(os.environ["SNAP"]) / "usr" / "bin" / "ffmpeg"
        if snap_bin.is_file() and os.access(snap_bin, os.X_OK):
            return str(snap_bin)
    if "APPDIR" in os.environ:
        appdir_bin = Path(os.environ["APPDIR"]) / "usr" / "bin" / "ffmpeg"
        if appdir_bin.is_file() and os.access(appdir_bin, os.X_OK):
            return str(appdir_bin)
    return None


def get_container_ffprobe_path() -> str | None:
    """Return path to container/bundled ffprobe if available."""
    if is_flatpak():
        for cand in ("/app/bin/ffprobe", "/usr/bin/ffprobe"):
            p = Path(cand)
            if p.is_file() and os.access(p, os.X_OK):
                return str(p)
    if "SNAP" in os.environ:
        snap_bin = Path(os.environ["SNAP"]) / "usr" / "bin" / "ffprobe"
        if snap_bin.is_file() and os.access(snap_bin, os.X_OK):
            return str(snap_bin)
    if "APPDIR" in os.environ:
        appdir_bin = Path(os.environ["APPDIR"]) / "usr" / "bin" / "ffprobe"
        if appdir_bin.is_file() and os.access(appdir_bin, os.X_OK):
            return str(appdir_bin)
    return None


def get_system_ffmpeg_path() -> str | None:
    """Find system FFmpeg binary on host, ignoring container/runtime directories."""
    if is_flatpak():
        # Inside Flatpak sandbox, host binaries cannot be cleanly executed due to
        # runtime glibc and shared library isolation.
        return None

    skip_prefixes: list[str] = []
    if "APPDIR" in os.environ:
        skip_prefixes.append(os.environ["APPDIR"])
    if "SNAP" in os.environ:
        skip_prefixes.append(os.environ["SNAP"])

    if not skip_prefixes:
        return shutil.which("ffmpeg")

    # 1. Search PATH entries that do not belong to container
    path_dirs = os.environ.get("PATH", "").split(os.pathsep)
    for d in path_dirs:
        if not d or any(d.startswith(pfx) for pfx in skip_prefixes):
            continue
        cand = Path(d) / "ffmpeg"
        if cand.is_file() and os.access(cand, os.X_OK):
            return str(cand)

    # 2. Standard system locations
    for sp in ("/usr/bin/ffmpeg", "/usr/local/bin/ffmpeg", "/bin/ffmpeg"):
        if any(sp.startswith(pfx) for pfx in skip_prefixes):
            continue
        p = Path(sp)
        if p.is_file() and os.access(p, os.X_OK):
            return str(p)

    return None


def get_system_ffprobe_path() -> str | None:
    """Find system FFprobe binary on host, ignoring container/runtime directories."""
    if is_flatpak():
        return None

    skip_prefixes: list[str] = []
    if "APPDIR" in os.environ:
        skip_prefixes.append(os.environ["APPDIR"])
    if "SNAP" in os.environ:
        skip_prefixes.append(os.environ["SNAP"])

    if not skip_prefixes:
        return shutil.which("ffprobe")

    path_dirs = os.environ.get("PATH", "").split(os.pathsep)
    for d in path_dirs:
        if not d or any(d.startswith(pfx) for pfx in skip_prefixes):
            continue
        cand = Path(d) / "ffprobe"
        if cand.is_file() and os.access(cand, os.X_OK):
            return str(cand)

    for sp in ("/usr/bin/ffprobe", "/usr/local/bin/ffprobe", "/bin/ffprobe"):
        if any(sp.startswith(pfx) for pfx in skip_prefixes):
            continue
        p = Path(sp)
        if p.is_file() and os.access(p, os.X_OK):
            return str(p)

    return None


def resolve_ffmpeg_binaries(
    source: str | None = None,
    custom_path: str | None = None,
) -> tuple[str, str, str, str | None]:
    """Resolve (ffmpeg_path, ffprobe_path, effective_source, resolution_note).

    effective_source is one of 'container', 'system', 'manual'.
    resolution_note provides human-readable context for auto decisions.
    """
    global _RESOLVED_CACHE, _CACHE_KEY

    src = source if source is not None else get_ffmpeg_source_setting()
    custom = custom_path if custom_path is not None else get_custom_ffmpeg_path_setting()
    cache_key = (
        source,
        custom_path,
        src,
        custom,
        os.environ.get("SNAP"),
        os.environ.get("APPDIR"),
        os.environ.get("FLATPAK_ID"),
    )

    if source is None and custom_path is None and _RESOLVED_CACHE is not None and _CACHE_KEY == cache_key:
        return _RESOLVED_CACHE

    c_ffmpeg = get_container_ffmpeg_path()
    c_ffprobe = get_container_ffprobe_path()
    s_ffmpeg = get_system_ffmpeg_path()
    s_ffprobe = get_system_ffprobe_path()

    resolved_ffmpeg: str = "ffmpeg"
    resolved_ffprobe: str = "ffprobe"
    effective_source: str = src
    note: str | None = None

    if src == "manual":
        if custom and Path(custom).is_file() and os.access(custom, os.X_OK):
            resolved_ffmpeg = custom
            cand_probe = Path(custom).parent / "ffprobe"
            if cand_probe.is_file() and os.access(cand_probe, os.X_OK):
                resolved_ffprobe = str(cand_probe)
            else:
                resolved_ffprobe = s_ffprobe or c_ffprobe or "ffprobe"
            effective_source = "manual"
            note = f"Manual: {custom}"
        else:
            effective_source = "system" if s_ffmpeg else ("container" if c_ffmpeg else "system")
            resolved_ffmpeg = s_ffmpeg or c_ffmpeg or "ffmpeg"
            resolved_ffprobe = s_ffprobe or c_ffprobe or "ffprobe"
            note = "Manual path invalid; fell back to default"

    elif src == "container":
        if c_ffmpeg:
            resolved_ffmpeg = c_ffmpeg
            resolved_ffprobe = c_ffprobe or s_ffprobe or "ffprobe"
            effective_source = "container"
            note = "Flatpak runtime FFmpeg" if is_flatpak() else "Container bundled FFmpeg"
        else:
            resolved_ffmpeg = s_ffmpeg or "ffmpeg"
            resolved_ffprobe = s_ffprobe or "ffprobe"
            effective_source = "system"
            note = "Container requested, but none found; using system FFmpeg"

    elif src == "system":
        if s_ffmpeg:
            resolved_ffmpeg = s_ffmpeg
            resolved_ffprobe = s_ffprobe or c_ffprobe or "ffprobe"
            effective_source = "system"
            note = "System FFmpeg"
        else:
            resolved_ffmpeg = c_ffmpeg or "ffmpeg"
            resolved_ffprobe = c_ffprobe or "ffprobe"
            effective_source = "container" if c_ffmpeg else "system"
            if is_flatpak() and c_ffmpeg:
                note = "Host system FFmpeg is not accessible inside Flatpak sandbox; using Flatpak runtime"
            else:
                note = "System FFmpeg requested, but none found; using container fallback" if c_ffmpeg else "System FFmpeg not found"

    else:  # auto
        if c_ffmpeg and s_ffmpeg:
            s_ver: tuple[int, int] | None = None
            try:
                res = subprocess.run(
                    [s_ffmpeg, "-version"],
                    capture_output=True,
                    text=True,
                    timeout=2.0,
                    creationflags=SUBPROCESS_FLAGS,
                )
                if res.returncode == 0 and res.stdout:
                    s_ver = parse_ffmpeg_version(res.stdout.splitlines()[0])
            except Exception:
                pass

            if s_ver is None or s_ver < MIN_FFMPEG_VERSION:
                resolved_ffmpeg = c_ffmpeg
                resolved_ffprobe = c_ffprobe or s_ffprobe or "ffprobe"
                effective_source = "container"
                ver_display = f"{s_ver[0]}.{s_ver[1]}" if s_ver else "unknown"
                note = f"Auto: System FFmpeg version ({ver_display}) is older than required (4.3+); selected Container"
            else:
                s_encoders = probe_binary_encoders(s_ffmpeg)
                if not ESSENTIAL_ENCODERS.issubset(s_encoders):
                    resolved_ffmpeg = c_ffmpeg
                    resolved_ffprobe = c_ffprobe or s_ffprobe or "ffprobe"
                    effective_source = "container"
                    note = "Auto: System FFmpeg lacks essential codecs (libx264/aac); selected Container"
                elif s_encoders.intersection(HW_ENCODERS):
                    resolved_ffmpeg = s_ffmpeg
                    resolved_ffprobe = s_ffprobe or c_ffprobe or "ffprobe"
                    effective_source = "system"
                    note = "Auto: Selected System FFmpeg (hardware acceleration supported)"
                else:
                    resolved_ffmpeg = c_ffmpeg
                    resolved_ffprobe = c_ffprobe or s_ffprobe or "ffprobe"
                    effective_source = "container"
                    note = "Auto: Selected Container FFmpeg"
        elif c_ffmpeg:
            resolved_ffmpeg = c_ffmpeg
            resolved_ffprobe = c_ffprobe or s_ffprobe or "ffprobe"
            effective_source = "container"
            note = (
                "Auto: Selected Flatpak runtime FFmpeg"
                if is_flatpak()
                else "Auto: Using Container FFmpeg (no system FFmpeg found)"
            )
        elif s_ffmpeg:
            resolved_ffmpeg = s_ffmpeg
            resolved_ffprobe = s_ffprobe or c_ffprobe or "ffprobe"
            effective_source = "system"
            note = "Auto: Using System FFmpeg"
        else:
            resolved_ffmpeg = "ffmpeg"
            resolved_ffprobe = c_ffprobe or s_ffprobe or "ffprobe"
            effective_source = "system"
            note = "Auto: No FFmpeg detected"

    result = (resolved_ffmpeg, resolved_ffprobe, effective_source, note)
    if source is None and custom_path is None:
        _RESOLVED_CACHE = result
        _CACHE_KEY = cache_key
    return result


def get_ffmpeg_path() -> str:
    """Return resolved path to ffmpeg executable."""
    return resolve_ffmpeg_binaries()[0]


def get_ffprobe_path() -> str:
    """Return resolved path to ffprobe executable."""
    return resolve_ffmpeg_binaries()[1]


def get_active_ffmpeg_source_info() -> dict[str, Any]:
    """Return diagnostic metadata about current FFmpeg resolution."""
    ffmpeg_bin, ffprobe_bin, eff_src, note = resolve_ffmpeg_binaries()
    return {
        "configured_source": get_ffmpeg_source_setting(),
        "custom_path": get_custom_ffmpeg_path_setting(),
        "effective_source": eff_src,
        "ffmpeg_path": ffmpeg_bin,
        "ffprobe_path": ffprobe_bin,
        "resolution_note": note,
        "has_container": bool(get_container_ffmpeg_path()),
        "has_system": bool(get_system_ffmpeg_path()),
    }


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
