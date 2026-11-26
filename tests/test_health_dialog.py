"""Unit and UI tests for HealthCheckDialog."""

from unittest.mock import MagicMock, patch

import gi
import pytest

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk

from video_cut.health_dialog import HealthCheckDialog, StartupHealthAlertModal
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
    assert dialog.grp_hwaccel is not None
    assert dialog.row_hwaccel_summary is not None
    assert dialog.grp_codecs is not None
    assert dialog.row_codecs_summary is not None
    assert dialog.grp_association is not None
    assert dialog.row_association is not None
    assert dialog.grp_preferences is not None
    assert dialog.row_startup_check is not None
    dialog.destroy()


def test_health_dialog_apply_all_ok():
    """Test dialog UI when all health checks are OK."""
    dialog = HealthCheckDialog()

    items = [
        HealthCheckItem(id="ffmpeg", title="FFmpeg", status="ok", summary="Working", details="ffmpeg v6.1"),
        HealthCheckItem(id="hwaccel", title="Hardware Acceleration", status="ok", summary="VA-API active", details="VA-API"),
        HealthCheckItem(id="codecs", title="Codecs", status="ok", summary="All codecs ready", details="h264, aac"),
        HealthCheckItem(id="file_association", title="Association", status="ok", summary="Registered", details="Default"),
    ]

    dialog._apply_results(items)
    assert dialog.row_overall.get_title() == _("All Systems Operational")
    assert dialog.row_ffmpeg.get_subtitle() == "Working"
    assert dialog.row_association.get_subtitle() == "Registered"
    assert dialog._suffix_ffmpeg is not None
    assert dialog._suffix_association is not None
    assert dialog._row_hwaccel_table is not None
    assert dialog._row_codecs_table is not None

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

    with patch("video_cut.services.health_service.HealthService.remove_file_association", return_value=(True, "Removed")) as mock_remove, \
         patch.object(dialog, "refresh_checks") as mock_refresh:
        btn_remove.emit("clicked")
        mock_remove.assert_called_once()
        mock_refresh.assert_called_once()

    dialog.destroy()


