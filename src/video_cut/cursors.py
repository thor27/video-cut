"""Cross-platform cursor helpers with native AppKit Retina cursors on macOS.

Two GTK 4 macOS backend limitations break interactive cursors on modern macOS:
1. GTK 4 maps resize/move cursor names (`ew-resize`, `ns-resize`, `nwse-resize`,
   `nesw-resize`, `move`) to its private `gdkCoreCursor` subclass overriding
   `_coreCursorType`, which WindowServer ignores on modern macOS.
2. `_gdk_surface_set_cursor_internal` silently ignores cursor updates whenever
   `pointer_info->surface_under_pointer != surface`. On macOS, closing any
   popover (such as `Gtk.EmojiChooser`, tooltips, or context menus) or native
   modal dialog (`Gtk.FileDialog`, child dialogs) triggers `NSEventTypeMouseExited`
   and resets `surface_under_pointer` to `NULL` without synthesizing a new enter
   event for the underlying window, leaving `gdk_surface_set_cursor()` stuck as
   a no-op until the pointer leaves and re-enters the window frame.

To make cursors reliable everywhere on macOS, this module:
- Builds texture-backed `Gdk.Cursor` instances (for GTK state & fallback).
- Renders 2x Retina `NSCursor` instances via AppKit and applies them directly
  via `[NSCursor set]` only when `[NSCursor currentCursor]` differs.
- Provides `install_macos_cursor_sync(window)` to mirror the picked widget's
  `Gdk.Cursor` onto AppKit on every enter/motion event in the window.
"""

from __future__ import annotations

import ctypes
import io
import math
import sys
from typing import Any

import cairo
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, Gtk

_CURSOR_CACHE: dict[str, Gdk.Cursor | None] = {}

_CANONICAL_TEXTURE_CURSORS: dict[str, str] = {
    "ew-resize": "ew-resize",
    "col-resize": "ew-resize",
    "e-resize": "ew-resize",
    "w-resize": "ew-resize",
    "ns-resize": "ns-resize",
    "row-resize": "ns-resize",
    "n-resize": "ns-resize",
    "s-resize": "ns-resize",
    "nwse-resize": "nwse-resize",
    "nw-resize": "nwse-resize",
    "se-resize": "nwse-resize",
    "nesw-resize": "nesw-resize",
    "ne-resize": "nesw-resize",
    "sw-resize": "nesw-resize",
    "move": "move",
    "all-scroll": "move",
}

_APPKIT_NAMED_SELECTORS: dict[str, bytes] = {
    "default": b"arrowCursor",
    "pointer": b"pointingHandCursor",
    "grab": b"openHandCursor",
    "grabbing": b"closedHandCursor",
    "crosshair": b"crosshairCursor",
    "text": b"IBeamCursor",
    "vertical-text": b"IBeamCursorForVerticalLayout",
    "not-allowed": b"operationNotAllowedCursor",
    "no-drop": b"operationNotAllowedCursor",
    "copy": b"dragCopyCursor",
    "alias": b"dragLinkCursor",
    "context-menu": b"contextualMenuCursor",
}


class _CGPoint(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]


class _CGSize(ctypes.Structure):
    _fields_ = [("width", ctypes.c_double), ("height", ctypes.c_double)]


def _draw_double_arrow_path(cr: cairo.Context, half_len: float = 8.2, head: float = 4.2, shaft: float = 1.2) -> None:
    """Draw a crisp horizontal double-headed arrow centered at (0, 0)."""
    cr.new_path()
    cr.move_to(-half_len, 0.0)
    cr.line_to(-half_len + head, -head)
    cr.line_to(-half_len + head, -shaft)
    cr.line_to(half_len - head, -shaft)
    cr.line_to(half_len - head, -head)
    cr.line_to(half_len, 0.0)
    cr.line_to(half_len - head, head)
    cr.line_to(half_len - head, shaft)
    cr.line_to(-half_len + head, shaft)
    cr.line_to(-half_len + head, head)
    cr.close_path()


