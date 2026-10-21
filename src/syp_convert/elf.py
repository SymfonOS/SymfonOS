"""Minimaler ELF-Leser: DT_NEEDED und DT_SONAME aus dynamischen Binärdateien."""
from __future__ import annotations

import struct
from pathlib import Path

DT_NULL, DT_NEEDED, DT_STRTAB, DT_SONAME = 0, 1, 5, 14
PT_LOAD, PT_DYNAMIC = 1, 2


def dynamic_info(path: Path) -> tuple[list[str], str | None]:
    """Liefert (needed, soname) oder ([], None), falls keine dynamische ELF-Datei."""
    try:
        data = path.read_bytes()
    except OSError:
        return [], None
    if len(data) < 64 or data[:4] != b"\x7fELF":
        return [], None
    is64 = data[4] == 2
    end = "<" if data[5] == 1 else ">"
    try:
        if is64:
            phoff, = struct.unpack_from(end + "Q", data, 0x20)
            phentsize, phnum = struct.unpack_from(end + "HH", data, 0x36)
        else:
            phoff, = struct.unpack_from(end + "I", data, 0x1C)
            phentsize, phnum = struct.unpack_from(end + "HH", data, 0x2A)
        loads, dyn = [], None
        for i in range(phnum):
            off = phoff + i * phentsize
            if is64:
                p_type, _, p_offset, p_vaddr, _, p_filesz, _, _ = struct.unpack_from(end + "IIQQQQQQ", data, off)
            else:
                p_type, p_offset, p_vaddr, _, p_filesz, _, _, _ = struct.unpack_from(end + "IIIIIIII", data, off)
            if p_type == PT_LOAD:
                loads.append((p_vaddr, p_offset, p_filesz))
            elif p_type == PT_DYNAMIC:
                dyn = (p_offset, p_filesz)
        if dyn is None:
            return [], None
        fmt, size = (end + "qQ", 16) if is64 else (end + "iI", 8)
        entries = []
        for off in range(dyn[0], dyn[0] + dyn[1], size):
            tag, val = struct.unpack_from(fmt, data, off)
            if tag == DT_NULL:
                break
            entries.append((tag, val))
        strtab = next((v for t, v in entries if t == DT_STRTAB), None)
        if strtab is None:
            return [], None
        stroff = next((o + strtab - va for va, o, sz in loads if va <= strtab < va + sz), None)
        if stroff is None:
            return [], None

        def s(v: int) -> str:
            e = data.index(b"\0", stroff + v)
            return data[stroff + v:e].decode(errors="replace")

        needed = [s(v) for t, v in entries if t == DT_NEEDED]
        soname = next((s(v) for t, v in entries if t == DT_SONAME), None)
        return needed, soname
    except (struct.error, ValueError):
        return [], None
