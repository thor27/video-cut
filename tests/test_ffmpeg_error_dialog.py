"""Unit tests for FFmpegErrorDialog."""

from unittest.mock import MagicMock, patch

import gi
import pytest

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk

from video_cut.ffmpeg_error_dialog import FFmpegErrorDialog


@pytest.fixture(scope="session", autouse=True)
def init_gtk():
    """Ensure GTK/Adwaita is initialized."""
    Adw.init()


def test_ffmpeg_error_dialog_defaults():
    """Test dialog creation with default arguments."""
    dialog = FFmpegErrorDialog()
    assert dialog.get_title() == "Export Failed"
    assert dialog.get_modal() is True
    assert dialog._error_text == "No error details available."
    dialog.destroy()


def test_ffmpeg_error_dialog_custom():
    """Test dialog creation with custom title, heading, message, and error details."""
    custom_error = """Error: Invalid argument
Stream specifier ':v' not found
Conversion failed!"""
    dialog = FFmpegErrorDialog(
        title="Custom Export Error",
        heading="Failed Processing Video",
        message="Please check your settings below:",
        error_details=custom_error,
    )
    assert dialog.get_title() == "Custom Export Error"
    assert dialog._error_text == custom_error
    dialog.destroy()


def test_ffmpeg_error_dialog_copy_action():
    """Test copy button callback copies text to clipboard and updates UI."""
    error_msg = """ffmpeg exited with code 1
Unrecognized option 'invalid_flag'"""
    dialog = FFmpegErrorDialog(error_details=error_msg)

    mock_display = MagicMock()
    mock_clipboard = MagicMock()
    mock_display.get_clipboard.return_value = mock_clipboard

    with patch.object(dialog, "get_display", return_value=mock_display):
        dialog._on_copy_clicked(MagicMock())
        mock_clipboard.set.assert_called_once_with(error_msg)
        assert dialog._copy_btn_content.get_label() == "Copied!"

    dialog.destroy()
