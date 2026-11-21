"""Video playback and timeline synchronization controller using Gtk.Picture and Gtk.MediaFile."""

import os
import sys
import time
from collections.abc import Callable
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, GLib, GObject, Graphene, Gsk, Gtk

from .models import OverlayItem, ProjectTimeline, Segment
from .services.overlay_audio_player import OverlayAudioPlayer
from .services.overlay_service import OverlayRenderService
from .utils import clamp

# Playback debug logging, enabled with VIDEO_CUT_DEBUG=1
_DEBUG = bool(os.environ.get("VIDEO_CUT_DEBUG"))
_T0 = time.monotonic()


def _dbg(msg: str) -> None:
    if _DEBUG:
        print(f"[player {time.monotonic() - _T0:8.3f}] {msg}", file=sys.stderr, flush=True)


def _mf_state(mf: Gtk.MediaFile | None) -> str:
    """Compact description of a media file's state for debug logs."""
    if mf is None:
        return "mf=None"
    try:
        err = mf.get_error()
        return (
            f"mf#{hash(mf) & 0xFFFF:04x}(prep={int(mf.is_prepared())} play={int(mf.get_playing())} "
            f"seek={int(mf.get_property('seeking'))} end={int(mf.get_ended())} "
            f"ts={mf.get_timestamp() / 1e6:.3f} audio={int(mf.has_audio())} "
            f"vol={mf.get_volume():.2f} mute={int(mf.get_muted())}"
            f"{' ERR=' + err.message if err else ''})"
        )
    except Exception as e:  # pragma: no cover - debug only
        return f"mf=<state error {e}>"


