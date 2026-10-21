"""Fedora/openSUSE .rpm -> Zwischenformat (reiner Python-Parser, kein rpm nötig)."""
from __future__ import annotations

import bz2
import gzip
import io
import lzma
import os
import stat
import struct
from pathlib import Path, PurePosixPath

import zstandard

from .common import ConvertError, Converted, norm_name, norm_version, wrap_script
from .mapping import SourceAtom, SourceDep

T = dict(NAME=1000, VERSION=1001, RELEASE=1002, EPOCH=1003, SUMMARY=1004, DESCRIPTION=1005,
         LICENSE=1014, PACKAGER=1015, URL=1020, ARCH=1022,
         PREIN=1023, POSTIN=1024, PREUN=1025, POSTUN=1026,
         FILEFLAGS=1037, PROVIDENAME=1047, REQUIREFLAGS=1048, REQUIRENAME=1049,
         REQUIREVERSION=1050, CONFLICTFLAGS=1053, CONFLICTNAME=1054, CONFLICTVERSION=1055,
         OBSOLETENAME=1090, PREINPROG=1085, POSTINPROG=1086, PREUNPROG=1087, POSTUNPROG=1088,
         PROVIDEFLAGS=1112, PROVIDEVERSION=1113, DIRINDEXES=1116, BASENAMES=1117, DIRNAMES=1118,
         PAYLOADFORMAT=1124, PAYLOADCOMPRESSOR=1125, RECOMMENDNAME=5046, SUGGESTNAME=5049)
SENSE_LESS, SENSE_GREATER, SENSE_EQUAL, SENSE_RPMLIB = 2, 4, 8, 1 << 24
RPMFILE_CONFIG = 1
HEADER_MAGIC = b"\x8e\xad\xe8\x01"


def _read_header(data: bytes, off: int) -> tuple[dict[int, object], int]:
    if data[off:off + 4] != HEADER_MAGIC:
        raise ConvertError("RPM-Header-Magic fehlt")
    nindex, hsize = struct.unpack_from(">II", data, off + 8)
    idx = off + 16
    store = idx + nindex * 16
    tags: dict[int, object] = {}
    for i in range(nindex):
        tag, typ, o, count = struct.unpack_from(">IIII", data, idx + i * 16)
        p = store + o
        if typ == 6 or typ == 9 and count == 1:                     # STRING / I18N
            tags[tag] = data[p:data.index(b"\0", p)].decode(errors="replace")
        elif typ in (8, 9):                                          # STRING_ARRAY
            vals = []
            for _ in range(count):
                e = data.index(b"\0", p)
                vals.append(data[p:e].decode(errors="replace"))
                p = e + 1
            tags[tag] = vals
        elif typ == 4:
            tags[tag] = list(struct.unpack_from(f">{count}I", data, p))
        elif typ == 3:
            tags[tag] = list(struct.unpack_from(f">{count}H", data, p))
        elif typ == 7:
            tags[tag] = data[p:p + count]
    return tags, store + hsize


def read_headers(data: bytes) -> tuple[dict[int, object], int]:
    if data[:4] != b"\xed\xab\xee\xdb":
        raise ConvertError("Keine RPM-Datei (Lead-Magic fehlt)")
    _, end = _read_header(data, 96)                  # Signatur-Header
    end += (8 - end % 8) % 8
    return _read_header(data, end)


def _decompress(comp: str, blob: bytes) -> bytes:
    if comp in ("gzip", ""):
        return gzip.decompress(blob)
    if comp == "bzip2":
        return bz2.decompress(blob)
    if comp in ("xz", "lzma"):
        return lzma.decompress(blob)
    if comp == "zstd":
        return zstandard.ZstdDecompressor().decompressobj().decompress(blob)
    raise ConvertError(f"Unbekannte Payload-Kompression: {comp}")


def extract_cpio(blob: bytes, dest: Path, warnings: list[str]) -> None:
    """cpio newc (070701/070702) sicher entpacken, inkl. Hardlinks."""
    off, pending = 0, {}
    while off + 110 <= len(blob):
        magic = blob[off:off + 6]
        if magic not in (b"070701", b"070702"):
            raise ConvertError(f"Unbekanntes cpio-Format {magic!r}")
        f = [int(blob[off + 6 + i * 8: off + 14 + i * 8], 16) for i in range(13)]
        ino, mode, nlink, fsize, namesize = f[0], f[1], f[4], f[6], f[11]
        name = blob[off + 110: off + 110 + namesize - 1].decode(errors="replace")
        off = (off + 110 + namesize + 3) & ~3
        content = blob[off: off + fsize]
        off = (off + fsize + 3) & ~3
        if name == "TRAILER!!!":
            break
        rel = PurePosixPath(name.lstrip("./").lstrip("/"))
        if not rel.parts or ".." in rel.parts:
            continue
        target = dest / rel
        if not target.resolve().is_relative_to(dest.resolve()):
            raise ConvertError(f"Unsicherer Pfad im RPM: {name}")
        target.parent.mkdir(parents=True, exist_ok=True)
        if mode & 0o6000:
            warnings.append(f"setuid/setgid entfernt: /{rel}")
        perm = stat.S_IMODE(mode) & ~0o6000
        if stat.S_ISDIR(mode):
            target.mkdir(exist_ok=True)
            os.chmod(target, perm | 0o700)
        elif stat.S_ISLNK(mode):
            if target.is_symlink() or target.exists():
                target.unlink()
            os.symlink(content.decode(), target)
        elif stat.S_ISREG(mode):
            if nlink > 1 and fsize == 0:
                pending.setdefault(ino, []).append(target)
                continue
            target.write_bytes(content)
            os.chmod(target, perm)
            for other in pending.pop(ino, []):
                other.write_bytes(content)
                os.chmod(other, perm)
        else:
            warnings.append(f"Gerätedatei/FIFO übersprungen: /{rel}")


