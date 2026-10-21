"""Erzeugt echte Test-Pakete (.deb via dpkg-deb oder ar, .rpm via Mini-Writer, Arch via tar)."""
from __future__ import annotations

import gzip
import io
import shutil
import struct
import subprocess
import tarfile
from pathlib import Path

import zstandard

ELF_SRC = Path(shutil.which("true") or "/usr/bin/true").resolve()


def _tar_bytes(files: dict[str, tuple[bytes, int]]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        for name, (data, mode) in files.items():
            ti = tarfile.TarInfo(name)
            ti.size, ti.mode = len(data), mode
            tf.addfile(ti, io.BytesIO(data))
    return buf.getvalue()


def make_deb(tmp: Path, name="paintbox", version="1:2.1~rc1-3ubuntu1", depends="libc6 (>= 2.34), "
             "libgtk-3-0 (>= 3.24) | libgtk-4-1, python3-numpy, debconf (>= 0.5), libfoo-missing1",
             postinst="#!/bin/sh\nset -e\necho configured \"$1\"\n") -> Path:
    files = {
        f"./usr/bin/{name}": (ELF_SRC.read_bytes(), 0o755),
        f"./etc/{name}.conf": (b"x=1\n", 0o644),
        f"./usr/share/applications/{name}.desktop":
            (b"[Desktop Entry]\nName=Paintbox\nCategories=Graphics;2DGraphics;\n", 0o644),
        f"./usr/share/doc/{name}/copyright": (b"Format: dep5\nLicense: GPL-3.0-or-later\n", 0o644),
    }
    control = (f"Package: {name}\nVersion: {version}\nArchitecture: amd64\nMaintainer: Test <t@e.st>\n"
               f"Depends: {depends}\nRecommends: ffmpeg\nHomepage: https://example.org\n"
               f"Description: Ein Malprogramm\n Langer Text.\n .\n Zweiter Absatz.\n").encode()
    ctl = {"./control": (control, 0o644), "./conffiles": (f"/etc/{name}.conf\n".encode(), 0o644),
           "./postinst": (postinst.encode(), 0o755)}
    out = tmp / f"{name}.deb"
    members = [("debian-binary", b"2.0\n"), ("control.tar.gz", gzip.compress(_tar_bytes(ctl))),
               ("data.tar.zst", zstandard.ZstdCompressor().compress(_tar_bytes(files)))]
    with open(out, "wb") as f:
        f.write(b"!<arch>\n")
        for n, data in members:
            f.write(f"{n + '/':<16}{0:<12}{0:<6}{0:<6}{'100644':<8}{len(data):<10}`\n".encode())
            f.write(data + (b"\n" if len(data) % 2 else b""))
    if shutil.which("dpkg-deb"):
        subprocess.run(["dpkg-deb", "--info", out], check=True, capture_output=True)  # Formatprüfung
    return out


# --- RPM ------------------------------------------------------------------------------------
def _rpm_header(entries: list[tuple[int, int, object]]) -> bytes:
    index, store = b"", b""
    for tag, typ, val in sorted(entries):
        if typ == 4:
            store += b"\0" * ((4 - len(store) % 4) % 4)
            data, count = struct.pack(f">{len(val)}I", *val), len(val)
        elif typ == 6:
            data, count = val.encode() + b"\0", 1
        else:  # 8 = STRING_ARRAY
            data, count = b"".join(v.encode() + b"\0" for v in val), len(val)
        index += struct.pack(">IIII", tag, typ, len(store), count)
        store += data
    return b"\x8e\xad\xe8\x01\0\0\0\0" + struct.pack(">II", len(entries), len(store)) + index + store


def _cpio(files: dict[str, tuple[bytes, int]]) -> bytes:
    out = b""
    for i, (name, (data, mode)) in enumerate(list(files.items()) + [("TRAILER!!!", (b"", 0))]):
        nb = name.encode() + b"\0"
        hdr = b"070701" + b"".join(f"{v:08X}".encode() for v in
                                   (i + 1, mode, 0, 0, 1, 0, len(data), 0, 0, 0, 0, len(nb), 0))
        out += hdr + nb
        out += b"\0" * ((4 - len(out) % 4) % 4) + data
        out += b"\0" * ((4 - len(out) % 4) % 4)
    return out


def make_rpm(tmp: Path, name="colorlab") -> Path:
    files = {f"./usr/bin/{name}": (ELF_SRC.read_bytes(), 0o100755),
             f"./etc/{name}.conf": (b"a=1\n", 0o100644),
             "./usr/lib64": (b"", 0o40755),
             f"./usr/lib64/lib{name}.so.1": (b"not-elf", 0o100644)}
    E = [(1000, 6, name), (1001, 6, "3.0~beta2"), (1002, 6, "1.fc43"), (1004, 6, "Farblabor"),
         (1005, 6, "Beschreibung"), (1014, 6, "MIT"), (1020, 6, "https://example.org"),
         (1022, 6, "x86_64"), (1024, 6, "echo post $1"), (1086, 6, "/bin/sh"),
         (1047, 8, [name, f"{name}(x86-64)", "colour-lab"]), (1112, 4, [8, 8, 8]),
         (1113, 8, ["3.0-1", "3.0-1", "3.0"]),
         (1049, 8, ["libc.so.6()(64bit)", "rpmlib(CompressedFileNames)", "gtk3", "/bin/sh",
                    "python3dist(requests)", "perl(Foo::Bar)", "nonexistent-lib"]),
         (1048, 4, [0, 1 << 24, 12, 0, 0, 0, 0]),
         (1050, 8, ["", "3.0.4-1", "3.24", "", "", "", ""]),
         (1117, 8, [name, f"{name}.conf", f"lib{name}.so.1"]), (1118, 8, ["/usr/bin/", "/etc/", "/usr/lib64/"]),
         (1116, 4, [0, 1, 2]), (1037, 4, [0, 1, 0]),
         (1124, 6, "cpio"), (1125, 6, "gzip")]
    lead = b"\xed\xab\xee\xdb\x03\x00" + b"\0" * 90
    sig = _rpm_header([])
    out = tmp / f"{name}.rpm"
    out.write_bytes(lead + sig + _rpm_header(E) + gzip.compress(_cpio(files)))
    return out


def make_arch(tmp: Path, name="brushkit") -> Path:
    pkginfo = (f"pkgname = {name}\npkgver = 1.4-2\npkgdesc = Pinsel\nurl = https://example.org\n"
               f"arch = x86_64\nlicense = GPL-2.0-or-later\ndepend = glibc\ndepend = qt6-base>=6.5\n"
               f"optdepend = krita: Integration\nprovides = brushes=1.4\nbackup = etc/{name}.conf\n").encode()
    install = b"post_install() {\n  echo hi $1\n}\n"
    files = {".PKGINFO": (pkginfo, 0o644), ".INSTALL": (install, 0o644),
             f"usr/bin/{name}": (ELF_SRC.read_bytes(), 0o755), f"etc/{name}.conf": (b"z\n", 0o644)}
    out = tmp / f"{name}-1.4-2-x86_64.pkg.tar.zst"
    out.write_bytes(zstandard.ZstdCompressor().compress(_tar_bytes(files)))
    return out