def _draw_move_path(cr: cairo.Context, half_len: float = 8.8, head: float = 3.4, shaft: float = 1.1) -> None:
    """Draw a crisp 4-way move arrow centered at (0, 0)."""
    cr.new_path()
    cr.move_to(0.0, -half_len)
    cr.line_to(head, -half_len + head)
    cr.line_to(shaft, -half_len + head)
    cr.line_to(shaft, -shaft)
    cr.line_to(half_len - head, -shaft)
    cr.line_to(half_len - head, -head)
    cr.line_to(half_len, 0.0)
    cr.line_to(half_len - head, head)
    cr.line_to(half_len - head, shaft)
    cr.line_to(shaft, shaft)
    cr.line_to(shaft, half_len - head)
    cr.line_to(head, half_len - head)
    cr.line_to(0.0, half_len)
    cr.line_to(-head, half_len - head)
    cr.line_to(-shaft, half_len - head)
    cr.line_to(-shaft, shaft)
    cr.line_to(-half_len + head, shaft)
    cr.line_to(-half_len + head, head)
    cr.line_to(-half_len, 0.0)
    cr.line_to(-half_len + head, -head)
    cr.line_to(-half_len + head, -shaft)
    cr.line_to(-shaft, -shaft)
    cr.line_to(-shaft, -half_len + head)
    cr.line_to(-head, -half_len + head)
    cr.close_path()


def _render_cursor_surface(kind: str, logical_size: int = 24, scale: float = 1.0) -> cairo.ImageSurface:
    """Render a native macOS-styled black-with-white-outline cursor to a Cairo surface."""
    px = max(1, int(round(logical_size * scale)))
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, px, px)
    cr = cairo.Context(surf)
    cr.scale(scale, scale)
    cr.set_antialias(cairo.ANTIALIAS_BEST)
    cr.set_line_join(cairo.LINE_JOIN_ROUND)
    cr.set_line_cap(cairo.LINE_CAP_ROUND)

    cx = logical_size / 2.0
    cy = logical_size / 2.0

    def _trace(dx: float = 0.0, dy: float = 0.0) -> None:
        cr.save()
        cr.translate(cx + dx, cy + dy)
        if kind == "ew-resize":
            _draw_double_arrow_path(cr)
        elif kind == "ns-resize":
            cr.rotate(math.pi / 2.0)
            _draw_double_arrow_path(cr)
        elif kind == "nwse-resize":
            cr.rotate(math.pi / 4.0)
            _draw_double_arrow_path(cr, half_len=8.6, head=4.0, shaft=1.15)
        elif kind == "nesw-resize":
            cr.rotate(-math.pi / 4.0)
            _draw_double_arrow_path(cr, half_len=8.6, head=4.0, shaft=1.15)
        elif kind == "move":
            _draw_move_path(cr)
        cr.restore()

    # Subtle drop shadow
    _trace(0.0, 0.9)
    cr.set_source_rgba(0.0, 0.0, 0.0, 0.30)
    cr.set_line_width(3.4)
    cr.stroke()

    # Crisp white outer halo (macOS cursor signature style)
    _trace()
    cr.set_source_rgba(1.0, 1.0, 1.0, 1.0)
    cr.set_line_width(2.6)
    cr.stroke_preserve()
    cr.fill()

    # Jet-black inner arrow fill
    _trace()
    cr.set_source_rgba(0.05, 0.05, 0.05, 1.0)
    cr.fill()

    surf.flush()
    return surf


def _render_cursor_texture(kind: str, size: int = 24) -> Gdk.Texture:
    """Render a native macOS-styled black-with-white-outline cursor to a Gdk.MemoryTexture."""
    surf = _render_cursor_surface(kind, logical_size=size, scale=1.0)
    raw = bytes(surf.get_data())
    gbytes = GLib.Bytes.new(raw)
    return Gdk.MemoryTexture.new(
        size,
        size,
        Gdk.MemoryFormat.B8G8R8A8_PREMULTIPLIED,
        gbytes,
        surf.get_stride(),
    )


