"""Centralized path definitions and resource loaders for Video Cut."""

from __future__ import annotations

import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gtk

APP_ID = "io.github.thor27.video-cut"
APP_NAME = "Video Cut"

# Package filesystem layout
PACKAGE_DIR = Path(__file__).parent.resolve()
RESOURCES_DIR = PACKAGE_DIR / "resources"
STYLE_CSS_PATH = PACKAGE_DIR / "style.css"


def configure_platform_fonts(display: Gdk.Display | None = None) -> None:
    """Configure crisp native font sizing and rendering on macOS.

    On macOS, GdkMacosDisplay defaults gtk-xft-dpi to 72 DPI (73728), which makes
    Libadwaita's pt-based typography render 25% smaller than designed when using
    Fontconfig/FreeType. Setting 96 DPI (98304) and Gtk.FontRendering.MANUAL ensures
    proper proportions and crisp antialiased rendering across 1x and Retina displays.
    """
    if sys.platform != "darwin":
        return
    active_display = display or Gdk.Display.get_default()
    if not active_display:
        return
    try:
        settings = Gtk.Settings.get_for_display(active_display)
        if settings is None:
            return
        settings.set_property("gtk-xft-dpi", 96 * 1024)
        settings.set_property("gtk-font-name", "System Font, Apple Color Emoji 10")
        if hasattr(Gtk, "FontRendering"):
            settings.set_property("gtk-font-rendering", Gtk.FontRendering.MANUAL)
        settings.set_property("gtk-hint-font-metrics", False)
        settings.set_property("gtk-xft-antialias", 1)
        settings.set_property("gtk-xft-hinting", 1)
        settings.set_property("gtk-xft-hintstyle", "hintslight")
        settings.set_property("gtk-xft-rgba", "none")
    except Exception:
        pass


def register_resource_icons(display: Gdk.Display | None = None) -> None:
    """Register application bundled icons in the GTK IconTheme search path."""
    if not RESOURCES_DIR.exists():
        return
    active_display = display or Gdk.Display.get_default()
    if active_display:
        icon_theme = Gtk.IconTheme.get_for_display(active_display)
        icon_theme.add_search_path(str(RESOURCES_DIR))
    Gtk.Window.set_default_icon_name(APP_ID)


_loaded_displays: set[int] = set()


def load_application_css(display: Gdk.Display | None = None) -> Gtk.CssProvider | None:
    """Load and register application CSS with GTK style provider."""
    if not STYLE_CSS_PATH.exists():
        return None
    active_display = display or Gdk.Display.get_default()
    if not active_display:
        return None

    configure_platform_fonts(active_display)

    disp_id = hash(active_display)
    if disp_id in _loaded_displays:
        return None

    provider = Gtk.CssProvider()
    provider.load_from_path(str(STYLE_CSS_PATH))
    Gtk.StyleContext.add_provider_for_display(
        active_display,
        provider,
        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
    )
    _loaded_displays.add(disp_id)
    return provider
