"""Project serialization and deserialization service for saving and loading .vcut projects."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

from gi.repository import GLib

from ..models import ProjectTimeline, Segment
from .probe_service import ProbeService

logger = logging.getLogger(__name__)

VCUT_FORMAT_IDENTIFIER = "video-cut-project"
CURRENT_PROJECT_FORMAT_VERSION = 1
RECENT_PROJECTS_FILE = "recent_projects.json"
MAX_RECENT_PROJECTS = 10


class LoadedProject(NamedTuple):
    """Result of loading a .vcut project."""

    segments: list[Segment]
    global_time: float
    selected_segment_id: str | None
    missing_files: list[str]
    snap_to_keyframe: bool = False


class ProjectService:
    """Handles saving and loading Video Cut projects (.vcut) and managing recent projects."""

    @classmethod
    def save_project(
        cls,
        project_path: Path | str,
        timeline: ProjectTimeline,
        current_global_time: float = 0.0,
        app_version: str = "devel",
    ) -> None:
        """Serialize current timeline session to a .vcut JSON file."""
        target_path = Path(project_path).resolve()
        base_dir = target_path.parent
        base_dir.mkdir(parents=True, exist_ok=True)

        now_iso = datetime.now(timezone.utc).isoformat()

        # Collect deduplicated keyframes per media file
        keyframes_map: dict[str, list[float]] = {}
        for seg in timeline.segments:
            kfs = seg.keyframes
            if not kfs and ProbeService.is_keyframes_cached(seg.source_path):
                kfs = ProbeService.get_keyframe_timestamps(seg.source_path)
                seg.keyframes = list(kfs)
            if kfs:
                kfs_clean = [round(float(k), 6) for k in kfs]
                try:
                    rel_key = str(seg.source_path.relative_to(base_dir))
                except ValueError:
                    rel_key = str(seg.source_path)
                keyframes_map[rel_key] = kfs_clean

        segments_data = [seg.to_dict(base_dir=base_dir) for seg in timeline.segments]

        payload = {
            "format": VCUT_FORMAT_IDENTIFIER,
            "version": CURRENT_PROJECT_FORMAT_VERSION,
            "app_version": app_version,
            "created_at": now_iso,
            "modified_at": now_iso,
            "timeline": {
                "global_time": float(current_global_time),
                "selected_segment_id": timeline.selected_segment_id,
                "snap_to_keyframe": bool(timeline.snap_to_keyframe),
                "keyframes": keyframes_map,
                "segments": segments_data,
            },
        }

        # Write atomically via temp file
        temp_file = target_path.with_suffix(".vcut.tmp")
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        temp_file.replace(target_path)
        cls.add_recent_project(target_path)

    @classmethod
    def load_project(
        cls,
        project_path: Path | str,
    ) -> LoadedProject:
        """Load and deserialize a .vcut project file.

        Returns:
            LoadedProject(segments, global_time, selected_segment_id, missing_files, snap_to_keyframe)
        """
        source_path = Path(project_path).resolve()
        if not source_path.exists():
            raise FileNotFoundError(f"Project file not found: {source_path}")

        with open(source_path, encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, dict):
            raise ValueError("Invalid project file: root must be a JSON object")

        if data.get("format") != VCUT_FORMAT_IDENTIFIER:
            raise ValueError(f"Not a valid Video Cut project file: format '{data.get('format')}'")

        timeline_data = data.get("timeline")
        if not isinstance(timeline_data, dict):
            raise ValueError("Invalid project file: missing timeline section")

        base_dir = source_path.parent
        raw_segments = timeline_data.get("segments", [])
        raw_media_keyframes = timeline_data.get("keyframes", {})
        media_keyframes_map: dict[str, list[float]] = {}
        if isinstance(raw_media_keyframes, dict):
            for k_path, kf_list in raw_media_keyframes.items():
                if isinstance(kf_list, list):
                    media_keyframes_map[str(k_path)] = [
                        float(k) for k in kf_list if isinstance(k, (int, float))
                    ]

        segments: list[Segment] = []
        missing_files: set[str] = set()

        for raw_seg in raw_segments:
            if not isinstance(raw_seg, dict):
                continue
            seg = Segment.from_dict(raw_seg, base_dir=base_dir)
            if seg is None:
                continue

            # If segment doesn't have keyframes from its dict, look up in media_keyframes_map
            if not seg.keyframes:
                try:
                    rel_p = str(seg.source_path.relative_to(base_dir))
                except ValueError:
                    rel_p = ""
                if rel_p and rel_p in media_keyframes_map:
                    seg.keyframes = list(media_keyframes_map[rel_p])
                elif str(seg.source_path) in media_keyframes_map:
                    seg.keyframes = list(media_keyframes_map[str(seg.source_path)])
                elif seg.source_path.name in media_keyframes_map:
                    seg.keyframes = list(media_keyframes_map[seg.source_path.name])

            # Check if source media exists
            if not seg.source_path.exists():
                missing_files.add(str(seg.source_path))
            else:
                # If keyframes exist, cache them in ProbeService immediately
                if seg.keyframes:
                    ProbeService.set_cached_keyframes(seg.source_path, seg.keyframes)

                # If source duration was 0 or unprobed, probe it
                if seg.source_duration <= 0.0:
                    dur = ProbeService.probe_duration(seg.source_path)
                    if dur:
                        seg.source_duration = dur
                        if seg.end_time <= 0.0:
                            seg.end_time = dur

            if seg.orig_image_path and not seg.orig_image_path.exists():
                missing_files.add(str(seg.orig_image_path))

            for ov in seg.overlays:
                if ov.media_path and not Path(ov.media_path).exists():
                    missing_files.add(str(ov.media_path))

            segments.append(seg)

        global_time = float(timeline_data.get("global_time", 0.0))
        selected_segment_id = timeline_data.get("selected_segment_id")
        snap_to_keyframe = bool(timeline_data.get("snap_to_keyframe", False))
        cls.add_recent_project(source_path)
        return LoadedProject(
            segments=segments,
            global_time=global_time,
            selected_segment_id=selected_segment_id,
            missing_files=sorted(missing_files),
            snap_to_keyframe=snap_to_keyframe,
        )

    @classmethod
    def relink_missing_media(
        cls,
        segments: list[Segment],
        relink_map: dict[str, Path | str],
    ) -> None:
        """Update media references using a relink mapping of {old_path: new_path}."""
        for seg in segments:
            old_src = str(seg.source_path)
            if old_src in relink_map:
                new_path = Path(relink_map[old_src])
                seg.source_path = new_path
                dur = ProbeService.probe_duration(new_path)
                if dur:
                    seg.source_duration = dur
                if seg.keyframes and new_path.exists():
                    ProbeService.set_cached_keyframes(new_path, seg.keyframes)

            if seg.orig_image_path:
                old_img = str(seg.orig_image_path)
                if old_img in relink_map:
                    seg.orig_image_path = Path(relink_map[old_img])

            for ov in seg.overlays:
                if ov.media_path:
                    old_ov = str(ov.media_path)
                    if old_ov in relink_map:
                        ov.media_path = Path(relink_map[old_ov])

    @classmethod
    def _get_recent_projects_file_path(cls) -> Path:
        """Get the path to recent_projects.json in the user's config directory."""
        config_dir = Path(GLib.get_user_config_dir()) / "video-cut"
        config_dir.mkdir(parents=True, exist_ok=True)
        return config_dir / RECENT_PROJECTS_FILE

    @classmethod
    def get_recent_projects(cls) -> list[Path]:
        """Return a list of recently opened project file paths that exist."""
        p = cls._get_recent_projects_file_path()
        if not p.exists():
            return []
        try:
            with open(p, encoding="utf-8") as f:
                entries = json.load(f)
            if isinstance(entries, list):
                result: list[Path] = []
                for item in entries:
                    path = Path(str(item))
                    if path.exists() and path not in result:
                        result.append(path)
                return result[:MAX_RECENT_PROJECTS]
        except Exception as e:
            logger.warning("Error reading recent projects: %s", e)
        return []

    @classmethod
    def add_recent_project(cls, project_path: Path | str) -> None:
        """Add a project path to the recent projects list."""
        path_obj = Path(project_path).resolve()
        p = cls._get_recent_projects_file_path()
        existing = cls.get_recent_projects()

        updated = [path_obj] + [x for x in existing if x != path_obj]
        updated = updated[:MAX_RECENT_PROJECTS]

        try:
            with open(p, "w", encoding="utf-8") as f:
                json.dump([str(x) for x in updated], f, indent=2)
        except Exception as e:
            logger.warning("Error saving recent projects: %s", e)

    @classmethod
    def clear_recent_projects(cls) -> None:
        """Clear recent projects list."""
        p = cls._get_recent_projects_file_path()
        if p.exists():
            try:
                p.unlink()
            except Exception as e:
                logger.warning("Error clearing recent projects: %s", e)
