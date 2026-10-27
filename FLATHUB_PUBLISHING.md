# Publishing Video Cut on Flathub 🚀

This document is a complete, step-by-step guide on how to submit and publish **Video Cut** on [Flathub](https://flathub.org/), making it directly installable across all Linux distributions (Ubuntu, Fedora, Arch, SteamOS, Debian, openSUSE, etc.) via software centers (GNOME Software, KDE Discover) and terminal:

```bash
flatpak install flathub io.github.thomaz.videocut
```

---

## 1. Prerequisites Checklist

Before submitting, ensure all the required files are ready and validated:

- [x] **App ID**: `io.github.thomaz.videocut` (matches reverse-DNS for `https://github.com/thor27/video-cut`).
- [x] **Flatpak Manifest**: `io.github.thomaz.videocut.yaml` (uses GNOME 49 platform runtime and standard permissions).
- [x] **AppStream MetaInfo**: `io.github.thomaz.videocut.metainfo.xml` (validates with `appstreamcli validate`).
- [x] **Screenshots**: Active release screenshots accessible over HTTPS (raw GitHub).
- [x] **Icons**: Scalable SVG and sizes (16, 24, 32, 48, 64, 128, 256, 512).
- [x] **Desktop File**: `src/video_cut/resources/io.github.thomaz.videocut.desktop` (passes `desktop-file-validate`).

---

## 2. Local Validation

You can run our automated validation script anytime:

```bash
./scripts/build_flatpak.sh --validate-only
```

This verifies:
1. `appstreamcli validate --explain io.github.thomaz.videocut.metainfo.xml`
2. `desktop-file-validate src/video_cut/resources/io.github.thomaz.videocut.desktop`
3. Manifest syntax and structure in `io.github.thomaz.videocut.yaml`

---

## 3. Submitting to Flathub (First-Time Publication)

Flathub accepts new applications via a GitHub Pull Request to the [`flathub/flathub`](https://github.com/flathub/flathub) repository.

### Step 3.1: Fork the Flathub Repository
1. Log in to your GitHub account (`thor27`).
2. Go to **https://github.com/flathub/flathub**.
3. Click the **Fork** button (top right) to create a copy under your account (`thor27/flathub`).

### Step 3.2: Clone Your Fork Locally
Clone your fork and make sure to branch from the **`new-pr`** branch:

```bash
git clone --branch=new-pr git@github.com:thor27/flathub.git flathub-submission
cd flathub-submission
```

### Step 3.3: Create a Branch for Video Cut
```bash
git checkout -b io.github.thomaz.videocut
```

### Step 3.4: Add the Manifest and Files
Flathub recommends pointing to the release tarball or repository tag. Copy `io.github.thomaz.videocut.yaml` into the root of `flathub-submission`:

For Flathub, create `io.github.thomaz.videocut.yaml`:

```yaml
app-id: io.github.thomaz.videocut
runtime: org.gnome.Platform
runtime-version: '49'
sdk: org.gnome.Sdk
command: video-cut

finish-args:
  # Display (Wayland + X11 fallback)
  - --socket=wayland
  - --socket=fallback-x11
  # Audio
  - --socket=pulseaudio
  # Hardware acceleration
  - --device=dri
  # File access
  - --filesystem=host
  - --filesystem=xdg-run/gvfs
  # Inter-process communication
  - --share=ipc

cleanup:
  - /include
  - /lib/pkgconfig
  - /man
  - /share/man
  - /share/gtk-doc
  - '*.la'
  - '*.a'

modules:
  - name: video-cut
    buildsystem: simple
    build-commands:
      - python3 -m pip install --prefix=/app --no-deps --no-build-isolation .
      - install -Dm644 src/video_cut/resources/io.github.thomaz.videocut.desktop -t /app/share/applications/
      - install -Dm644 io.github.thomaz.videocut.metainfo.xml -t /app/share/metainfo/
      - install -Dm644 src/video_cut/resources/io.github.thomaz.videocut.svg /app/share/icons/hicolor/scalable/apps/io.github.thomaz.videocut.svg
      - install -Dm644 src/video_cut/resources/hicolor/scalable/actions/video-split-symbolic.svg /app/share/icons/hicolor/scalable/actions/video-split-symbolic.svg
      - install -Dm644 src/video_cut/resources/icon_16.png /app/share/icons/hicolor/16x16/apps/io.github.thomaz.videocut.png
      - install -Dm644 src/video_cut/resources/icon_24.png /app/share/icons/hicolor/24x24/apps/io.github.thomaz.videocut.png
      - install -Dm644 src/video_cut/resources/icon_32.png /app/share/icons/hicolor/32x32/apps/io.github.thomaz.videocut.png
      - install -Dm644 src/video_cut/resources/icon_48.png /app/share/icons/hicolor/48x48/apps/io.github.thomaz.videocut.png
      - install -Dm644 src/video_cut/resources/icon_64.png /app/share/icons/hicolor/64x64/apps/io.github.thomaz.videocut.png
      - install -Dm644 src/video_cut/resources/icon_128.png /app/share/icons/hicolor/128x128/apps/io.github.thomaz.videocut.png
      - install -Dm644 src/video_cut/resources/icon_256.png /app/share/icons/hicolor/256x256/apps/io.github.thomaz.videocut.png
      - install -Dm644 src/video_cut/resources/icon_512.png /app/share/icons/hicolor/512x512/apps/io.github.thomaz.videocut.png
    sources:
      - type: archive
        url: https://github.com/thor27/video-cut/releases/download/v0.3.0/video-cut-0.3.0.tar.gz
        sha256: 5152f33f25e05c79bd09034935d667febc678c80ef90eae4b4eb0005eac14312
```

### Step 3.5: Commit and Push
```bash
git add io.github.thomaz.videocut.yaml
git commit -m "Add io.github.thomaz.videocut"
git push -u origin io.github.thomaz.videocut
```

### Step 3.6: Open the Pull Request on GitHub
1. Open **https://github.com/flathub/flathub**.
2. GitHub will show a banner with your newly pushed branch: click **Compare & pull request**.
3. **IMPORTANT**: Make sure the base branch is **`new-pr`** (NOT `master`).
4. Title: `Add io.github.thomaz.videocut`
5. Fill out the pull request checklist template:
   - Confirm you are the upstream developer / authorized packager.
   - Confirm license is open source (`GPL-3.0-or-later`).
   - Confirm metainfo passes validation.
6. Submit the PR.

---

## 4. What Happens Next: The Flathub Review Process

1. **Automated CI Bot**:
   - Flathub's automated build bot (`flathubbot`) triggers builds for `x86_64` and `aarch64`.
   - The bot tests building, lints the manifest, and verifies the AppStream metainfo.
   - It will post a test build status link directly on the PR.
2. **Reviewer Feedback**:
   - A Flathub reviewer will audit the manifest, permissions, and metadata.
   - If changes are needed, you simply push new commits to your branch and the bot will re-test automatically.
3. **Approval & Repository Creation**:
   - Once approved, the Flathub team merges the PR.
   - A new GitHub repository is automatically created under the Flathub organization:
     **`https://github.com/flathub/io.github.thomaz.videocut`**
   - You will receive an invitation as a maintainer of this repository!
4. **App Published to Flathub**:
   - The initial build is published to the Flathub stable channel.
   - The app page becomes live at:
     **`https://flathub.org/apps/io.github.thomaz.videocut`**

---

## 5. Publishing Future Releases (v0.3.0, v1.0.0, etc.)

Once your app is accepted on Flathub, publishing updates is simple:

1. Create your new release on GitHub (e.g. `v0.3.0`) with its release tarball.
2. Clone your Flathub repository:
   ```bash
   git clone git@github.com:flathub/io.github.thomaz.videocut.git
   cd io.github.thomaz.videocut
   ```
3. Update the `url` and `sha256` of the archive in `io.github.thomaz.videocut.yaml`.
4. Commit and push directly to `master`:
   ```bash
   git commit -am "Update to v0.3.0"
   git push origin master
   ```
5. Flathub builds and publishes the update automatically within a couple of hours!
