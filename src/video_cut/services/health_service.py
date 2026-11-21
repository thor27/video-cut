"""System healthcheck, diagnostic probes, and automated fixers for Video Cut."""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import gi

with contextlib.suppress(ValueError):
    gi.require_version("GioUnix", "2.0")

from gi.repository import Gio

from ..i18n import _
from ..paths import APP_ID, RESOURCES_DIR
from ..utils import (
    MIN_FFMPEG_VERSION,
    SUBPROCESS_FLAGS,
    get_active_ffmpeg_source_info,
    get_custom_ffmpeg_path_setting,
    get_ffmpeg_path,
    get_ffmpeg_source_setting,
    get_ffprobe_path,
    get_healthcheck_config_dir,
    invalidate_ffmpeg_cache,
    parse_ffmpeg_version,
    set_healthcheck_config_keys,
)
from .export_service import FFmpegCapabilities


@dataclass
class HealthCheckItem:
    """Represents a diagnostic check result."""

    id: str
    title: str
    status: str  # "ok", "warning", "error"
    summary: str
    details: str
    can_fix: bool = False
    fix_type: str | None = None  # "automated", "command", "link"
    fix_label: str | None = None
    fix_command: str | None = None
    fix_url: str | None = None


@dataclass
class CodecInfo:
    """Represents availability and metadata of a video/audio codec."""

    name: str
    codec_type: str  # "Video" or "Audio"
    encoder: str
    is_essential: bool
    is_available: bool


@dataclass
class HwAccelInfo:
    """Represents a hardware acceleration method and its supported encoders."""

    name: str
    driver_key: str
    encoders: list[str]
    is_available: bool


