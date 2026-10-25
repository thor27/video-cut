# Building Video Cut for Windows (.exe Standalone)

This guide documents how **Video Cut** is packaged into a standalone Windows executable (`VideoCut.exe`), portable `.zip`, and installer (`VideoCut-Setup.exe`).

---

## 1. Architecture & Windows Compatibility

Video Cut is built on **Python 3**, **GTK 4**, **Libadwaita**, and **GStreamer**. On Windows, these native libraries and typelibs are officially distributed via **MSYS2 (UCRT64)**.

To provide a seamless, standalone experience on Windows with **zero user installation of Python or GTK**:

1. **Bootstrap Runtime (`src/video_cut/__main__.py`)**:
   Automatically configures runtime paths for Windows when running frozen (`sys.frozen`):
   - `GI_TYPELIB_PATH`: GObject-introspection typelibs (`Gtk-4.0`, `Adw-1`, `Gdk-4.0`, `Gio-2.0`, `Gst-1.0`)
   - `GSETTINGS_SCHEMA_DIR`: Compiled GLib schemas for Adwaita styling and preferences
   - `GDK_PIXBUF_MODULE_FILE`: Pixbuf loaders for images and icons
   - `GST_PLUGIN_SYSTEM_PATH`: GStreamer plugins for native playback
   - `GST_REGISTRY`: Isolated per-user registry under `%LOCALAPPDATA%\VideoCut` to prevent multi-user permission collisions.

2. **Background Process Execution without Black CMD Windows**:
   When invoking CLI tools (`ffmpeg.exe`, `ffprobe.exe`), `SUBPROCESS_FLAGS` passes `subprocess.CREATE_NO_WINDOW`, preventing console window flashing during background thumbnail generation and video export.

3. **Bundled Media Pipeline**:
   `ffmpeg.exe` and `ffprobe.exe` are bundled directly inside the application folder and resolved automatically by `get_ffmpeg_path()` and `get_ffprobe_path()`.

4. **Desktop Player Launch**:
   Uses `os.startfile()` on Windows to open exported video files directly in the user's default media player.

---

## 2. Automated Build via GitHub Actions (Recommended)

The easiest way to build the standalone Windows `.exe` is via the automated CI/CD workflow:

1. Push to the `win` branch or trigger **Build Windows Standalone** under the **Actions** tab in GitHub.
2. The GitHub runner (`windows-latest`) will:
   - Set up the MSYS2 UCRT64 environment
   - Install GTK 4, Libadwaita, GStreamer, and FFmpeg
   - Build the standalone `dist/VideoCut/VideoCut.exe` bundle
   - Generate `VideoCut-<version>-Windows-x86_64.zip` and `VideoCut-<version>-Setup.exe`
   - Upload both files as downloadable artifacts under the workflow run.
3. On Git release tags (`v*`), the workflow automatically attaches the Windows zip and installer to the GitHub Release.

---

## 3. Local Build on Windows (via MSYS2)

If you are developing or building on a Windows machine:

### Step 1: Install MSYS2
Download and install MSYS2 from [msys2.org](https://www.msys2.org/).

### Step 2: Open MSYS2 UCRT64 Terminal
Launch the **MSYS2 UCRT64** terminal and install the required dependencies:

```bash
pacman -Syu --noconfirm
pacman -S --needed --noconfirm \
    base-devel git zip \
    mingw-w64-ucrt-x86_64-toolchain \
    mingw-w64-ucrt-x86_64-python \
    mingw-w64-ucrt-x86_64-python-pip \
    mingw-w64-ucrt-x86_64-python-gobject \
    mingw-w64-ucrt-x86_64-python-pillow \
    mingw-w64-ucrt-x86_64-python-pyinstaller \
    mingw-w64-ucrt-x86_64-gtk4 \
    mingw-w64-ucrt-x86_64-libadwaita \
    mingw-w64-ucrt-x86_64-ffmpeg \
    mingw-w64-ucrt-x86_64-gstreamer \
    mingw-w64-ucrt-x86_64-gst-plugins-base \
    mingw-w64-ucrt-x86_64-gst-plugins-good \
    mingw-w64-ucrt-x86_64-gst-plugins-bad \
    mingw-w64-ucrt-x86_64-gst-libav \
    mingw-w64-ucrt-x86_64-inno-setup
```

### Step 3: Run the Build Script
From the repository root inside MSYS2 UCRT64:

```bash
chmod +x scripts/build_windows.sh
./scripts/build_windows.sh
```

### Generated Outputs
Outputs will be placed in the `dist/` directory:
- **`dist/VideoCut/`**: Portable application directory containing `VideoCut.exe` and all DLLs/assets.
- **`dist/VideoCut-<version>-Windows-x86_64.zip`**: Standalone portable archive for distribution.
- **`dist/VideoCut-<version>-Setup.exe`**: Inno Setup installer wizard (if `iscc` is installed).
