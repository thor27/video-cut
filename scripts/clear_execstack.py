#!/usr/bin/env python3
"""Clear the executable stack flag (PT_GNU_STACK PF_X) on all ELF files in a directory."""

import os
import struct
import sys
from pathlib import Path


def clear_execstack(filepath: Path | str) -> bool:
    """Clear PF_X (executable bit) on PT_GNU_STACK program header for an ELF file.

    Returns True if the executable stack bit was found and cleared, False otherwise.
    """
    fpath = Path(filepath)
    if fpath.is_symlink() or not fpath.is_file():
        return False

    try:
        with open(fpath, "r+b") as f:
            magic = f.read(4)
            if magic != b"\x7fELF":
                return False

            ei_class = f.read(1)[0]  # 1 = 32-bit (e.g. armhf), 2 = 64-bit
            ei_data = f.read(1)[0]   # 1 = little-endian, 2 = big-endian
            if ei_class not in (1, 2) or ei_data not in (1, 2):
                return False

            endian = "<" if ei_data == 1 else ">"
            f.seek(0)
            header = f.read(64 if ei_class == 2 else 52)
            if ei_class == 1:
                e_phoff, = struct.unpack_from(endian + "I", header, 28)
                e_phentsize, e_phnum = struct.unpack_from(endian + "HH", header, 42)
            else:
                e_phoff, = struct.unpack_from(endian + "Q", header, 32)
                e_phentsize, e_phnum = struct.unpack_from(endian + "HH", header, 54)

            cleared = False
            for i in range(e_phnum):
                f.seek(e_phoff + i * e_phentsize)
                ph = f.read(e_phentsize)
                p_type, = struct.unpack_from(endian + "I", ph, 0)
                if p_type == 0x6474E551:  # PT_GNU_STACK
                    flags_offset = e_phoff + i * e_phentsize + (24 if ei_class == 1 else 4)
                    f.seek(flags_offset)
                    p_flags, = struct.unpack(endian + "I", f.read(4))
                    if p_flags & 1:  # PF_X is set
                        f.seek(flags_offset)
                        f.write(struct.pack(endian + "I", p_flags & ~1))
                        cleared = True

            return cleared
    except (OSError, PermissionError, struct.error):
        return False


def scan_and_clear(root_dir: Path | str) -> int:
    """Recursively scan directory and clear executable stack on all ELF binaries."""
    root_path = Path(root_dir)
    count = 0
    for root, _, files in os.walk(root_path):
        for fname in files:
            p = Path(root) / fname
            if clear_execstack(p):
                print(f"Cleared executable stack: {p}")
                count += 1
    return count


def main() -> int:
    target = sys.argv[1] if len(sys.argv) > 1 else "."
    cleared_count = scan_and_clear(target)
    if cleared_count > 0:
        print(f"Successfully cleared executable stack from {cleared_count} file(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
