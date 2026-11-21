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

from gi.repository import Gio, GLib

from ..i18n import _
from ..paths import APP_ID, RESOURCES_DIR
from ..utils import SUBPROCESS_FLAGS, get_ffmpeg_path, get_ffprobe_path
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


class HealthService:
    """Handles system diagnostic checks, package manager detection, and automated repairs."""

    @classmethod
    def get_config_file(cls) -> Path:
        config_dir = Path(GLib.get_user_config_dir()) / "video-cut"
        config_dir.mkdir(parents=True, exist_ok=True)
        return config_dir / "healthcheck.json"

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

        return HealthCheckItem(
            id="ffmpeg",
            title=_("FFmpeg Multimedia Engine"),
            status="ok",
            summary=_("FFmpeg and FFprobe are installed and working."),
            details=f"{version_str}\nFFmpeg: {ffmpeg_bin}\nFFprobe: {ffprobe_bin}",
            can_fix=False,
        )

    @classmethod
    def check_file_association(cls) -> HealthCheckItem:
        """Check if .vcut project files are associated with Video Cut."""
        mime_type = "application/x-video-cut"
        is_associated = False
        current_handler = ""

        # 1. Check if the MIME database recognizes *.vcut files
        content_type, _uncertain = Gio.content_type_guess("project.vcut", None)
        mime_registered = (content_type == mime_type)

        # 2. Check if an active, installed Video Cut handler is default
        handler_valid = False
        try:
            app_info = Gio.AppInfo.get_default_for_type(mime_type, False)
            if app_info:
                app_id = app_info.get_id() or ""
                current_handler = app_id
                executable = app_info.get_executable() or ""
                executable_exists = bool(shutil.which(executable) or (executable and Path(executable).exists()))
                if (APP_ID in app_id or "video-cut" in app_id.lower()) and executable_exists:
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
    def check_codecs(cls) -> HealthCheckItem:
        """Check availability of key video and audio encoders."""
        encoders = FFmpegCapabilities.get_encoders()

        recommended_codecs = {
            "libx264": ("H.264 / AVC", True),
            "libx265": ("H.265 / HEVC", False),
            "libvpx-vp9": ("VP9", False),
            "libsvtav1": ("AV1", False),
            "aac": ("AAC Audio", True),
            "libmp3lame": ("MP3 Audio", False),
            "libopus": ("Opus Audio", False),
        }

        if "libaom-av1" in encoders and "libsvtav1" not in encoders:
            encoders = set(encoders)
            encoders.add("libsvtav1")

        available = []
        missing_essential = []
        missing_optional = []

        for cid, (name, is_essential) in recommended_codecs.items():
            if cid in encoders:
                available.append(name)
            else:
                if is_essential:
                    missing_essential.append(name)
                else:
                    missing_optional.append(name)

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
                fix_type="link" if not extra_pkg else "command",
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
    def run_all_checks(cls, include_codecs: bool = False) -> list[HealthCheckItem]:
        """Execute all health probes."""
        items = [
            cls.check_ffmpeg(),
            cls.check_file_association(),
        ]
        if include_codecs:
            items.append(cls.check_codecs())
        return items

