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


def test_resolve_ffmpeg_binaries_auto_container_has_hwaccel():
    """Test auto mode prefers container FFmpeg when it has both essential codecs and hardware acceleration."""
    from video_cut.utils import resolve_ffmpeg_binaries

    with patch("video_cut.utils.get_container_ffmpeg_path", return_value="/appdir/usr/bin/ffmpeg"), \
         patch("video_cut.utils.get_container_ffprobe_path", return_value="/appdir/usr/bin/ffprobe"), \
         patch("video_cut.utils.get_system_ffmpeg_path", return_value="/usr/bin/ffmpeg"), \
         patch("video_cut.utils.get_system_ffprobe_path", return_value="/usr/bin/ffprobe"), \
         patch("video_cut.utils.probe_binary_encoders", return_value={"libx264", "aac", "h264_vaapi"}):
        ff, probe, eff_src, note = resolve_ffmpeg_binaries(source="auto")
        assert ff == "/appdir/usr/bin/ffmpeg"
        assert eff_src == "container"
        assert "essential codecs and hardware acceleration supported" in (note or "")


def test_resolve_ffmpeg_binaries_auto_system_has_hwaccel():
    """Test auto mode prefers system FFmpeg when system has hardware acceleration and container lacks it."""
    from video_cut.utils import resolve_ffmpeg_binaries

    def fake_encoders(binary_path: str):
        if "appdir" in binary_path:
            return {"libx264", "aac"}  # Container has software only
        return {"libx264", "aac", "h264_vaapi"}  # System has hardware acceleration

    with patch("video_cut.utils.get_container_ffmpeg_path", return_value="/appdir/usr/bin/ffmpeg"), \
         patch("video_cut.utils.get_container_ffprobe_path", return_value="/appdir/usr/bin/ffprobe"), \
         patch("video_cut.utils.get_system_ffmpeg_path", return_value="/usr/bin/ffmpeg"), \
         patch("video_cut.utils.get_system_ffprobe_path", return_value="/usr/bin/ffprobe"), \
         patch("subprocess.run") as mock_run, \
         patch("video_cut.utils.probe_binary_encoders", side_effect=fake_encoders):
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

    def fake_encoders(binary_path: str):
        if "appdir" in binary_path:
            return {"libx264", "aac"}  # Container has software only
        return {"libx264", "aac", "h264_vaapi"}  # System has hwaccel, but version is old

    with patch("video_cut.utils.get_container_ffmpeg_path", return_value="/appdir/usr/bin/ffmpeg"), \
         patch("video_cut.utils.get_container_ffprobe_path", return_value="/appdir/usr/bin/ffprobe"), \
         patch("video_cut.utils.get_system_ffmpeg_path", return_value="/usr/bin/ffmpeg"), \
         patch("video_cut.utils.get_system_ffprobe_path", return_value="/usr/bin/ffprobe"), \
         patch("subprocess.run") as mock_run, \
         patch("video_cut.utils.probe_binary_encoders", side_effect=fake_encoders):
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


def test_is_flatpak_detection():
    """Test is_flatpak helper with environment variable and filesystem marker."""
    from video_cut.utils import is_flatpak

    with patch.dict("os.environ", {"FLATPAK_ID": "io.github.thor27.video-cut"}):
        assert is_flatpak() is True

    with patch.dict("os.environ", {}, clear=True), \
         patch("pathlib.Path.exists", return_value=True):
        assert is_flatpak() is True

    with patch.dict("os.environ", {}, clear=True), \
         patch("pathlib.Path.exists", return_value=False):
        assert is_flatpak() is False


def test_resolve_ffmpeg_binaries_flatpak():
    """Test FFmpeg resolution inside Flatpak sandbox uses runtime and reports isolation."""
    from video_cut.utils import resolve_ffmpeg_binaries

    with patch("video_cut.utils.is_flatpak", return_value=True), \
         patch("video_cut.utils.get_container_ffmpeg_path", return_value="/usr/bin/ffmpeg"), \
         patch("video_cut.utils.get_container_ffprobe_path", return_value="/usr/bin/ffprobe"):
        # Auto mode selects Flatpak runtime
        ff, probe, eff_src, note = resolve_ffmpeg_binaries(source="auto")
        assert ff == "/usr/bin/ffmpeg"
        assert probe == "/usr/bin/ffprobe"
        assert eff_src == "container"
        assert "Flatpak runtime" in (note or "")

        # System mode falls back with informative isolation note
        ff2, probe2, eff_src2, note2 = resolve_ffmpeg_binaries(source="system")
        assert ff2 == "/usr/bin/ffmpeg"
        assert eff_src2 == "container"
        assert "Flatpak sandbox" in (note2 or "")


