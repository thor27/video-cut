"""Project serialization and deserialization service for saving and loading .vcut projects."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from gi.repository import GLib

from ..models import ProjectTimeline, Segment
from .probe_service import ProbeService

logger = logging.getLogger(__name__)

VCUT_FORMAT_IDENTIFIER = "video-cut-project"
CURRENT_PROJECT_FORMAT_VERSION = 1
RECENT_PROJECTS_FILE = "recent_projects.json"
MAX_RECENT_PROJECTS = 10


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
    ) -> tuple[list[Segment], float, str | None, list[str]]:
        """Load and deserialize a .vcut project file.

        Returns:
            (segments, global_time, selected_segment_id, missing_files)
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
        segments: list[Segment] = []
        missing_files: set[str] = set()

        for raw_seg in raw_segments:
            if not isinstance(raw_seg, dict):
                continue
            seg = Segment.from_dict(raw_seg, base_dir=base_dir)
            if seg is None:
                continue

            # Check if source media exists
            if not seg.source_path.exists():
                missing_files.add(str(seg.source_path))
            else:
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
        cls.add_recent_project(source_path)
        return segments, global_time, selected_segment_id, sorted(missing_files)

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