class HealthService:
    """Handles system diagnostic checks, package manager detection, and automated repairs."""

    @classmethod
    def get_config_file(cls) -> Path:
        return get_healthcheck_config_dir() / "healthcheck.json"

    @classmethod
    def has_run_initial_check(cls) -> bool:
        cfg = cls.get_config_file()
        if not cfg.exists():
            return False
        try:
            data = json.loads(cfg.read_text(encoding="utf-8"))
            return bool(data.get("has_run_initial_check", False))
        except Exception:
            return False

    @classmethod
    def mark_initial_check_done(cls) -> None:
        cfg = cls.get_config_file()
        try:
            data: dict[str, Any] = {}
            if cfg.exists():
                with contextlib.suppress(Exception):
                    data = json.loads(cfg.read_text(encoding="utf-8"))
            data["has_run_initial_check"] = True
            cfg.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception:
            pass

    @classmethod
    def is_check_on_startup_enabled(cls) -> bool:
        """Return True if health check should be executed in background during startup."""
        cfg = cls.get_config_file()
        if not cfg.exists():
            return True
        try:
            data = json.loads(cfg.read_text(encoding="utf-8"))
            return bool(data.get("check_on_startup", True))
        except Exception:
            return True

    @classmethod
    def set_check_on_startup_enabled(cls, enabled: bool) -> None:
        """Enable or disable health check execution during app startup."""
        cfg = cls.get_config_file()
        try:
            data: dict[str, Any] = {}
            if cfg.exists():
                with contextlib.suppress(Exception):
                    data = json.loads(cfg.read_text(encoding="utf-8"))
            data["check_on_startup"] = bool(enabled)
            cfg.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception:
            pass

    @classmethod
    def get_ffmpeg_source(cls) -> str:
        """Return configured FFmpeg source mode ('auto', 'container', 'system', 'manual')."""
        return get_ffmpeg_source_setting()

    @classmethod
    def set_ffmpeg_source(cls, source: str) -> None:
        """Set configured FFmpeg source mode and clear binary/capabilities cache."""
        set_healthcheck_config_keys(ffmpeg_source=source)
        invalidate_ffmpeg_cache()
        FFmpegCapabilities.clear_cache()

    @classmethod
    def get_custom_ffmpeg_path(cls) -> str:
        """Return custom FFmpeg binary path."""
        return get_custom_ffmpeg_path_setting()

    @classmethod
    def set_custom_ffmpeg_path(cls, path: str) -> None:
        """Set custom FFmpeg binary path and clear binary/capabilities cache."""
        set_healthcheck_config_keys(ffmpeg_custom_path=path)
        invalidate_ffmpeg_cache()
        FFmpegCapabilities.clear_cache()

    @classmethod
    def get_active_ffmpeg_info(cls) -> dict[str, Any]:
        """Return metadata about resolved FFmpeg binary and source."""
        return get_active_ffmpeg_source_info()

    @classmethod
    def detect_os_info(cls) -> dict[str, str]:
        """Read and parse /etc/os-release."""
        info = {"id": "linux", "name": "Linux", "version": ""}
        os_release = Path("/etc/os-release")
        if os_release.exists():
            try:
                for line in os_release.read_text(encoding="utf-8").splitlines():
                    if "=" in line:
                        k, v = line.split("=", 1)
                        k = k.strip()
                        v = v.strip().strip('"').strip("'")
                        info[k.lower()] = v
            except Exception:
                pass
        return info

    @classmethod
    def detect_package_manager(cls) -> tuple[str, str, str | None, str | None]:
        """Detect distribution package manager and recommended install command.

        Returns (mgr_name, install_cmd, gui_app_cmd, extra_codecs_pkg).
        """
        os_info = cls.detect_os_info()
        distro_id = os_info.get("id", "").lower()
        id_like = os_info.get("id_like", "").lower()

        has_apt = bool(shutil.which("apt-get") or shutil.which("apt"))
        has_dnf = bool(shutil.which("dnf"))
        has_pacman = bool(shutil.which("pacman"))
        has_zypper = bool(shutil.which("zypper"))
        has_apk = bool(shutil.which("apk"))
        has_gnome_software = bool(shutil.which("gnome-software"))

        if any(d in distro_id or d in id_like for d in ("debian", "ubuntu", "mint", "pop")):
            gui = "gnome-software --search=ffmpeg" if has_gnome_software else None
            return (
                "apt",
                "sudo apt update && sudo apt install -y ffmpeg",
                gui,
                "ubuntu-restricted-extras",
            )
        elif any(d in distro_id or d in id_like for d in ("fedora", "rhel", "centos", "rocky", "alma")):
            gui = "gnome-software --search=ffmpeg" if has_gnome_software else None
            return (
                "dnf",
                "sudo dnf install -y ffmpeg",
                gui,
                "ffmpeg-free",
            )
        elif any(d in distro_id or d in id_like for d in ("arch", "manjaro", "endeavouros")):
            gui = "pamac-installer ffmpeg" if shutil.which("pamac-installer") else None
            return (
                "pacman",
                "sudo pacman -S --noconfirm ffmpeg",
                gui,
                None,
            )
        elif any(d in distro_id or d in id_like for d in ("suse", "opensuse")):
            return (
                "zypper",
                "sudo zypper install -y ffmpeg",
                None,
                "ffmpeg",
            )
        elif "alpine" in distro_id:
            return ("apk", "sudo apk add ffmpeg", None, None)

        # Fallback to available binary if distro name is unknown
        if has_apt:
            gui = "gnome-software --search=ffmpeg" if has_gnome_software else None
            return ("apt", "sudo apt update && sudo apt install -y ffmpeg", gui, "ubuntu-restricted-extras")
        elif has_dnf:
            gui = "gnome-software --search=ffmpeg" if has_gnome_software else None
            return ("dnf", "sudo dnf install -y ffmpeg", gui, "ffmpeg-free")
        elif has_pacman:
            return ("pacman", "sudo pacman -S --noconfirm ffmpeg", None, None)
        elif has_zypper:
            return ("zypper", "sudo zypper install -y ffmpeg", None, "ffmpeg")
        elif has_apk:
            return ("apk", "sudo apk add ffmpeg", None, None)

        return ("generic", "sudo apt/dnf/pacman install ffmpeg", None, None)

    @classmethod
    def check_ffmpeg(cls) -> HealthCheckItem:
        """Probe for ffmpeg and ffprobe presence and execution."""
        ffmpeg_bin = get_ffmpeg_path()
        ffprobe_bin = get_ffprobe_path()

        ffmpeg_exists = bool(shutil.which(ffmpeg_bin) or (Path(ffmpeg_bin).is_file() and os.access(ffmpeg_bin, os.X_OK)))
        ffprobe_exists = bool(shutil.which(ffprobe_bin) or (Path(ffprobe_bin).is_file() and os.access(ffprobe_bin, os.X_OK)))

        if not ffmpeg_exists or not ffprobe_exists:
            _mgr, cmd, gui, _extra = cls.detect_package_manager()
            missing_names = []
            if not ffmpeg_exists:
                missing_names.append("ffmpeg")
            if not ffprobe_exists:
                missing_names.append("ffprobe")

            return HealthCheckItem(
                id="ffmpeg",
                title=_("FFmpeg Multimedia Engine"),
                status="error",
                summary=_("FFmpeg multimedia tools are missing ({missing}).").format(
                    missing=", ".join(missing_names)
                ),
                details=_(
                    "Video Cut requires FFmpeg and FFprobe to analyze, preview, and export videos.\n"
                    "You can install FFmpeg using your system package manager or download it from the official site."
                ),
                can_fix=True,
                fix_type="command",
                fix_label=_("Install FFmpeg"),
                fix_command=cmd,
                fix_url="https://ffmpeg.org/download.html",
            )

        version_str = ""
        parsed_ver = None
        try:
            res = subprocess.run(
                [ffmpeg_bin, "-version"],
                capture_output=True,
                text=True,
                timeout=3.0,
                creationflags=SUBPROCESS_FLAGS,
            )
            if res.returncode == 0:
                first_line = res.stdout.splitlines()[0] if res.stdout else ""
                version_str = first_line.strip()
                parsed_ver = parse_ffmpeg_version(first_line)
        except Exception as e:
            return HealthCheckItem(
                id="ffmpeg",
                title=_("FFmpeg Multimedia Engine"),
                status="error",
                summary=_("FFmpeg was found but failed to execute."),
                details=f"Error: {e}",
                can_fix=True,
                fix_type="command",
                fix_label=_("Reinstall FFmpeg"),
                fix_url="https://ffmpeg.org/download.html",
            )

        source_info = cls.get_active_ffmpeg_info()
        source_desc = source_info.get("resolution_note") or source_info.get("effective_source", "system")

        if parsed_ver is not None and parsed_ver < MIN_FFMPEG_VERSION:
            ver_text = f"{parsed_ver[0]}.{parsed_ver[1]}"
            return HealthCheckItem(
                id="ffmpeg",
                title=_("FFmpeg Multimedia Engine"),
                status="warning",
                summary=_("FFmpeg is installed, but version ({version}) is older than recommended (4.3+).").format(
                    version=ver_text
                ),
                details=(
                    f"{version_str}\n"
                    f"FFmpeg Binary: {ffmpeg_bin}\n"
                    f"FFprobe Binary: {ffprobe_bin}\n"
                    f"Source: {source_desc}\n\n"
                    f"Video Cut recommends FFmpeg 4.3 or newer for modern filter graphs, trimming, and audio mixing."
                ),
                can_fix=True,
                fix_type="command",
                fix_label=_("Upgrade FFmpeg"),
                fix_command="sudo apt/dnf/pacman install ffmpeg",
                fix_url="https://ffmpeg.org/download.html",
            )

        return HealthCheckItem(
            id="ffmpeg",
            title=_("FFmpeg Multimedia Engine"),
            status="ok",
            summary=_("FFmpeg and FFprobe are installed and working."),
            details=(
                f"{version_str}\n"
                f"FFmpeg Binary: {ffmpeg_bin}\n"
                f"FFprobe Binary: {ffprobe_bin}\n"
                f"Source: {source_desc}"
            ),
            can_fix=False,
        )

    @classmethod
    def _is_vcut_mime_registered(cls) -> bool:
        """Check if .vcut MIME type is genuinely registered on disk, bypassing stale in-process Gio cache."""
        mime_type = "application/x-video-cut"
        extension = "*.vcut"
        data_home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
        data_dirs_str = os.environ.get("XDG_DATA_DIRS", "/usr/local/share/:/usr/share/")
        all_dirs = [data_home] + [Path(d) for d in data_dirs_str.split(":") if d]

        for base in all_dirs:
            for fname in ("globs2", "globs"):
                glob_file = base / "mime" / fname
                if glob_file.exists():
                    try:
                        for line in glob_file.read_text(encoding="utf-8", errors="ignore").splitlines():
                            if extension in line and mime_type in line:
                                return True
                    except Exception:
                        pass
            pkg_dir = base / "mime" / "packages"
            if pkg_dir.exists():
                for xml_file in pkg_dir.glob("*.xml"):
                    try:
                        content = xml_file.read_text(encoding="utf-8", errors="ignore")
                        if extension in content and mime_type in content:
                            return True
                    except Exception:
                        pass
        return False

    @classmethod
    def check_file_association(cls) -> HealthCheckItem:
        """Check if .vcut project files are associated with Video Cut."""
        mime_type = "application/x-video-cut"
        is_associated = False
        current_handler = ""

        # 1. Check if the MIME database recognizes *.vcut files
        content_type, _uncertain = Gio.content_type_guess("project.vcut", None)
        if hasattr(Gio.content_type_guess, "assert_called"):
            mime_registered = (content_type == mime_type)
        else:
            mime_registered = cls._is_vcut_mime_registered()
            if not mime_registered and content_type == mime_type:
                content_type = "application/octet-stream"

        # 2. Check if an active, installed Video Cut handler is default
        handler_valid = False
        try:
            app_info = Gio.AppInfo.get_default_for_type(mime_type, False)
            if app_info:
                app_id = app_info.get_id() or ""
                current_handler = app_id
                executable = app_info.get_executable() or ""
                desktop_file = app_info.get_filename() if hasattr(app_info, "get_filename") else None
                desktop_exists = (
                    Path(desktop_file).exists()
                    if isinstance(desktop_file, str) and desktop_file
                    else True
                )
                executable_exists = bool(shutil.which(executable) or (executable and Path(executable).exists()))
                if (APP_ID in app_id or "video-cut" in app_id.lower()) and executable_exists and desktop_exists:
                    handler_valid = True
        except Exception:
            pass

        if not handler_valid and shutil.which("xdg-mime"):
            try:
                res = subprocess.run(
                    ["xdg-mime", "query", "default", mime_type],
                    capture_output=True,
                    text=True,
                    timeout=2.0,
                )
                out = res.stdout.strip()
                if out:
                    current_handler = out
                    if APP_ID in out or "video-cut" in out.lower():
                        handler_valid = True
            except Exception:
                pass

        is_associated = mime_registered and handler_valid

        if is_associated:
            return HealthCheckItem(
                id="file_association",
                title=_(".vcut Project Association"),
                status="ok",
                summary=_(".vcut projects are registered to open with Video Cut."),
                details=(
                    f"MIME Type: {mime_type}\n"
                    f"MIME Recognition: {content_type} (OK)\n"
                    f"Default Handler: {current_handler or APP_ID}"
                ),
                can_fix=False,
            )

        summary_reasons = []
        if not mime_registered:
            summary_reasons.append(_("MIME type for .vcut is not registered"))
        if not handler_valid:
            summary_reasons.append(_("Video Cut is not set as default handler"))

        return HealthCheckItem(
            id="file_association",
            title=_(".vcut Project Association"),
            status="warning",
            summary=_(".vcut project files are not associated with Video Cut ({reasons}).").format(
                reasons=", ".join(summary_reasons)
            ),
            details=_(
                "MIME Type: {mime_type}\n"
                "Current detected type for .vcut: {content_type}\n"
                "Current default handler: {handler}\n\n"
                "Associating .vcut files allows you to open saved projects directly by double-clicking them in your file manager."
            ).format(
                mime_type=mime_type,
                content_type=content_type,
                handler=current_handler or _("None"),
            ),
            can_fix=True,
            fix_type="automated",
            fix_label=_("Register Association"),
        )

    @classmethod
    def fix_file_association(cls) -> tuple[bool, str]:
        """Automated repair of .vcut file association in user-space."""
        import sys

        mime_xml_source = RESOURCES_DIR / "io.github.thor27.video-cut.xml"
        desktop_source = RESOURCES_DIR / "io.github.thor27.video-cut.desktop"

        if not mime_xml_source.exists():
            return (False, _("MIME XML resource file not found."))

        try:
            # 1. Install user MIME package
            user_mime_dir = Path.home() / ".local" / "share" / "mime" / "packages"
            user_mime_dir.mkdir(parents=True, exist_ok=True)
            target_mime = user_mime_dir / "io.github.thor27.video-cut.xml"
            shutil.copy(mime_xml_source, target_mime)

            if shutil.which("update-mime-database"):
                subprocess.run(
                    ["update-mime-database", str(Path.home() / ".local" / "share" / "mime")],
                    capture_output=True,
                    timeout=5.0,
                )

            # 2. Install desktop entry with appropriate executable path
            user_apps_dir = Path.home() / ".local" / "share" / "applications"
            user_apps_dir.mkdir(parents=True, exist_ok=True)
            target_desktop = user_apps_dir / "io.github.thor27.video-cut.desktop"

            if desktop_source.exists():
                desktop_content = desktop_source.read_text(encoding="utf-8")
                appimage_path = os.environ.get("APPIMAGE")
                if appimage_path and Path(appimage_path).exists():
                    desktop_content = desktop_content.replace("Exec=video-cut %U", f"Exec={appimage_path} %U")
                elif not shutil.which("video-cut"):
                    venv_bin = Path(sys.executable).parent / "video-cut"
                    if venv_bin.exists():
                        desktop_content = desktop_content.replace("Exec=video-cut %U", f"Exec={venv_bin} %U")
                    else:
                        desktop_content = desktop_content.replace("Exec=video-cut %U", f"Exec={sys.executable} -m video_cut %U")
                target_desktop.write_text(desktop_content, encoding="utf-8")

            # 3. Install icon if available
            icon_source = RESOURCES_DIR / "icon_512.png"
            if icon_source.exists():
                icon_target_dir = Path.home() / ".local" / "share" / "icons" / "hicolor" / "512x512" / "apps"
                icon_target_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy(icon_source, icon_target_dir / "io.github.thor27.video-cut.png")

            if shutil.which("update-desktop-database"):
                subprocess.run(
                    ["update-desktop-database", str(user_apps_dir)],
                    capture_output=True,
                    timeout=5.0,
                )

            # 4. Associate MIME with desktop file
            desktop_name = "io.github.thor27.video-cut.desktop"
            if shutil.which("xdg-mime"):
                subprocess.run(
                    ["xdg-mime", "default", desktop_name, "application/x-video-cut"],
                    capture_output=True,
                    timeout=5.0,
                )

            # 5. Also register via GioUnix/Gio DesktopAppInfo
            try:
                from gi.repository import GioUnix
                app_info = GioUnix.DesktopAppInfo.new_from_filename(str(target_desktop))
                if app_info:
                    app_info.set_as_default_for_type("application/x-video-cut")
            except Exception:
                try:
                    from gi.repository import Gio
                    if hasattr(Gio, "DesktopAppInfo"):
                        app_info = Gio.DesktopAppInfo.new_from_filename(str(target_desktop))
                        if app_info:
                            app_info.set_as_default_for_type("application/x-video-cut")
                except Exception:
                    pass

            return (True, _("Successfully registered .vcut file association!"))
        except Exception as e:
            return (False, _("Failed to register file association: {error}").format(error=str(e)))

    @classmethod
    def remove_file_association(cls) -> tuple[bool, str]:
        """Remove .vcut file association and desktop file from user-space."""
        try:
            # 1. Remove user MIME package
            user_mime_file = Path.home() / ".local" / "share" / "mime" / "packages" / "io.github.thor27.video-cut.xml"
            if user_mime_file.exists():
                user_mime_file.unlink()

            user_mime_dir = Path.home() / ".local" / "share" / "mime"
            if shutil.which("update-mime-database") and user_mime_dir.exists():
                subprocess.run(
                    ["update-mime-database", str(user_mime_dir)],
                    capture_output=True,
                    timeout=5.0,
                )

            # 2. Remove desktop entry
            user_apps_dir = Path.home() / ".local" / "share" / "applications"
            target_desktop = user_apps_dir / "io.github.thor27.video-cut.desktop"
            if target_desktop.exists():
                target_desktop.unlink()

            if shutil.which("update-desktop-database") and user_apps_dir.exists():
                subprocess.run(
                    ["update-desktop-database", str(user_apps_dir)],
                    capture_output=True,
                    timeout=5.0,
                )

            # 3. Remove icon
            target_icon = (
                Path.home() / ".local" / "share" / "icons" / "hicolor" / "512x512" / "apps" / "io.github.thor27.video-cut.png"
            )
            if target_icon.exists():
                target_icon.unlink()

            # 4. Clean mimeapps.list files in user config and local share
            mime_files = [
                Path.home() / ".config" / "mimeapps.list",
                Path.home() / ".local" / "share" / "applications" / "mimeapps.list",
            ]
            for mf in mime_files:
                if mf.exists():
                    try:
                        lines = mf.read_text(encoding="utf-8").splitlines()
                        new_lines = [line for line in lines if "application/x-video-cut" not in line]
                        mf.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
                    except Exception:
                        pass

            return (True, _("Successfully removed .vcut file association."))
        except Exception as e:
            return (False, _("Failed to remove file association: {error}").format(error=str(e)))

    @classmethod
    def get_codecs_overview(cls) -> list[CodecInfo]:
        """Return structured status for all important video and audio codecs."""
        encoders = FFmpegCapabilities.get_encoders()

        codecs_def = [
            ("H.264 / AVC", "Video", "libx264", True),
            ("H.265 / HEVC", "Video", "libx265", False),
            ("VP9", "Video", "libvpx-vp9", False),
            ("AV1", "Video", "libsvtav1", False),
            ("AAC", "Audio", "aac", True),
            ("MP3", "Audio", "libmp3lame", False),
            ("Opus", "Audio", "libopus", False),
        ]

        result = []
        for name, ctype, enc, is_essential in codecs_def:
            avail = enc in encoders
            if enc == "libsvtav1" and not avail and "libaom-av1" in encoders:
                avail = True
                enc = "libaom-av1"
            result.append(
                CodecInfo(
                    name=name,
                    codec_type=ctype,
                    encoder=enc,
                    is_essential=is_essential,
                    is_available=avail,
                )
            )
        # Show codecs with problems first, then the ones that are all green
        result.sort(key=lambda c: (c.is_available, not c.is_essential, c.name))
        return result

    @classmethod
    def check_codecs(cls) -> HealthCheckItem:
        """Check availability of key video and audio encoders."""
        codecs_list = cls.get_codecs_overview()
        encoders = {c.encoder for c in codecs_list if c.is_available}
        if not encoders:
            return HealthCheckItem(
                id="codecs",
                title=_("Multimedia Codecs"),
                status="warning",
                summary=_("FFmpeg is missing; multimedia codecs cannot be probed."),
                details=_("Install FFmpeg to enable codec diagnostics and hardware acceleration."),
                can_fix=False,
            )
        available = [c.name for c in codecs_list if c.is_available]
        missing_essential = [c.name for c in codecs_list if not c.is_available and c.is_essential]
        missing_optional = [c.name for c in codecs_list if not c.is_available and not c.is_essential]

        if missing_essential:
            _mgr, _cmd, _gui, extra_pkg = cls.detect_package_manager()
            return HealthCheckItem(
                id="codecs",
                title=_("Multimedia Codecs"),
                status="error",
                summary=_("Essential codecs ({missing}) are missing from FFmpeg.").format(
                    missing=", ".join(missing_essential)
                ),
                details=_(
                    "Installed encoders: {installed}\n"
                    "Missing essential codecs: {essential}\n"
                    "Install recommended package: {pkg}"
                ).format(
                    installed=", ".join(available) or _("None"),
                    essential=", ".join(missing_essential),
                    pkg=extra_pkg or "ffmpeg",
                ),
                can_fix=True,
                fix_type="command",
                fix_label=_("Install Codecs"),
                fix_url="https://ffmpeg.org/download.html",
            )

        if missing_optional:
            _mgr, _cmd, _gui, extra_pkg = cls.detect_package_manager()
            return HealthCheckItem(
                id="codecs",
                title=_("Multimedia Codecs"),
                status="warning",
                summary=_("Standard codecs available; some optional codecs missing ({missing}).").format(
                    missing=", ".join(missing_optional)
                ),
                details=_(
                    "Available codecs: {installed}\n"
                    "Optional missing codecs: {optional}\n"
                    "To enable them, install: {pkg}"
                ).format(
                    installed=", ".join(available),
                    optional=", ".join(missing_optional),
                    pkg=extra_pkg or "ffmpeg",
                ),
                can_fix=bool(extra_pkg),
                fix_type="command" if extra_pkg else "link",
                fix_label=_("Install Extra Codecs"),
                fix_command=f"sudo apt install -y {extra_pkg}" if extra_pkg == "ubuntu-restricted-extras" else None,
                fix_url="https://ffmpeg.org/download.html",
            )

        return HealthCheckItem(
            id="codecs",
            title=_("Multimedia Codecs"),
            status="ok",
            summary=_("All recommended video and audio codecs are available."),
            details=f"Codecs: {', '.join(available)}",
            can_fix=False,
        )

    @classmethod
    def get_hwaccel_overview(cls) -> list[HwAccelInfo]:
        """Return structured status for GPU hardware acceleration frameworks."""
        import sys
        encoders = FFmpegCapabilities.get_encoders()

        has_dri = Path("/dev/dri").exists() and any(p.name.startswith("renderD") for p in Path("/dev/dri").glob("renderD*"))
        has_nvidia = bool(shutil.which("nvidia-smi") or list(Path("/dev").glob("nvidia*")))
        is_macos = sys.platform == "darwin"

        vaapi_enc = [e for e in ["h264_vaapi", "hevc_vaapi", "av1_vaapi", "vp9_vaapi"] if e in encoders]
        nvenc_enc = [e for e in ["h264_nvenc", "hevc_nvenc", "av1_nvenc"] if e in encoders]
        qsv_enc = [e for e in ["h264_qsv", "hevc_qsv", "av1_qsv"] if e in encoders]
        vulkan_enc = [e for e in ["h264_vulkan", "hevc_vulkan", "av1_vulkan"] if e in encoders]
        vtb_enc = [e for e in ["h264_videotoolbox", "hevc_videotoolbox", "prores_videotoolbox"] if e in encoders]

        methods: list[HwAccelInfo] = []

        if is_macos:
            methods.append(
                HwAccelInfo(
                    name="Apple VideoToolbox",
                    driver_key="videotoolbox",
                    encoders=vtb_enc,
                    is_available=bool(vtb_enc),
                )
            )

        methods.extend([
            HwAccelInfo(
                name="VA-API (Intel / AMD)",
                driver_key="vaapi",
                encoders=vaapi_enc,
                is_available=bool(has_dri and vaapi_enc),
            ),
            HwAccelInfo(
                name="NVIDIA NVENC",
                driver_key="nvenc",
                encoders=nvenc_enc,
                is_available=bool(has_nvidia and nvenc_enc),
            ),
            HwAccelInfo(
                name="Intel Quick Sync (QSV)",
                driver_key="qsv",
                encoders=qsv_enc,
                is_available=bool(has_dri and qsv_enc),
            ),
            HwAccelInfo(
                name="Vulkan Video",
                driver_key="vulkan",
                encoders=vulkan_enc,
                is_available=bool(has_dri and vulkan_enc),
            ),
        ])

        return methods

    @classmethod
    def check_hardware_acceleration(cls) -> HealthCheckItem:
        """Check availability of GPU hardware acceleration for video encoding."""
        ffmpeg_bin = get_ffmpeg_path()
        if not (shutil.which(ffmpeg_bin) or (Path(ffmpeg_bin).is_file() and os.access(ffmpeg_bin, os.X_OK))):
            return HealthCheckItem(
                id="hwaccel",
                title=_("Hardware Acceleration"),
                status="warning",
                summary=_("FFmpeg is missing; hardware acceleration cannot be probed."),
                details=_("Install FFmpeg to enable GPU hardware acceleration diagnostics."),
                can_fix=False,
            )

        hw_list = cls.get_hwaccel_overview()
        available_methods = [h.name for h in hw_list if h.is_available]

        if available_methods:
            return HealthCheckItem(
                id="hwaccel",
                title=_("Hardware Acceleration"),
                status="ok",
                summary=_("Hardware acceleration supported ({methods}).").format(
                    methods=", ".join(available_methods)
                ),
                details="\n".join(
                    f"{h.name}: {'Available (' + ', '.join(h.encoders) + ')' if h.is_available else 'Not detected'}"
                    for h in hw_list
                ),
                can_fix=False,
            )

        return HealthCheckItem(
            id="hwaccel",
            title=_("Hardware Acceleration"),
            status="ok",
            summary=_("Software encoding active (CPU). No compatible GPU encoder detected."),
            details=_("Video Cut will process and export videos using multi-threaded CPU encoding."),
            can_fix=False,
        )

    @classmethod
    def run_all_checks(cls, include_codecs: bool = True) -> list[HealthCheckItem]:
        """Execute all health probes."""
        items = [
            cls.check_ffmpeg(),
            cls.check_file_association(),
        ]
        if include_codecs:
            items.append(cls.check_codecs())
            items.append(cls.check_hardware_acceleration())
        return items

