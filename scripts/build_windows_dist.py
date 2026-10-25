#!/usr/bin/env python3
"""Build script for Windows standalone distribution on Linux using MSYS2 UCRT64 packages."""

import concurrent.futures
import hashlib
import os
import shutil
import ssl
import subprocess
import sys
from pathlib import Path
from urllib.request import Request, urlopen

REPO_URL = "https://repo.msys2.org/mingw/ucrt64"
DB_NAME = "ucrt64.db"

ROOT_DIR = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT_DIR / "build" / "msys2_cache"
PKG_CACHE_DIR = CACHE_DIR / "pkgs"
ENV_DIR = ROOT_DIR / "build" / "windows_root"
UCRT64_DIR = ENV_DIR / "ucrt64"
DIST_DIR = ROOT_DIR / "dist" / "VideoCut"

TARGET_PACKAGES = [
    "mingw-w64-ucrt-x86_64-python",
    "mingw-w64-ucrt-x86_64-python-gobject",
    "mingw-w64-ucrt-x86_64-gtk4",
    "mingw-w64-ucrt-x86_64-libadwaita",
    "mingw-w64-ucrt-x86_64-gst-plugins-base",
    "mingw-w64-ucrt-x86_64-gst-plugins-good",
    "mingw-w64-ucrt-x86_64-gst-plugins-bad",
    "mingw-w64-ucrt-x86_64-gst-plugin-gtk",
    "mingw-w64-ucrt-x86_64-ffmpeg",
    "mingw-w64-ucrt-x86_64-python-pillow",
    "mingw-w64-ucrt-x86_64-pyinstaller",
]

ssl_ctx = ssl.create_default_context(cafile="/etc/ssl/certs/ca-certificates.crt")


def download_file(url: str, dest_path: Path) -> None:
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = dest_path.with_suffix(dest_path.suffix + ".tmp")
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(req, context=ssl_ctx, timeout=60) as resp, open(temp_path, "wb") as f:
        shutil.copyfileobj(resp, f)
    temp_path.replace(dest_path)


def get_repo_db() -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    db_path = CACHE_DIR / DB_NAME
    if not db_path.exists():
        print(f"Downloading {DB_NAME}...")
        download_file(f"{REPO_URL}/{DB_NAME}", db_path)
    return db_path


def parse_db(db_path: Path):
    db_extract_dir = CACHE_DIR / "db_extracted"
    if not db_extract_dir.exists():
        print("Extracting package database...")
        db_extract_dir.mkdir(parents=True, exist_ok=True)
        subprocess.run(["tar", "-I", "zstd", "-xf", str(db_path), "-C", str(db_extract_dir)], check=True)

    pkgs = {}
    provides_map = {}

    for entry in db_extract_dir.iterdir():
        desc_file = entry / "desc"
        if not desc_file.is_file():
            continue
        content = desc_file.read_text(encoding="utf-8", errors="ignore")
        sections = {}
        lines = []
        sec = None
        for line in content.splitlines():
            line = line.strip()
            if line.startswith("%") and line.endswith("%"):
                if sec:
                    sections[sec] = lines
                sec = line[1:-1]
                lines = []
            elif line:
                lines.append(line)
        if sec:
            sections[sec] = lines

        name = sections.get("NAME", [""])[0]
        filename = sections.get("FILENAME", [""])[0]
        sha256 = sections.get("SHA256SUM", [""])[0]
        depends = sections.get("DEPENDS", [])
        csize = int(sections.get("CSIZE", ["0"])[0])

        clean_deps = []
        for d in depends:
            for op in [">=", "<=", "=", ">", "<"]:
                if op in d:
                    d = d.split(op)[0]
                    break
            clean_deps.append(d)

        pkgs[name] = {"filename": filename, "sha256": sha256, "depends": clean_deps, "csize": csize}
        for prov in sections.get("PROVIDES", []):
            prov_clean = prov.split("=")[0].split(">=")[0].split("<=")[0]
            provides_map[prov_clean] = name

    return pkgs, provides_map


