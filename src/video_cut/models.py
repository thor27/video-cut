"""Data models representing video segments and the project timeline with Undo/Redo."""

from __future__ import annotations

import math
from dataclasses import Field, dataclass, field, fields, replace
from pathlib import Path

from .utils import clamp, generate_id

OVERLAY_ITEM_TYPES = frozenset(
    {"text", "rect", "square", "circle", "image", "svg", "gif", "video", "audio", "emoji"}
)
_OPTIONAL_STR_FIELDS = frozenset({"bg_color", "media_path"})
_OPTIONAL_NUMBER_FIELDS = frozenset({"end_time", "audio_end"})


def _is_finite_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _accepts_overlay_value(f: Field, value: object) -> bool:
    if value is None:
        return f.name in _OPTIONAL_STR_FIELDS or f.name in _OPTIONAL_NUMBER_FIELDS
    if f.name in _OPTIONAL_STR_FIELDS:
        return isinstance(value, str)
    if f.name in _OPTIONAL_NUMBER_FIELDS:
        return _is_finite_number(value)
    default = f.default
    if isinstance(default, bool):
        return isinstance(value, bool)
    if isinstance(default, int):
        return isinstance(value, int) and not isinstance(value, bool)
    if isinstance(default, float):
        return _is_finite_number(value)
    if isinstance(default, str):
        return isinstance(value, str)
    return False


@dataclass
class OverlayItem:
    """Represents a graphic, text, shape, image, sticker, or video overlay on a video segment."""

    id: str = field(default_factory=generate_id)
    item_type: str = "text"  # "text", "rect", "circle", "image", "svg", "gif", "video"
    x: float = 0.5  # Center X normalized (0.0 to 1.0)
    y: float = 0.5  # Center Y normalized (0.0 to 1.0)
    width: float = 0.3  # Width normalized (0.0 to 1.0)
    height: float = 0.2  # Height normalized (0.0 to 1.0)
    rotation: float = 0.0  # Angle in degrees (0.0 to 360.0)
    opacity: float = 1.0  # 0.0 to 1.0
    z_index: int = 0

    # Text attributes
    text: str = ""
    font_family: str = "Sans"
    font_size: int = 36
    font_color: str = "#ffffff"
    bold: bool = False
    italic: bool = False
    bg_color: str | None = None
    text_align: str = "center"  # "left", "center", "right"
    text_valign: str = "middle"  # "top", "middle", "bottom"

    # Shape attributes
    fill_color: str = "#ff5500"
    border_color: str = "#ffffff"
    border_width: int = 3
    corner_radius: float = 0.0  # Normalized (0.0 to 0.5)

    # Media attributes
    media_path: Path | str | None = None
    is_loop: bool = True

    # Timing attributes (relative to segment start)
    start_time: float = 0.0
    end_time: float | None = None  # None = entire segment duration

    # Transform constraints
    keep_aspect_ratio: bool = True

    # Audio attributes (for video overlays and audio elements)
    enable_audio: bool = False  # For video overlay: mix video audio into export
    audio_start: float = 0.0    # For audio element: start point in source audio file
    audio_end: float | None = None  # For audio element: finish point in source audio file
    audio_volume: float = 1.0   # Audio volume multiplier (0.0 to 2.0, default 1.0 = 100%)

    @classmethod
    def from_dict(cls, data: object) -> OverlayItem | None:
        """Build an item from an untrusted mapping such as clipboard JSON.

        Unknown keys are dropped, a value of the wrong type rejects the whole
        item, the item type must be one the editor knows, and the id is always
        regenerated.
        """
        if not isinstance(data, dict):
            return None
        kwargs: dict[str, object] = {}
        for f in fields(cls):
            if f.name == "id" or f.name not in data:
                continue
            value = data[f.name]
            if not _accepts_overlay_value(f, value):
                return None
            kwargs[f.name] = value
        if kwargs.get("item_type", "text") not in OVERLAY_ITEM_TYPES:
            return None
        try:
            return cls(**kwargs)
        except (TypeError, ValueError):
            return None

    def __post_init__(self) -> None:
        if self.media_path is not None:
            self.media_path = Path(self.media_path)
        self.rotation = float(self.rotation) % 360.0
        self.opacity = clamp(float(self.opacity), 0.0, 1.0)
        self.width = max(0.01, float(self.width))
        self.height = max(0.01, float(self.height))
        self.audio_start = max(0.0, float(self.audio_start))
        self.audio_volume = clamp(float(self.audio_volume), 0.0, 2.0)
        if self.text_align not in ("left", "center", "right"):
            self.text_align = "center"
        if self.text_valign not in ("top", "middle", "bottom"):
            self.text_valign = "middle"

    def copy(self) -> OverlayItem:
        """Create an exact copy with identical ID."""
        return replace(self)

    def clone(self) -> OverlayItem:
        """Create a duplicate with a new ID."""
        item = self.copy()
        item.id = generate_id()
        return item