class _MacOSCursorBridge:
    """Lightweight ctypes bridge to AppKit's NSCursor for reliable Retina cursors on macOS."""

    _instance: _MacOSCursorBridge | None = None
    _init_failed: bool = False

    def __init__(self) -> None:
        self._objc = ctypes.CDLL("/usr/lib/libobjc.A.dylib")
        self._appkit = ctypes.CDLL("/System/Library/Frameworks/AppKit.framework/AppKit")

        self._objc.objc_getClass.argtypes = [ctypes.c_char_p]
        self._objc.objc_getClass.restype = ctypes.c_void_p
        self._objc.sel_registerName.argtypes = [ctypes.c_char_p]
        self._objc.sel_registerName.restype = ctypes.c_void_p

        msg_ptr = ctypes.cast(self._objc.objc_msgSend, ctypes.c_void_p).value
        self._send_id = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)(msg_ptr)
        self._send_void = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p)(msg_ptr)
        self._send_id_id = ctypes.CFUNCTYPE(
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p
        )(msg_ptr)
        self._send_id_bytes = ctypes.CFUNCTYPE(
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_char_p, ctypes.c_ulong
        )(msg_ptr)
        self._send_void_size = ctypes.CFUNCTYPE(
            None, ctypes.c_void_p, ctypes.c_void_p, _CGSize
        )(msg_ptr)
        self._send_id_id_pt = ctypes.CFUNCTYPE(
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, _CGPoint
        )(msg_ptr)

        self._cls_cursor = self._objc.objc_getClass(b"NSCursor")
        self._cls_image = self._objc.objc_getClass(b"NSImage")
        self._cls_data = self._objc.objc_getClass(b"NSData")

        self._sel_alloc = self._objc.sel_registerName(b"alloc")
        self._sel_release = self._objc.sel_registerName(b"release")
        self._sel_set = self._objc.sel_registerName(b"set")
        self._sel_current = self._objc.sel_registerName(b"currentCursor")
        self._sel_data_with_bytes = self._objc.sel_registerName(b"dataWithBytes:length:")
        self._sel_init_with_data = self._objc.sel_registerName(b"initWithData:")
        self._sel_set_size = self._objc.sel_registerName(b"setSize:")
        self._sel_init_with_image = self._objc.sel_registerName(b"initWithImage:hotSpot:")

        self._ns_cursors: dict[str, int] = {}

    @classmethod
    def get(cls) -> _MacOSCursorBridge | None:
        if sys.platform != "darwin" or cls._init_failed:
            return None
        if cls._instance is None:
            try:
                cls._instance = cls()
            except Exception:
                cls._init_failed = True
                return None
        return cls._instance

    def _build_retina_cursor(self, kind: str) -> int | None:
        # Render at 2x (48x48 pixels) and assign 24x24 pt logical size for crisp Retina cursors
        surf = _render_cursor_surface(kind, logical_size=24, scale=2.0)
        buf = io.BytesIO()
        surf.write_to_png(buf)
        png = buf.getvalue()

        ns_data = self._send_id_bytes(self._cls_data, self._sel_data_with_bytes, png, len(png))
        if not ns_data:
            return None
        ns_img = self._send_id_id(self._send_id(self._cls_image, self._sel_alloc), self._sel_init_with_data, ns_data)
        if not ns_img:
            return None
        try:
            self._send_void_size(ns_img, self._sel_set_size, _CGSize(24.0, 24.0))
            ns_cur = self._send_id_id_pt(
                self._send_id(self._cls_cursor, self._sel_alloc),
                self._sel_init_with_image,
                ns_img,
                _CGPoint(12.0, 12.0),
            )
            return int(ns_cur) if ns_cur else None
        finally:
            self._send_void(ns_img, self._sel_release)

    def get_ns_cursor(self, name: str | None) -> int | None:
        key = name or "default"
        canonical = _CANONICAL_TEXTURE_CURSORS.get(key, key)
        if canonical in self._ns_cursors:
            return self._ns_cursors[canonical]

        ns_cur: int | None = None
        if canonical in _CANONICAL_TEXTURE_CURSORS:
            ns_cur = self._build_retina_cursor(canonical)
        else:
            sel_name = _APPKIT_NAMED_SELECTORS.get(canonical, b"arrowCursor")
            sel = self._objc.sel_registerName(sel_name)
            ptr = self._send_id(self._cls_cursor, sel)
            ns_cur = int(ptr) if ptr else None

        if ns_cur:
            self._ns_cursors[canonical] = ns_cur
        return ns_cur

    def apply(self, name: str | None) -> bool:
        target = self.get_ns_cursor(name)
        if not target:
            return False
        current = self._send_id(self._cls_cursor, self._sel_current)
        if current != target:
            self._send_void(target, self._sel_set)
        return True


