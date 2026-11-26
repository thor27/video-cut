"""Cross-platform media playback backend with GStreamer fallback when GTK4 has no media module."""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, GObject, Graphene, Gtk

_GST_AVAILABLE: bool | None = None
_Gst: Any = None


def _ensure_gst() -> Any | None:
    """Lazily initialize GStreamer once and return the Gst module, or None if unavailable."""
    global _GST_AVAILABLE, _Gst
    if _GST_AVAILABLE is not None:
        return _Gst if _GST_AVAILABLE else None
    try:
        gi.require_version("Gst", "1.0")
        from gi.repository import Gst

        if not Gst.is_initialized():
            Gst.init(None)
        _Gst = Gst
        _GST_AVAILABLE = True
        return _Gst
    except Exception:
        _GST_AVAILABLE = False
        _Gst = None
        return None


def has_gtk_media_backend() -> bool:
    """Return True if GTK4 has a working native gtk-media-file backend extension."""
    if os.environ.get("VIDEO_CUT_MEDIA_BACKEND", "").lower() == "gst":
        return False
    try:
        ep = Gio.IOExtensionPoint.lookup("gtk-media-file")
        if ep is None:
            return False
        return len(ep.get_extensions()) > 0
    except Exception:
        return False


class GstMediaFile(GObject.Object, Gdk.Paintable):
    """GStreamer-backed drop-in replacement for Gtk.MediaFile when GTK4 lacks media-gstreamer.

    Uses playbin3/playbin with an appsink delivering BGRx frames as Gdk.MemoryTexture on the
    GLib main loop. This avoids synchronous main-thread invokes from GStreamer streaming threads,
    eliminating AB-BA deadlocks and Rust plugin panics while working across GL and Cairo renderers.
    """

    def __init__(self, filename: str | Path) -> None:
        super().__init__()
        self._path = Path(filename)
        self._uri = self._path.resolve().as_uri()
        self._pipeline: Any = None
        self._bus: Any = None
        self._bus_watch_id: int | None = None
        self._texture: Gdk.Texture | None = None
        self._video_width: int = 0
        self._video_height: int = 0

        # Thread-safe single-slot frame handoff (coalesces bursts during scrubbing)
        self._frame_lock = threading.Lock()
        self._pending_frame: tuple[int, int, int, bytes] | None = None
        self._frame_idle_scheduled: bool = False

        self._prepared: bool = False
        self._seeking: bool = False
        self._playing: bool = False
        self._ended: bool = False
        self._has_audio: bool = False
        self._has_video: bool = False
        self._volume: float = 1.0
        self._muted: bool = False
        self._duration_us: int = 0
        self._last_timestamp_us: int = 0
        self._error: GLib.Error | None = None
        self._seek_timeout_id: int | None = None
        self._closed: bool = False

        self._build_pipeline()

    @classmethod
    def new_for_filename(cls, filename: str | Path) -> GstMediaFile:
        return cls(filename)

    # ------------------------------------------------------------------
    # GObject Properties (mirroring Gtk.MediaStream / Gtk.MediaFile)
    # ------------------------------------------------------------------

    @GObject.Property(type=bool, default=False)
    def prepared(self) -> bool:
        return self._prepared

    @GObject.Property(type=bool, default=False)
    def seeking(self) -> bool:
        return self._seeking

    @GObject.Property(type=bool, default=False)
    def playing(self) -> bool:
        return self._playing

    @GObject.Property(type=bool, default=False)
    def ended(self) -> bool:
        return self._ended

    @GObject.Property(type=int, default=0)
    def timestamp(self) -> int:
        return self.get_timestamp()

    @GObject.Property(type=int, default=0)
    def duration(self) -> int:
        return self._duration_us

    @GObject.Property(type=bool, default=False)
    def has_audio_prop(self) -> bool:
        return self._has_audio

    @GObject.Property(type=float, default=1.0)
    def volume(self) -> float:
        return self._volume

    @GObject.Property(type=bool, default=False)
    def muted(self) -> bool:
        return self._muted

    # ------------------------------------------------------------------
    # Pipeline setup
    # ------------------------------------------------------------------

    def _build_pipeline(self) -> None:
        if not self._path.exists():
            self._error = GLib.Error(f"Media file not found: {self._path}")
            return

        Gst = _ensure_gst()
        if Gst is None:
            self._error = GLib.Error("GStreamer is not available")
            return

        playbin = Gst.ElementFactory.make("playbin3", None) or Gst.ElementFactory.make("playbin", None)
        if playbin is None:
            self._error = GLib.Error("GStreamer playbin element is not available")
            return

        video_sink = self._create_video_sink(Gst)
        if video_sink is None:
            self._error = GLib.Error("No compatible GStreamer video sink available")
            return

        playbin.set_property("video-sink", video_sink)
        playbin.set_property("uri", self._uri)

        bus = playbin.get_bus()
        if bus is not None:
            bus.add_signal_watch()
            self._bus = bus
            self._bus_watch_id = bus.connect("message", self._on_bus_message)

        self._pipeline = playbin
        # Preroll in PAUSED state so the first frame and stream metadata become ready
        playbin.set_state(Gst.State.PAUSED)

    def _create_video_sink(self, Gst: Any) -> Any | None:
        """Create a videoconvert -> appsink(BGRx) bin for thread-safe frame delivery."""
        appsink = Gst.ElementFactory.make("appsink", None)
        if appsink is None:
            return None

        caps = Gst.Caps.from_string("video/x-raw,format=BGRx")
        appsink.set_property("caps", caps)
        appsink.set_property("emit-signals", True)
        appsink.set_property("sync", True)
        appsink.set_property("max-buffers", 2)
        appsink.set_property("drop", True)
        appsink.connect("new-sample", self._on_appsink_sample)
        appsink.connect("new-preroll", self._on_appsink_preroll)

        # Wrap with videoconvert in a bin so playbin can negotiate any decoder output format
        videoconvert = Gst.ElementFactory.make("videoconvert", None)
        if videoconvert is None:
            return appsink

        vbin = Gst.Bin.new("videocut_video_sink_bin")
        vbin.add(videoconvert)
        vbin.add(appsink)
        if not videoconvert.link(appsink):
            return appsink
        sink_pad = videoconvert.get_static_pad("sink")
        if sink_pad is not None:
            vbin.add_pad(Gst.GhostPad.new("sink", sink_pad))
            return vbin
        return appsink

    def _handle_appsink_sample(self, sample: Any) -> None:
        if sample is None or self._closed:
            return
        buf = sample.get_buffer()
        caps = sample.get_caps()
        if buf is None or caps is None or caps.get_size() == 0:
            return
        struct = caps.get_structure(0)
        ok_w, width = struct.get_int("width")
        ok_h, height = struct.get_int("height")
        if not (ok_w and ok_h and width > 0 and height > 0):
            return
        ok, map_info = buf.map(_Gst.MapFlags.READ)
        if not ok:
            return
        try:
            raw = bytes(map_info.data)
        finally:
            buf.unmap(map_info)

        # Compute actual row stride (respecting possible GStreamer padding)
        stride = width * 4
        if len(raw) >= height * stride and len(raw) % height == 0:
            stride = len(raw) // height
        elif len(raw) < height * stride:
            return

        schedule_idle = False
        with self._frame_lock:
            if self._closed:
                return
            self._pending_frame = (width, height, stride, raw)
            if not self._frame_idle_scheduled:
                self._frame_idle_scheduled = True
                schedule_idle = True

        if schedule_idle:
            GLib.idle_add(self._apply_pending_frame)

    def _apply_pending_frame(self) -> bool:
        with self._frame_lock:
            self._frame_idle_scheduled = False
            frame = self._pending_frame
            self._pending_frame = None

        if self._closed or frame is None:
            return False

        width, height, stride, raw = frame
        size_changed = (width != self._video_width) or (height != self._video_height)
        self._video_width = width
        self._video_height = height
        gbytes = GLib.Bytes.new(raw)
        self._texture = Gdk.MemoryTexture.new(
            width,
            height,
            Gdk.MemoryFormat.B8G8R8X8,
            gbytes,
            stride,
        )
        if size_changed:
            self.invalidate_size()
        self.invalidate_contents()
        return False

    def _on_appsink_sample(self, sink: Any) -> Any:
        self._handle_appsink_sample(sink.emit("pull-sample"))
        return _Gst.FlowReturn.OK

    def _on_appsink_preroll(self, sink: Any) -> Any:
        self._handle_appsink_sample(sink.emit("pull-preroll"))
        return _Gst.FlowReturn.OK

    # ------------------------------------------------------------------
    # Bus handling
    # ------------------------------------------------------------------

    def _refresh_stream_info(self) -> None:
        if self._pipeline is None or _Gst is None:
            return
        ok_dur, dur_ns = self._pipeline.query_duration(_Gst.Format.TIME)
        if ok_dur and dur_ns > 0:
            self._duration_us = int(dur_ns // 1000)
        try:
            n_audio = self._pipeline.get_property("n-audio")
            self._has_audio = bool(n_audio and n_audio > 0)
        except Exception:
            self._has_audio = False
        try:
            n_video = self._pipeline.get_property("n-video")
            self._has_video = bool(n_video and n_video > 0)
        except Exception:
            self._has_video = True

    def _clear_seek_timeout(self) -> None:
        if self._seek_timeout_id is not None:
            try:
                GLib.source_remove(self._seek_timeout_id)
            except Exception:
                pass
            self._seek_timeout_id = None

    def _finish_seeking(self) -> None:
        self._clear_seek_timeout()
        if self._seeking:
            self._seeking = False
            self.notify("seeking")

    def _on_bus_message(self, _bus: Any, msg: Any) -> None:
        if self._closed or _Gst is None:
            return

        mtype = msg.type
        if mtype == _Gst.MessageType.ASYNC_DONE:
            # Flush any pending preroll frame synchronously so dimensions are ready before notify::prepared
            self._apply_pending_frame()
            self._refresh_stream_info()
            if self._pipeline is not None:
                ok_pos, pos_ns = self._pipeline.query_position(_Gst.Format.TIME)
                if ok_pos and pos_ns >= 0:
                    self._last_timestamp_us = int(pos_ns // 1000)
            if not self._prepared:
                self._prepared = True
                self.notify("prepared")
            if self._seeking:
                self._finish_seeking()
            self.invalidate_size()
            self.invalidate_contents()

        elif mtype == _Gst.MessageType.STREAM_COLLECTION:
            collection = msg.parse_stream_collection()
            if collection is not None:
                has_a = False
                has_v = False
                for i in range(collection.get_size()):
                    st = collection.get_stream(i)
                    stype = st.get_stream_type()
                    if stype & _Gst.StreamType.AUDIO:
                        has_a = True
                    if stype & _Gst.StreamType.VIDEO:
                        has_v = True
                self._has_audio = has_a
                self._has_video = has_v

        elif mtype == _Gst.MessageType.DURATION_CHANGED:
            self._refresh_stream_info()

        elif mtype == _Gst.MessageType.EOS:
            self._finish_seeking()
            self._ended = True
            was_playing = self._playing
            self._playing = False
            if self._pipeline is not None:
                self._pipeline.set_state(_Gst.State.PAUSED)
            if was_playing:
                self.notify("playing")
            self.notify("ended")

        elif mtype == _Gst.MessageType.ERROR:
            err, _dbg = msg.parse_error()
            self._error = GLib.Error(err.message if err else "GStreamer playback error")
            self._finish_seeking()
            if self._playing:
                self._playing = False
                self.notify("playing")

    # ------------------------------------------------------------------
    # Gtk.MediaFile / Gtk.MediaStream API
    # ------------------------------------------------------------------

    def is_prepared(self) -> bool:
        return self._prepared

    def get_playing(self) -> bool:
        return self._playing

    def get_ended(self) -> bool:
        return self._ended

    def get_timestamp(self) -> int:
        if self._pipeline is not None and _Gst is not None and not self._seeking:
            ok, pos_ns = self._pipeline.query_position(_Gst.Format.TIME)
            if ok and pos_ns >= 0:
                self._last_timestamp_us = int(pos_ns // 1000)
        return self._last_timestamp_us

    def get_duration(self) -> int:
        return self._duration_us

    def has_audio(self) -> bool:
        return self._has_audio

    def has_video(self) -> bool:
        return self._has_video

    def get_volume(self) -> float:
        return self._volume

    def set_volume(self, volume: float) -> None:
        self._volume = max(0.0, float(volume))
        if self._pipeline is not None:
            try:
                self._pipeline.set_property("volume", self._volume)
            except Exception:
                pass
        self.notify("volume")

    def get_muted(self) -> bool:
        return self._muted

    def set_muted(self, muted: bool) -> None:
        self._muted = bool(muted)
        if self._pipeline is not None:
            try:
                self._pipeline.set_property("mute", self._muted)
            except Exception:
                pass
        self.notify("muted")

    def get_error(self) -> GLib.Error | None:
        return self._error

    def play(self) -> None:
        if self._closed or self._pipeline is None or _Gst is None:
            return
        if self._ended:
            self._ended = False
            self.notify("ended")
        self._playing = True
        self._pipeline.set_state(_Gst.State.PLAYING)
        self.notify("playing")

    def pause(self) -> None:
        if self._closed or self._pipeline is None or _Gst is None:
            return
        was_playing = self._playing
        self._playing = False
        self._pipeline.set_state(_Gst.State.PAUSED)
        if was_playing:
            self.notify("playing")

    def seek(self, timestamp_us: int) -> None:
        if self._closed or self._pipeline is None or _Gst is None or not self._prepared:
            return

        target_us = max(0, int(timestamp_us))
        if self._duration_us > 0:
            target_us = min(target_us, self._duration_us)
        target_ns = target_us * 1000

        if self._ended:
            self._ended = False
            self.notify("ended")

        self._last_timestamp_us = target_us
        was_seeking = self._seeking
        self._seeking = True
        if not was_seeking:
            self.notify("seeking")

        self._clear_seek_timeout()

        def _on_seek_timeout() -> bool:
            self._seek_timeout_id = None
            if not self._closed and self._seeking:
                self._finish_seeking()
            return False

        self._seek_timeout_id = GLib.timeout_add(1200, _on_seek_timeout)

        flags = _Gst.SeekFlags.FLUSH | _Gst.SeekFlags.ACCURATE
        ok = self._pipeline.seek_simple(_Gst.Format.TIME, flags, target_ns)
        if not ok:
            self._finish_seeking()

    def close(self) -> None:
        """Stop pipeline and release GStreamer and signal resources."""
        if self._closed:
            return
        with self._frame_lock:
            self._closed = True
            self._pending_frame = None
        self._clear_seek_timeout()
        self._playing = False
        self._seeking = False

        if self._bus is not None:
            if self._bus_watch_id is not None:
                try:
                    self._bus.disconnect(self._bus_watch_id)
                except Exception:
                    pass
                self._bus_watch_id = None
            try:
                self._bus.remove_signal_watch()
            except Exception:
                pass
            self._bus = None

        if self._pipeline is not None and _Gst is not None:
            try:
                self._pipeline.set_state(_Gst.State.NULL)
            except Exception:
                pass
            self._pipeline = None

        self._texture = None

    # ------------------------------------------------------------------
    # Gdk.Paintable virtual methods
    # ------------------------------------------------------------------

    def do_get_intrinsic_width(self) -> int:
        if self._texture is not None:
            return self._texture.get_width()
        return self._video_width

    def do_get_intrinsic_height(self) -> int:
        if self._texture is not None:
            return self._texture.get_height()
        return self._video_height

    def do_get_intrinsic_aspect_ratio(self) -> float:
        w = self.do_get_intrinsic_width()
        h = self.do_get_intrinsic_height()
        return (float(w) / float(h)) if (w > 0 and h > 0) else 0.0

    def do_snapshot(self, snapshot: Gtk.Snapshot, width: float, height: float) -> None:
        if width <= 0 or height <= 0 or self._texture is None:
            return
        rect = Graphene.Rect().init(0, 0, float(width), float(height))
        snapshot.append_texture(self._texture, rect)


def create_media_file(path: Path | str) -> Any:
    """Create a Gtk.MediaFile or GstMediaFile fallback when GTK4 lacks a media backend."""
    if has_gtk_media_backend():
        mf = Gtk.MediaFile.new_for_filename(str(path))
        if type(mf).__name__ != "GtkNoMediaFile":
            return mf
    return GstMediaFile.new_for_filename(str(path))
