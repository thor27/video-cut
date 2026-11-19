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

    with patch("gi.repository.Gio.AppInfo.get_default_for_type", return_value=mock_app_info):
        item = HealthService.check_file_association()
        assert item.status == "ok"
        assert item.can_fix is False


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