def test_health_dialog_apply_warning_and_error():
    """Test dialog UI when errors and warnings are present with spacious action rows bellow."""
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
            id="hwaccel",
            title="Hardware Acceleration",
            status="warning",
            summary="FFmpeg is missing",
            details="Cannot probe hwaccel",
        ),
        HealthCheckItem(
            id="codecs",
            title="Codecs",
            status="warning",
            summary="FFmpeg is missing",
            details="Codecs cannot be probed",
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

    # Action rows are bellow in grp_ffmpeg
    assert dialog._row_ffmpeg_cmd is not None
    assert dialog._row_ffmpeg_web is not None

    # Association row has fix button in suffix
    assert dialog._suffix_association is not None
    assoc_children: list[Gtk.Widget] = []
    child = dialog._suffix_association.get_first_child()
    while child is not None:
        assoc_children.append(child)
        child = child.get_next_sibling()

    assert any(isinstance(w, Gtk.Button) and not isinstance(w, Gtk.MenuButton) for w in assoc_children)
    assert any(isinstance(w, Gtk.MenuButton) for w in assoc_children)

    dialog.destroy()


def test_health_dialog_hwaccel_and_codecs_tables():
    """Test that hardware acceleration and codecs tables are created when FFmpeg is OK."""
    dialog = HealthCheckDialog()
    items = [
        HealthCheckItem(id="ffmpeg", title="FFmpeg", status="ok", summary="Working", details="ffmpeg v6.1"),
        HealthCheckItem(id="hwaccel", title="Hardware Acceleration", status="ok", summary="VA-API", details="hwaccel"),
        HealthCheckItem(id="codecs", title="Codecs", status="ok", summary="All codecs ready", details="h264"),
    ]
    dialog._apply_results(items)

    assert dialog._row_hwaccel_table is not None
    grid_hw = dialog._row_hwaccel_table.get_child()
    assert isinstance(grid_hw, Gtk.Grid)

    assert dialog._row_codecs_table is not None
    grid_codecs = dialog._row_codecs_table.get_child()
    assert isinstance(grid_codecs, Gtk.Grid)

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
        assert dialog._row_ffmpeg_cmd is not None
        btn_copy = getattr(dialog._row_ffmpeg_cmd, "_btn_copy", None)
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


def test_health_dialog_groups_ordered_problems_first():
    """Test that groups with problems (error/warning) are ordered before all-green groups."""
    dialog = HealthCheckDialog()
    items = [
        HealthCheckItem(id="ffmpeg", title="FFmpeg", status="ok", summary="Working", details="ok"),
        HealthCheckItem(id="hwaccel", title="Hardware Acceleration", status="ok", summary="ok", details="ok"),
        HealthCheckItem(id="codecs", title="Codecs", status="ok", summary="ok", details="ok"),
        HealthCheckItem(id="file_association", title="Association", status="warning", summary="Missing", details="warning"),
    ]
    dialog._apply_results(items)

    def find_groups(widget):
        res = []
        child = widget.get_first_child()
        while child is not None:
            if isinstance(child, Adw.PreferencesGroup):
                res.append(child)
            else:
                res.extend(find_groups(child))
            child = child.get_next_sibling()
        return res

    groups = find_groups(dialog.pref_page)
    # The first group is overall banner
    assert groups[0] == dialog.grp_status
    # The second group must be the problem group (association), NOT ffmpeg!
    assert groups[1] == dialog.grp_association
    # The remaining groups are the all-green groups
    assert dialog.grp_ffmpeg in groups[2:]
    assert dialog.grp_hwaccel in groups[2:]
    assert dialog.grp_codecs in groups[2:]
    assert dialog.grp_preferences in groups
    dialog.destroy()


def test_health_dialog_startup_check_toggle():
    """Test toggling row_startup_check updates HealthService setting."""
    with patch("video_cut.services.health_service.HealthService.is_check_on_startup_enabled", return_value=True):
        dialog = HealthCheckDialog()
    with patch("video_cut.services.health_service.HealthService.set_check_on_startup_enabled") as mock_set:
        dialog.row_startup_check.set_active(False)
        mock_set.assert_called_with(False)
        dialog.row_startup_check.set_active(True)
        mock_set.assert_called_with(True)
    dialog.destroy()


def test_startup_health_alert_modal_dismiss():
    """Test dismissing StartupHealthAlertModal saves preference if checkbox is checked."""
    items = [
        HealthCheckItem(id="ffmpeg", title="FFmpeg", status="error", summary="Missing", details="Not found"),
    ]
    modal = StartupHealthAlertModal(items=items)
    modal.chk_dont_show.set_active(True)

    with patch("video_cut.services.health_service.HealthService.set_check_on_startup_enabled") as mock_set:
        modal._on_dismiss_clicked(MagicMock())
        mock_set.assert_called_once_with(False)

    modal.destroy()


def test_startup_health_alert_modal_review_and_fix():
    """Test clicking Review & Fix invokes callback and saves preference if checked."""
    items = [
        HealthCheckItem(id="file_association", title="Association", status="warning", summary="Missing", details="Not registered"),
    ]
    on_setup = MagicMock()
    modal = StartupHealthAlertModal(items=items, on_open_setup=on_setup)
    modal.chk_dont_show.set_active(True)

    with patch("video_cut.services.health_service.HealthService.set_check_on_startup_enabled") as mock_set:
        modal._on_review_clicked(MagicMock())
        mock_set.assert_called_once_with(False)
        on_setup.assert_called_once()

    modal.destroy()


def test_health_dialog_ffmpeg_source_combo_selection():
    """Test changing FFmpeg source combo updates preference and refreshes checks."""
    dialog = HealthCheckDialog()
    assert dialog.row_ffmpeg_source is not None
    assert dialog.row_ffmpeg_manual is not None

    with patch("video_cut.services.health_service.HealthService.set_ffmpeg_source") as mock_set, \
         patch.object(dialog, "refresh_checks") as mock_refresh:
        # Index 1 corresponds to "container"
        dialog.row_ffmpeg_source.set_selected(1)
        mock_set.assert_called_with("container")
        mock_refresh.assert_called_once()
        assert not dialog.row_ffmpeg_manual.get_visible()

    with patch("video_cut.services.health_service.HealthService.set_ffmpeg_source") as mock_set, \
         patch.object(dialog, "refresh_checks") as mock_refresh:
        # Index 3 corresponds to "manual"
        dialog.row_ffmpeg_source.set_selected(3)
        mock_set.assert_called_with("manual")
        mock_refresh.assert_called_once()
        assert dialog.row_ffmpeg_manual.get_visible()

    dialog.destroy()


def test_health_dialog_ffmpeg_manual_entry():
    """Test submitting custom manual FFmpeg path updates preference and refreshes."""
    dialog = HealthCheckDialog()
    assert dialog._entry_manual_path is not None

    with patch("video_cut.services.health_service.HealthService.set_custom_ffmpeg_path") as mock_set, \
         patch.object(dialog, "refresh_checks") as mock_refresh:
        dialog._entry_manual_path.set_text("/opt/custom/bin/ffmpeg")
        dialog._entry_manual_path.emit("activate")
        mock_set.assert_called_with("/opt/custom/bin/ffmpeg")
        mock_refresh.assert_called_once()

    dialog.destroy()


def test_health_dialog_flatpak_source_options():
    """Test HealthCheckDialog displays simplified runtime options inside Flatpak."""
    with patch("video_cut.health_dialog.is_flatpak", return_value=True):
        dialog = HealthCheckDialog()
        assert dialog.row_ffmpeg_source is not None
        model = dialog.row_ffmpeg_source.get_model()
        assert model.get_n_items() == 2
        assert model.get_string(0) == _("Flatpak Runtime (Bundled)")
        assert model.get_string(1) == _("Manual (Custom Path)")

        # Verify selection maps to container and manual
        with patch("video_cut.services.health_service.HealthService.set_ffmpeg_source") as mock_set, \
             patch.object(dialog, "refresh_checks"):
            dialog.row_ffmpeg_source.set_selected(1)
            mock_set.assert_called_with("manual")

            dialog.row_ffmpeg_source.set_selected(0)
            mock_set.assert_called_with("container")

        dialog.destroy()


def test_health_dialog_flatpak_no_remove_association_button():
    """Test HealthCheckDialog does not show Remove Association button inside Flatpak."""
    with patch("video_cut.health_dialog.is_flatpak", return_value=True):
        dialog = HealthCheckDialog()
        item = HealthCheckItem(
            id="file_association",
            title="Association",
            status="ok",
            summary="Registered via Flatpak",
            details="Default",
        )
        dialog._apply_results([item])

        child = dialog._suffix_association.get_first_child()
        btn_remove = None
        while child is not None:
            if isinstance(child, Gtk.Button) and not isinstance(child, Gtk.MenuButton):
                btn_remove = child
                break
            child = child.get_next_sibling()

        assert btn_remove is None
        dialog.destroy()