def test_check_file_association_flatpak(tmp_path: Path):
    """Test check_file_association inside Flatpak returns ok and cleans up broken desktop entry."""
    broken_apps = tmp_path / ".local" / "share" / "applications"
    broken_apps.mkdir(parents=True, exist_ok=True)
    broken_desktop = broken_apps / "io.github.thor27.video-cut.desktop"
    broken_desktop.write_text("[Desktop Entry]\nExec=video-cut %U\n", encoding="utf-8")

    with patch("video_cut.services.health_service.is_flatpak", return_value=True), \
         patch("pathlib.Path.home", return_value=tmp_path):
        item = HealthService.check_file_association()
        assert item.status == "ok"
        assert item.can_fix is False
        assert "Flatpak" in item.summary
        # Broken desktop entry with raw Exec=video-cut must be removed to avoid shadowing
        assert not broken_desktop.exists()


def test_fix_and_remove_file_association_flatpak(tmp_path: Path):
    """Test fix_file_association and remove_file_association inside Flatpak."""
    with patch("video_cut.services.health_service.is_flatpak", return_value=True), \
         patch("pathlib.Path.home", return_value=tmp_path):
        ok_fix, msg_fix = HealthService.fix_file_association()
        assert ok_fix is True
        assert "Flatpak" in msg_fix
        # No files written to fake user home
        assert not (tmp_path / ".local" / "share" / "applications" / "io.github.thor27.video-cut.desktop").exists()

        ok_rem, msg_rem = HealthService.remove_file_association()
        assert ok_rem is False
        assert "Flatpak" in msg_rem


def test_packaging_type_detection(tmp_path: Path):
    """Test packaging distribution type detection for flatpak, snap, appimage, deb, and source."""
    from video_cut import utils
    from video_cut.i18n import set_language

    set_language("en_US")

    # 1. Flatpak
    with patch("video_cut.utils.is_flatpak", return_value=True):
        assert utils.get_packaging_type() == "flatpak"
        assert utils.get_packaging_type_display_name() == "Flatpak"

    # 2. Snap
    with patch("video_cut.utils.is_flatpak", return_value=False), \
         patch.dict("os.environ", {"SNAP": "/snap/video-cut/current"}):
        assert utils.is_snap() is True
        assert utils.get_packaging_type() == "snap"
        assert utils.get_packaging_type_display_name() == "Snap"

    # 3. AppImage
    with patch("video_cut.utils.is_flatpak", return_value=False), \
         patch.dict("os.environ", {"APPIMAGE": "/path/to/VideoCut.AppImage"}, clear=True):
        assert utils.is_appimage() is True
        assert utils.get_packaging_type() == "appimage"
        assert utils.get_packaging_type_display_name() == "AppImage"

    # 4. Debian package (explicit verification, not an else-fallback)
    with patch("video_cut.utils.is_flatpak", return_value=False), \
         patch.dict("os.environ", {}, clear=True), \
         patch("video_cut.utils.is_source_distribution", return_value=False), \
         patch.object(Path, "resolve", return_value=Path("/usr/lib/python3/dist-packages/video_cut/utils.py")), \
         patch("shutil.which", return_value="/usr/bin/dpkg-query"), \
         patch("subprocess.run") as mock_run:
        def fake_dpkg(cmd, **kwargs):
            if "-W" in cmd:
                return MagicMock(returncode=0, stdout="install ok installed")
            if "-S" in cmd:
                return MagicMock(returncode=0, stdout="video-cut: /usr/lib/python3/dist-packages/video_cut/utils.py")
            return MagicMock(returncode=1, stdout="")

        mock_run.side_effect = fake_dpkg
        assert utils.is_deb_package() is True
        assert utils.get_packaging_type() == "deb"
        assert utils.get_packaging_type_display_name() == "Debian package"

    # 5. Non-deb system installation does NOT fall back to deb
    with patch("video_cut.utils.is_flatpak", return_value=False), \
         patch.dict("os.environ", {}, clear=True), \
         patch("video_cut.utils.is_source_distribution", return_value=False), \
         patch("video_cut.utils.is_deb_package", return_value=False):
        assert utils.get_packaging_type() == "unknown"
        assert utils.get_packaging_type_display_name() == ""

    # 6. Source / git development checkout (takes precedence even if deb installed on OS)
    with patch("video_cut.utils.is_flatpak", return_value=False), \
         patch.dict("os.environ", {}, clear=True), \
         patch("video_cut.utils.is_source_distribution", return_value=True):
        assert utils.is_source_distribution() is True
        assert utils.is_deb_package() is False
        assert utils.get_packaging_type() == "source"
        with patch("video_cut.utils.get_git_commit_hash", return_value="9b2c490"):
            assert utils.get_packaging_type_display_name() == "git-9b2c490"
        with patch("video_cut.utils.get_git_commit_hash", return_value=None):
            assert utils.get_packaging_type_display_name() == "Source distribution"


