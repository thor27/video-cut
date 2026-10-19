"""Video playback and timeline synchronization controller using Gtk.Picture and Gtk.MediaFile."""

import time
from pathlib import Path
from typing import Callable, Optional
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Gdk, Gsk, Graphene, GObject, GLib

from .models import ProjectTimeline, Segment
from .utils import clamp


class TransformedPaintable(GObject.Object, Gdk.Paintable):
    """Wraps a Gdk.Paintable (such as Gtk.MediaFile) to apply crop, rotation, and flip in real-time."""

    def __init__(
        self,
        base: Gdk.Paintable,
        rotation: int = 0,
        hflip: bool = False,
        vflip: bool = False,
        crop: Optional[tuple[float, float, float, float]] = None,
    ) -> None:
        super().__init__()
        self.base = base
        self.rotation = rotation % 360
        self.hflip = hflip
        self.vflip = vflip
        self.crop = crop or (0.0, 0.0, 1.0, 1.0)

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
        crop: Optional[tuple[float, float, float, float]] = None,
    ) -> None:
        """Update transformation parameters and trigger size & content recalculation."""
        self.rotation = rotation % 360
        self.hflip = hflip
        self.vflip = vflip
        self.crop = crop or (0.0, 0.0, 1.0, 1.0)
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

        self.current_media_file: Optional[Gtk.MediaFile] = None
        self.current_transformed_paintable: Optional[TransformedPaintable] = None
        self.current_source_path: Optional[Path] = None
        self.current_global_time: float = 0.0

        self._is_playing: bool = False
        self._tick_timer_id: Optional[int] = None
        self._last_tick_time: float = 0.0

    @property
    def is_playing(self) -> bool:
        return self._is_playing

    def clear(self) -> None:
        """Clear playback and reset picture paintable."""
        self.pause()
        self.current_global_time = 0.0
        self.current_source_path = None
        if self.current_transformed_paintable:
            self.current_transformed_paintable.detach()
            self.current_transformed_paintable = None
        if self.current_media_file:
            self.current_media_file = None
        if self.video_picture and self.video_picture.get_paintable() is not None:
            self.video_picture.set_paintable(None)

    def load_initial(self) -> None:
        """Load the first segment in the timeline if available."""
        if not self.timeline.is_empty:
            self.seek_to_global_time(0.0)
        else:
            self.clear()

    def _apply_segment_transform(self, seg: Optional[Segment] = None, force: bool = False) -> None:
        """Attach or update TransformedPaintable if segment has transforms, else use raw media file."""
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

        if seg is not None and seg.has_transform:
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
                ):
                    tp.update_transform(
                        rotation=seg.rotation,
                        hflip=seg.hflip,
                        vflip=seg.vflip,
                        crop=seg.crop,
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
            self._apply_segment_transform(seg, force=True)
            self._seek_media_file(source_ts)
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

        if self.current_source_path != seg.source_path or self.current_media_file is None:
            self._load_file_and_seek(seg.source_path, source_ts, and_play=self._is_playing, seg=seg)
        else:
            self._apply_segment_transform(seg)
            self._seek_media_file(source_ts)

        self.on_time_update(self.current_global_time)

    def _load_file_and_seek(
        self,
        path: Path,
        source_ts: float,
        and_play: bool = False,
        seg: Optional[Segment] = None,
    ) -> None:
        """Load a video file into Gtk.MediaFile and attach to Gtk.Picture."""
        if self.current_media_file:
            self.current_media_file.pause()

        self.current_source_path = path
        mf = Gtk.MediaFile.new_for_filename(str(path))
        self.current_media_file = mf
        self._apply_segment_transform(seg)

        def on_prepared(obj, pspec):
            if obj.is_prepared():
                self._seek_media_file(source_ts)
                self.on_time_update(self.current_global_time)
                if and_play and self._is_playing:
                    obj.play()

        mf.connect("notify::prepared", on_prepared)

    def _seek_media_file(self, source_ts: float) -> None:
        if self.current_media_file:
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

        self._is_playing = True
        self._last_tick_time = time.monotonic()

        # Ensure correct media file and position
        try:
            idx, seg, source_ts = self.timeline.map_global_time(self.current_global_time)
            if self.current_source_path != seg.source_path or self.current_media_file is None:
                self._load_file_and_seek(seg.source_path, source_ts, and_play=True, seg=seg)
            else:
                self._apply_segment_transform(seg)
                self._seek_media_file(source_ts)
                self.current_media_file.play()
        except ValueError:
            pass

        if self._tick_timer_id is None:
            self._tick_timer_id = GLib.timeout_add(30, self._on_playback_tick)

        self.on_playback_state_changed(True)

    def pause(self) -> None:
        """Pause playback."""
        self._is_playing = False
        if self._tick_timer_id is not None:
            GLib.source_remove(self._tick_timer_id)
            self._tick_timer_id = None

        if self.current_media_file:
            self.current_media_file.pause()

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

        if not self.timeline.is_empty:
            try:
                idx, seg, source_ts = self.timeline.map_global_time(self.current_global_time)
                if self.current_source_path != seg.source_path or self.current_media_file is None:
                    self._load_file_and_seek(seg.source_path, source_ts, and_play=was_playing, seg=seg)
                else:
                    self._apply_segment_transform(seg)
                    self._seek_media_file(source_ts)
                    if was_playing:
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
        dt = max(0.0, now - self._last_tick_time)
        self._last_tick_time = now

        new_time = self.current_global_time + dt

        # Reached end of project timeline
        if new_time >= self.timeline.total_duration:
            self.pause()
            self.seek_to_global_time(0.0)
            return False

        self.current_global_time = new_time

        try:
            idx, seg, source_ts = self.timeline.map_global_time(self.current_global_time)

            # Check if source video file needs to change
            if seg.source_path != self.current_source_path:
                self._load_file_and_seek(seg.source_path, source_ts, and_play=True, seg=seg)
            elif self.current_media_file:
                self._apply_segment_transform(seg)
                # Ensure media file is playing
                if not self.current_media_file.get_playing():
                    self.current_media_file.play()

                # Correct any drift > 0.3s (e.g. across split segments in same file)
                cur_stream_ts = self.current_media_file.get_timestamp() / 1_000_000.0
                if abs(cur_stream_ts - source_ts) > 0.35:
                    self._seek_media_file(source_ts)

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
        try:
            idx, seg, _ = self.timeline.map_global_time(self.current_global_time)
            if idx < len(self.timeline.segments) - 1:
                next_start = self.timeline.get_segment_global_start(idx + 1)
                self.seek_to_global_time(next_start)
            else:
                self.seek_to_global_time(self.timeline.total_duration)
        except ValueError:
            pass
