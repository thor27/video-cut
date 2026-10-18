# Video Cut 🎬

A simple, fast, and modern Python GTK 4 / Libadwaita tool to edit and cut videos, inspired by macOS Preview and QuickTime video editing.

<p align="center">
  <img src="screenshot.png" alt="Video Cut Screenshot" width="850">
</p>

## Features

- **Large Video Player**: Full preview canvas with zero hover obstructions, hardware-accelerated via GStreamer and GTK 4.
- **Filmstrip Timeline with Thumbnails**: Storyboard-style clip sequence displaying thumbnail contact frames for each segment, with ultra-fast initial thumbnail (< 50ms) and keyframe extraction (600x realtime).
- **Centralized Playhead Needle**: Clear, thin vertical bar with a prominent orange ball, perfectly centralized on top of thumbnail images to show exactly where the cut will occur.
- **Split at Cursor**: Slice clips at cursor position (<kbd>S</kbd>, hover pill over the cursor, or right-click context menu).
- **Undo & Redo**: Full undo/redo history for splits, deletions, reorders, and clip additions (<kbd>Ctrl+Z</kbd>, <kbd>Ctrl+Shift+Z</kbd> / <kbd>Ctrl+Y</kbd>).
- **Delete Segment**: Remove selected segment (<kbd>Delete</kbd>, <kbd>Backspace</kbd>, bottom timeline button, or right-click context menu).
- **Reorder Segments**: Drag and drop clips or use Move Left (<kbd>◀</kbd> / <kbd>Alt+Left</kbd>) and Move Right (<kbd>▶</kbd> / <kbd>Alt+Right</kbd>) buttons.
- **Add Video or Image as Segment**: Insert or append another video file (MP4, MKV, MOV, WebM, AVI) or image (PNG, JPG, JPEG, WEBP, BMP, etc. automatically converted to a 1-second segment) into the timeline using the prominent Add card.
- **Integrated Compact Transport Controls**: Located neatly in the bottom gray area:
  - Skip to previous / next segment (`Home` / `End`)
  - Previous / next single frame (`<` `,` / `>` `.`)
  - Step backward / forward 1 second (`Left` / `Right`)
  - Play / Pause (`Space`)
- **Smart Export Naming**: Automatically defaults to the original video name with `_edited.mp4` (e.g. `vacation_edited.mp4`).
- **High-Quality Export**: Combines and renders all segments into a seamless MP4 using FFmpeg filter complex (accurate cuts, zero audio drift, automatic resolution and sample rate standardization).
- **Real-Time Export Progress**: Export dialog with live percentage and duration progress.
- **Custom Application Icon**: Beautiful vector icon combining a film frame and scissors.

## Requirements

- Python 3.10+
- GTK 4 & Libadwaita (`gir1.2-gtk-4.0`, `gir1.2-adw-1`, `python3-gi`)
- FFmpeg & FFprobe (`ffmpeg`, `ffprobe`)

On Ubuntu / Debian:
```bash
sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 gstreamer1.0-gtk4 ffmpeg
```

## Installation

```bash
# Set up a virtual environment sharing system site-packages (for GTK4 bindings)
python3 -m venv --system-site-packages .venv
source .venv/bin/activate

# Install in editable mode
pip install -e .
```

## Usage

Launch the application:
```bash
video-cut
```

Or open a video directly from the terminal:
```bash
video-cut /path/to/my_video.mp4
```

### Keyboard Shortcuts

| Shortcut | Action |
| :--- | :--- |
| `Space` | Play / Pause |
| `,` (comma) | Step backward 1 frame (~0.033s) |
| `.` (period) | Step forward 1 frame (~0.033s) |
| `Left` / `Right` | Step backward / forward 1 second |
| `Home` / `End` | Jump to start of previous / next segment |
| `S` | Split at cursor |
| `Delete` / `Backspace` | Delete selected clip |
| `Ctrl+Z` | Undo last edit |
| `Ctrl+Shift+Z` or `Ctrl+Y` | Redo undone edit |
| `Alt+Left` / `Alt+Right` | Move selected clip earlier / later |
| `Ctrl+O` | Open video or image file |
| `Ctrl+E` | Export combined video |

## Architecture

- `src/video_cut/models.py`: Data models for `Segment` and `ProjectTimeline` with undo/redo snapshot stack.
- `src/video_cut/services/`:
  - `probe_service.py`: `ffprobe` metadata extraction (duration, dimensions, framerate).
  - `thumbnail_service.py`: Asynchronous thumbnail generator & cache with instant first-frame and 600x realtime keyframe extraction.
  - `image_service.py`: Detection and conversion of images (PNG, JPG, WEBP, etc.) into 1-second video segments.
  - `export_service.py`: FFmpeg `filter_complex` concatenation engine with live progress parsing.
- `src/video_cut/player.py`: Multi-segment monotonic clock playback controller using `Gtk.Picture` and `Gtk.MediaFile`.
- `src/video_cut/timeline_widget.py` & `segment_card.py`: Filmstrip UI, centralized playhead needle, compact transport controls, and drag-and-drop reordering.
- `src/video_cut/window.py`: Libadwaita application window with HeaderBar actions, keyboard shortcuts, undo/redo handling, and export modal.

## Running Tests

```bash
pytest tests/
```

## License

This project is licensed under the GNU General Public License v3.0 - see the [LICENSE](LICENSE) file for details.
