"""Unit and integration tests for ProjectService and project serialization."""

import json
from pathlib import Path

from video_cut.models import OverlayItem, ProjectTimeline, Segment
from video_cut.services.project_service import (
    CURRENT_PROJECT_FORMAT_VERSION,
    VCUT_FORMAT_IDENTIFIER,
    ProjectService,
)


def test_overlay_item_to_and_from_dict(tmp_path: Path):
    media = tmp_path / "sticker.png"
    media.write_text("dummy", encoding="utf-8")

    item = OverlayItem(
        id="custom-id-123",
        item_type="image",
        x=0.25,
        y=0.75,
        width=0.4,
        height=0.3,
        rotation=45.0,
        opacity=0.85,
        media_path=media,
        text="Sample",
        font_size=24,
    )

    d = item.to_dict(base_dir=tmp_path)
    assert d["id"] == "custom-id-123"
    assert d["item_type"] == "image"
    assert d["media_relative_path"] == "sticker.png"
    assert d["x"] == 0.25
    assert d["rotation"] == 45.0

    # Restore preserving ID
    restored = OverlayItem.from_dict(d, base_dir=tmp_path, preserve_id=True)
    assert restored is not None
    assert restored.id == "custom-id-123"
    assert restored.item_type == "image"
    assert restored.media_path == media
    assert restored.rotation == 45.0
    assert restored.opacity == 0.85


def test_segment_to_and_from_dict(tmp_path: Path):
    media = tmp_path / "clip.mp4"
    media.write_text("video", encoding="utf-8")

    seg = Segment(
        source_path=media,
        source_duration=60.0,
        start_time=5.0,
        end_time=25.0,
        crop=(0.1, 0.1, 0.8, 0.8),
        rotation=90,
        hflip=True,
        vflip=False,
        display_name="Intro Clip",
        overlays=[
            OverlayItem(item_type="text", text="Title", x=0.5, y=0.2),
        ],
    )

    d = seg.to_dict(base_dir=tmp_path)
    assert d["source_relative_path"] == "clip.mp4"
    assert d["start_time"] == 5.0
    assert d["end_time"] == 25.0
    assert d["rotation"] == 90
    assert d["hflip"] is True
    assert d["crop"] == [0.1, 0.1, 0.8, 0.8]
    assert len(d["overlays"]) == 1

    restored = Segment.from_dict(d, base_dir=tmp_path)
    assert restored is not None
    assert restored.source_path == media
    assert restored.start_time == 5.0
    assert restored.end_time == 25.0
    assert restored.crop == (0.1, 0.1, 0.8, 0.8)
    assert restored.rotation == 90
    assert restored.hflip is True
    assert restored.vflip is False
    assert restored.display_name == "Intro Clip"
    assert len(restored.overlays) == 1
    assert restored.overlays[0].text == "Title"


def test_project_save_and_load_roundtrip(tmp_path: Path):
    proj_dir = tmp_path / "project"
    proj_dir.mkdir()
    media1 = proj_dir / "vid1.mp4"
    media2 = proj_dir / "vid2.mp4"
    media1.write_text("sample1", encoding="utf-8")
    media2.write_text("sample2", encoding="utf-8")

    timeline = ProjectTimeline()
    seg1 = timeline.add_video(media1, source_duration=30.0)
    seg1.start_time = 2.0
    seg1.end_time = 18.0
    seg1.rotation = 180
    seg1.crop = (0.05, 0.05, 0.9, 0.9)
    seg1.overlays.append(OverlayItem(item_type="text", text="Segment 1", font_size=40))

    seg2 = timeline.add_video(media2, source_duration=45.0)
    seg2.start_time = 10.0
    seg2.end_time = 40.0
    seg2.vflip = True

    timeline.selected_segment_id = seg2.id

    project_file = proj_dir / "my_movie.vcut"
    ProjectService.save_project(
        project_path=project_file,
        timeline=timeline,
        current_global_time=12.5,
        app_version="0.5.0.dev0",
    )

    assert project_file.exists()
    with open(project_file, encoding="utf-8") as f:
        raw_json = json.load(f)

    assert raw_json["format"] == VCUT_FORMAT_IDENTIFIER
    assert raw_json["version"] == CURRENT_PROJECT_FORMAT_VERSION
    assert raw_json["timeline"]["global_time"] == 12.5
    assert raw_json["timeline"]["selected_segment_id"] == seg2.id
    assert len(raw_json["timeline"]["segments"]) == 2

    # Now load project back
    segments, global_time, selected_id, missing = ProjectService.load_project(project_file)
    assert len(missing) == 0
    assert global_time == 12.5
    assert selected_id == seg2.id
    assert len(segments) == 2

    s1, s2 = segments
    assert s1.source_path == media1
    assert s1.start_time == 2.0
    assert s1.end_time == 18.0
    assert s1.rotation == 180
    assert s1.crop == (0.05, 0.05, 0.9, 0.9)
    assert len(s1.overlays) == 1
    assert s1.overlays[0].text == "Segment 1"

    assert s2.source_path == media2
    assert s2.start_time == 10.0
    assert s2.end_time == 40.0
    assert s2.vflip is True


def test_project_load_missing_files_and_relink(tmp_path: Path):
    proj_dir = tmp_path / "proj"
    proj_dir.mkdir()
    project_file = proj_dir / "missing.vcut"

    missing_video = tmp_path / "nonexistent" / "lost_video.mp4"
    missing_overlay = tmp_path / "nonexistent" / "lost_overlay.png"

    timeline = ProjectTimeline()
    seg = timeline.add_video(missing_video, source_duration=20.0)
    seg.overlays.append(OverlayItem(item_type="image", media_path=missing_overlay))

    ProjectService.save_project(project_file, timeline)

    # Loading should report missing files
    segments, _, _, missing = ProjectService.load_project(project_file)
    assert len(missing) == 2
    assert str(missing_video) in missing
    assert str(missing_overlay) in missing

    # Now create replacements and relink
    found_video = tmp_path / "found_video.mp4"
    found_overlay = tmp_path / "found_overlay.png"
    found_video.write_text("content", encoding="utf-8")
    found_overlay.write_text("overlay", encoding="utf-8")

    relink_map = {
        str(missing_video): found_video,
        str(missing_overlay): found_overlay,
    }
    ProjectService.relink_missing_media(segments, relink_map)

    assert segments[0].source_path == found_video
    assert segments[0].overlays[0].media_path == found_overlay


def test_recent_projects_management(tmp_path: Path):
    p1 = tmp_path / "p1.vcut"
    p2 = tmp_path / "p2.vcut"
    p1.touch()
    p2.touch()

    # Clear
    ProjectService.clear_recent_projects()

    # Add p1, then p2
    ProjectService.add_recent_project(p1)
    ProjectService.add_recent_project(p2)

    recents = ProjectService.get_recent_projects()
    assert len(recents) >= 2
    # Most recent should be first
    assert recents[0] == p2.resolve()
    assert recents[1] == p1.resolve()

    # Re-adding p1 moves it to the top
    ProjectService.add_recent_project(p1)
    recents2 = ProjectService.get_recent_projects()
    assert recents2[0] == p1.resolve()
    assert recents2[1] == p2.resolve()

    # Cleanup
    ProjectService.clear_recent_projects()
    assert len(ProjectService.get_recent_projects()) == 0