def resolve_dependencies(roots, pkgs, provides_map):
    resolved = set()
    queue = list(roots)
    while queue:
        item = queue.pop(0)
        if item not in pkgs and item in provides_map:
            item = provides_map[item]
        if item in resolved or item not in pkgs:
            continue
        resolved.add(item)
        for dep in pkgs[item]["depends"]:
            actual_dep = dep
            if actual_dep not in pkgs and actual_dep in provides_map:
                actual_dep = provides_map[actual_dep]
            if actual_dep not in resolved and actual_dep not in queue:
                queue.append(actual_dep)
    return resolved


def verify_sha256(path: Path, expected_sha256: str) -> bool:
    if not path.exists():
        return False
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest() == expected_sha256


def download_package(pkg_info: dict) -> Path:
    filename = pkg_info["filename"]
    sha256 = pkg_info["sha256"]
    dest_path = PKG_CACHE_DIR / filename
    if dest_path.exists() and verify_sha256(dest_path, sha256):
        return dest_path

    url = f"{REPO_URL}/{filename}"
    download_file(url, dest_path)
    if not verify_sha256(dest_path, sha256):
        dest_path.unlink(missing_ok=True)
        raise RuntimeError(f"Checksum verification failed for {filename}")
    return dest_path


def post_process_environment():
    schemas_dir = UCRT64_DIR / "share" / "glib-2.0" / "schemas"
    if schemas_dir.is_dir() and not (schemas_dir / "gschemas.compiled").is_file():
        print("Compiling GLib schemas...")
        subprocess.run(["glib-compile-schemas", str(schemas_dir)], check=True)

    loaders_cache = UCRT64_DIR / "lib" / "gdk-pixbuf-2.0" / "2.10.0" / "loaders.cache"
    query_loaders = UCRT64_DIR / "bin" / "gdk-pixbuf-query-loaders.exe"
    if query_loaders.is_file() and not loaders_cache.is_file():
        print("Generating GdkPixbuf loaders.cache via Wine...")
        loaders_cache.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            "wine",
            "cmd",
            "/c",
            f"set PATH=%CD%\\build\\windows_root\\ucrt64\\bin;%PATH% && {query_loaders} > {loaders_cache}",
        ]
        subprocess.run(cmd, check=True)


def setup_windows_environment(clean: bool = False):
    python_exe = UCRT64_DIR / "bin" / "python.exe"
    if not clean and python_exe.is_file():
        print(f"Windows environment already installed at {UCRT64_DIR}.")
        post_process_environment()
        return

    db_path = get_repo_db()
    pkgs, provides_map = parse_db(db_path)
    print(f"Loaded {len(pkgs)} packages from database.")

    resolved_pkgs = resolve_dependencies(TARGET_PACKAGES, pkgs, provides_map)
    print(f"Resolved {len(resolved_pkgs)} total packages needed.")

    PKG_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    to_download = [pkgs[p] for p in resolved_pkgs if p in pkgs]

    needed = [p for p in to_download if not (PKG_CACHE_DIR / p["filename"]).exists()]
    total_needed_size = sum(p["csize"] for p in needed)
    if needed:
        print(f"Downloading {len(needed)} packages ({total_needed_size / (1024*1024):.1f} MB)...")
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            futures = {executor.submit(download_package, p): p["filename"] for p in needed}
            for i, future in enumerate(concurrent.futures.as_completed(futures), 1):
                fname = futures[future]
                try:
                    future.result()
                    if i % 10 == 0 or i == len(needed):
                        print(f"Downloaded [{i}/{len(needed)}] {fname}")
                except Exception as e:
                    print(f"Error downloading {fname}: {e}")
                    raise
    else:
        print("All required packages are already cached.")

    print(f"Extracting packages into {ENV_DIR}...")
    ENV_DIR.mkdir(parents=True, exist_ok=True)
    for i, p in enumerate(to_download, 1):
        pkg_file = PKG_CACHE_DIR / p["filename"]
        cmd = ["tar", "-I", "zstd", "-xf", str(pkg_file), "-C", str(ENV_DIR)]
        subprocess.run(cmd, check=True)
        if i % 25 == 0 or i == len(to_download):
            print(f"Extracted [{i}/{len(to_download)}] {p['filename']}")

    post_process_environment()
    print("Windows runtime environment setup complete!")


def main():
    clean = "--clean" in sys.argv
    setup_windows_environment(clean=clean)


if __name__ == "__main__":
    main()
