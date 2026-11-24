"""Overlay rendering engine for drawing shapes, text, images, SVGs, and GIFs onto video frames."""

from __future__ import annotations

import io
import math
import re
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

_measure_local = threading.local()
_pango_emoji_lock = threading.Lock()

import cairo
import gi
from PIL import Image, ImageDraw, ImageFont

gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gdk", "4.0")
from gi.repository import GdkPixbuf

try:
    gi.require_version("Pango", "1.0")
    gi.require_version("PangoCairo", "1.0")
    from gi.repository import Pango, PangoCairo
except (ValueError, ImportError):
    Pango = None  # type: ignore[assignment]
    PangoCairo = None  # type: ignore[assignment]

from ..models import OverlayItem
from ..utils import clamp
from .ffmpeg_service import (
    FLAG_FORMAT,
    FLAG_INPUT,
    FLAG_NO_STDIN,
    FLAG_OVERWRITE,
    SUBPROCESS_FLAGS,
    FFmpegService,
)
from .probe_service import ProbeService


def is_emoji_string(text: str) -> bool:
    """Return True if string consists of Unicode emoji / symbol characters."""
    if not text:
        return False
    s = text.strip()
    if not s:
        return False
    for ch in s:
        cp = ord(ch)
        if not (
            0x1F000 <= cp <= 0x1FAFF
            or 0x2600 <= cp <= 0x27BF
            or 0x2B00 <= cp <= 0x2BFF
            or 0xFE00 <= cp <= 0xFE0F
            or cp in (0x200D, 0x20E3, 0x00A9, 0x00AE)
        ):
            return False
    return True


def parse_color(color_str: str | None, default: tuple[float, float, float, float] = (1.0, 1.0, 1.0, 1.0)) -> tuple[float, float, float, float]:
    """Parse hex, rgb, rgba or named colors into (r, g, b, a) float tuple (0.0 to 1.0)."""
    if not color_str:
        return default

    s = color_str.strip().lower()
    if s == "transparent" or s == "none":
        return (0.0, 0.0, 0.0, 0.0)

    # Named basic colors
    named = {
        "black": (0.0, 0.0, 0.0, 1.0),
        "white": (1.0, 1.0, 1.0, 1.0),
        "red": (1.0, 0.0, 0.0, 1.0),
        "green": (0.0, 0.8, 0.0, 1.0),
        "blue": (0.0, 0.4, 1.0, 1.0),
        "yellow": (1.0, 0.9, 0.0, 1.0),
        "cyan": (0.0, 0.9, 1.0, 1.0),
        "magenta": (1.0, 0.0, 1.0, 1.0),
        "orange": (1.0, 0.5, 0.0, 1.0),
    }
    if s in named:
        return named[s]

    # Hex formats: #RGB, #RGBA, #RRGGBB, #RRGGBBAA
    if s.startswith("#"):
        hex_val = s[1:]
        try:
            if len(hex_val) == 3:
                r = int(hex_val[0] * 2, 16) / 255.0
                g = int(hex_val[1] * 2, 16) / 255.0
                b = int(hex_val[2] * 2, 16) / 255.0
                return (r, g, b, 1.0)
            elif len(hex_val) == 4:
                r = int(hex_val[0] * 2, 16) / 255.0
                g = int(hex_val[1] * 2, 16) / 255.0
                b = int(hex_val[2] * 2, 16) / 255.0
                a = int(hex_val[3] * 2, 16) / 255.0
                return (r, g, b, a)
            elif len(hex_val) == 6:
                r = int(hex_val[0:2], 16) / 255.0
                g = int(hex_val[2:4], 16) / 255.0
                b = int(hex_val[4:6], 16) / 255.0
                return (r, g, b, 1.0)
            elif len(hex_val) == 8:
                r = int(hex_val[0:2], 16) / 255.0
                g = int(hex_val[2:4], 16) / 255.0
                b = int(hex_val[4:6], 16) / 255.0
                a = int(hex_val[6:8], 16) / 255.0
                return (r, g, b, a)
        except ValueError:
            return default

    # rgba(...) or rgb(...)
    m = re.match(r"rgba?\s*\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)(?:\s*,\s*([\d.]+))?\s*\)", s)
    if m:
        try:
            r = float(m.group(1)) / (255.0 if float(m.group(1)) > 1.0 else 1.0)
            g = float(m.group(2)) / (255.0 if float(m.group(2)) > 1.0 else 1.0)
            b = float(m.group(3)) / (255.0 if float(m.group(3)) > 1.0 else 1.0)
            a = float(m.group(4)) if m.group(4) is not None else 1.0
            return (clamp(r, 0.0, 1.0), clamp(g, 0.0, 1.0), clamp(b, 0.0, 1.0), clamp(a, 0.0, 1.0))
        except ValueError:
            return default

    return default


