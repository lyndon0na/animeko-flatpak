#!/usr/bin/env python3
"""Remove bundled shared libraries that the Flatpak runtime also provides.

The Animeko AppImage ships its own FFmpeg 8.0 build plus the support libraries
it was built against. The Flatpak runtime already provides FFmpeg 8.1 with the
same SONAMEs (libavcodec.so.62, libavformat.so.62, ...), so keeping the
AppImage's copies would only duplicate them.

It is also not enough to delete libav*/libsw* while leaving the other bundled
libraries in place: the JNI wrapper's DT_RPATH puts ``native/`` first, and the
runtime FFmpeg 8.1 expects newer symbols from its dependencies than the
AppImage's copies provide (for example ``vaMapBuffer2`` in libva and
``g_string_copy`` in libglib). The older copies therefore shadow the runtime's
libraries and make libav* fail to load.

This script removes every file in the native directory whose ELF SONAME is
already present in the runtime. Libraries the runtime does not ship - libmpv,
libass, libplacebo, libunibreak and libXpresent - stay where they are.

Usage: strip-bundled-libs.py <native-dir>
"""

import os
import struct
import sys

SHT_DYNAMIC = 6
DT_SONAME = 14

# The runtime libraries live here. This is also what the dynamic loader sees
# inside the app sandbox. `ldconfig -p` is not usable from apply_extra: that
# sandbox has no /proc and ldconfig exits 1 there, so scan the directories.
RUNTIME_LIBRARY_DIRS = (
    "/usr/lib/x86_64-linux-gnu",
    "/usr/lib/x86_64-linux-gnu/codecs-extra/lib",
    "/usr/lib",
    "/usr/lib64",
    "/lib/x86_64-linux-gnu",
    "/lib",
    "/lib64",
)


def section_headers(data: bytes) -> list[tuple[int, int, int, int, int, int]]:
    """Return (name, type, offset, size, link, entsize) for every section."""
    if len(data) < 64 or data[:4] != b"\x7fELF":
        raise ValueError("not an ELF file")
    elf_class = data[4]
    endian = "<" if data[5] == 1 else ">"
    if elf_class == 2:
        shoff = struct.unpack_from(endian + "Q", data, 0x28)[0]
        shentsize = struct.unpack_from(endian + "H", data, 0x3A)[0]
        shnum = struct.unpack_from(endian + "H", data, 0x3C)[0]
    elif elf_class == 1:
        shoff = struct.unpack_from(endian + "I", data, 0x20)[0]
        shentsize = struct.unpack_from(endian + "H", data, 0x2E)[0]
        shnum = struct.unpack_from(endian + "H", data, 0x30)[0]
    else:
        raise ValueError("unknown ELF class")

    sections = []
    for index in range(shnum):
        base = shoff + index * shentsize
        if elf_class == 2:
            sh_name = struct.unpack_from(endian + "I", data, base)[0]
            sh_type = struct.unpack_from(endian + "I", data, base + 4)[0]
            sh_offset = struct.unpack_from(endian + "Q", data, base + 24)[0]
            sh_size = struct.unpack_from(endian + "Q", data, base + 32)[0]
            sh_link = struct.unpack_from(endian + "I", data, base + 40)[0]
            sh_entsize = struct.unpack_from(endian + "Q", data, base + 56)[0]
        else:
            sh_name = struct.unpack_from(endian + "I", data, base)[0]
            sh_type = struct.unpack_from(endian + "I", data, base + 4)[0]
            sh_offset = struct.unpack_from(endian + "I", data, base + 16)[0]
            sh_size = struct.unpack_from(endian + "I", data, base + 20)[0]
            sh_link = struct.unpack_from(endian + "I", data, base + 24)[0]
            sh_entsize = struct.unpack_from(endian + "I", data, base + 36)[0]
        sections.append((sh_name, sh_type, sh_offset, sh_size, sh_link, sh_entsize))
    return sections


def soname(path: str) -> str | None:
    """Read DT_SONAME from an ELF shared object."""
    with open(path, "rb") as f:
        data = f.read()
    if len(data) < 64 or data[:4] != b"\x7fELF":
        return None
    endian = "<" if data[5] == 1 else ">"
    elf_class = data[4]
    sections = section_headers(data)

    for _, sh_type, sh_offset, sh_size, sh_link, sh_entsize in sections:
        if sh_type != SHT_DYNAMIC:
            continue
        dynstr = sections[sh_link]
        dynstr_offset = dynstr[2]
        entry_size = sh_entsize or (16 if elf_class == 2 else 8)
        for offset in range(sh_offset, sh_offset + sh_size, entry_size):
            if elf_class == 2:
                tag = struct.unpack_from(endian + "q", data, offset)[0]
                value = struct.unpack_from(endian + "Q", data, offset + 8)[0]
            else:
                tag = struct.unpack_from(endian + "i", data, offset)[0]
                value = struct.unpack_from(endian + "I", data, offset + 4)[0]
            if tag == DT_SONAME:
                start = dynstr_offset + value
                end = data.index(b"\0", start)
                return data[start:end].decode("utf-8", "replace")
    return None


def runtime_libraries() -> set[str]:
    """Library file names available in the runtime, indexed by SONAME."""
    names = set()
    for directory in RUNTIME_LIBRARY_DIRS:
        try:
            entries = os.scandir(directory)
        except FileNotFoundError:
            continue
        with entries:
            for entry in entries:
                if ".so" in entry.name:
                    names.add(entry.name)
    return names


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__.strip(), file=sys.stderr)
        return 2

    native_dir = sys.argv[1]
    if not os.path.isdir(native_dir):
        print(f"error: {native_dir} is not a directory", file=sys.stderr)
        return 1

    try:
        available = runtime_libraries()
    except OSError as error:
        print(f"error: cannot list the runtime's libraries: {error}", file=sys.stderr)
        return 1
    if "libavcodec.so.62" not in available:
        print(
            "error: the runtime does not provide libavcodec.so.62; "
            "refusing to remove the bundled FFmpeg",
            file=sys.stderr,
        )
        return 1

    removed = 0
    kept = 0
    for entry in sorted(os.listdir(native_dir)):
        path = os.path.join(native_dir, entry)
        if not os.path.isfile(path) or ".so" not in entry:
            continue
        try:
            name = soname(path)
        except (OSError, ValueError, IndexError, struct.error) as error:
            print(f"warning: cannot read {entry}: {error}", file=sys.stderr)
            kept += 1
            continue
        if name is not None and name in available:
            os.unlink(path)
            removed += 1
        else:
            kept += 1

    print(f"removed {removed} runtime-provided libraries, kept {kept}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