class TransformedPaintable(GObject.Object, Gdk.Paintable):
    """Wraps a Gdk.Paintable (such as Gtk.MediaFile) to apply crop, rotation, and flip in real-time."""

    def __init__(
        self,
        base: Gdk.Paintable,
        rotation: int = 0,
        hflip: bool = False,
        vflip: bool = False,
        crop: tuple[float, float, float, float] | None = None,
        overlays: list[OverlayItem] | None = None,
        get_current_time: Callable[[], float] | None = None,
    ) -> None:
        super().__init__()
        self.base = base
        self.rotation = rotation % 360
        self.hflip = hflip
        self.vflip = vflip
        self.crop = crop or (0.0, 0.0, 1.0, 1.0)
        self.overlays: list[OverlayItem] = list(overlays) if overlays else []
        self.get_current_time = get_current_time

        self._signal_handlers: list[int] = []
        try:
            self._signal_handlers.append(
                self.base.connect("invalidate-contents", self._on_base_invalidate_contents)
            )
            self._signal_handlers.append(
                self.base.connect("invalidate-size", self._on_base_invalidate_size)
            )
        except Exception:
            pass

    def detach(self) -> None:
        """Disconnect signals from the underlying base paintable."""
        if hasattr(self, "_signal_handlers") and self._signal_handlers:
            for hid in self._signal_handlers:
                try:
                    self.base.disconnect(hid)
                except Exception:
                    pass
            self._signal_handlers.clear()

    def update_transform(
        self,
        rotation: int = 0,
        hflip: bool = False,
        vflip: bool = False,
        crop: tuple[float, float, float, float] | None = None,
        overlays: list[OverlayItem] | None = None,
        get_current_time: Callable[[], float] | None = None,
    ) -> None:
        """Update transformation parameters and trigger size & content recalculation."""
        self.rotation = rotation % 360
        self.hflip = hflip
        self.vflip = vflip
        self.crop = crop or (0.0, 0.0, 1.0, 1.0)
        if overlays is not None:
            self.overlays = list(overlays)
        if get_current_time is not None:
            self.get_current_time = get_current_time
        self.invalidate_size()
        self.invalidate_contents()

    def _on_base_invalidate_contents(self, *args) -> None:
        self.invalidate_contents()

    def _on_base_invalidate_size(self, *args) -> None:
        self.invalidate_size()

    def _get_oriented_dims(self) -> tuple[float, float]:
        bw = self.base.get_intrinsic_width()
        bh = self.base.get_intrinsic_height()
        if bw <= 0 or bh <= 0:
            return 1920.0, 1080.0
        if self.rotation in (90, 270):
            return float(bh), float(bw)
        return float(bw), float(bh)

    def do_get_intrinsic_width(self) -> int:
        ow, _ = self._get_oriented_dims()
        cw = self.crop[2] if self.crop else 1.0
        return max(1, int(round(ow * cw)))

    def do_get_intrinsic_height(self) -> int:
        _, oh = self._get_oriented_dims()
        ch = self.crop[3] if self.crop else 1.0
        return max(1, int(round(oh * ch)))

    def do_get_intrinsic_aspect_ratio(self) -> float:
        w = self.do_get_intrinsic_width()
        h = self.do_get_intrinsic_height()
        return (w / h) if h > 0 else 0.0

    def do_snapshot(self, snapshot: Gtk.Snapshot, width: float, height: float) -> None:
        bw = self.base.get_intrinsic_width()
        bh = self.base.get_intrinsic_height()
        if bw <= 0 or bh <= 0 or width <= 0 or height <= 0:
            return

        rot = self.rotation % 360
        if rot in (90, 270):
            rw, rh = float(bh), float(bw)
        else:
            rw, rh = float(bw), float(bh)

        ow, oh = rw, rh
        cx, cy, cw, ch = self.crop if self.crop else (0.0, 0.0, 1.0, 1.0)
        cx_px = cx * ow
        cy_px = cy * oh
        cw_px = max(1.0, cw * ow)
        ch_px = max(1.0, ch * oh)

        sx = width / cw_px
        sy = height / ch_px

        rect = Graphene.Rect().init(0, 0, float(width), float(height))
        snapshot.push_clip(rect)
        try:
            tf = Gsk.Transform.new()
            tf = tf.scale(sx, sy)
            tf = tf.translate(Graphene.Point().init(-cx_px, -cy_px))

            if self.hflip and self.vflip:
                tf = tf.translate(Graphene.Point().init(rw, rh))
                tf = tf.scale(-1, -1)
            elif self.hflip:
                tf = tf.translate(Graphene.Point().init(rw, 0))
                tf = tf.scale(-1, 1)
            elif self.vflip:
                tf = tf.translate(Graphene.Point().init(0, rh))
                tf = tf.scale(1, -1)

            if rot == 90:
                tf = tf.translate(Graphene.Point().init(bh, 0))
                tf = tf.rotate(90)
            elif rot == 180:
                tf = tf.translate(Graphene.Point().init(bw, bh))
                tf = tf.rotate(180)
            elif rot == 270:
                tf = tf.translate(Graphene.Point().init(0, bw))
                tf = tf.rotate(270)

            snapshot.transform(tf)
            self.base.snapshot(snapshot, float(bw), float(bh))
        finally:
            snapshot.pop()

        if self.overlays:
            curr_t = self.get_current_time() if self.get_current_time else 0.0
            tex = OverlayRenderService.render_overlays_to_texture(
                self.overlays,
                int(round(width)),
                int(round(height)),
                current_time=curr_t,
                include_audio=False,
            )
            if tex is not None:
                overlay_rect = Graphene.Rect().init(0, 0, float(width), float(height))
                snapshot.append_texture(tex, overlay_rect)