def draw_rounded_rect(cr: cairo.Context, x: float, y: float, w: float, h: float, radius: float) -> None:
    """Draw a rectangle path with rounded corners in Cairo."""
    r = min(radius, min(w / 2.0, h / 2.0))
    if r <= 0:
        cr.rectangle(x, y, w, h)
        return

    degrees = math.pi / 180.0
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -90 * degrees, 0 * degrees)
    cr.arc(x + w - r, y + h - r, r, 0 * degrees, 90 * degrees)
    cr.arc(x + r, y + h - r, r, 90 * degrees, 180 * degrees)
    cr.arc(x + r, y + r, r, 180 * degrees, 270 * degrees)
    cr.close_path()


class CachedVideoClip:
    """Pre-decodes video frames into in-memory Cairo ImageSurfaces for 60fps playback and looping."""

    def __init__(
        self,
        path: Path,
        mtime: float,
        duration: float,
        fps: float,
        width: int,
        height: int,
        buffers: list[bytearray],
        surfaces: list[cairo.ImageSurface],
    ) -> None:
        self.path = path
        self.mtime = mtime
        self.duration = duration
        self.fps = fps
        self.width = width
        self.height = height
        self.buffers = buffers
        self.surfaces = surfaces

    def get_frame(self, time_offset: float, is_loop: bool = True) -> cairo.ImageSurface | None:
        if not self.surfaces:
            return None
        t = max(0.0, time_offset)
        total_d = self.duration if self.duration > 0.0 else (len(self.surfaces) / self.fps)
        if total_d <= 0.0:
            total_d = 1.0

        if is_loop:
            anim_time = t % total_d
        else:
            anim_time = min(t, total_d)

        frame_idx = int(anim_time * self.fps)
        if frame_idx >= len(self.surfaces):
            frame_idx = len(self.surfaces) - 1
        elif frame_idx < 0:
            frame_idx = 0

        return self.surfaces[frame_idx]


