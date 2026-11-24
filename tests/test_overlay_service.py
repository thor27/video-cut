"""Unit tests for OverlayRenderService (shapes, text, SVGs, colors, transparent PNG)."""

from pathlib import Path

import pytest

from video_cut.models import OverlayItem
from video_cut.services.overlay_service import OverlayRenderService, parse_color


def test_parse_color():
    assert parse_color("#fff") == (1.0, 1.0, 1.0, 1.0)
    assert parse_color("#000") == (0.0, 0.0, 0.0, 1.0)
    assert parse_color("transparent") == (0.0, 0.0, 0.0, 0.0)
    assert parse_color("red") == (1.0, 0.0, 0.0, 1.0)
    assert parse_color("#ff000080") == (1.0, 0.0, 0.0, 128 / 255.0)
    assert parse_color("rgba(255, 0, 128, 0.5)") == (1.0, 0.0, 128 / 255.0, 0.5)


def test_render_rect_and_circle():
    items = [
        OverlayItem(item_type="rect", x=0.3, y=0.4, width=0.2, height=0.2, fill_color="#ff0000", border_color="#ffffff", border_width=2),
        OverlayItem(item_type="circle", x=0.7, y=0.6, width=0.2, height=0.2, fill_color="#00ff00", border_color="#000000", border_width=1),
    ]
    surf = OverlayRenderService.render_overlays_to_surface(items, 640, 480)
    assert surf is not None
    assert surf.get_width() == 640
    assert surf.get_height() == 480


def test_render_text():
    items = [
        OverlayItem(
            item_type="text",
            text="Hello Cairo!",
            font_family="Sans",
            font_size=40,
            font_color="#ffffff",
            bold=True,
            italic=False,
            bg_color="#000000cc",
            x=0.5,
            y=0.5,
            width=0.4,
            height=0.1,
        )
    ]
    surf = OverlayRenderService.render_overlays_to_surface(items, 800, 600)
    assert surf is not None
    assert surf.get_width() == 800


def test_render_svg_and_png_export(tmp_path):
    svg_path = Path("src/video_cut/resources/io.github.thor27.video-cut.svg")
    assert svg_path.is_file()

    items = [
        OverlayItem(
            item_type="svg",
            media_path=svg_path,
            x=0.5,
            y=0.5,
            width=0.3,
            height=0.3,
            rotation=15.0,
            opacity=0.9,
        )
    ]

    out_png = tmp_path / "test_overlay.png"
    OverlayRenderService.render_overlays_to_png(items, out_png, 640, 360)
    assert out_png.is_file()
    assert out_png.stat().st_size > 0


def test_render_text_followed_by_circle_path_isolation():
    """Verify that rendering a circle after text does not create a connecting path line."""
    import cairo

    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, 800, 600)
    cr = cairo.Context(surf)

    text_item = OverlayItem(item_type="text", text="Title Text", x=0.3, y=0.3, width=0.4, height=0.1)
    circle_item = OverlayItem(item_type="circle", x=0.7, y=0.7, width=0.2, height=0.2, fill_color="#ff0000", border_width=2)

    OverlayRenderService.render_item(cr, text_item, 800, 600)
    # The path in cr should be empty after text rendering
    assert not cr.has_current_point()

    # Now render circle
    OverlayRenderService.render_item(cr, circle_item, 800, 600)
    assert not cr.has_current_point()