@dataclass
class Segment:
    """Represents a cut portion of a video file."""

    source_path: Path
    source_duration: float
    start_time: float = 0.0
    end_time: float = 0.0
    id: str = field(default_factory=generate_id)
    thumbnails: list[Path] = field(default_factory=list)
    display_name: str | None = None
    crop: tuple[float, float, float, float] | None = None
    rotation: int = 0
    hflip: bool = False
    vflip: bool = False
    orig_image_path: Path | None = None
    overlays: list[OverlayItem] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.source_path = Path(self.source_path)
        if self.orig_image_path is not None:
            self.orig_image_path = Path(self.orig_image_path)
        if self.end_time <= 0.0:
            self.end_time = self.source_duration
        self.start_time = clamp(self.start_time, 0.0, self.source_duration)
        self.end_time = clamp(self.end_time, self.start_time, self.source_duration)
        self.rotation = int(self.rotation) % 360
        self.overlays = [item.copy() if hasattr(item, "copy") else item for item in self.overlays]

    @property
    def duration(self) -> float:
        """Duration of this segment in seconds."""
        return max(0.0, self.end_time - self.start_time)

    @property
    def source_name(self) -> str:
        """Filename of the source video or custom display name."""
        if self.display_name:
            return self.display_name
        return self.source_path.name

    @property
    def stem(self) -> str:
        """Stem (filename without extension) of the source or custom display name."""
        return Path(self.source_name).stem

    @property
    def has_transform(self) -> bool:
        """Return True if any crop, rotation, or flip transform is active."""
        has_crop = self.crop is not None and not (
            abs(self.crop[0]) < 1e-4 and abs(self.crop[1]) < 1e-4 and
            abs(self.crop[2] - 1.0) < 1e-4 and abs(self.crop[3] - 1.0) < 1e-4
        )
        return has_crop or (self.rotation % 360 != 0) or self.hflip or self.vflip

    @property
    def has_overlays(self) -> bool:
        """Return True if this segment has any overlay items."""
        return len(self.overlays) > 0

    def clone(self, new_start: float | None = None, new_end: float | None = None) -> Segment:
        """Create a new segment copy with a new ID."""
        is_trimmed = (new_start is not None) or (new_end is not None)
        return Segment(
            source_path=self.source_path,
            source_duration=self.source_duration,
            start_time=self.start_time if new_start is None else new_start,
            end_time=self.end_time if new_end is None else new_end,
            id=generate_id(),
            thumbnails=[] if is_trimmed else list(self.thumbnails),
            display_name=self.display_name,
            crop=self.crop,
            rotation=self.rotation,
            hflip=self.hflip,
            vflip=self.vflip,
            orig_image_path=self.orig_image_path,
            overlays=[item.clone() for item in self.overlays],
        )

    def copy(self) -> Segment:
        """Create an exact copy with identical ID."""
        return Segment(
            source_path=self.source_path,
            source_duration=self.source_duration,
            start_time=self.start_time,
            end_time=self.end_time,
            id=self.id,
            thumbnails=list(self.thumbnails),
            display_name=self.display_name,
            crop=self.crop,
            rotation=self.rotation,
            hflip=self.hflip,
            vflip=self.vflip,
            orig_image_path=self.orig_image_path,
            overlays=[item.copy() for item in self.overlays],
        )


@dataclass
class TimelineSnapshot:
    """State snapshot for Undo and Redo operations."""

    segments: list[Segment]
    selected_segment_id: str | None
    global_time: float
    selected_segment_ids: set[str] = field(default_factory=set)