class OverlayRenderService:
    """Provides high-performance rendering of overlay elements to Cairo surfaces and PNG files."""

    _surface_cache: dict[tuple[str, int, int, int], cairo.ImageSurface] = {}
    _video_clips: dict[str, CachedVideoClip] = {}
    _gif_durations: dict[str, tuple[float, list[float]]] = {}

    @classmethod
    def _cache_surface(cls, key: tuple[str, int, int, int], surface: cairo.ImageSurface) -> None:
        """Store a surface in cache, evicting the oldest entries if capacity is exceeded."""
        if len(cls._surface_cache) >= 150:
            oldest_key = next(iter(cls._surface_cache))
            del cls._surface_cache[oldest_key]
        cls._surface_cache[key] = surface

    @classmethod
    def clear_cache(cls) -> None:
        """Clear memory cache of loaded overlay media surfaces and video clips."""
        cls._surface_cache.clear()
        cls._video_clips.clear()
        cls._gif_durations.clear()

    @classmethod
    def get_video_clip(cls, media_path: Path | str) -> CachedVideoClip | None:
        """Decode and cache video frames into memory for ultra-fast, smooth playback and seamless looping."""
        p = Path(media_path)
        if not p.is_file():
            return None

        p_str = str(p.resolve())
        try:
            mtime = p.stat().st_mtime
        except OSError:
            mtime = 0.0

        if p_str in cls._video_clips:
            cached = cls._video_clips[p_str]
            if cached.mtime == mtime:
                return cached

        # Probe video metadata
        meta = ProbeService.probe(p)
        duration = meta.duration if meta and meta.duration > 0 else 5.0
        native_w = meta.width if meta and meta.width > 0 else 640
        native_h = meta.height if meta and meta.height > 0 else 360
        fps = 15.0

        max_dim = 480
        if max(native_w, native_h) > max_dim:
            if native_w >= native_h:
                target_w = max_dim
                target_h = int(round(native_h * (max_dim / native_w)))
            else:
                target_h = max_dim
                target_w = int(round(native_w * (max_dim / native_h)))
        else:
            target_w = native_w
            target_h = native_h

        target_w = max(2, target_w & ~1)
        target_h = max(2, target_h & ~1)

        extract_d = min(duration, 30.0)
        stride = cairo.ImageSurface.format_stride_for_width(cairo.FORMAT_ARGB32, target_w)
        frame_size = stride * target_h

        cmd = [
            FFmpegService.get_binary_path(),
            FLAG_OVERWRITE,
            FLAG_NO_STDIN,
            "-t",
            f"{extract_d:.3f}",
            FLAG_INPUT,
            str(p),
            "-an",
            "-vf",
            f"fps={fps},scale={target_w}:{target_h}",
            FLAG_FORMAT,
            "rawvideo",
            "-pix_fmt",
            "bgra",
            "pipe:1",
        ]

        try:
            res = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                check=True,
                creationflags=SUBPROCESS_FLAGS,
            )
            raw = res.stdout
            n_frames = len(raw) // frame_size
            buffers: list[bytearray] = []
            surfaces: list[cairo.ImageSurface] = []

            for i in range(n_frames):
                b = bytearray(raw[i * frame_size : (i + 1) * frame_size])
                s = cairo.ImageSurface.create_for_data(b, cairo.FORMAT_ARGB32, target_w, target_h, stride)
                buffers.append(b)
                surfaces.append(s)

            if surfaces:
                clip = CachedVideoClip(p, mtime, duration, fps, target_w, target_h, buffers, surfaces)
                cls._video_clips[p_str] = clip
                return clip
        except Exception:
            pass

        # Fallback to single static frame if raw extraction failed
        try:
            raw_bytes = FFmpegService.extract_frame_to_bytes(p, 0.0)
            if raw_bytes:
                with Image.open(io.BytesIO(raw_bytes)) as v_img:
                    scaled = v_img.convert("RGBA").resize((target_w, target_h), Image.Resampling.BILINEAR)
                    bio = io.BytesIO()
                    scaled.save(bio, format="PNG")
                    bio.seek(0)
                    surf = cairo.ImageSurface.create_from_png(bio)
                    clip = CachedVideoClip(p, mtime, duration, 1.0, target_w, target_h, [], [surf])
                    cls._video_clips[p_str] = clip
                    return clip
        except Exception:
            pass

        return None

    @classmethod
    def get_media_surface(
        cls,
        media_path: Path | str,
        target_w: int,
        target_h: int,
        time_offset: float = 0.0,
        is_loop: bool = True,
    ) -> cairo.ImageSurface | None:
        """Load, scale, and cache an image, SVG, GIF, or video frame as a Cairo ImageSurface."""
        p = Path(media_path)
        if not p.is_file():
            return None

        w = max(1, int(target_w))
        h = max(1, int(target_h))
        suffix = p.suffix.lower()

        # Handle animated GIF
        if suffix == ".gif":
            try:
                p_key = str(p.resolve())
                if p_key not in cls._gif_durations:
                    with Image.open(p) as img:
                        if getattr(img, "is_animated", False):
                            durations: list[float] = []
                            for idx in range(img.n_frames):
                                img.seek(idx)
                                durations.append(img.info.get("duration", 100) / 1000.0)
                            cls._gif_durations[p_key] = (sum(durations) or 1.0, durations)
                        else:
                            cls._gif_durations[p_key] = (0.0, [])

                total_duration, frame_durations = cls._gif_durations[p_key]
                if total_duration > 0.0 and frame_durations:
                    anim_time = (time_offset % total_duration) if is_loop else min(time_offset, total_duration)
                    accum = 0.0
                    target_frame = 0
                    for i, d in enumerate(frame_durations):
                        accum += d
                        if anim_time <= accum:
                            target_frame = i
                            break

                    cache_key = (f"{p}:{target_frame}", w, h, 0)
                    if cache_key in cls._surface_cache:
                        return cls._surface_cache[cache_key]

                    with Image.open(p) as img:
                        img.seek(target_frame)
                        frame_rgba = img.convert("RGBA").resize((w, h), Image.Resampling.LANCZOS)
                        bio = io.BytesIO()
                        frame_rgba.save(bio, format="PNG")
                        bio.seek(0)
                        surf = cairo.ImageSurface.create_from_png(bio)
                        cls._cache_surface(cache_key, surf)
                        return surf
            except Exception:
                pass

        # Handle small video clips (looping)
        if suffix in (".mp4", ".mkv", ".mov", ".webm", ".avi"):
            clip = cls.get_video_clip(p)
            if clip:
                return clip.get_frame(time_offset=time_offset, is_loop=is_loop)
            return None

        # Handle Static Image / SVG (cached once by size)
        cache_key = (str(p), w, h, 0)
        if cache_key in cls._surface_cache:
            return cls._surface_cache[cache_key]

        try:
            # GdkPixbuf natively renders and scales SVGs, PNGs, JPEGs, and WebPs
            pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(p), w, h, False)
            ok, data = pb.save_to_bufferv("png", [], [])
            if ok:
                surf = cairo.ImageSurface.create_from_png(io.BytesIO(data))
                cls._cache_surface(cache_key, surf)
                return surf
        except Exception:
            try:
                with Image.open(p) as img:
                    scaled = img.convert("RGBA").resize((w, h), Image.Resampling.LANCZOS)
                    bio = io.BytesIO()
                    scaled.save(bio, format="PNG")
                    bio.seek(0)
                    surf = cairo.ImageSurface.create_from_png(bio)
                    cls._cache_surface(cache_key, surf)
                    return surf
            except Exception:
                pass

        return None

    _system_emoji_font_path: Path | None = None
    _checked_system_emoji_font: bool = False

    @classmethod
    def _find_system_emoji_font(cls) -> Path | None:
        """Find the system emoji font used by the OS and GTK Emoji Chooser."""
        if cls._checked_system_emoji_font:
            return cls._system_emoji_font_path

        cls._checked_system_emoji_font = True
        # 1. Ask fontconfig for the active emoji font matching GTK / Pango
        # 0. On macOS, prefer native Apple Color Emoji directly (avoids fc-match subprocess)
        if sys.platform == "darwin":
            apple_emoji = Path("/System/Library/Fonts/Apple Color Emoji.ttc")
            if apple_emoji.exists() and apple_emoji.is_file():
                cls._system_emoji_font_path = apple_emoji
                return apple_emoji

        # 1. Try fontconfig fc-match
        try:
            res = subprocess.run(
                ["fc-match", "emoji", "-f", "%{file}"],
                capture_output=True,
                text=True,
                timeout=2.0,
            )
            if res.returncode == 0 and res.stdout.strip():
                p = Path(res.stdout.strip())
                if p.exists() and p.is_file():
                    cls._system_emoji_font_path = p
                    return p
        except Exception:
            pass

        # 2. Common standard paths across Linux distros, Windows, and macOS
        standard_paths = [
            Path("/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf"),
            Path("/usr/share/fonts/google-noto-color-emoji-fonts/NotoColorEmoji.ttf"),
            Path("/usr/share/fonts/noto-color-emoji/NotoColorEmoji.ttf"),
            Path.home() / ".local/share/fonts/NotoColorEmoji.ttf",
            Path("C:/Windows/Fonts/seguiemj.ttf"),
            Path("/System/Library/Fonts/Apple Color Emoji.ttc"),
        ]
        for p in standard_paths:
            if p.exists() and p.is_file():
                cls._system_emoji_font_path = p
                return p
        return None

    @classmethod
    def _render_emoji_with_pangocairo(cls, emoji_char: str, px: int) -> cairo.ImageSurface | None:
        """Render an emoji using PangoCairo so shaping & glyphs match Gtk.EmojiChooser identically."""
        if Pango is None or PangoCairo is None:
            return None
        try:
            render_px = max(160, px)
            canvas_sz = int(render_px * 2.5)
            pad = int(render_px * 0.4)
            raw_surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, canvas_sz, canvas_sz)
            with _pango_emoji_lock:
                cr = cairo.Context(raw_surf)
                layout = PangoCairo.create_layout(cr)
                family = "Apple Color Emoji" if sys.platform == "darwin" else "emoji"
                layout.set_font_description(Pango.FontDescription.from_string(f"{family} {render_px}px"))
                layout.set_text(emoji_char, -1)
                cr.set_source_rgba(1.0, 1.0, 1.0, 1.0)
                cr.move_to(float(pad), float(pad))
                PangoCairo.show_layout(cr, layout)
                raw_surf.flush()

            # Compute tight non-transparent bounding box via Pillow (fast C alpha scan)
            raw_img = Image.frombuffer(
                "RGBA",
                (canvas_sz, canvas_sz),
                raw_surf.get_data(),
                "raw",
                "BGRA",
                raw_surf.get_stride(),
                1,
            )
            bbox = raw_img.getbbox()
            if not bbox:
                return None

            x0, y0, x1, y1 = bbox
            bw = max(1, x1 - x0)
            bh = max(1, y1 - y0)
            max_dim = max(bw, bh)
            scale = float(px) / float(max_dim)
            off_x = (float(px) - bw * scale) / 2.0
            off_y = (float(px) - bh * scale) / 2.0

            out_surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, px, px)
            out_cr = cairo.Context(out_surf)
            out_cr.translate(off_x, off_y)
            out_cr.scale(scale, scale)
            out_cr.set_source_surface(raw_surf, -float(x0), -float(y0))
            out_cr.get_source().set_filter(cairo.FILTER_BEST)
            out_cr.paint()
            out_surf.flush()
            return out_surf
        except Exception:
            return None

    @classmethod
    def get_emoji_surface(cls, emoji_char: str, size: int) -> cairo.ImageSurface | None:
        """Render the exact emoji image from the system font used by the emoji selector."""
        px = max(16, size)
        cache_key = (f"emoji:{emoji_char}", px, px, 0)
        if cache_key in cls._surface_cache:
            return cls._surface_cache[cache_key]

        fpath = cls._find_system_emoji_font()
        if fpath and fpath.exists():
            # On macOS, render directly through PangoCairo (the exact same HarfBuzz/Fontconfig
            # shaping stack used by Gtk.EmojiChooser) so ZWJ, flags, skin tones, and variation
            # selectors always match the selector window identically even in bundled .app builds.
            if sys.platform == "darwin":
                pango_surf = cls._render_emoji_with_pangocairo(emoji_char, px)
                if pango_surf is not None:
                    cls._cache_surface(cache_key, pango_surf)
                    return pango_surf

            # Bitmap emoji fonts only support specific embedded strike sizes:
            # NotoColorEmoji uses 109px; Apple Color Emoji uses 160/96/64/48/40/32/20px.
            font = None
            strike_sz = 109
            for cand_sz in (109, 160, 128, 96, 64, 48, 32):
                try:
                    font = ImageFont.truetype(str(fpath), cand_sz)
                    strike_sz = cand_sz
                    break
                except OSError:
                    continue
            if font is not None:
                try:
                    canvas_sz = int(strike_sz * 2)
                    pad = int(strike_sz * 0.25)
                    img = Image.new("RGBA", (canvas_sz, canvas_sz), (0, 0, 0, 0))
                    draw = ImageDraw.Draw(img)
                    draw.text((pad, pad), emoji_char, font=font, embedded_color=True)
                    bbox = img.getbbox()
                    if bbox:
                        img = img.crop(bbox)
                        # Preserve original aspect ratio inside a square canvas
                        max_dim = max(img.width, img.height)
                        square_img = Image.new("RGBA", (max_dim, max_dim), (0, 0, 0, 0))
                        paste_x = (max_dim - img.width) // 2
                        paste_y = (max_dim - img.height) // 2
                        square_img.paste(img, (paste_x, paste_y))
                        img = square_img
                    if img.size != (px, px):
                        img = img.resize((px, px), Image.Resampling.LANCZOS)
                    bio = io.BytesIO()
                    img.save(bio, format="PNG")
                    bio.seek(0)
                    surf = cairo.ImageSurface.create_from_png(bio)
                    cls._cache_surface(cache_key, surf)
                    return surf
                except Exception:
                    pass

        return None

    @classmethod
    def render_item(
        cls,
        cr: cairo.Context,
        item: OverlayItem,
        canvas_w: float,
        canvas_h: float,
        current_time: float = 0.0,
    ) -> None:
        """Render a single overlay item onto a Cairo context with proper geometry and styles."""
        if canvas_w <= 0 or canvas_h <= 0:
            return

        # Timing check
        end_t = item.end_time if item.end_time is not None else float("inf")
        if not (item.start_time <= current_time <= end_t):
            return

        cx = item.x * canvas_w
        cy = item.y * canvas_h
        w = max(4.0, item.width * canvas_w)
        h = max(4.0, item.height * canvas_h)

        cr.save()
        cr.new_path()
        try:
            cr.translate(cx, cy)
            if item.item_type != "audio" and item.rotation != 0.0:
                cr.rotate(math.radians(item.rotation))

            half_w = w / 2.0
            half_h = h / 2.0

            # 1. Rectangle / Square
            if item.item_type in ("rect", "square"):
                radius_px = item.corner_radius * min(w, h)
                draw_rounded_rect(cr, -half_w, -half_h, w, h, radius_px)

                # Fill
                fill_r, fill_g, fill_b, fill_a = parse_color(item.fill_color)
                eff_fill_a = fill_a * item.opacity
                if eff_fill_a > 0.0:
                    cr.set_source_rgba(fill_r, fill_g, fill_b, eff_fill_a)
                    if item.border_width > 0:
                        cr.fill_preserve()
                    else:
                        cr.fill()

                # Stroke Border
                if item.border_width > 0:
                    b_r, b_g, b_b, b_a = parse_color(item.border_color)
                    cr.set_source_rgba(b_r, b_g, b_b, b_a * item.opacity)
                    cr.set_line_width(float(item.border_width))
                    cr.stroke()

            # 2. Circle / Ellipse
            elif item.item_type in ("circle", "ellipse"):
                cr.save()
                scale_y = h / w if w > 0 else 1.0
                cr.scale(1.0, scale_y)
                cr.new_sub_path()
                cr.arc(0, 0, half_w, 0, 2 * math.pi)
                cr.close_path()

                fill_r, fill_g, fill_b, fill_a = parse_color(item.fill_color)
                eff_fill_a = fill_a * item.opacity
                if eff_fill_a > 0.0:
                    cr.set_source_rgba(fill_r, fill_g, fill_b, eff_fill_a)
                    if item.border_width > 0:
                        cr.fill_preserve()
                    else:
                        cr.fill()

                if item.border_width > 0:
                    cr.restore()
                    cr.save()
                    cr.scale(1.0, scale_y)
                    cr.new_sub_path()
                    cr.arc(0, 0, half_w, 0, 2 * math.pi)
                    cr.close_path()
                    b_r, b_g, b_b, b_a = parse_color(item.border_color)
                    cr.set_source_rgba(b_r, b_g, b_b, b_a * item.opacity)
                    cr.set_line_width(float(item.border_width))
                    cr.stroke()
                cr.restore()

            # 3. Emoji or Text
            elif item.item_type in ("emoji", "text"):
                if item.item_type == "emoji":
                    text_content = item.text or "😀"
                else:
                    text_content = item.text if item.text is not None else ""
                is_pure_emoji = item.item_type == "emoji" or (bool(text_content) and is_emoji_string(text_content))

                if is_pure_emoji:
                    emoji_px = int(round(min(w, h)))
                    emoji_surf = cls.get_emoji_surface(text_content, max(16, emoji_px))
                    if emoji_surf:
                        ew = emoji_surf.get_width()
                        eh = emoji_surf.get_height()
                        cr.set_source_surface(emoji_surf, -ew / 2.0, -eh / 2.0)
                        cr.paint_with_alpha(item.opacity)
                    else:
                        cls._render_text(cr, item, text_content, w, h, canvas_h, canvas_w)
                else:
                    cls._render_text(cr, item, text_content, w, h, canvas_h, canvas_w)

            # 4. Media (Image, SVG, GIF, Video clip)
            elif item.item_type in ("image", "svg", "gif", "video"):
                if item.media_path:
                    surf = cls.get_media_surface(
                        item.media_path,
                        int(w),
                        int(h),
                        time_offset=current_time - item.start_time,
                        is_loop=item.is_loop,
                    )
                    if surf:
                        sw = surf.get_width()
                        sh = surf.get_height()
                        if sw > 0 and sh > 0 and (abs(sw - w) > 1 or abs(sh - h) > 1):
                            cr.save()
                            cr.scale(w / sw, h / sh)
                            cr.set_source_surface(surf, -sw / 2.0, -sh / 2.0)
                            cr.paint_with_alpha(item.opacity)
                            cr.restore()
                        else:
                            cr.set_source_surface(surf, -half_w, -half_h)
                            cr.paint_with_alpha(item.opacity)

            # 5. Audio element (interactive canvas badge)
            elif item.item_type == "audio":
                pill_radius = min(half_h, 16.0)
                draw_rounded_rect(cr, -half_w, -half_h, w, h, pill_radius)
                cr.set_source_rgba(0.12, 0.14, 0.18, 0.90 * item.opacity)
                cr.fill_preserve()
                cr.set_source_rgba(0.35, 0.65, 1.0, 0.95 * item.opacity)
                cr.set_line_width(1.5)
                cr.stroke()

                fname = Path(item.media_path).name if item.media_path else "Audio"
                if len(fname) > 22:
                    fname = fname[:19] + "…"

                font_sz = max(10.0, min(14.0, h * 0.42))
                cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
                cr.set_font_size(font_sz)
                ext = cr.text_extents(fname)

                # Vector music note icon (beamed eighth notes)
                icon_sz = font_sz * 0.9
                gap = 6.0
                total_w = icon_sz + gap + ext.width
                start_x = -total_w / 2.0

                cls._draw_music_note(cr, start_x, -icon_sz / 2.0, icon_sz, item.opacity)

                # Filename label
                cr.set_source_rgba(1.0, 1.0, 1.0, 0.95 * item.opacity)
                cr.move_to(start_x + icon_sz + gap, ext.height / 2.0)
                cr.show_text(fname)
                cr.new_path()

        finally:
            cr.restore()

    @classmethod
    def _get_measuring_context(cls) -> cairo.Context:
        """Return a thread-local lightweight Cairo context for text metrics."""
        cr = getattr(_measure_local, "cr", None)
        if cr is None:
            surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
            cr = cairo.Context(surf)
            _measure_local.cr = cr
        return cr

    @classmethod
    def get_wrapped_text_lines(
        cls,
        item: OverlayItem,
        aspect_ratio: float = 16.0 / 9.0,
        display_text: str | None = None,
    ) -> list[str]:
        """Compute canonical multi-line word wrapping at reference 1080p scale.

        Ensures identical line breaks across preview canvas and video export at any resolution.
        """
        text = item.text if display_text is None else display_text
        if not text:
            return []

        slant = cairo.FONT_SLANT_ITALIC if item.italic else cairo.FONT_SLANT_NORMAL
        weight = cairo.FONT_WEIGHT_BOLD if item.bold else cairo.FONT_WEIGHT_NORMAL
        font_fam = item.font_family or "Sans"

        ref_w = 1080.0 * max(0.1, aspect_ratio)
        box_w_ref = max(4.0, item.width * ref_w)
        ref_font_size = float(item.font_size)
        ref_pad = 4.0
        max_line_w_ref = max(10.0, box_w_ref - ref_pad * 2.0)

        cr = cls._get_measuring_context()

        def _wrap(fam: str) -> list[str]:
            cr.select_font_face(fam, slant, weight)
            cr.set_font_size(ref_font_size)

            raw_paragraphs = text.split("\n")
            lines: list[str] = []
            for paragraph in raw_paragraphs:
                if not paragraph:
                    lines.append("")
                    continue
                words = paragraph.split(" ")
                curr_line = ""
                for word in words:
                    candidate = word if not curr_line else f"{curr_line} {word}"
                    ext = cr.text_extents(candidate)
                    if ext.width <= max_line_w_ref:
                        curr_line = candidate
                    else:
                        if curr_line:
                            lines.append(curr_line)
                            curr_line = word
                        else:
                            lines.append(candidate)
                            curr_line = ""
                if curr_line:
                    lines.append(curr_line)
            return lines

        try:
            return _wrap(font_fam)
        except Exception:
            try:
                return _wrap("Sans")
            except Exception:
                return text.split("\n")

    @classmethod
    def _render_text(
        cls,
        cr: cairo.Context,
        item: OverlayItem,
        display_text: str,
        box_w: float,
        box_h: float,
        canvas_h: float,
        canvas_w: float | None = None,
    ) -> None:
        """Render typography with custom font family, size, style, color, alignment, wrapping, and clipping."""
        slant = cairo.FONT_SLANT_ITALIC if item.italic else cairo.FONT_SLANT_NORMAL
        weight = cairo.FONT_WEIGHT_BOLD if item.bold else cairo.FONT_WEIGHT_NORMAL
        font_fam = item.font_family or "Sans"

        ref_scale = canvas_h / 1080.0 if canvas_h > 0 else 1.0
        eff_font_size = float(item.font_size) * ref_scale

        half_w = box_w / 2.0
        half_h = box_h / 2.0

        # Background box if specified
        if item.bg_color:
            bg_r, bg_g, bg_b, bg_a = parse_color(item.bg_color)
            eff_bg_a = bg_a * item.opacity
            if eff_bg_a > 0.0:
                rad_px = min(6.0 * ref_scale, half_h, half_w)
                draw_rounded_rect(cr, -half_w, -half_h, box_w, box_h, rad_px)
                cr.set_source_rgba(bg_r, bg_g, bg_b, eff_bg_a)
                cr.fill()

        if not display_text:
            return

        # Clip all text strictly inside the box to hide overflow
        cr.save()
        cr.new_path()
        cr.rectangle(-half_w, -half_h, box_w, box_h)
        cr.clip()

        pad = 4.0 * ref_scale

        # Calculate aspect ratio
        if canvas_w is not None and canvas_h > 0:
            aspect = canvas_w / canvas_h
        elif item.width > 0 and canvas_h > 0:
            aspect = (box_w / item.width) / canvas_h
        else:
            aspect = 16.0 / 9.0

        wrapped_lines = cls.get_wrapped_text_lines(item, aspect_ratio=aspect, display_text=display_text)

        # Multi-line word rendering with safe font fallback
        def _draw_lines_with_font(fam: str) -> None:
            cr.select_font_face(fam, slant, weight)
            cr.set_font_size(eff_font_size)

            # Line height and vertical positioning
            line_height = eff_font_size * 1.25
            total_text_h = len(wrapped_lines) * line_height

            valign = getattr(item, "text_valign", "middle") or "middle"
            if valign == "top":
                start_y = -half_h + pad + eff_font_size * 0.9
            elif valign == "bottom":
                start_y = half_h - pad - total_text_h + eff_font_size * 0.9
            else:  # "middle"
                if total_text_h < box_h:
                    start_y = -total_text_h / 2.0 + eff_font_size * 0.9
                else:
                    start_y = -half_h + pad + eff_font_size * 0.9

            # Text fill color
            txt_r, txt_g, txt_b, txt_a = parse_color(item.font_color)
            cr.set_source_rgba(txt_r, txt_g, txt_b, txt_a * item.opacity)

            align = getattr(item, "text_align", "center") or "center"

            curr_y = start_y
            for line in wrapped_lines:
                if line:
                    line_ext = cr.text_extents(line)
                    if align == "left":
                        line_x = -half_w + pad
                    elif align == "right":
                        line_x = half_w - pad - line_ext.width
                    else:  # "center"
                        line_x = -(line_ext.width / 2.0 + line_ext.x_bearing)
                    cr.move_to(line_x, curr_y)
                    cr.show_text(line)
                curr_y += line_height

        try:
            _draw_lines_with_font(font_fam)
        except Exception:
            try:
                _draw_lines_with_font("Sans")
            except Exception:
                pass

        cr.restore()
        cr.new_path()

    @classmethod
    def _draw_music_note(
        cls, cr: cairo.Context, x: float, y: float, size: float, opacity: float = 1.0
    ) -> None:
        """Draw a clean, font-independent vector music note icon (beamed eighth notes)."""
        scale = size / 24.0

        cr.set_source_rgba(0.35, 0.65, 1.0, 0.95 * opacity)

        # Notehead 1 (left ellipse)
        cr.save()
        cr.translate(x + 7.0 * scale, y + 17.5 * scale)
        cr.rotate(math.radians(-25))
        cr.scale(3.6 * scale, 2.6 * scale)
        cr.arc(0, 0, 1.0, 0, 2 * math.pi)
        cr.restore()
        cr.fill()

        # Notehead 2 (right ellipse)
        cr.save()
        cr.translate(x + 18.0 * scale, y + 14.5 * scale)
        cr.rotate(math.radians(-25))
        cr.scale(3.6 * scale, 2.6 * scale)
        cr.arc(0, 0, 1.0, 0, 2 * math.pi)
        cr.restore()
        cr.fill()

        # Stems
        stem_w = max(1.2, 1.8 * scale)
        cr.set_line_width(stem_w)
        cr.move_to(x + 9.5 * scale, y + 17.5 * scale)
        cr.line_to(x + 9.5 * scale, y + 4.0 * scale)
        cr.move_to(x + 20.5 * scale, y + 14.5 * scale)
        cr.line_to(x + 20.5 * scale, y + 1.5 * scale)
        cr.stroke()

        # Connecting beam
        cr.new_path()
        cr.move_to(x + 8.5 * scale, y + 5.5 * scale)
        cr.line_to(x + 21.5 * scale, y + 3.0 * scale)
        cr.line_to(x + 21.5 * scale, y + 0.5 * scale)
        cr.line_to(x + 8.5 * scale, y + 3.0 * scale)
        cr.close_path()
        cr.fill()

    @classmethod
    def render_overlays_to_surface(
        cls,
        items: list[OverlayItem],
        width: int,
        height: int,
        current_time: float = 0.0,
        include_audio: bool = False,
    ) -> cairo.ImageSurface:
        """Render active overlays onto a transparent ARGB32 Cairo surface."""
        w = max(1, width)
        h = max(1, height)
        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
        cr = cairo.Context(surf)

        # Clear to fully transparent
        cr.set_operator(cairo.OPERATOR_CLEAR)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)

        active_items = items if include_audio else [it for it in items if it.item_type != "audio"]

        # Sort items by z_index
        sorted_items = sorted(active_items, key=lambda it: it.z_index)
        for it in sorted_items:
            cls.render_item(cr, it, float(w), float(h), current_time=current_time)

        return surf

    @classmethod
    def render_overlays_to_texture(
        cls,
        items: list[OverlayItem],
        width: int,
        height: int,
        current_time: float = 0.0,
        include_audio: bool = False,
    ) -> Any | None:
        """Render overlays and return a Gdk.Texture (using Gdk.MemoryTexture)."""
        visual_items = items if include_audio else [it for it in items if it.item_type != "audio"]
        if not visual_items:
            return None
        surf = cls.render_overlays_to_surface(
            visual_items, width, height, current_time=current_time, include_audio=include_audio
        )
        try:
            from gi.repository import Gdk, GLib

            data = surf.get_data()
            mem_bytes = GLib.Bytes.new(bytes(data))
            return Gdk.MemoryTexture.new(
                surf.get_width(),
                surf.get_height(),
                Gdk.MemoryFormat.B8G8R8A8_PREMULTIPLIED,
                mem_bytes,
                surf.get_stride(),
            )
        except Exception:
            return None

    @classmethod
    def render_overlays_to_png(
        cls,
        items: list[OverlayItem],
        output_path: Path | str,
        width: int,
        height: int,
        current_time: float = 0.0,
    ) -> Path:
        """Render overlays and save to transparent PNG file."""
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        surf = cls.render_overlays_to_surface(items, width, height, current_time=current_time, include_audio=False)
        surf.write_to_png(str(out))
        return out