def cursor_name_from_gdk(cursor: Gdk.Cursor | None) -> str | None:
    """Extract the logical CSS cursor name from a named or texture-backed Gdk.Cursor."""
    if cursor is None:
        return None
    name = cursor.get_name()
    if name is not None:
        return name
    fallback = cursor.get_fallback()
    return fallback.get_name() if fallback is not None else None


def apply_macos_cursor(name: str | None) -> None:
    """Directly apply the macOS AppKit NSCursor, bypassing GTK4's stale surface_under_pointer check."""
    if sys.platform != "darwin":
        return
    try:
        bridge = _MacOSCursorBridge.get()
        if bridge is not None:
            bridge.apply(name)
    except Exception:
        pass


def get_cursor(name: str) -> Gdk.Cursor | None:
    """Return a Gdk.Cursor for `name`, using reliable texture cursors for broken macOS resize/move cursors."""
    if not name:
        return None
    if name in _CURSOR_CACHE:
        return _CURSOR_CACHE[name]

    fallback = Gdk.Cursor.new_from_name(name, None)
    if sys.platform == "darwin" and name in _CANONICAL_TEXTURE_CURSORS:
        kind = _CANONICAL_TEXTURE_CURSORS[name]
        try:
            tex = _render_cursor_texture(kind, size=24)
            cursor = Gdk.Cursor.new_from_texture(tex, 12, 12, fallback)
            _CURSOR_CACHE[name] = cursor
            return cursor
        except Exception:
            pass

    _CURSOR_CACHE[name] = fallback
    return fallback


def set_widget_cursor(widget: Gtk.Widget, name: str | None, *, update_surface: bool = False) -> None:
    """Set a widget's cursor cross-platform, optionally forcing an immediate macOS cursor update."""
    cursor = get_cursor(name) if name else None
    widget.set_cursor(cursor)

    if update_surface and sys.platform == "darwin":
        try:
            native = widget.get_native()
            surface = native.get_surface() if native else None
            if surface is not None:
                surface.set_cursor(cursor)
        except Exception:
            pass
        apply_macos_cursor(name)


def install_macos_cursor_sync(window: Gtk.Window) -> None:
    """Attach a window-level motion sync controller on macOS so widget cursors always work.

    On macOS, closing any popover (e.g. `Gtk.EmojiChooser`, tooltips) or modal dialog
    clears GTK's `surface_under_pointer`, causing `gdk_surface_set_cursor` to silently
    no-op. Syncing the picked widget's cursor directly to AppKit on enter/motion
    guarantees instant hover cursors across the entire window at all times.
    """
    if sys.platform != "darwin":
        return
    if getattr(window, "_macos_cursor_sync_installed", False):
        return
    window._macos_cursor_sync_installed = True

    def _sync_cursor(ctrl: Gtk.EventControllerMotion, x: float, y: float) -> None:
        if not window.get_visible():
            return

        # 1. Window-level cursor override (e.g. active timeline drag-scrubbing) takes priority
        win_cursor = window.get_cursor()
        if win_cursor is not None:
            apply_macos_cursor(cursor_name_from_gdk(win_cursor))
            return

        # 2. Preserve active drag cursor while primary button is held down
        try:
            state = ctrl.get_current_event_state()
            if state & Gdk.ModifierType.BUTTON1_MASK:
                return
        except Exception:
            pass

        # 3. Walk up from the picked widget to find the nearest cursor
        try:
            widget: Any = window.pick(x, y, Gtk.PickFlags.INSENSITIVE)
        except Exception:
            widget = None

        while widget is not None:
            cur = widget.get_cursor()
            if cur is not None:
                apply_macos_cursor(cursor_name_from_gdk(cur))
                return
            widget = widget.get_parent()

        apply_macos_cursor("default")

    motion = Gtk.EventControllerMotion.new()
    motion.connect("enter", _sync_cursor)
    motion.connect("motion", _sync_cursor)
    window.add_controller(motion)
