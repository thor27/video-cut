"""Unit and UI tests for HealthCheckDialog."""

from unittest.mock import MagicMock, patch

import gi
import pytest

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk

from video_cut.health_dialog import HealthCheckDialog
from video_cut.i18n import _
from video_cut.services.health_service import HealthCheckItem


@pytest.fixture(scope="session", autouse=True)
def init_gtk():
    """Ensure GTK/Adwaita is initialized."""
    Adw.init()


def test_health_dialog_init():
    """Test dialog initialization, widgets creation, and layout with visual groups."""
    dialog = HealthCheckDialog()
    assert dialog.get_title() == _("System Health & Setup")
    assert dialog.get_modal() is True
    assert dialog.row_overall is not None
    assert dialog.grp_ffmpeg is not None
    assert dialog.row_ffmpeg is not None
    assert dialog.grp_association is not None
    assert dialog.row_association is not None
    dialog.destroy()


def test_health_dialog_apply_all_ok():
    """Test dialog UI when all health checks are OK."""
    dialog = HealthCheckDialog()

    items = [
        HealthCheckItem(id="ffmpeg", title="FFmpeg", status="ok", summary="Working", details="ffmpeg v6.1"),
        HealthCheckItem(id="file_association", title="Association", status="ok", summary="Registered", details="Default"),
    ]

    dialog._apply_results(items)
    assert dialog.row_overall.get_title() == _("All Systems Operational")
    assert dialog.row_ffmpeg.get_subtitle() == "Working"
    assert dialog.row_association.get_subtitle() == "Registered"
    assert dialog._suffix_ffmpeg is not None
    assert dialog._suffix_association is not None

    # Verify Remove Association button is present when status is ok
    btn_remove = None
    child = dialog._suffix_association.get_first_child()
    while child is not None:
        if isinstance(child, Gtk.Button) and not isinstance(child, Gtk.MenuButton):
            btn_remove = child
            break
        child = child.get_next_sibling()
    assert btn_remove is not None
    assert btn_remove.get_label() == _("Remove Association")

    dialog.destroy()


def test_health_dialog_remove_association_button_click():
    """Test clicking Remove Association button calls remove_file_association."""
    dialog = HealthCheckDialog()
    item = HealthCheckItem(id="file_association", title="Association", status="ok", summary="Registered", details="Default")
    dialog._apply_results([item])

    child = dialog._suffix_association.get_first_child()
    btn_remove = None
    while child is not None:
        if isinstance(child, Gtk.Button) and not isinstance(child, Gtk.MenuButton):
            btn_remove = child
            break
        child = child.get_next_sibling()

    assert btn_remove is not None

    with patch("video_cut.services.health_service.HealthService.remove_file_association", return_value=(True, "Removed")):
        btn_remove.emit("clicked")
        assert btn_remove.get_label() == _("Removing...")

    dialog.destroy()


def test_health_dialog_apply_warning_and_error():
    """Test dialog UI when errors and warnings are present with inline fix widgets."""
    dialog = HealthCheckDialog()

    items_err = [
        HealthCheckItem(
            id="ffmpeg",
            title="FFmpeg",
            status="error",
            summary="FFmpeg missing",
            details="Not found",
            can_fix=True,
            fix_command="sudo apt install ffmpeg",
            fix_url="https://ffmpeg.org",
        ),
        HealthCheckItem(
            id="file_association",
            title="Association",
            status="warning",
            summary="Not registered",
            details="Needs association",
            can_fix=True,
            fix_type="automated",
        ),
    ]

    dialog._apply_results(items_err)
    assert dialog.row_overall.get_title() == _("Action Required")
    assert dialog.row_ffmpeg.get_subtitle() == "FFmpeg missing"
    assert dialog.row_association.get_subtitle() == "Not registered"

    # Verify inline fix controls in FFmpeg row suffix
    assert dialog._suffix_ffmpeg is not None
    ffmpeg_children: list[Gtk.Widget] = []
    child = dialog._suffix_ffmpeg.get_first_child()
    while child is not None:
        ffmpeg_children.append(child)
        child = child.get_next_sibling()

    # Should contain Entry, Copy button, Website button, and Info MenuButton
    assert any(isinstance(w, Gtk.Entry) for w in ffmpeg_children)
    assert any(isinstance(w, Gtk.Button) and not isinstance(w, Gtk.MenuButton) for w in ffmpeg_children)
    assert any(isinstance(w, Gtk.MenuButton) for w in ffmpeg_children)

    # Verify inline fix controls in Association row suffix
    assert dialog._suffix_association is not None
    assoc_children: list[Gtk.Widget] = []
    child = dialog._suffix_association.get_first_child()
    while child is not None:
        assoc_children.append(child)
        child = child.get_next_sibling()

    # Should contain Fix Button and Info MenuButton
    assert any(isinstance(w, Gtk.Button) and not isinstance(w, Gtk.MenuButton) for w in assoc_children)
    assert any(isinstance(w, Gtk.MenuButton) for w in assoc_children)

    dialog.destroy()