class VideoPlayerController:
    """Manages video playback and synchronization across multi-segment timeline without hover controls."""

    def __init__(
        self,
        timeline: ProjectTimeline,
        video_picture: Gtk.Picture,
        on_time_update: Callable[[float], None],
        on_playback_state_changed: Callable[[bool], None],
    ) -> None:
        self.timeline = timeline
        self.video_picture = video_picture
        self.on_time_update = on_time_update
        self.on_playback_state_changed = on_playback_state_changed

        self.current_media_file: Gtk.MediaFile | None = None
        self.current_transformed_paintable: TransformedPaintable | None = None
        self.current_source_path: Path | None = None
        self.current_segment_id: str | None = None
        self.current_global_time: float = 0.0

        self._is_playing: bool = False
        self._tick_timer_id: int | None = None
        self._last_tick_time: float = 0.0

        self._is_seeking: bool = False
        self._last_seek_time: float = 0.0
        self._play_on_seek_done: bool = False
        self._media_file_handlers: list[int] = []
        self._overlay_audio_player: OverlayAudioPlayer = OverlayAudioPlayer()

    @property
    def is_playing(self) -> bool:
        return self._is_playing

    def _cleanup_media_file(self) -> None:
        """Safely disconnect signal handlers and release media file."""
        if self.current_media_file:
            if self.video_picture and self.video_picture.get_paintable() == self.current_media_file:
                self.video_picture.set_paintable(None)
            for hid in self._media_file_handlers:
                try:
                    self.current_media_file.disconnect(hid)
                except Exception:
                    pass
            self._media_file_handlers.clear()
            self.current_media_file = None

    def clear(self) -> None:
        """Clear playback and reset picture paintable."""
        self.pause()
        self.current_global_time = 0.0
        self.current_source_path = None
        self.current_segment_id = None
        self._is_seeking = False
        self._play_on_seek_done = False
        if self.video_picture and self.video_picture.get_paintable() is not None:
            self.video_picture.set_paintable(None)
        if self.current_transformed_paintable:
            self.current_transformed_paintable.detach()
            self.current_transformed_paintable = None
        self._cleanup_media_file()
        if hasattr(self, "_overlay_audio_player") and self._overlay_audio_player:
            self._overlay_audio_player.cleanup()

    def load_initial(self) -> None:
        """Load the first segment in the timeline if available."""
        if not self.timeline.is_empty:
            self.seek_to_global_time(0.0)
        else:
            self.clear()

    def _get_segment_rel_time(self) -> float:
        """Return playback time relative to current segment start."""
        if self.timeline.is_empty:
            return 0.0
        try:
            _, seg, source_ts = self.timeline.map_global_time(self.current_global_time)
            return max(0.0, source_ts - seg.start_time)
        except Exception:
            return 0.0

    def _apply_segment_transform(self, seg: Segment | None = None, force: bool = False) -> None:
        """Attach or update TransformedPaintable if segment has transforms or overlays, else use raw media file."""
        if self.current_media_file is None:
            if self.current_transformed_paintable:
                self.current_transformed_paintable.detach()
                self.current_transformed_paintable = None
            if self.video_picture and self.video_picture.get_paintable() is not None:
                self.video_picture.set_paintable(None)
            return

        if seg is None:
            try:
                _, seg, _ = self.timeline.map_global_time(self.current_global_time)
            except ValueError:
                seg = None

        if seg is not None and (seg.has_transform or seg.has_overlays):
            if seg.has_overlays:
                for it in seg.overlays:
                    if it.media_path and (
                        it.item_type == "video"
                        or Path(it.media_path).suffix.lower() in (".mp4", ".webm", ".mov", ".mkv", ".avi")
                    ):
                        OverlayRenderService.get_video_clip(it.media_path)
            if (
                self.current_transformed_paintable is not None
                and self.current_transformed_paintable.base == self.current_media_file
            ):
                tp = self.current_transformed_paintable
                if (
                    force
                    or tp.rotation != (seg.rotation % 360)
                    or tp.hflip != seg.hflip
                    or tp.vflip != seg.vflip
                    or tp.crop != seg.crop
                    or tp.overlays != seg.overlays
                ):
                    tp.update_transform(
                        rotation=seg.rotation,
                        hflip=seg.hflip,
                        vflip=seg.vflip,
                        crop=seg.crop,
                        overlays=seg.overlays,
                        get_current_time=self._get_segment_rel_time,
                    )
                if self.video_picture.get_paintable() != tp:
                    self.video_picture.set_paintable(tp)
            else:
                if self.current_transformed_paintable:
                    self.current_transformed_paintable.detach()
                self.current_transformed_paintable = TransformedPaintable(
                    base=self.current_media_file,
                    rotation=seg.rotation,
                    hflip=seg.hflip,
                    vflip=seg.vflip,
                    crop=seg.crop,
                    overlays=seg.overlays,
                    get_current_time=self._get_segment_rel_time,
                )
                self.video_picture.set_paintable(self.current_transformed_paintable)
        else:
            if self.current_transformed_paintable:
                self.current_transformed_paintable.detach()
                self.current_transformed_paintable = None
            if self.video_picture.get_paintable() != self.current_media_file:
                self.video_picture.set_paintable(self.current_media_file)

    def refresh_current_transform(self) -> None:
        """Refresh transform of the current segment without restarting playback."""
        if self.timeline.is_empty:
            return
        try:
            idx, seg, source_ts = self.timeline.map_global_time(self.current_global_time)
            self.current_segment_id = seg.id
            self._apply_segment_transform(seg, force=True)
            self._seek_media_file(source_ts, force=True)
            self.on_time_update(self.current_global_time)
        except ValueError:
            pass

    def seek_to_global_time(self, global_time: float) -> None:
        """Seek player to an arbitrary global timeline timestamp."""
        if self.timeline.is_empty:
            self.clear()
            return

        self.current_global_time = clamp(global_time, 0.0, self.timeline.total_duration)

        try:
            idx, seg, source_ts = self.timeline.map_global_time(self.current_global_time)
        except ValueError:
            return

        self.current_segment_id = seg.id
        _dbg(
            f"seek_to_global_time({global_time:.3f}) -> seg#{idx} src_ts={source_ts:.3f} "
            f"playing={self._is_playing} {_mf_state(self.current_media_file)}"
        )

        if self.current_source_path != seg.source_path or self.current_media_file is None:
            self._load_file_and_seek(seg.source_path, source_ts, and_play=self._is_playing, seg=seg)
        else:
            self._apply_segment_transform(seg)
            self._seek_media_file(source_ts, force=True)

        if hasattr(self, "_overlay_audio_player") and self._overlay_audio_player:
            rel_t = max(0.0, source_ts - seg.start_time)
            self._overlay_audio_player.update_playback(
                items=seg.overlays,
                rel_time=rel_t,
                is_playing=self._is_playing,
                master_seeking=True,
            )

        self.on_time_update(self.current_global_time)

    def _load_file_and_seek(
        self,
        path: Path,
        source_ts: float,
        and_play: bool = False,
        seg: Segment | None = None,
    ) -> None:
        """Load a video file into Gtk.MediaFile and attach to Gtk.Picture."""
        _dbg(f"LOAD {path.name} src_ts={source_ts:.3f} and_play={and_play} old={_mf_state(self.current_media_file)}")
        self._cleanup_media_file()

        self.current_source_path = path
        if seg:
            self.current_segment_id = seg.id

        mf = Gtk.MediaFile.new_for_filename(str(path))
        self.current_media_file = mf
        self._apply_segment_transform(seg)

        def on_prepared(obj, pspec):
            if obj.is_prepared():
                self._seek_media_file(source_ts, force=True)
                self.on_time_update(self.current_global_time)
                if and_play and self._is_playing:
                    if obj.get_property("seeking"):
                        self._play_on_seek_done = True
                    else:
                        _dbg("mf.play() [on_prepared]")
                        obj.play()

        def on_seeking_changed(obj, pspec):
            is_seeking = obj.get_property("seeking")
            if not is_seeking:
                self._is_seeking = False
                now = time.monotonic()
                self._last_seek_time = now
                self._last_tick_time = now
                if self._play_on_seek_done:
                    self._play_on_seek_done = False
                    if self._is_playing and not obj.get_playing():
                        _dbg("mf.play() [seek done]")
                        obj.play()

        h_prep = mf.connect("notify::prepared", on_prepared)
        h_seek = mf.connect("notify::seeking", on_seeking_changed)
        self._media_file_handlers = [h_prep, h_seek]

        if _DEBUG:
            def on_any_notify(obj, pspec):
                if pspec.name != "timestamp":
                    _dbg(f"  notify::{pspec.name} -> {_mf_state(obj)}")

            self._media_file_handlers.append(mf.connect("notify", on_any_notify))

    def _seek_media_file(self, source_ts: float, force: bool = False) -> None:
        if not self.current_media_file or not self.current_media_file.is_prepared():
            _dbg(f"seek({source_ts:.3f}) SKIPPED: not prepared")
            return

        # Do not spam seek if already seeking, unless forced (e.g. user seek or segment boundary)
        if not force and (self._is_seeking or self.current_media_file.get_property("seeking")):
            _dbg(f"seek({source_ts:.3f}) SKIPPED: already seeking")
            return

        _dbg(f"mf.seek({source_ts:.3f}) force={force} before={_mf_state(self.current_media_file)}")
        self._is_seeking = True
        self._last_seek_time = time.monotonic()
        us = int(source_ts * 1_000_000)
        self.current_media_file.seek(us)

    def play(self) -> None:
        """Start playback."""
        if self.timeline.is_empty:
            self.clear()
            return

        # If already at the end, restart from beginning
        if self.current_global_time >= self.timeline.total_duration - 0.05:
            self.current_global_time = 0.0

        _dbg(f"PLAY at glob={self.current_global_time:.3f} {_mf_state(self.current_media_file)}")
        self._is_playing = True
        self._last_tick_time = time.monotonic()
        self._last_heartbeat = 0.0

        # Ensure correct media file and position
        try:
            idx, seg, source_ts = self.timeline.map_global_time(self.current_global_time)
            self.current_segment_id = seg.id
            if self.current_source_path != seg.source_path or self.current_media_file is None:
                self._load_file_and_seek(seg.source_path, source_ts, and_play=True, seg=seg)
            else:
                self._apply_segment_transform(seg)
                if self.current_media_file.is_prepared():
                    cur_stream_ts = self.current_media_file.get_timestamp() / 1_000_000.0
                    is_ended = self.current_media_file.get_ended()

                    is_seeking = self._is_seeking or self.current_media_file.get_property("seeking")
                    if is_seeking:
                        _dbg("play() while seeking -> queue play on seek done")
                        self._play_on_seek_done = True
                    elif is_ended or abs(cur_stream_ts - source_ts) > 0.2:
                        self._play_on_seek_done = True
                        self._seek_media_file(source_ts, force=True)
                    else:
                        if not self.current_media_file.get_playing():
                            _dbg("mf.play() [play]")
                            self.current_media_file.play()
                else:
                    self._play_on_seek_done = True
        except ValueError:
            pass

        if self._tick_timer_id is None:
            self._tick_timer_id = GLib.timeout_add(30, self._on_playback_tick)

        self.on_playback_state_changed(True)

    def pause(self) -> None:
        """Pause playback."""
        _dbg(f"PAUSE at glob={self.current_global_time:.3f} {_mf_state(self.current_media_file)}")
        self._is_playing = False
        self._play_on_seek_done = False
        if self._tick_timer_id is not None:
            GLib.source_remove(self._tick_timer_id)
            self._tick_timer_id = None

        if self.current_media_file:
            self.current_media_file.pause()

        if hasattr(self, "_overlay_audio_player") and self._overlay_audio_player:
            self._overlay_audio_player.pause_all()

        self.on_playback_state_changed(False)

    def toggle_playback(self) -> None:
        """Toggle between play and pause."""
        if self._is_playing:
            self.pause()
        else:
            self.play()

    def sync_timeline_change(self, new_global_time: float) -> None:
        """Called when timeline is split, reordered, or deleted."""
        was_playing = self._is_playing
        self.current_global_time = clamp(new_global_time, 0.0, self.timeline.total_duration)
        _dbg(
            f"SYNC_TIMELINE glob={self.current_global_time:.3f} was_playing={was_playing} "
            f"segs={[(round(s.start_time, 2), round(s.end_time, 2)) for s in self.timeline.segments]}"
        )

        if not self.timeline.is_empty:
            try:
                idx, seg, source_ts = self.timeline.map_global_time(self.current_global_time)
                self.current_segment_id = seg.id
                if self.current_source_path != seg.source_path or self.current_media_file is None:
                    self._load_file_and_seek(seg.source_path, source_ts, and_play=was_playing, seg=seg)
                else:
                    self._apply_segment_transform(seg)
                    self._seek_media_file(source_ts, force=True)
                    if was_playing and not self.current_media_file.get_ended():
                        _dbg("mf.play() [sync_timeline_change]")
                        self.current_media_file.play()
            except ValueError:
                pass
        else:
            self.clear()

        self.on_time_update(self.current_global_time)

    def _on_playback_tick(self) -> bool:
        """High-resolution playback loop advancing the timeline smoothly."""
        if not self._is_playing or self.timeline.is_empty:
            return False

        now = time.monotonic()

        # Check if stream is currently seeking or buffering
        is_stream_seeking = (
            self._is_seeking
            or (self.current_media_file is not None and self.current_media_file.get_property("seeking"))
        )
        if is_stream_seeking:
            # Safety timeout: if seeking for > 2.0s, clear is_seeking flag
            if now - self._last_seek_time > 2.0:
                _dbg("Seek timed out after 2s, clearing _is_seeking flag")
                self._is_seeking = False
            else:
                self._last_tick_time = now
                return True

        dt = max(0.0, now - self._last_tick_time)
        self._last_tick_time = now

        new_time = self.current_global_time + dt

        if _DEBUG and now - getattr(self, "_last_heartbeat", 0.0) >= 1.0:
            self._last_heartbeat = now
            _dbg(f"tick glob={new_time:.3f} dt={dt * 1000:.0f}ms {_mf_state(self.current_media_file)}")

        # Reached end of project timeline
        if new_time >= self.timeline.total_duration:
            _dbg("END OF TIMELINE -> pause + seek 0")
            self.pause()
            self.seek_to_global_time(0.0)
            return False

        self.current_global_time = new_time

        try:
            idx, seg, source_ts = self.timeline.map_global_time(self.current_global_time)

            # Check if active segment has changed
            if seg.id != self.current_segment_id:
                prev_id = self.current_segment_id
                self.current_segment_id = seg.id
                self._apply_segment_transform(seg)

                if seg.source_path != self.current_source_path:
                    # Switch to different source file
                    _dbg(f"SEGMENT CHANGE -> seg#{idx} (different file)")
                    self._load_file_and_seek(seg.source_path, source_ts, and_play=True, seg=seg)
                else:
                    # Same source file: check whether playback is contiguous
                    prev_seg = self.timeline.get_segment(prev_id) if prev_id else None
                    is_contiguous = (
                        prev_seg is not None
                        and prev_seg.source_path == seg.source_path
                        and abs(prev_seg.end_time - seg.start_time) < 0.05
                    )
                    _dbg(
                        f"SEGMENT CHANGE -> seg#{idx} [{seg.start_time:.2f}-{seg.end_time:.2f}] "
                        f"src_ts={source_ts:.3f} contiguous={is_contiguous} {_mf_state(self.current_media_file)}"
                    )

                    if not is_contiguous:
                        is_ended = (
                            self.current_media_file is not None
                            and self.current_media_file.get_ended()
                        )
                        if is_ended:
                            self._play_on_seek_done = True
                            self._seek_media_file(source_ts, force=True)
                        else:
                            self._seek_media_file(source_ts, force=True)
                            if (
                                self.current_media_file
                                and not self.current_media_file.get_playing()
                            ):
                                _dbg("mf.play() [segment change]")
                                self.current_media_file.play()

            elif self.current_media_file and self.current_media_file.is_prepared():
                self._apply_segment_transform(seg)

                if (
                    now - self._last_seek_time > 0.35
                    and not self.current_media_file.get_playing()
                    and not self.current_media_file.get_ended()
                ):
                    _dbg("mf.play() [tick: stream was not playing]")
                    self.current_media_file.play()

                # Align global time smoothly with the actual stream playback position
                if self.current_media_file.get_playing():
                    cur_stream_ts = self.current_media_file.get_timestamp() / 1_000_000.0
                    seg_start_global = self.timeline.get_segment_global_start(idx)
                    actual_global = seg_start_global + max(0.0, cur_stream_ts - seg.start_time)
                    drift = actual_global - self.current_global_time
                    if abs(drift) > 0.03:
                        # Gently converge timeline time to actual video frame timestamp
                        self.current_global_time += drift * 0.25

            if hasattr(self, "_overlay_audio_player") and self._overlay_audio_player:
                rel_t = max(0.0, source_ts - seg.start_time)
                self._overlay_audio_player.update_playback(
                    items=seg.overlays,
                    rel_time=rel_t,
                    is_playing=True,
                )

            if self.current_transformed_paintable and self.current_transformed_paintable.overlays:
                self.current_transformed_paintable.invalidate_contents()

            self.on_time_update(self.current_global_time)
        except ValueError:
            self.pause()
            return False

        return True

    def step(self, seconds: float, current_global_time: float) -> None:
        """Step playback backward or forward by seconds."""
        new_time = clamp(current_global_time + seconds, 0.0, self.timeline.total_duration)
        self.seek_to_global_time(new_time)

    def step_frame(self, direction: int = 1) -> None:
        """Step backward (-1) or forward (+1) by a single frame (~1/30s)."""
        frame_duration = 0.033333
        self.step(direction * frame_duration, self.current_global_time)

    def jump_to_prev_segment(self) -> None:
        """Jump playhead to start of previous segment (or 0.0 if on first segment)."""
        if self.timeline.is_empty:
            return
        _dbg(f"JUMP_PREV (Home) from glob={self.current_global_time:.3f}")
        try:
            idx, seg, _ = self.timeline.map_global_time(self.current_global_time)
            seg_start = self.timeline.get_segment_global_start(idx)
            # If we are noticeably past the start of the current segment (>0.5s), rewind to start of current segment;
            # otherwise, jump to previous segment.
            if self.current_global_time > seg_start + 0.5:
                self.seek_to_global_time(seg_start)
            elif idx > 0:
                prev_start = self.timeline.get_segment_global_start(idx - 1)
                self.seek_to_global_time(prev_start)
            else:
                self.seek_to_global_time(0.0)
        except ValueError:
            pass

    def jump_to_next_segment(self) -> None:
        """Jump playhead to start of next segment."""
        if self.timeline.is_empty:
            return
        _dbg(f"JUMP_NEXT (End) from glob={self.current_global_time:.3f}")
        try:
            idx, seg, _ = self.timeline.map_global_time(self.current_global_time)
            if idx < len(self.timeline.segments) - 1:
                next_start = self.timeline.get_segment_global_start(idx + 1)
                self.seek_to_global_time(next_start)
            else:
                self.seek_to_global_time(self.timeline.total_duration)
        except ValueError:
            pass

    def get_current_frame_pixbuf(
        self,
        renderer: Gsk.Renderer | None = None,
        max_dim: int = 1920,
    ) -> GdkPixbuf.Pixbuf | None:
        """Capture the current un-transformed raw frame from the player as a GdkPixbuf.Pixbuf."""
        if not self.current_media_file or not self.current_media_file.is_prepared():
            return None

        mf = self.current_media_file
        iw = mf.get_intrinsic_width()
        ih = mf.get_intrinsic_height()
        if iw <= 0 or ih <= 0:
            return None

        if renderer is None and self.video_picture:
            native = self.video_picture.get_native()
            if native:
                renderer = native.get_renderer()

        if renderer is None:
            return None

        target_w = iw
        target_h = ih
        if max_dim > 0 and (iw > max_dim or ih > max_dim):
            scale = min(max_dim / float(iw), max_dim / float(ih))
            target_w = max(1, int(round(iw * scale)))
            target_h = max(1, int(round(ih * scale)))

        try:
            snap = Gtk.Snapshot.new()
            mf.snapshot(snap, target_w, target_h)
            node = snap.to_node()
            if not node:
                return None
            rect = Graphene.Rect().init(0, 0, target_w, target_h)
            tex = renderer.render_texture(node, rect)
            if not tex:
                return None
            tiff_bytes = tex.save_to_tiff_bytes()
            loader = GdkPixbuf.PixbufLoader.new_with_type("tiff")
            loader.write(tiff_bytes.get_data())
            loader.close()
            return loader.get_pixbuf()
        except Exception as e:
            _dbg(f"get_current_frame_pixbuf error: {e}")
            return None