class ProjectTimeline:
    """Manages the ordered sequence of video segments on the timeline with Undo/Redo."""

    def __init__(self) -> None:
        self.segments: list[Segment] = []
        self.selected_segment_id: str | None = None
        self._selected_segment_ids: set[str] = set()
        self.undo_stack: list[TimelineSnapshot] = []
        self.redo_stack: list[TimelineSnapshot] = []

    @property
    def selected_segment_ids(self) -> set[str]:
        if not self._selected_segment_ids and self.selected_segment_id:
            return {self.selected_segment_id}
        return self._selected_segment_ids

    @selected_segment_ids.setter
    def selected_segment_ids(self, ids: set[str] | list[str]) -> None:
        self._selected_segment_ids = set(ids)
        if self._selected_segment_ids:
            if self.selected_segment_id not in self._selected_segment_ids:
                for seg in self.segments:
                    if seg.id in self._selected_segment_ids:
                        self.selected_segment_id = seg.id
                        break
        else:
            self.selected_segment_id = None

    @property
    def is_empty(self) -> bool:
        return len(self.segments) == 0

    @property
    def total_duration(self) -> float:
        return sum(seg.duration for seg in self.segments)

    @property
    def can_undo(self) -> bool:
        return len(self.undo_stack) > 0

    @property
    def can_redo(self) -> bool:
        return len(self.redo_stack) > 0

    def save_state(self, global_time: float) -> None:
        """Save current timeline state to the undo stack."""
        snapshot = TimelineSnapshot(
            segments=[seg.copy() for seg in self.segments],
            selected_segment_id=self.selected_segment_id,
            global_time=global_time,
            selected_segment_ids=set(self.selected_segment_ids),
        )
        self.undo_stack.append(snapshot)
        if len(self.undo_stack) > 50:
            self.undo_stack.pop(0)
        self.redo_stack.clear()

    def undo(self, current_global_time: float) -> float | None:
        """Undo last timeline change. Returns restored global_time or None."""
        if not self.can_undo:
            return None

        current_snap = TimelineSnapshot(
            segments=[seg.copy() for seg in self.segments],
            selected_segment_id=self.selected_segment_id,
            global_time=current_global_time,
            selected_segment_ids=set(self.selected_segment_ids),
        )
        self.redo_stack.append(current_snap)

        target = self.undo_stack.pop()
        self.segments = [seg.copy() for seg in target.segments]
        self.selected_segment_id = target.selected_segment_id
        self._selected_segment_ids = set(target.selected_segment_ids)
        return target.global_time

    def redo(self, current_global_time: float) -> float | None:
        """Redo previously undone timeline change. Returns restored global_time or None."""
        if not self.can_redo:
            return None

        current_snap = TimelineSnapshot(
            segments=[seg.copy() for seg in self.segments],
            selected_segment_id=self.selected_segment_id,
            global_time=current_global_time,
            selected_segment_ids=set(self.selected_segment_ids),
        )
        self.undo_stack.append(current_snap)

        target = self.redo_stack.pop()
        self.segments = [seg.copy() for seg in target.segments]
        self.selected_segment_id = target.selected_segment_id
        self._selected_segment_ids = set(target.selected_segment_ids)
        return target.global_time

    def get_segment(self, segment_id: str) -> Segment | None:
        for seg in self.segments:
            if seg.id == segment_id:
                return seg
        return None

    def get_segment_index(self, segment_id: str) -> int:
        for idx, seg in enumerate(self.segments):
            if seg.id == segment_id:
                return idx
        return -1

    def get_segment_global_start(self, segment_index: int) -> float:
        """Return the global timeline start time for the segment at index."""
        if segment_index < 0 or segment_index >= len(self.segments):
            return 0.0
        return sum(self.segments[i].duration for i in range(segment_index))

    def map_global_time(self, global_time: float) -> tuple[int, Segment, float]:
        """
        Map a global timeline timestamp to (segment_index, segment, source_timestamp).
        If timeline is empty, raises ValueError.
        """
        if not self.segments:
            raise ValueError("Timeline is empty")

        global_time = clamp(global_time, 0.0, self.total_duration)
        accumulated = 0.0

        for idx, seg in enumerate(self.segments):
            next_accumulated = accumulated + seg.duration
            if global_time < next_accumulated or idx == len(self.segments) - 1:
                offset_in_seg = global_time - accumulated
                source_ts = clamp(seg.start_time + offset_in_seg, seg.start_time, seg.end_time)
                return idx, seg, source_ts
            accumulated = next_accumulated

        last_seg = self.segments[-1]
        return len(self.segments) - 1, last_seg, last_seg.end_time

    def split_at(
        self, global_time: float, min_segment_duration: float = 0.05
    ) -> tuple[Segment, Segment] | None:
        """
        Split the active segment at global_time into two segments.
        Returns (left_segment, right_segment) or None if cut position is invalid.
        """
        if not self.segments:
            return None

        idx, seg, source_ts = self.map_global_time(global_time)

        left_duration = source_ts - seg.start_time
        right_duration = seg.end_time - source_ts

        if left_duration < min_segment_duration or right_duration < min_segment_duration:
            return None

        right_seg = seg.clone(new_start=source_ts, new_end=seg.end_time)
        seg.end_time = source_ts
        seg.thumbnails = []

        self.segments.insert(idx + 1, right_seg)
        self.selected_segment_id = right_seg.id
        return seg, right_seg

    def delete_segment(self, segment_id: str, allow_last: bool = True) -> bool:
        """Remove a segment by ID. Returns True if removed. Allows deleting the last segment when allow_last=True."""
        if len(self.segments) <= 1 and not allow_last:
            return False

        idx = self.get_segment_index(segment_id)
        if idx == -1:
            return False

        del self.segments[idx]

        if self.selected_segment_id == segment_id:
            if self.segments:
                new_idx = min(idx, len(self.segments) - 1)
                self.selected_segment_id = self.segments[new_idx].id
            else:
                self.selected_segment_id = None
        self._selected_segment_ids.discard(segment_id)
        return True

    def delete_segments(self, segment_ids: set[str] | list[str], allow_last: bool = True) -> int:
        """Remove multiple segments by ID. Returns count of removed segments."""
        ids_to_del = set(segment_ids)
        if not ids_to_del:
            return 0
        if len(self.segments) <= len(ids_to_del) and not allow_last:
            return 0

        orig_count = len(self.segments)
        self.segments = [seg for seg in self.segments if seg.id not in ids_to_del]
        removed_count = orig_count - len(self.segments)

        self._selected_segment_ids = {sid for sid in self._selected_segment_ids if sid not in ids_to_del}
        if self.selected_segment_id in ids_to_del:
            if self._selected_segment_ids:
                for seg in self.segments:
                    if seg.id in self._selected_segment_ids:
                        self.selected_segment_id = seg.id
                        break
            elif self.segments:
                self.selected_segment_id = self.segments[-1].id
            else:
                self.selected_segment_id = None
        return removed_count

    def reorder(self, from_index: int, to_index: int) -> bool:
        """Move segment from from_index to to_index."""
        if not (0 <= from_index < len(self.segments)) or not (0 <= to_index < len(self.segments)):
            return False
        if from_index == to_index:
            return True

        seg = self.segments.pop(from_index)
        self.segments.insert(to_index, seg)
        return True

    def move_left(self, segment_id: str) -> bool:
        """Move segment one slot to the left (earlier)."""
        idx = self.get_segment_index(segment_id)
        if idx > 0:
            return self.reorder(idx, idx - 1)
        return False

    def move_right(self, segment_id: str) -> bool:
        """Move segment one slot to the right (later)."""
        idx = self.get_segment_index(segment_id)
        if 0 <= idx < len(self.segments) - 1:
            return self.reorder(idx, idx + 1)
        return False

    def add_video(
        self,
        source_path: Path | str,
        source_duration: float,
        insert_after_id: str | None = None,
        display_name: str | None = None,
        orig_image_path: Path | str | None = None,
    ) -> Segment:
        """Add a whole video or converted image file as a new segment."""
        seg = Segment(
            source_path=Path(source_path),
            source_duration=source_duration,
            start_time=0.0,
            end_time=source_duration,
            display_name=display_name,
            orig_image_path=Path(orig_image_path) if orig_image_path else None,
        )

        if insert_after_id:
            idx = self.get_segment_index(insert_after_id)
            if idx != -1:
                self.segments.insert(idx + 1, seg)
                self.selected_segment_id = seg.id
                return seg

        self.segments.append(seg)
        self.selected_segment_id = seg.id
        return seg

    def paste_segment(
        self,
        segment: Segment,
        after_segment_id: str | None = None,
    ) -> Segment:
        """Paste a cloned segment after after_segment_id (or at the end)."""
        new_seg = segment.clone()
        if after_segment_id:
            idx = self.get_segment_index(after_segment_id)
            if idx != -1:
                self.segments.insert(idx + 1, new_seg)
                self.selected_segment_id = new_seg.id
                return new_seg

        self.segments.append(new_seg)
        self.selected_segment_id = new_seg.id
        return new_seg

    def paste_segments(
        self,
        segments: list[Segment],
        after_segment_id: str | None = None,
    ) -> list[Segment]:
        """Paste multiple cloned segments in sequence after after_segment_id."""
        if not segments:
            return []
        cloned = [seg.clone() for seg in segments]
        insert_idx = len(self.segments)
        if after_segment_id:
            idx = self.get_segment_index(after_segment_id)
            if idx != -1:
                insert_idx = idx + 1
        for i, s in enumerate(cloned):
            self.segments.insert(insert_idx + i, s)
        self.selected_segment_ids = {s.id for s in cloned}
        return cloned

    def clear(self) -> None:
        self.segments.clear()
        self.selected_segment_id = None
        self._selected_segment_ids.clear()
        self.undo_stack.clear()
        self.redo_stack.clear()


@dataclass
class ExportSettings:
    """Configurable parameters for exporting video."""

    container: str = "mp4"           # mp4, mkv, webm, mov
    video_codec: str = "libx264"     # libx264, libx265, libvpx-vp9, libsvtav1, prores_ks
    crf: int = 21                    # 18, 21, 26
    preset: str = "veryfast"         # ultrafast, veryfast, fast, medium, slow
    target_width: int | None = None
    target_height: int | None = None
    fps: float | None = None      # 24, 30, 60 or None for original
    audio_codec: str = "aac"         # aac, libopus, libmp3lame, flac
    audio_bitrate: str = "192k"      # 128k, 192k, 256k, 320k