def test_cached_video_clip_loop_and_single_playback(tmp_path: Path):
    """Verify that CachedVideoClip seamlessly loops when is_loop=True and stops on last frame when is_loop=False."""
    import subprocess

    from video_cut.services.ffmpeg_service import FFmpegService

    # Generate a 2.0 second test video clip with color bars
    video_path = tmp_path / "subvideo.mp4"
    cmd = [
        FFmpegService.get_binary_path(),
        "-y",
        "-f",
        "lavfi",
        "-i",
        "testsrc=duration=2:size=320x240:rate=15",
        "-pix_fmt",
        "yuv420p",
        str(video_path),
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

    # 1. Fetch cached video clip
    clip = OverlayRenderService.get_video_clip(video_path)
    assert clip is not None
    assert clip.duration == pytest.approx(2.0, rel=0.1)
    assert len(clip.surfaces) > 0
    assert clip.width > 0 and clip.height > 0

    # 2. Test Looping (is_loop = True)
    # At t = 0.5s:
    frame_0_5 = clip.get_frame(time_offset=0.5, is_loop=True)
    # At t = 2.5s (which is 0.5s past the 2.0s duration):
    frame_2_5_loop = clip.get_frame(time_offset=2.5, is_loop=True)
    # At t = 4.5s (which is 0.5s into the 3rd loop cycle):
    frame_4_5_loop = clip.get_frame(time_offset=4.5, is_loop=True)

    assert frame_0_5 is not None
    assert frame_2_5_loop is not None
    assert frame_4_5_loop is not None
    # Looping frames at identical modulo timestamps point to the exact same surface
    assert frame_0_5 == frame_2_5_loop == frame_4_5_loop

    # 3. Test Non-Looping (is_loop = False)
    # At t = 2.5s (past duration), should hold the last frame
    frame_last = clip.surfaces[-1]
    frame_past_no_loop = clip.get_frame(time_offset=2.5, is_loop=False)
    frame_far_past_no_loop = clip.get_frame(time_offset=10.0, is_loop=False)

    assert frame_past_no_loop == frame_last
    assert frame_far_past_no_loop == frame_last

    # 4. Fast rendering: 30 consecutive frame renders should be instantaneous (pure memory lookups)
    import time
    t0 = time.time()
    for i in range(30):
        f = clip.get_frame(time_offset=i * 0.1, is_loop=True)
        assert f is not None
    elapsed = time.time() - t0
    # 30 frames in memory should take less than 10 milliseconds
    assert elapsed < 0.05


def test_video_overlay_rendering_into_surface(tmp_path: Path):
    """Verify that an OverlayItem with item_type='video' renders onto an ARGB32 canvas smoothly."""
    import subprocess

    from video_cut.services.ffmpeg_service import FFmpegService

    video_path = tmp_path / "subvideo2.mp4"
    cmd = [
        FFmpegService.get_binary_path(),
        "-y",
        "-f",
        "lavfi",
        "-i",
        "testsrc=duration=2:size=320x240:rate=15",
        "-pix_fmt",
        "yuv420p",
        str(video_path),
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

    item = OverlayItem(
        item_type="video",
        media_path=video_path,
        x=0.5,
        y=0.5,
        width=0.4,
        height=0.3,
        opacity=0.9,
        is_loop=True,
    )

    # Render at t = 3.5 (in 2nd loop cycle)
    surf = OverlayRenderService.render_overlays_to_surface([item], 800, 600, current_time=3.5)
    assert surf is not None
    assert surf.get_width() == 800
    assert surf.get_height() == 600


def test_render_off_canvas_overlays_clipped_to_surface(tmp_path: Path):
    """Test that items positioned partially or fully off-canvas render safely and surface bounds remain exact."""
    items = [
        # Partially off left edge
        OverlayItem(item_type="rect", x=-0.1, y=0.5, width=0.3, height=0.2, fill_color="#ff0000"),
        # Partially off top-right corner
        OverlayItem(item_type="circle", x=1.1, y=-0.1, width=0.4, height=0.4, fill_color="#00ff00"),
        # Completely off bottom edge
        OverlayItem(item_type="text", text="Offscreen", x=0.5, y=1.8, width=0.3, height=0.1),
    ]

    surf = OverlayRenderService.render_overlays_to_surface(items, 640, 480)
    assert surf is not None
    assert surf.get_width() == 640
    assert surf.get_height() == 480

    out_png = tmp_path / "off_canvas_export.png"
    OverlayRenderService.render_overlays_to_png(items, out_png, 640, 480)
    assert out_png.exists()

    assert out_png.stat().st_size > 0


def test_render_audio_item_badge():
    """Test that audio item badge renders with vector music note icon without error."""
    import cairo

    audio_item = OverlayItem(
        item_type="audio",
        media_path=Path("/tmp/soundtrack.mp3"),
        x=0.5,
        y=0.5,
        width=0.25,
        height=0.08,
    )
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, 400, 200)
    cr = cairo.Context(surf)
    OverlayRenderService.render_item(cr, audio_item, 400, 200)
    # Check that data is non-empty (badge was drawn)
    data = surf.get_data()
    assert any(b != 0 for b in data)


def test_render_overlays_excludes_audio_by_default():
    """Test that render_overlays_to_surface and render_overlays_to_texture omit audio badges by default."""
    audio_item = OverlayItem(item_type="audio", media_path=Path("/tmp/bg.mp3"), x=0.5, y=0.5, width=0.2, height=0.1)

    # 1. Texture with only audio should return None
    tex = OverlayRenderService.render_overlays_to_texture([audio_item], 640, 360, include_audio=False)
    assert tex is None

    # 2. Texture with include_audio=True should produce texture
    tex_with_audio = OverlayRenderService.render_overlays_to_texture([audio_item], 640, 360, include_audio=True)
    assert tex_with_audio is not None

    # 3. Surface with only audio and default include_audio=False should be completely transparent
    surf_default = OverlayRenderService.render_overlays_to_surface([audio_item], 100, 100)
    data_default = surf_default.get_data()
    assert all(b == 0 for b in data_default)


def test_render_text_multiline_alignment_and_clipping():
    """Verify that multi-line text with left, center, and right alignments renders correctly and clips overflow."""
    import cairo

    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, 400, 200)
    cr = cairo.Context(surf)

    # Multi-line text with alignment left
    item_left = OverlayItem(
        item_type="text",
        text="Line One\nLine Two with lots of words that wrap\nLine Three",
        text_align="left",
        font_size=20,
        x=0.5,
        y=0.5,
        width=0.4,
        height=0.3,
        bg_color="#00000088",
    )
    OverlayRenderService.render_item(cr, item_left, 400, 200)
    assert not cr.has_current_point()

    # Multi-line text with alignment right and valign top
    item_right = OverlayItem(
        item_type="text",
        text="Line One\nLine Two\nLine Three",
        text_align="right",
        text_valign="top",
        font_size=20,
        x=0.5,
        y=0.5,
        width=0.4,
        height=0.3,
    )
    OverlayRenderService.render_item(cr, item_right, 400, 200)
    assert not cr.has_current_point()

    # Multi-line text with valign bottom
    item_bottom = OverlayItem(
        item_type="text",
        text="Bottom Aligned Text",
        text_align="center",
        text_valign="bottom",
        font_size=20,
        x=0.5,
        y=0.5,
        width=0.4,
        height=0.3,
    )
    OverlayRenderService.render_item(cr, item_bottom, 400, 200)
    assert not cr.has_current_point()


