"""Tests for ImageImportDialog."""

import subprocess
from pathlib import Path
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw

from video_cut.image_import_dialog import ImageImportDialog
from video_cut.services.probe_service import ProbeService


def _create_sample_image(path: Path) -> Path:
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=purple:s=400x300:d=1", "-frames:v", "1", str(path)],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return path


def _create_sample_audio(path: Path, duration: float = 6.0) -> Path:
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}", str(path)],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return path


def test_image_import_dialog_initial_state(tmp_path):
    img_path = _create_sample_image(tmp_path / "test.png")

    parent = Gtk.Window()
    dialog = ImageImportDialog(parent=parent, image_path=img_path, is_replace=False)

    assert dialog.image_path == img_path
    assert dialog.duration == 3.0
    assert dialog.audio_path is None
    assert dialog.audio_duration == 0.0
    assert dialog.audio_start_time == 0.0

    # UI visibility
    assert dialog.row_no_audio.get_visible() is True
    assert dialog.row_audio_info.get_visible() is False
    assert dialog.row_audio_slice.get_visible() is False
    assert dialog.row_preview.get_visible() is False
    assert dialog.btn_confirm.get_label() == "Add Segment"

    # Replace mode check
    replace_dialog = ImageImportDialog(parent=parent, image_path=img_path, is_replace=True)
    assert replace_dialog.btn_confirm.get_label() == "Open Image"


def test_image_import_dialog_duration_changes(tmp_path):
    img_path = _create_sample_image(tmp_path / "test.png")
    parent = Gtk.Window()
    dialog = ImageImportDialog(parent=parent, image_path=img_path, is_replace=False)

    dialog.spin_duration.set_value(5.5)
    assert dialog.duration == 5.5
    assert "00:05.50" in dialog.badge_duration.get_label()


def test_image_import_dialog_audio_selection_and_clamping(tmp_path):
    img_path = _create_sample_image(tmp_path / "test.png")
    audio_path = _create_sample_audio(tmp_path / "track.wav", duration=8.0)

    parent = Gtk.Window()
    dialog = ImageImportDialog(parent=parent, image_path=img_path, is_replace=False)

    # Set audio file
    dialog._set_audio_file(audio_path)

    assert dialog.audio_path == audio_path
    assert abs(dialog.audio_duration - 8.0) < 0.2
    assert dialog.row_no_audio.get_visible() is False
    assert dialog.row_audio_info.get_visible() is True
    assert dialog.row_audio_slice.get_visible() is True
    assert dialog.row_preview.get_visible() is True

    # Initial max offset should be 8.0 - 3.0 = 5.0
    dialog.scale_offset.set_value(4.0)
    assert abs(dialog.audio_start_time - 4.0) < 0.01
    assert "00:04.00" in dialog.lbl_slice_range.get_label()

    # Changing duration to 6.0 should clamp max offset to 8.0 - 6.0 = 2.0
    dialog.spin_duration.set_value(6.0)
    assert dialog.duration == 6.0
    assert dialog.audio_start_time <= 2.1

    # Remove audio
    dialog._remove_audio()
    assert dialog.audio_path is None
    assert dialog.row_no_audio.get_visible() is True
    assert dialog.row_audio_info.get_visible() is False
    assert dialog.row_audio_slice.get_visible() is False
    assert dialog.row_preview.get_visible() is False


def test_image_import_dialog_preview_player(tmp_path):
    img_path = _create_sample_image(tmp_path / "test.png")
    audio_path = _create_sample_audio(tmp_path / "track.wav", duration=4.0)

    parent = Gtk.Window()
    dialog = ImageImportDialog(parent=parent, image_path=img_path, is_replace=False)
    dialog._set_audio_file(audio_path)

    # Start preview
    dialog._start_preview()
    assert dialog._is_previewing is True
    assert dialog.btn_preview_play.get_icon_name() == "media-playback-pause-symbolic"

    # Stop preview
    dialog._stop_preview(reset=True)
    assert dialog._is_previewing is False
    assert dialog.btn_preview_play.get_icon_name() == "media-playback-start-symbolic"
    assert dialog.preview_progress.get_fraction() == 0.0

    dialog._cleanup_audio()
    assert dialog.media_file is None


def test_image_import_dialog_confirmation(tmp_path):
    img_path = _create_sample_image(tmp_path / "test.png")
    audio_path = _create_sample_audio(tmp_path / "track.wav", duration=5.0)

    confirmed_args = []

    def on_confirm(duration, audio, audio_start):
        confirmed_args.append((duration, audio, audio_start))

    parent = Gtk.Window()
    dialog = ImageImportDialog(parent=parent, image_path=img_path, is_replace=False, on_confirm=on_confirm)
    dialog.spin_duration.set_value(4.0)
    dialog._set_audio_file(audio_path)
    dialog.scale_offset.set_value(1.0)

    dialog._on_confirm_clicked()

    assert len(confirmed_args) == 1
    dur, aud, start = confirmed_args[0]
    assert dur == 4.0
    assert aud == audio_path
    assert abs(start - 1.0) < 0.01
    assert dialog.media_file is None


def test_image_import_dialog_mp3_with_album_art(tmp_path):
    img_path = _create_sample_image(tmp_path / "test.png")
    mp3_path = tmp_path / "song_with_cover.mp3"

    # Create MP3 with embedded album art
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=5",
            "-f", "lavfi", "-i", "color=c=red:s=100x100:d=1",
            "-map", "0:a", "-map", "1:v",
            "-c:a", "libmp3lame",
            "-c:v", "mjpeg",
            "-id3v2_version", "3",
            "-metadata:s:v", "title=Album cover",
            "-metadata:s:v", "comment=Cover (front)",
            str(mp3_path)
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    parent = Gtk.Window()
    dialog = ImageImportDialog(parent=parent, image_path=img_path, is_replace=False)

    # Must not crash with decodebin3 assertion error
    dialog._set_audio_file(mp3_path)
    assert dialog.audio_path == mp3_path
    assert dialog.audio_player is not None

    dialog._start_preview()
    assert dialog._is_previewing is True

    dialog._stop_preview(reset=True)
    assert dialog._is_previewing is False

    dialog._cleanup_audio()
    assert dialog.audio_player is None


def test_audio_offset_label_consistency(tmp_path):
    img_path = _create_sample_image(tmp_path / "test.png")
    audio_path = _create_sample_audio(tmp_path / "track.wav", duration=20.0)

    parent = Gtk.Window()
    dialog = ImageImportDialog(parent=parent, image_path=img_path, is_replace=False)
    dialog.spin_duration.set_value(3.0)
    dialog._set_audio_file(audio_path)

    # Move slider across middle and boundary values
    for offset_val in [0.0, 5.2, 10.0, 15.7, 17.0]:
        dialog.scale_offset.set_value(offset_val)
        # Badge must match formatted offset
        assert f"{offset_val:05.2f}" in dialog.lbl_offset_badge.get_label()
        # Label must consistently say Plays start -> end (3.0s)
        label_text = dialog.lbl_slice_range.get_label()
        assert label_text.startswith("Plays ")
        assert "3.0s" in label_text
        assert "silence padded" not in label_text

    dialog._cleanup_audio()
