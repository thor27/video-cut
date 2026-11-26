"""Unit tests for cross-platform cursor helper and macOS AppKit cursor bridge."""

from __future__ import annotations

import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gtk

from video_cut import cursors


def test_macos_resize_and_move_cursors_use_textures(monkeypatch):
    monkeypatch.setattr(cursors.sys, "platform", "darwin")
    cursors._CURSOR_CACHE.clear()

    for name in ("ew-resize", "ns-resize", "nwse-resize", "nesw-resize", "move"):
        cur = cursors.get_cursor(name)
        assert cur is not None
        assert cur.get_texture() is not None, f"{name} should be backed by a texture on macOS"
        assert cursors.cursor_name_from_gdk(cur) == name

    # Public NSCursor names remain named cursors
    for name in ("default", "pointer", "grab", "grabbing"):
        cur = cursors.get_cursor(name)
        assert cur is not None
        assert cur.get_name() == name
        assert cur.get_texture() is None
        assert cursors.cursor_name_from_gdk(cur) == name


def test_non_macos_uses_named_cursors(monkeypatch):
    monkeypatch.setattr(cursors.sys, "platform", "linux")
    cursors._CURSOR_CACHE.clear()

    for name in ("ew-resize", "ns-resize", "nwse-resize", "nesw-resize", "move", "default"):
        cur = cursors.get_cursor(name)
        assert cur is not None
        assert cur.get_name() == name
        assert cur.get_texture() is None


def test_set_widget_cursor_sets_and_clears(monkeypatch):
    monkeypatch.setattr(cursors.sys, "platform", "darwin")
    cursors._CURSOR_CACHE.clear()

    box = Gtk.Box()
    cursors.set_widget_cursor(box, "nwse-resize", update_surface=True)
    cur = box.get_cursor()
    assert cur is not None
    assert cur.get_texture() is not None
    assert cursors.cursor_name_from_gdk(cur) == "nwse-resize"

    cursors.set_widget_cursor(box, None, update_surface=True)
    assert box.get_cursor() is None


def test_macos_appkit_bridge_builds_and_applies_cursors():
    if sys.platform != "darwin":
        return

    bridge = cursors._MacOSCursorBridge.get()
    assert bridge is not None

    # Verify all custom Retina resize/move cursors build distinct NSCursor handles and apply cleanly
    seen = set()
    for name in ("ew-resize", "ns-resize", "nwse-resize", "nesw-resize", "move"):
        ptr = bridge.get_ns_cursor(name)
        assert ptr is not None and ptr != 0
        seen.add(ptr)
        assert bridge.apply(name) is True

    assert len(seen) == 5

    # Standard cursors & reset back to arrowCursor
    for name in ("pointer", "grab", "text", "default"):
        ptr = bridge.get_ns_cursor(name)
        assert ptr is not None and ptr != 0
        assert bridge.apply(name) is True


def test_install_macos_cursor_sync_is_idempotent(monkeypatch):
    monkeypatch.setattr(cursors.sys, "platform", "darwin")
    win = Gtk.Window()
    before = win.observe_controllers().get_n_items()
    cursors.install_macos_cursor_sync(win)
    after_first = win.observe_controllers().get_n_items()
    cursors.install_macos_cursor_sync(win)
    after_second = win.observe_controllers().get_n_items()
    assert after_first == before + 1
    assert after_second == after_first
    win.destroy()