def test_snap_ffmpeg_isolation():
    """Test that Snap environment prevents accessing host system binaries."""
    from video_cut.utils import (
        get_container_ffmpeg_path,
        get_system_ffmpeg_path,
        get_system_ffprobe_path,
        resolve_ffmpeg_binaries,
    )

    with patch("video_cut.utils.is_snap", return_value=True), \
         patch.dict("os.environ", {"SNAP": "/snap/video-cut/current"}):
        assert get_system_ffmpeg_path() is None
        assert get_system_ffprobe_path() is None

        # Container path resolution
        with patch("pathlib.Path.is_file", return_value=True), \
             patch("os.access", return_value=True):
            snap_ffmpeg = get_container_ffmpeg_path()
            assert snap_ffmpeg is not None
            assert "snap" in snap_ffmpeg or "/usr/bin/ffmpeg" in snap_ffmpeg

            # Resolve binaries in snap returns container
            ff, probe, eff_src, note = resolve_ffmpeg_binaries(source="auto")
            assert eff_src == "container"
            assert "Snap runtime" in (note or "")


def test_snap_file_association(tmp_path: Path):
    """Test HealthService file association performs proper checks and allows fix/remove under Snap."""
    from video_cut.i18n import set_language

    set_language("en_US")
    # 1. Unregistered MIME under Snap warns and allows fix
    with patch("video_cut.services.health_service.is_flatpak", return_value=False), \
         patch("video_cut.services.health_service.is_snap", return_value=True), \
         patch("pathlib.Path.home", return_value=tmp_path), \
         patch.dict("os.environ", {"SNAP": "/snap/video-cut/current", "SNAP_REAL_HOME": str(tmp_path)}), \
         patch.object(HealthService, "_is_vcut_mime_registered", return_value=False), \
         patch("gi.repository.Gio.content_type_guess", return_value=("application/octet-stream", False)):
        item = HealthService.check_file_association()
        assert item.status == "warning"
        assert item.can_fix is True
        assert item.fix_label is not None

        # Fix creates files and returns success
        ok_fix, fix_msg = HealthService.fix_file_association()
        assert ok_fix is True
        assert (tmp_path / ".local" / "share" / "mime" / "packages" / "io.github.thor27.video-cut.xml").exists()
        assert (tmp_path / ".local" / "share" / "applications" / "io.github.thor27.video-cut.desktop").exists()

        # Removal is allowed under Snap
        assert HealthService.can_remove_file_association() is True
        ok_rem, rem_msg = HealthService.remove_file_association()
        assert ok_rem is True
        assert not (tmp_path / ".local" / "share" / "mime" / "packages" / "io.github.thor27.video-cut.xml").exists()
        assert not (tmp_path / ".local" / "share" / "applications" / "io.github.thor27.video-cut.desktop").exists()


def test_get_git_commit_hash():
    """Test get_git_commit_hash returns short commit hash from git."""
    from video_cut.utils import get_git_commit_hash

    commit = get_git_commit_hash()
    assert commit is not None
    assert len(commit) >= 7
    assert all(c in "0123456789abcdefABCDEF" for c in commit)







