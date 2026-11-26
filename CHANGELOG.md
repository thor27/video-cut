# Changelog

All notable changes to **Video Cut** will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [0.7.0] - 2026-11-26

### 🚀 Smart FFmpeg Resolution & Source Selection
- **Smart FFmpeg Engine**: Automatic resolution of the optimal FFmpeg executable available on the system. When running inside containerized packages (AppImage or Snap), prioritizes host FFmpeg if version >= 4.3 and hardware acceleration is present, while strictly guarding against degraded codec support (such as missing `libx264` or `aac` in Fedora's `ffmpeg-free`).
- **User-Configurable Source Modes**: Choose which FFmpeg binary to use directly in the System Health & Setup dialog:
  - **Auto (Recommended)**: Automatically selects the best FFmpeg engine favoring hardware acceleration without losing codec capabilities.
  - **Container (Bundled)**: Uses the bundled static FFmpeg from AppImage/Snap (falls back to system when running from source).
  - **System**: Enforces the host operating system's FFmpeg binary.
  - **Manual (Custom Path)**: Custom path selection with integrated path entry and native GTK file picker.
- **Dynamic Invalidation & Cache Reset**: Switching sources immediately clears capability and binary caches, re-evaluating codecs and hardware encoders on the fly.
- **Minimum Version Enforcement**: Established minimum version requirement (FFmpeg >= 4.3) with warning notifications and one-click package upgrade actions for legacy binaries.

### 🏥 System Health & Automated Diagnostics
- **Comprehensive Diagnostics Dialog**: Accessible from the main menu, providing visual categorized status groups:
  - **System & Packaging**: Detected distribution/OS, version, architecture, and package manager.
  - **FFmpeg Engine**: Active binary location, effective source, version validation, and upgrade suggestions.
  - **GPU Hardware Acceleration**: Real-time detection matrix for VA-API, NVIDIA NVENC, Intel Quick Sync (QSV), Vulkan Video, Apple VideoToolbox, and AMD AMF.
  - **Multimedia Codecs**: Availability inspection across H.264, HEVC/H.265, AV1, VP9, ProRes, AAC, MP3, Opus, FLAC, and Vorbis.
  - **File Association**: Status of `.vcut` project file registration.
- **Automated Fixers**: Inline action buttons generate tailored distribution package commands (`apt`, `dnf`, `pacman`, `zypper`, `apk`, `gnome-software`, `pamac-installer`, `brew`, `port`, `winget`, `choco`, `scoop`).
- **One-Click File Association**: Instant registration and clean removal of `.vcut` project MIME and desktop associations with automatic cache refreshing (`update-mime-database`, `update-desktop-database`).
- **First-Time Health Alert**: Startup alert modal dialog (`StartupHealthAlertModal`) warning users when vital export or playback dependencies are missing.
- **Configurable Startup Healthcheck**: Toggle in Preferences to enable or disable automatic health verification on startup.

### 🚨 Diagnostic Error Handling
- **Modal Error Dialog on FFmpeg Failure**: Replaced silent failures or terminal logs with a dedicated `FFmpegErrorDialog` modal when video export, transcode, or image conversion fails.
- **One-Click Copy Details**: Full command arguments, working directory, exit code, and complete stderr output with visual feedback and GNOME HIG dismiss ergonomics (Escape key support).

### 📐 Text Overlay Parity
- **Exact Wrapping Parity**: Multi-line text overlay wrapping is now calculated at canonical 1080p reference scale, ensuring identical line breaks and text layout between timeline preview canvases and final exports at any resolution.
- **Automatic Text Box Height Fitting**: Dynamic text height calculation using canonical line breaks to prevent vertical clipping or overflow.

### 📦 Portability & AppImage Hardening
- **Self-Contained Python & GStreamer**: Bundled Python 3 runtime, versioned packages, pycairo extensions, and full GStreamer playback plugin suite (GIO, demuxers, audio/video decoders, audio effects, OpenGL/PipeWire/Pulse sinks).
- **GIO Isolation**: Isolated module search directory to eliminate symbol clashes with host GVFS.
- **OpenGL Acceleration**: Default to hardware-accelerated OpenGL renderer (`GSK_RENDERER=gl`).
- **Seek Freezing Fix**: Eliminated race conditions during scrubbing and rapid timeline seeking by pausing timeline clock during seeks and queuing playback until seek completion.

### 🌐 Internationalization
- 100% translation coverage for English (`en_US`), Portuguese (`pt_BR`), Spanish (`es`), and French (`fr`) across all new health check, error reporting, and FFmpeg source configuration dialogs.

---

## [0.6.1] - 2026-11-19

### 📦 Packaging & AppImage Portability Hardening
- **Fully Self-Contained AppImage**: Bundled glibc loader (`ld-linux-x86-64.so.2`) and core C runtime (`libc.so.6`, `libm.so.6`, etc.) in `$APPDIR/glibc` alongside recursive non-driver shared libraries (`libgtk-4`, `libadwaita-1`, `libgraphene-1.0`, etc.) in `$APPDIR/usr/lib`. This eliminates GLIBC and library version mismatches on older distributions and AppImage catalog verification runners (Ubuntu 22.04 LTS).
- **Automated Display Fallback in AppRun**: Added automatic detection for available Wayland sockets; gracefully falls back to `GDK_BACKEND=x11` when running in headless, Xvfb, or sandboxed environments without an active Wayland compositor.
- **Bundled & Compiled GSettings Schemas**: Bundled and compiled GTK4 and Libadwaita GSettings schemas in `$APPDIR/usr/share/glib-2.0/schemas`.
- **Packaging Pipeline Hardening**: Preserved and restored `$LD_LIBRARY_PATH` during dependency resolution in `build_appimage.sh` to avoid polluting subsequent packaging tools.

---

## [0.6.0] - 2026-11-19

### ⚡ Lossless Video Cutting & Stream Copy
- **Instant Stream Copy Export**: Export video cuts without re-encoding using direct stream copy (`-c copy`), preserving 100% original video and audio quality with zero generation loss.
- **Blazing Export Speed**: Exports complete in seconds, bounded purely by drive disk read/write throughput rather than CPU/GPU encoding.
- **Accurate Keyframe Demuxer Seeking**: Added seek compensation (`3/23s` offset calculation) for non-PTS seeking containers (such as Matroska and WebM) with B-frames, eliminating unwanted preceding scenes in lossless exports.
- **Multi-Segment Lossless Concatenation**: Cut multiple portions out of a video and losslessly merge the remaining segments into a single cohesive output file.

### 🧲 Keyframe Snapping & Navigation
- **Stick to Keyframe**: Added a dedicated "Stick to Keyframe" toggle button in the transport controls with an informative visual guide dialog explaining stream-copy tradeoffs.
- **Magnetic Timeline Snapping**: Playhead scrubbing, segment boundaries, and cut splits magnetically snap to nearest I-frame (keyframe) timestamps when enabled.
- **Keyboard Navigation**: Stepping shortcuts `<kbd>,</kbd>` (comma) and `<kbd>.</kbd>` (period) step to previous/next keyframes across segments when Stick to Keyframe is active.
- **Two-Tier Keyframe Indexing**: Responsive background indexing with visual progress reporting dialog and multi-tier packet analysis.
- **Undo / Redo Integration**: Reverting keyframe-snapped cuts via `Ctrl+Z` reliably restores previous timestamps and updates the "Stick to Keyframe" toggle state.

### 💾 Project Persistence
- **Keyframe Timestamps & Snapping State in `.vcut`**: Stored cached keyframe lists and user keyframe-snap preference directly inside `.vcut` project files, eliminating re-probing and restoring workflow configuration instantly on open.

### 🖼️ Dynamic Background Thumbnail Refinement
- **Progressive Background Refinement**: Expanding or lengthening a timeline segment automatically schedules background thumbnail regeneration for newly exposed regions.
- **Smooth Pulsating Placeholders**: Clean CSS pulsating placeholders for loading thumbnails, replacing spinners and preventing layout jitter.

### 📦 Packaging & Portability Hardening
- **Self-Contained FFmpeg & FFprobe**: Bundled static `ffmpeg` and `ffprobe` binaries in AppImage and Snap packages, ensuring out-of-the-box operation on minimal distributions.
- **AppImage Catalog Compatibility**: Bundled GTK4, Libadwaita, Graphene, and GObject typelib files in the AppImage bundle, resolving sandbox errors on catalog runners without host GTK4.
- **Snap Environment Fixes**: Corrected `$PATH` and `$LD_LIBRARY_PATH` inheritance, resolved temporary file path naming to avoid AppArmor denials, and normalized generated package filenames to match `$VERSION`.

### 🛠️ Stability & Maintenance
- **Media Player Cleanup**: Improved player pipeline teardown and media disposal order.
- **Coordinate Translation**: Enhanced canvas coordinate mapping and dialog source timestamps.
- **Test Suite Reliability**: Expanded test coverage for keyframe probing, lossless export, undo/redo, and packaging utilities.

---

## [0.5.1] - 2026-11-15

### 📦 Distribution & Packaging
- **Automated Flatpak Bundles**: Added automated Flatpak build and bundling to CI release workflows.
- **Draft Releases**: Enabled automated draft release generation in GitHub Actions for safe release staging.

---

## [0.5.0] - 2026-11-13

### 💾 Project Save & Load (`.vcut`)
- **Native Project Files**: Full project persistence and restoration saving timeline segments, split timestamps, crop/rotate settings, and rich graphic overlays.
- **Missing Media Detection & Relinking**: Interactive relinking dialog to quickly locate moved or renamed media assets.
- **Recent Projects**: Fast access to recent `.vcut` projects from the header menu and welcome screen.

### ✂️ Multi-Segment Timeline Operations
- **Multiple Clip Selection**: Select multiple timeline clips with `Ctrl+Click`, `Shift+Click`, or `Ctrl+A` / Select All.
- **Bulk Transformations & Overlays**: Apply crop, rotation, and graphic overlays to all selected segments simultaneously.
- **Batch Editing**: Multi-copy, multi-cut, and multi-delete operations across selected timeline segments.

### 🎨 Media Import
- **SVG Image Import**: Import scalable vector graphics (SVG) directly as video segments with custom duration presets and soundtrack audio.

### 📦 Platform & Packaging
- **Flatpak Upgrade**: Updated Flatpak runtime to GNOME 51 and Pillow 13.0.0.
- **Distribution Hardening**: Hardened Debian, AppImage, Snap, and Flatpak builds.
- **Security & Community**: Added `SECURITY.md` and `CONTRIBUTING.md`.

---

## [0.4.0] - 2026-11-07

### 🎨 Video Overlay Editor
- **Rich Text Overlays**:
  - Full multiline text editing with automatic and manual bounding box sizing.
  - Native GTK Font Chooser dialog integration with system language filtering and safe font fallback.
  - Configurable font size, text color, bold, and italic styles.
  - Independent horizontal text alignment (Left, Center, Right) and vertical text alignment (Top, Middle, Bottom) with custom symbolic icons styled to match GNOME/Yaru themes.
  - Visual opacity control (0%–100%) and layer reordering (`Page Up` / `Page Down`).
- **Geometric Shapes**:
  - Vector rectangles and circles with configurable fill color, border color, border thickness, and corner radius.
  - Independent width and height resizing with optional aspect ratio preservation.
- **Media & Sticker Overlays**:
  - Import static images (PNG, JPEG, WebP) with transparent alpha channels.
  - Scalable vector graphics (SVG) with sharp, resolution-independent rendering.
  - Animated GIF support with frame-accurate looping, timing controls, and in-memory frame duration caching.
  - Picture-in-picture (PIP) video overlays (`.mp4`, `.webm`, `.mov`, `.mkv`) with sub-segment time trimming, looping, and volume control.
- **Audio Overlays & Live Playback**:
  - Add background audio tracks and sound effects to video segments.
  - Volume control (0%–200%) and timeline synchronization (`audio_start`, `audio_end`).
  - Dedicated `OverlayAudioPlayer` providing synchronized live audio playback during editing in the overlay dialog.
- **Multi-Element Selection & Manipulation**:
  - Multi-select via `Ctrl+Click`, `Shift+Click`, or click-and-drag rubberband marquee rectangle.
  - Translucent system accent color rubberband selection rectangle.
  - Simultaneous multi-element dragging, position adjustment, and proportional resizing.
  - Inter-object alignment (Align Left, Center, Right, Top, Middle, Bottom) and distribution (Distribute Horizontally, Vertically).
  - Contextual property inspector displaying shared attributes across selected elements without visual clutter.
- **Universal Clipboard & Context Menu**:
  - Cut (`Ctrl+X`), Copy (`Ctrl+C`), Paste (`Ctrl+V`), and Duplicate (`Ctrl+D`) operations for both single and multiple selected overlays.
  - Select All (`Ctrl+A`) support via keyboard shortcut and menu.
  - Custom right-click context menu with keyboard shortcut hints.
- **Undo / Redo History**:
  - Complete multi-level undo (`Ctrl+Z`) and redo (`Ctrl+Shift+Z` / `Ctrl+Y`) for all overlay creations, movements, transformations, deletions, and property modifications.
- **High-Performance Export Engine**:
  - Static overlay layer batching into single composite PNG surfaces, avoiding disk I/O and frame-by-frame overhead.
  - Dynamic animated overlay synchronization for GIFs and PIP videos.
  - Multi-track audio mixing (`amix`) and volume scaling.

---

## [0.3.0] - 2026-10-27

### ⚡ Performance & Responsiveness
- **Instant Crop & Rotate Dialog**: Dialog presentation is now instantaneous (< 20 ms) upon clicking the button, presenting immediately with an animated loading spinner overlay while frames load progressively in the background.
- **Ultra-Fast Canvas Surface Creation**: Implemented direct NumPy buffer memory mapping for Cairo ARGB32 surface generation in the Crop & Rotate dialog, reducing surface creation latency from ~226 ms down to ~16 ms.
- **Dual-Mode Full-Quality Preview Frame**:
  - When the playhead cursor is within the selected segment, Video Cut captures the current video player frame instantly via GdkPaintable without invoking FFmpeg.
  - When the playhead cursor is outside the segment, it loads a full-resolution keyframe thumb extracted from the segment (or reuses the high-quality timeline cache).
- **Direct Image Loading for Imported Photos**: Tracked `orig_image_path` on imported image segments to bypass FFmpeg entirely during preview generation, loading the source picture directly for instant preview rendering.
- **Timeline Computation Optimization**: Accelerated timeline segment width calculations and filmstrip thumbnail caching.

### 🖼️ Image Import & Progress Modal
- **Non-Blocking Asynchronous Import**: Image conversion and soundtrack muxing run in background worker threads, keeping the application fully responsive throughout the import process.
- **Reusable `ProgressModal`**: Added a reusable progress modal component with a spinner, real-time percentage progress bar, operation status text, and cancel support.
- **High-Quality Image Thumbnails**: Optimized thumbnail generation pipeline for imported photos to ensure crystal-clear timeline preview frames.

### 📦 Packaging & Multi-Platform Distribution
- **Flatpak Support**:
  - Added Flatpak manifest (`io.github.thor27.video-cut.yaml`) targeting the GNOME 47 runtime.
  - Added AppStream metainfo specification (`io.github.thor27.video-cut.metainfo.xml`).
  - Added automated build script (`scripts/build_flatpak.sh`) and Flathub publishing guide (`FLATHUB_PUBLISHING.md`).
- **Snapcraft Multi-Platform Refinements**:
  - Explicitly configured target platforms for 64-bit architectures (`amd64`, `arm64`) to prevent build failures on unsupported 32-bit `armhf` architectures.
  - Excluded unused legacy DirectFB libraries (`libdirect`, `libfusion`, `libvoodoo`) from staging to eliminate executable stack security warnings (`PROT_EXEC` / `GNU_STACK`).
  - Added 3:1 aspect ratio banner graphics for Snap Store listing.

### 🏗️ Code Architecture & Testing
- **Modular Clipboard Controller**: Extracted clipboard operations from `MainWindow` into a dedicated `ClipboardController` class for improved separation of concerns and maintainability.
- **Automated Benchmark Suite**: Added a dedicated benchmark test suite (`tests/test_benchmarks.py`) monitoring thumbnail cache hashing, segment width computation, and filtergraph generation.
- **Linting & Code Modernization**: Standardized codebase with strict Ruff linting and typing improvements across all source files and test suites.

### ℹ️ UI & Community
- **About Window Metadata**: Added official GitHub repository website, community discussions support link, copyright notices, and developer credits to the About dialog.
- **Documentation & Assets**: Updated screenshots showcasing the Crop & Rotate modal and the Image Import dialog with audio soundtracks.

---

## [0.2.0] - 2026-10-24

### Added
- **Desktop Drag & Drop**: Drag videos and photos directly from file managers into the editor.
- **Clipboard Integration**: Copy and paste video clips and image files across applications and within the timeline.
- **Crop & Rotate Presets**: Added `3:4` (vertical photo) and `9:21` (ultra-tall reel) presets with automatic aspect ratio inversion on 90° rotation.
- **Fullscreen Mode**: Dedicated fullscreen toggle button and keyboard shortcut (`F11`).
- **Image Import Presets**: Quick duration selector buttons (1s, 3s, 5s, 10s) with soundtrack audio support.
- **Advanced Export Popover**: Redesigned export flow with quick single-click primary export and advanced configuration options (containers, codecs, CRF, speed presets).

### Changed
- **Audio & Playback Continuity**: Eliminated audio drops and seek latency across split or reordered segment transitions.
- **UI Polish**: Streamlined header bar, improved empty state presentation, and unified action buttons.

---

## [0.1.1] - 2026-10-19

### Added
- Native Cairo drag preview with Libadwaita accent color integration.
- Hicolor symbolic icon integration for video split action.

### Fixed
- Enforce deletion of unthemed `video-split-symbolic.svg` in Snapcraft build and prime steps.
- Styling refinements for disabled button states across desktop themes.

---

## [0.1.0] - 2026-10-15

### Added
- Initial release of Video Cut: fast, lightweight video editor built with Python, GTK4, Libadwaita, and GStreamer.
- Frame-accurate video cutting, slicing, and trimming.
- Multi-segment timeline with proportional and constrained zoom modes.
- Interactive Crop & Rotate tool with rule-of-thirds grid.
- Image import with custom duration and background soundtrack support.
- Multi-format video export powered by FFmpeg.
