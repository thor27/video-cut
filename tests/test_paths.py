"""Tests for centralized paths and resource loading."""

from video_cut import paths


def test_paths_constants():
    assert paths.APP_ID == "io.github.thor27.video-cut"
    assert paths.APP_NAME == "Video Cut"
    assert paths.PACKAGE_DIR.is_dir()
    assert paths.RESOURCES_DIR.is_dir()
    assert paths.STYLE_CSS_PATH.is_file()


def test_load_application_css_and_icons():
    # Should not raise exception
    paths.load_application_css()
    paths.register_resource_icons()


def test_configure_platform_fonts_on_macos():
    """Verify macOS font configuration sets 96 DPI and crisp manual FreeType rendering."""
    import sys

    if sys.platform != "darwin":
        return

    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Gdk", "4.0")
    from gi.repository import Gdk, Gtk

    Gtk.init()
    display = Gdk.Display.get_default()
    if display is None:
        return

    paths.configure_platform_fonts(display)
    settings = Gtk.Settings.get_for_display(display)
    assert settings is not None
    assert settings.get_property("gtk-xft-dpi") == 96 * 1024
    assert settings.get_property("gtk-font-name") == "System Font, Apple Color Emoji 10"
    if hasattr(Gtk, "FontRendering"):
        assert settings.get_property("gtk-font-rendering") == Gtk.FontRendering.MANUAL
    assert settings.get_property("gtk-hint-font-metrics") is False
    assert settings.get_property("gtk-xft-hintstyle") == "hintslight"


def test_all_referenced_icons_exist_in_theme():
    """Verify every icon name referenced in src/video_cut resolves in Gtk.IconTheme."""
    import re
    from pathlib import Path

    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Gdk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gdk, Gtk

    Adw.init()
    display = Gdk.Display.get_default()
    if display is None:
        return
    paths.register_resource_icons(display)
    theme = Gtk.IconTheme.get_for_display(display)

    patterns = [
        r"set_icon_name\(\s*[\"']([^\"']+)[\"']",
        r"new_from_icon_name\(\s*[\"']([^\"']+)[\"']",
        r"set_from_icon_name\(\s*[\"']([^\"']+)[\"']",
        r"icon_name\s*=\s*[\"']([^\"']+)[\"']",
        r"[\"']([a-z0-9-]+-symbolic)[\"']",
    ]
    found: set[str] = set()
    src_dir = Path(paths.PACKAGE_DIR)
    for py_file in sorted(src_dir.rglob("*.py")):
        text = py_file.read_text(encoding="utf-8")
        for pat in patterns:
            for match in re.finditer(pat, text):
                found.add(match.group(1))

    missing = [name for name in sorted(found) if not theme.has_icon(name)]
    assert missing == [], f"Missing icons in Gtk.IconTheme: {missing}"
