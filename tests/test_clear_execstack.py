"""Tests for clear_execstack script used during packaging."""

import struct
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))
from clear_execstack import clear_execstack, scan_and_clear


def test_clear_execstack_32bit(tmp_path):
    """Test clearing PT_GNU_STACK PF_X on 32-bit ELF (such as armhf)."""
    target = tmp_path / "libsample32.so"
    ident = b"\x7fELF\x01\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00"
    hdr = struct.pack("<16sHHIIIIIHHHHHH", ident, 2, 3, 1, 0, 52, 0, 0, 52, 32, 1, 0, 0, 0)
    # p_type=0x6474e551, p_offset=0, p_vaddr=0, p_paddr=0, p_filesz=0, p_memsz=0, p_flags=7 (RWE), p_align=4
    ph = struct.pack("<IIIIIIII", 0x6474E551, 0, 0, 0, 0, 0, 7, 4)
    target.write_bytes(hdr + ph)

    # First run should clear executable bit and return True
    assert clear_execstack(target) is True

    # Verify that p_flags is now 6 (RW, without PF_X)
    with open(target, "rb") as f:
        f.seek(52 + 24)
        flags, = struct.unpack("<I", f.read(4))
        assert flags == 6

    # Second run should find nothing to clear and return False
    assert clear_execstack(target) is False


def test_clear_execstack_64bit(tmp_path):
    """Test clearing PT_GNU_STACK PF_X on 64-bit ELF."""
    target = tmp_path / "libsample64.so"
    ident = b"\x7fELF\x02\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00"
    hdr = struct.pack("<16sHHIQQQIHHHHHH", ident, 2, 62, 1, 0, 64, 0, 0, 64, 56, 1, 0, 0, 0)
    # 64-bit ph: p_type (4 bytes), p_flags (4 bytes) = 7 (RWE)
    ph = struct.pack("<IIQQQQQQ", 0x6474E551, 7, 0, 0, 0, 0, 0, 8)
    target.write_bytes(hdr + ph)

    assert clear_execstack(target) is True

    with open(target, "rb") as f:
        f.seek(64 + 4)
        flags, = struct.unpack("<I", f.read(4))
        assert flags == 6


def test_scan_and_clear_directory(tmp_path):
    sub = tmp_path / "subdir"
    sub.mkdir()

    # Create dummy 32-bit ELF with execstack
    f1 = sub / "libtest.so"
    ident = b"\x7fELF\x01\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00"
    hdr = struct.pack("<16sHHIIIIIHHHHHH", ident, 2, 3, 1, 0, 52, 0, 0, 52, 32, 1, 0, 0, 0)
    ph = struct.pack("<IIIIIIII", 0x6474E551, 0, 0, 0, 0, 0, 7, 4)
    f1.write_bytes(hdr + ph)

    # Create non-ELF file
    f2 = sub / "readme.txt"
    f2.write_text("not an elf")

    count = scan_and_clear(tmp_path)
    assert count == 1
