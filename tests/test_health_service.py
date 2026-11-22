"""Unit tests for HealthService diagnostic probes and repair actions."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from video_cut.services.health_service import HealthService


def test_detect_os_info(tmp_path: Path):
    """Test /etc/os-release parsing."""
    fake_os_release = tmp_path / "os-release"
    fake_os_release.write_text('ID=ubuntu\nID_LIKE=debian\nNAME="Ubuntu"\n', encoding="utf-8")

    with patch("video_cut.services.health_service.Path.exists", return_value=True), \
         patch("video_cut.services.health_service.Path.read_text", return_value=fake_os_release.read_text()):
        info = HealthService.detect_os_info()
        assert info.get("id") == "ubuntu"
        assert info.get("id_like") == "debian"


def test_detect_package_manager_debian():
    """Test detection of apt package manager."""
    with patch.object(HealthService, "detect_os_info", return_value={"id": "ubuntu", "id_like": "debian"}), \
         patch("shutil.which", return_value="/usr/bin/apt"):
        mgr, cmd, gui, extra_pkg = HealthService.detect_package_manager()
        assert mgr == "apt"
        assert "apt install" in cmd
        assert extra_pkg == "ubuntu-restricted-extras"


def test_detect_package_manager_fedora():
    """Test detection of dnf package manager."""
    with patch.object(HealthService, "detect_os_info", return_value={"id": "fedora"}), \
         patch("shutil.which", return_value="/usr/bin/dnf"):
        mgr, cmd, gui, extra_pkg = HealthService.detect_package_manager()
        assert mgr == "dnf"
        assert "dnf install" in cmd


def test_detect_package_manager_arch():
    """Test detection of pacman package manager."""
    with patch.object(HealthService, "detect_os_info", return_value={"id": "arch"}), \
         patch("shutil.which", return_value="/usr/bin/pacman"):
        mgr, cmd, gui, extra_pkg = HealthService.detect_package_manager()
        assert mgr == "pacman"
        assert "pacman -S" in cmd


def test_check_ffmpeg_success():
    """Test check_ffmpeg when ffmpeg is present and working."""
    with patch("shutil.which", return_value="/usr/bin/ffmpeg"), \
         patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="ffmpeg version 6.1.1\n")
        item = HealthService.check_ffmpeg()
        assert item.status == "ok"
        assert item.can_fix is False
        assert "ffmpeg version" in item.details


def test_check_ffmpeg_missing():
    """Test check_ffmpeg when ffmpeg executable is missing."""
    with patch("shutil.which", return_value=None), \
         patch("video_cut.services.health_service.get_ffmpeg_path", return_value="ffmpeg"), \
         patch("video_cut.services.health_service.get_ffprobe_path", return_value="ffprobe"), \
         patch("os.access", return_value=False):
        item = HealthService.check_ffmpeg()
        assert item.status == "error"
        assert item.can_fix is True
        assert item.fix_command is not None
        assert "ffmpeg.org" in (item.fix_url or "")


def test_check_file_association_registered():
    """Test check_file_association when registered."""
    mock_app_info = MagicMock()
    mock_app_info.get_id.return_value = "io.github.thor27.video-cut.desktop"
    mock_app_info.get_executable.return_value = "video-cut"

    with patch("gi.repository.Gio.content_type_guess", return_value=("application/x-video-cut", False)), \
         patch("gi.repository.Gio.AppInfo.get_default_for_type", return_value=mock_app_info), \
         patch("shutil.which", return_value="/usr/bin/video-cut"):
        item = HealthService.check_file_association()
        assert item.status == "ok"
        assert item.can_fix is False


def test_check_file_association_mime_unregistered():
    """Test check_file_association when MIME type is not recognized for .vcut."""
    with patch("gi.repository.Gio.content_type_guess", return_value=("application/octet-stream", True)):
        item = HealthService.check_file_association()
        assert item.status == "warning"
        assert item.can_fix is True
        assert item.fix_type == "automated"


def test_check_file_association_missing():
    """Test check_file_association when not registered."""
    with patch("gi.repository.Gio.AppInfo.get_default_for_type", return_value=None), \
         patch("shutil.which", return_value=None):
        item = HealthService.check_file_association()
        assert item.status == "warning"
        assert item.can_fix is True
        assert item.fix_type == "automated"


def test_fix_file_association(tmp_path: Path):
    """Test automated fix_file_association."""
    with patch("pathlib.Path.home", return_value=tmp_path), \
         patch("shutil.which", return_value=None):
        ok, msg = HealthService.fix_file_association()
        assert ok is True
        # Verify files were created in fake home
        mime_file = tmp_path / ".local" / "share" / "mime" / "packages" / "io.github.thor27.video-cut.xml"
        assert mime_file.exists()


def test_remove_file_association(tmp_path: Path):
    """Test remove_file_association deletes user MIME, desktop, and icon files."""
    # Setup dummy installed files in fake home
    mime_file = tmp_path / ".local" / "share" / "mime" / "packages" / "io.github.thor27.video-cut.xml"
    mime_file.parent.mkdir(parents=True, exist_ok=True)
    mime_file.write_text("<xml/>", encoding="utf-8")

    desktop_file = tmp_path / ".local" / "share" / "applications" / "io.github.thor27.video-cut.desktop"
    desktop_file.parent.mkdir(parents=True, exist_ok=True)
    desktop_file.write_text("[Desktop Entry]\n", encoding="utf-8")

    icon_file = tmp_path / ".local" / "share" / "icons" / "hicolor" / "512x512" / "apps" / "io.github.thor27.video-cut.png"
    icon_file.parent.mkdir(parents=True, exist_ok=True)
    icon_file.write_text("fake png", encoding="utf-8")

    mimeapps_file = tmp_path / ".config" / "mimeapps.list"
    mimeapps_file.parent.mkdir(parents=True, exist_ok=True)
    mimeapps_file.write_text("[Default Applications]\napplication/x-video-cut=io.github.thor27.video-cut.desktop\n", encoding="utf-8")

    with patch("pathlib.Path.home", return_value=tmp_path), \
         patch("shutil.which", return_value=None):
        ok, msg = HealthService.remove_file_association()
        assert ok is True
        assert not mime_file.exists()
        assert not desktop_file.exists()
        assert not icon_file.exists()
        assert "application/x-video-cut" not in mimeapps_file.read_text(encoding="utf-8")



def test_check_codecs_all_present():
    """Test check_codecs when all recommended codecs are available."""
    all_encoders = {"libx264", "libx265", "libvpx-vp9", "libsvtav1", "aac", "libmp3lame", "libopus"}
    with patch("video_cut.services.export_service.FFmpegCapabilities.get_encoders", return_value=all_encoders):
        item = HealthService.check_codecs()
        assert item.status == "ok"
        assert item.can_fix is False


def test_check_codecs_essential_missing():
    """Test check_codecs when essential codec (e.g. h264) is missing."""
    some_encoders = {"aac"}
    with patch("video_cut.services.export_service.FFmpegCapabilities.get_encoders", return_value=some_encoders):
        item = HealthService.check_codecs()
        assert item.status == "error"
        assert item.can_fix is True


def test_check_codecs_optional_missing():
    """Test check_codecs when only optional codec is missing."""
    codecs_without_hevc = {"libx264", "aac", "libvpx-vp9"}
    with patch("video_cut.services.export_service.FFmpegCapabilities.get_encoders", return_value=codecs_without_hevc):
        item = HealthService.check_codecs()
        assert item.status == "warning"


def test_initial_check_persistence(tmp_path: Path):
    """Test has_run_initial_check and mark_initial_check_done persistence."""
    fake_cfg = tmp_path / "healthcheck.json"
    with patch.object(HealthService, "get_config_file", return_value=fake_cfg):
        assert HealthService.has_run_initial_check() is False
        HealthService.mark_initial_check_done()
        assert HealthService.has_run_initial_check() is True


def test_startup_check_preference_persistence(tmp_path: Path):
    """Test is_check_on_startup_enabled and set_check_on_startup_enabled persistence."""
    fake_cfg = tmp_path / "healthcheck.json"
    with patch.object(HealthService, "get_config_file", return_value=fake_cfg):
        assert HealthService.is_check_on_startup_enabled() is True
        HealthService.set_check_on_startup_enabled(False)
        assert HealthService.is_check_on_startup_enabled() is False
        HealthService.set_check_on_startup_enabled(True)
        assert HealthService.is_check_on_startup_enabled() is True


def test_check_hardware_acceleration_detected():
    """Test check_hardware_acceleration when VA-API hardware acceleration is detected."""
    dummy_render = MagicMock()
    dummy_render.name = "renderD128"

    with patch("shutil.which", return_value="/usr/bin/ffmpeg"), \
         patch("pathlib.Path.exists", return_value=True), \
         patch("pathlib.Path.glob", return_value=[dummy_render]), \
         patch("video_cut.services.export_service.FFmpegCapabilities.get_encoders", return_value={"h264_vaapi"}):
        item = HealthService.check_hardware_acceleration()
        assert item.status == "ok"
        assert item.id == "hwaccel"
        overview = HealthService.get_hwaccel_overview()
        assert any(m.driver_key == "vaapi" and m.is_available for m in overview)


def test_check_hardware_acceleration_missing_ffmpeg():
    """Test check_hardware_acceleration when ffmpeg is missing."""
    with patch("shutil.which", return_value=None), \
         patch("os.access", return_value=False):
        item = HealthService.check_hardware_acceleration()
        assert item.status == "warning"
        assert item.id == "hwaccel"


def test_get_codecs_overview_problems_first():
    """Test that get_codecs_overview puts missing codecs first, then available codecs."""
    with patch("video_cut.services.export_service.FFmpegCapabilities.get_encoders", return_value={"libx264", "aac"}):
        codecs = HealthService.get_codecs_overview()
        missing = [c for c in codecs if not c.is_available]
        available = [c for c in codecs if c.is_available]
        assert len(missing) > 0
        assert len(available) > 0
        first_available_idx = next(i for i, c in enumerate(codecs) if c.is_available)
        last_missing_idx = max(i for i, c in enumerate(codecs) if not c.is_available)
        assert last_missing_idx < first_available_idx


def test_parse_ffmpeg_version():
    """Test parsing major and minor versions from ffmpeg -version first line."""
    from video_cut.utils import parse_ffmpeg_version

    assert parse_ffmpeg_version("ffmpeg version 6.1.1-1ubuntu1 Copyright (c) 2000-2023") == (6, 1)
    assert parse_ffmpeg_version("ffmpeg version n5.1.2 Copyright (c) 2000-2022") == (5, 1)
    assert parse_ffmpeg_version("ffmpeg version 4.4.2-0ubuntu0.22.04.1") == (4, 4)
    assert parse_ffmpeg_version("ffmpeg version 4.3 Copyright (c) 2000-2020") == (4, 3)
    assert parse_ffmpeg_version("ffmpeg version 4.2.7-0ubuntu0.1") == (4, 2)
    assert parse_ffmpeg_version("ffmpeg version 7.0-static https://johnvansickle.com/") == (7, 0)
    assert parse_ffmpeg_version("ffmpeg version 2024-05-02-git-5f7823b127-full_build") == (2024, 5)
    assert parse_ffmpeg_version("ffmpeg version N-112345-gabcdef") == (99, 0)
    assert parse_ffmpeg_version("invalid version string") is None
    assert parse_ffmpeg_version("") is None


def test_ffmpeg_source_get_set(tmp_path: Path):
    """Test getting and setting ffmpeg source and custom path."""
    with patch("pathlib.Path.home", return_value=tmp_path):
        assert HealthService.get_ffmpeg_source() == "auto"
        HealthService.set_ffmpeg_source("system")
        assert HealthService.get_ffmpeg_source() == "system"
        HealthService.set_ffmpeg_source("container")
        assert HealthService.get_ffmpeg_source() == "container"
        HealthService.set_ffmpeg_source("manual")
        assert HealthService.get_ffmpeg_source() == "manual"

        assert HealthService.get_custom_ffmpeg_path() == ""
        HealthService.set_custom_ffmpeg_path("/opt/my-ffmpeg/bin/ffmpeg")
        assert HealthService.get_custom_ffmpeg_path() == "/opt/my-ffmpeg/bin/ffmpeg"


def test_resolve_ffmpeg_binaries_manual(tmp_path: Path):
    """Test resolve_ffmpeg_binaries in manual mode."""
    from video_cut.utils import resolve_ffmpeg_binaries

    custom_bin = tmp_path / "custom_ffmpeg"
    custom_bin.write_text("#!/bin/sh\n", encoding="utf-8")
    custom_bin.chmod(0o755)

    custom_probe = tmp_path / "ffprobe"
    custom_probe.write_text("#!/bin/sh\n", encoding="utf-8")
    custom_probe.chmod(0o755)

    ff, probe, eff_src, note = resolve_ffmpeg_binaries(source="manual", custom_path=str(custom_bin))
    assert ff == str(custom_bin)
    assert probe == str(custom_probe)
    assert eff_src == "manual"


def test_resolve_ffmpeg_binaries_container_fallback_to_system():
    """Test container source falls back to system if not running inside a container."""
    from video_cut.utils import resolve_ffmpeg_binaries

    with patch("video_cut.utils.get_container_ffmpeg_path", return_value=None), \
         patch("video_cut.utils.get_system_ffmpeg_path", return_value="/usr/bin/ffmpeg"), \
         patch("video_cut.utils.get_system_ffprobe_path", return_value="/usr/bin/ffprobe"):
        ff, probe, eff_src, note = resolve_ffmpeg_binaries(source="container")
        assert ff == "/usr/bin/ffmpeg"
        assert eff_src == "system"


def test_resolve_ffmpeg_binaries_auto_hwaccel():
    """Test auto mode prefers system FFmpeg when it has hardware acceleration and essential codecs."""
    from video_cut.utils import resolve_ffmpeg_binaries

    with patch("video_cut.utils.get_container_ffmpeg_path", return_value="/appdir/usr/bin/ffmpeg"), \
         patch("video_cut.utils.get_container_ffprobe_path", return_value="/appdir/usr/bin/ffprobe"), \
         patch("video_cut.utils.get_system_ffmpeg_path", return_value="/usr/bin/ffmpeg"), \
         patch("video_cut.utils.get_system_ffprobe_path", return_value="/usr/bin/ffprobe"), \
         patch("subprocess.run") as mock_run, \
         patch("video_cut.utils.probe_binary_encoders", return_value={"libx264", "aac", "h264_vaapi"}):
        mock_run.return_value = MagicMock(returncode=0, stdout="ffmpeg version 6.1.1\n")
        ff, probe, eff_src, note = resolve_ffmpeg_binaries(source="auto")
        assert ff == "/usr/bin/ffmpeg"
        assert eff_src == "system"
        assert "hardware acceleration" in (note or "")


def test_resolve_ffmpeg_binaries_auto_degraded_codecs():
    """Test auto mode prefers container when system FFmpeg lacks essential codecs (e.g. ffmpeg-free)."""
    from video_cut.utils import resolve_ffmpeg_binaries

    with patch("video_cut.utils.get_container_ffmpeg_path", return_value="/appdir/usr/bin/ffmpeg"), \
         patch("video_cut.utils.get_container_ffprobe_path", return_value="/appdir/usr/bin/ffprobe"), \
         patch("video_cut.utils.get_system_ffmpeg_path", return_value="/usr/bin/ffmpeg"), \
         patch("video_cut.utils.get_system_ffprobe_path", return_value="/usr/bin/ffprobe"), \
         patch("subprocess.run") as mock_run, \
         patch("video_cut.utils.probe_binary_encoders", return_value={"aac", "h264_vaapi"}):  # Missing libx264!
        mock_run.return_value = MagicMock(returncode=0, stdout="ffmpeg version 6.1.1\n")
        ff, probe, eff_src, note = resolve_ffmpeg_binaries(source="auto")
        assert ff == "/appdir/usr/bin/ffmpeg"
        assert eff_src == "container"
        assert "essential codecs" in (note or "")


def test_resolve_ffmpeg_binaries_auto_outdated_version():
    """Test auto mode prefers container when system FFmpeg version is < 4.3."""
    from video_cut.utils import resolve_ffmpeg_binaries

    with patch("video_cut.utils.get_container_ffmpeg_path", return_value="/appdir/usr/bin/ffmpeg"), \
         patch("video_cut.utils.get_container_ffprobe_path", return_value="/appdir/usr/bin/ffprobe"), \
         patch("video_cut.utils.get_system_ffmpeg_path", return_value="/usr/bin/ffmpeg"), \
         patch("video_cut.utils.get_system_ffprobe_path", return_value="/usr/bin/ffprobe"), \
         patch("subprocess.run") as mock_run, \
         patch("video_cut.utils.probe_binary_encoders", return_value={"libx264", "aac", "h264_vaapi"}):
        mock_run.return_value = MagicMock(returncode=0, stdout="ffmpeg version 3.4.8\n")
        ff, probe, eff_src, note = resolve_ffmpeg_binaries(source="auto")
        assert ff == "/appdir/usr/bin/ffmpeg"
        assert eff_src == "container"
        assert "older than required" in (note or "")


def test_check_ffmpeg_outdated_warning():
    """Test check_ffmpeg returns warning status when version is < 4.3."""
    with patch("shutil.which", return_value="/usr/bin/ffmpeg"), \
         patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="ffmpeg version 3.4.8\n")
        item = HealthService.check_ffmpeg()
        assert item.status == "warning"
        assert "3.4" in item.summary
        assert item.can_fix is True


def test_detect_os_info_macos():
    """Test detect_os_info on macOS platform."""
    with patch("sys.platform", "darwin"), \
         patch("platform.mac_ver", return_value=("14.5", ("", "", ""), "arm64")), \
         patch("platform.machine", return_value="arm64"):
        info = HealthService.detect_os_info()
        assert info.get("id") == "macos"
        assert info.get("version") == "14.5"
        assert info.get("arch") == "arm64"
        assert "macOS 14.5 (arm64)" in info.get("name", "")


def test_detect_package_manager_macos_brew():
    """Test detection of brew package manager on macOS."""
    with patch("sys.platform", "darwin"), \
         patch("shutil.which", return_value="/opt/homebrew/bin/brew"):
        mgr, cmd, gui, extra_pkg = HealthService.detect_package_manager()
        assert mgr == "brew"
        assert cmd == "brew install ffmpeg"
        assert gui is None
        assert extra_pkg is None


def test_detect_package_manager_macos_port():
    """Test detection of MacPorts when brew is not installed."""
    with patch("sys.platform", "darwin"), \
         patch("shutil.which", side_effect=lambda x: "/opt/local/bin/port" if x == "port" else None), \
         patch("pathlib.Path.is_file", return_value=False):
        mgr, cmd, gui, extra_pkg = HealthService.detect_package_manager()
        assert mgr == "port"
        assert cmd == "sudo port install ffmpeg"


def test_check_ffmpeg_macos_upgrade_fix():
    """Test check_ffmpeg upgrade command on macOS uses brew upgrade."""
    with patch("sys.platform", "darwin"), \
         patch("shutil.which", return_value="/opt/homebrew/bin/ffmpeg"), \
         patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="ffmpeg version 3.4.8\n")
        with patch.object(HealthService, "detect_package_manager", return_value=("brew", "brew install ffmpeg", None, None)):
            item = HealthService.check_ffmpeg()
            assert item.status == "warning"
            assert item.fix_command == "brew upgrade ffmpeg"
            assert item.fix_url == "https://brew.sh"


def test_check_file_association_macos():
    """Test check_file_association returns ok on macOS via LaunchServices."""
    with patch("sys.platform", "darwin"):
        item = HealthService.check_file_association()
        assert item.status == "ok"
        assert item.can_fix is False
        assert "LaunchServices" in item.details


def test_fix_and_remove_file_association_macos():
    """Test fix_file_association and remove_file_association on macOS."""
    with patch("sys.platform", "darwin"):
        ok_fix, msg_fix = HealthService.fix_file_association()
        assert ok_fix is True
        assert "LaunchServices" in msg_fix

        ok_rem, msg_rem = HealthService.remove_file_association()
        assert ok_rem is True
        assert "LaunchServices" in msg_rem


def test_get_hwaccel_overview_macos():
    """Test get_hwaccel_overview on macOS reports Apple VideoToolbox only."""
    with patch("sys.platform", "darwin"), \
         patch("video_cut.services.export_service.FFmpegCapabilities.get_encoders", return_value={"h264_videotoolbox", "hevc_videotoolbox", "prores_videotoolbox"}):
        hw = HealthService.get_hwaccel_overview()
        assert len(hw) == 1
        assert hw[0].name == "Apple VideoToolbox"
        assert hw[0].driver_key == "videotoolbox"
        assert hw[0].is_available is True
        assert "h264_videotoolbox" in hw[0].encoders