def test_emoji_system_rendering_and_scaling():
    """Verify that emojis render from the system emoji font matching the emoji selector."""
    from unittest.mock import patch

    # 1. Test rendering at various sizes
    surf_64 = OverlayRenderService.get_emoji_surface("🚀", 64)
    assert surf_64 is not None
    assert surf_64.get_width() == 64
    assert surf_64.get_height() == 64
    data_64 = surf_64.get_data()
    assert any(b != 0 for b in data_64)

    surf_200 = OverlayRenderService.get_emoji_surface("😀", 200)
    assert surf_200 is not None
    assert surf_200.get_width() == 200
    assert surf_200.get_height() == 200

    # 2. Test fallback when system emoji font is not found
    with patch.object(OverlayRenderService, "_find_system_emoji_font", return_value=None):
        OverlayRenderService._surface_cache.pop(("emoji:🔥", 64, 64, 0), None)
        surf_none = OverlayRenderService.get_emoji_surface("🔥", 64)
        assert surf_none is None

    # 3. Test complex multi-codepoint emojis (skin tone, flags, ZWJ, keycap, newer Unicode)
    complex_emojis = ["👍🏽", "🇺🇸", "🇧🇷", "👨‍💻", "🏳️‍🌈", "1️⃣", "🤭", "🫣", "🫡"]
    rendered_bytes = set()
    for em in complex_emojis:
        s = OverlayRenderService.get_emoji_surface(em, 64)
        assert s is not None, f"Failed to render complex emoji {em!r}"
        assert s.get_width() == 64 and s.get_height() == 64
        raw = bytes(s.get_data())
        assert any(b != 0 for b in raw), f"Blank render for complex emoji {em!r}"
        rendered_bytes.add(raw)
    assert len(rendered_bytes) == len(complex_emojis)


def test_text_wrapping_fidelity_between_preview_and_export():
    """Verify that multi-line word wrapping is 100% identical between preview and export resolutions (Issue #52)."""
    import cairo

    class RecordingContext:
        def __init__(self, cr, line_collector):
            self._cr = cr
            self._lines = line_collector

        def show_text(self, text):
            self._lines.append(text)
            return self._cr.show_text(text)

        def __getattr__(self, name):
            return getattr(self._cr, name)

    test_cases = [
        # (text, font_size, width, bold, preview_size, export_size)
        ("Texto de Exemplo", 42, 0.20, True, (533, 300), (1920, 1080)),
        ("Sample Text", 42, 0.25, True, (300, 300), (1080, 1080)),
        ("A very long sentence that wraps across multiple lines cleanly", 36, 0.35, False, (640, 360), (1920, 1080)),
        ("Short", 42, 0.35, True, (480, 270), (3840, 2160)),
    ]

    for text, font_size, width, bold, (prev_w, prev_h), (exp_w, exp_h) in test_cases:
        item = OverlayItem(
            item_type="text",
            text=text,
            font_size=font_size,
            width=width,
            bold=bold,
        )

        # 1. Check get_wrapped_text_lines directly
        aspect_prev = prev_w / prev_h
        aspect_exp = exp_w / exp_h
        lines_calc_prev = OverlayRenderService.get_wrapped_text_lines(item, aspect_ratio=aspect_prev)
        lines_calc_exp = OverlayRenderService.get_wrapped_text_lines(item, aspect_ratio=aspect_exp)
        assert lines_calc_prev == lines_calc_exp

        # 2. Check actual rendered show_text calls
        prev_lines: list[str] = []
        surf_prev = cairo.ImageSurface(cairo.FORMAT_ARGB32, prev_w, prev_h)
        rec_prev = RecordingContext(cairo.Context(surf_prev), prev_lines)
        OverlayRenderService.render_item(rec_prev, item, prev_w, prev_h)

        exp_lines: list[str] = []
        surf_exp = cairo.ImageSurface(cairo.FORMAT_ARGB32, exp_w, exp_h)
        rec_exp = RecordingContext(cairo.Context(surf_exp), exp_lines)
        OverlayRenderService.render_item(rec_exp, item, exp_w, exp_h)

        assert prev_lines == exp_lines, f"Mismatch for '{text}': preview={prev_lines} vs export={exp_lines}"
        assert prev_lines == lines_calc_prev