def _deps(h: dict, names: str, flags: str | None, versions: str | None) -> list[SourceDep]:
    ns = h.get(T[names], []) or []
    fl = h.get(T[flags], [0] * len(ns)) if flags else [0] * len(ns)
    vs = h.get(T[versions], [""] * len(ns)) if versions else [""] * len(ns)
    out = []
    for n, f, v in zip(ns, fl, vs):
        if f & SENSE_RPMLIB or n.startswith("rpmlib("):
            continue
        op = None
        if v:
            if f & SENSE_LESS and f & SENSE_EQUAL:
                op = "<="
            elif f & SENSE_GREATER and f & SENSE_EQUAL:
                op = ">="
            elif f & SENSE_LESS:
                op = "<"
            elif f & SENSE_GREATER:
                op = ">"
            elif f & SENSE_EQUAL:
                op = "="
        out.append(SourceDep((SourceAtom(n, op, norm_version(v) if op else None),)))
    return out


def _as_str(v) -> str:
    return v[0] if isinstance(v, list) else (v or "")


def parse(path: Path, workdir: Path) -> Converted:
    data = path.read_bytes()
    h, payload_off = read_headers(data)
    comp = _as_str(h.get(T["PAYLOADCOMPRESSOR"], "gzip"))
    fmt = _as_str(h.get(T["PAYLOADFORMAT"], "cpio"))
    if fmt != "cpio":
        raise ConvertError(f"Payload-Format {fmt} nicht unterstützt")
    tree = workdir / "tree"
    tree.mkdir(parents=True)
    warnings: list[str] = []
    extract_cpio(_decompress(comp, data[payload_off:]), tree, warnings)
    epoch = h.get(T["EPOCH"])
    ver = f"{h[T['VERSION']]}-{h[T['RELEASE']]}"
    full = f"{epoch[0]}:{ver}" if epoch else ver
    name = _as_str(h[T["NAME"]])
    c = Converted(
        fmt="rpm", name=norm_name(name), version=norm_version(full),
        arch=_as_str(h.get(T["ARCH"], "noarch")), tree=tree,
        summary=_as_str(h.get(T["SUMMARY"], "")), description=_as_str(h.get(T["DESCRIPTION"], "")),
        license=_as_str(h.get(T["LICENSE"], "unknown")), url=_as_str(h.get(T["URL"], "")),
        packager=_as_str(h.get(T["PACKAGER"], "")),
        depends=_deps(h, "REQUIRENAME", "REQUIREFLAGS", "REQUIREVERSION"),
        optdepends=_deps(h, "RECOMMENDNAME", None, None) + _deps(h, "SUGGESTNAME", None, None),
        conflicts=_deps(h, "CONFLICTNAME", "CONFLICTFLAGS", "CONFLICTVERSION"),
        replaces=list(h.get(T["OBSOLETENAME"], []) or []), source_version=full, warnings=warnings,
    )
    pv = h.get(T["PROVIDEVERSION"], [])
    for i, p in enumerate(h.get(T["PROVIDENAME"], []) or []):
        if p == name or "(" in p:
            continue
        v = pv[i] if i < len(pv) else ""
        c.provides.append(f"{norm_name(p)}={norm_version(v)}" if v else norm_name(p))
    # Konfigurationsdateien (%config)
    dirs, bases = h.get(T["DIRNAMES"], []), h.get(T["BASENAMES"], [])
    idx, flags = h.get(T["DIRINDEXES"], []), h.get(T["FILEFLAGS"], [])
    for b, di, fl in zip(bases, idx, flags):
        if fl & RPMFILE_CONFIG:
            c.backup.append((dirs[di] + b).lstrip("/"))
    # Scriptlets: $1 = Anzahl verbleibender Installationen (1 = Installation, 2 = Upgrade, 0 = Entfernen)
    hooks = {"PREIN": [("pre_install", "1"), ("pre_upgrade", "2")],
             "POSTIN": [("post_install", "1"), ("post_upgrade", "2")],
             "PREUN": [("pre_remove", "0")], "POSTUN": [("post_remove", "0")]}
    for tag, targets in hooks.items():
        body = _as_str(h.get(T[tag], ""))
        if not body:
            continue
        prog = _as_str(h.get(T[tag + "PROG"], "/bin/sh")) or "/bin/sh"
        if not body.startswith("#!"):
            body = f"#!{prog}\n{body}"
        for hook, args in targets:
            c.scripts[hook] = wrap_script(body, args)
    return c
