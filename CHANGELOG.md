# Changelog

All notable changes to **Video Cut** will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