def test_health_dialog_info_button_popover():
    """Test that info MenuButton creates a Popover with details."""
    dialog = HealthCheckDialog()
    item = HealthCheckItem(
        id="ffmpeg",
        title="FFmpeg Multimedia Engine",
        status="ok",
        summary="Working",
        details="ffmpeg version 6.1.1\nBinary: /usr/bin/ffmpeg",
    )
    btn_info = dialog._create_info_button(item)
    assert btn_info.get_popover() is not None
    dialog.destroy()


def test_health_dialog_clipboard_copy():
    """Test copying commands to clipboard from health dialog."""
    dialog = HealthCheckDialog()
    mock_display = MagicMock()
    mock_clipboard = MagicMock()
    mock_display.get_clipboard.return_value = mock_clipboard

    with patch.object(dialog, "get_display", return_value=mock_display):
        dialog._copy_to_clipboard("sudo apt install ffmpeg")
        mock_clipboard.set.assert_called_once_with("sudo apt install ffmpeg")

    dialog.destroy()


def test_health_dialog_copy_command_button_click():
    """Test clicking the copy command button executes on_copy_cmd without error."""
    dialog = HealthCheckDialog()
    item = HealthCheckItem(
        id="ffmpeg",
        title="FFmpeg",
        status="error",
        summary="Missing",
        details="Not found",
        can_fix=True,
        fix_command="sudo apt install ffmpeg",
    )
    dialog._apply_results([item])

    mock_display = MagicMock()
    mock_clipboard = MagicMock()
    mock_display.get_clipboard.return_value = mock_clipboard

    with patch.object(dialog, "get_display", return_value=mock_display):
        # Locate copy button in suffix
        child = dialog._suffix_ffmpeg.get_first_child()
        btn_copy = None
        while child is not None:
            if isinstance(child, Gtk.Button) and not isinstance(child, Gtk.MenuButton):
                btn_copy = child
                break
            child = child.get_next_sibling()

        assert btn_copy is not None
        btn_copy.emit("clicked")
        mock_clipboard.set.assert_called_once_with("sudo apt install ffmpeg")

    dialog.destroy()


def test_health_dialog_copy_details_button_click():
    """Test clicking the copy details button in Popover executes on_copy_details without error."""
    dialog = HealthCheckDialog()
    item = HealthCheckItem(
        id="ffmpeg",
        title="FFmpeg",
        status="error",
        summary="Missing",
        details="Diagnosed detail message",
    )
    btn_info = dialog._create_info_button(item)
    popover = btn_info.get_popover()
    assert popover is not None

    mock_display = MagicMock()
    mock_clipboard = MagicMock()
    mock_display.get_clipboard.return_value = mock_clipboard

    with patch.object(dialog, "get_display", return_value=mock_display):
        # Locate btn_copy in popover child box
        popover_box = popover.get_child()
        child = popover_box.get_first_child()
        btn_copy_details = None
        while child is not None:
            if isinstance(child, Gtk.Button):
                btn_copy_details = child
            child = child.get_next_sibling()

        assert btn_copy_details is not None
        btn_copy_details.emit("clicked")
        mock_clipboard.set.assert_called_once_with("Diagnosed detail message")

    dialog.destroy()

