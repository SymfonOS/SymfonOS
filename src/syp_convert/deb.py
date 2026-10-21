"""Debian/Ubuntu .deb -> Zwischenformat."""
from __future__ import annotations

import bz2
import gzip
import io
import lzma
import re
import tarfile
from pathlib import Path

import zstandard

from .common import ConvertError, Converted, norm_name, norm_version, wrap_script
from .mapping import SourceAtom, SourceDep

DEB_OPS = {"<<": "<", "<=": "<=", "=": "=", ">=": ">=", ">>": ">", "<": "<=", ">": ">="}
_REL = re.compile(r"^\s*([^\s(\[<:]+)(?::\S+)?\s*(?:\(\s*(<<|<=|>=|>>|=|<|>)\s*([^)\s]+)\s*\))?")


def read_ar(path: Path) -> dict[str, bytes]:
    data = path.read_bytes()
    if not data.startswith(b"!<arch>\n"):
        raise ConvertError(f"{path.name}: kein ar-Archiv")
    off, out = 8, {}
    while off + 60 <= len(data):
        hdr = data[off:off + 60]
        name = hdr[:16].decode().strip().rstrip("/")
        size = int(hdr[48:58].decode().strip())
        out[name] = data[off + 60: off + 60 + size]
        off += 60 + size + (size & 1)
    return out


def decompress(name: str, blob: bytes) -> bytes:
    if name.endswith(".gz"):
        return gzip.decompress(blob)
    if name.endswith(".xz") or name.endswith(".lzma"):
        return lzma.decompress(blob)
    if name.endswith(".zst"):
        return zstandard.ZstdDecompressor().decompressobj().decompress(blob)
    if name.endswith(".bz2"):
        return bz2.decompress(blob)
    return blob


def parse_control(text: str) -> dict[str, str]:
    fields, cur = {}, None
    for line in text.splitlines():
        if line.startswith((" ", "\t")) and cur:
            fields[cur] += "\n" + line[1:]
        elif ":" in line:
            cur, v = line.split(":", 1)
            fields[cur.strip()] = v.strip()
    return fields


def parse_rel(s: str) -> list[SourceDep]:
    """Debian-Beziehungsfeld (Depends, Conflicts, ...) parsen."""
    out = []
    for group in filter(None, (g.strip() for g in s.split(","))):
        alts = []
        for alt in group.split("|"):
            alt = re.sub(r"\[[^\]]*\]|<[^>]*>", "", alt)   # Arch-/Profil-Einschränkungen
            m = _REL.match(alt)
            if not m:
                continue
            name, op, ver = m.groups()
            alts.append(SourceAtom(name, DEB_OPS.get(op) if op else None,
                                   norm_version(ver) if ver else None))
        if alts:
            out.append(SourceDep(tuple(alts)))
    return out


def deb_license(tree: Path, pkg: str) -> str:
    cp = tree / f"usr/share/doc/{pkg}/copyright"
    if not cp.exists():
        return "unknown"
    lic = []
    for line in cp.read_text(errors="replace").splitlines():
        if line.startswith("License:"):
            v = line.split(":", 1)[1].strip()
            if v and v not in lic:
                lic.append(v)
    return " AND ".join(lic[:4]) if lic else "unknown"


def extract_tar(blob: bytes, dest: Path, warnings: list[str] | None = None) -> None:
    """Sicheres Entpacken (keine absoluten/../-Pfade, keine Gerätedateien).

    Der tar-Filter entfernt setuid/setgid-Bits; betroffene Dateien werden gemeldet.
    """
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:") as tf:
        members = [m for m in tf.getmembers() if m.isfile() or m.isdir() or m.issym() or m.islnk()]
        if warnings is not None:
            warnings += [f"setuid/setgid entfernt: /{m.name.lstrip('./')}" for m in members
                         if m.mode & 0o6000]
        tf.extractall(dest, members=members, filter="tar")


def parse(path: Path, workdir: Path) -> Converted:
    parts = read_ar(path)
    ctl_name = next((n for n in parts if n.startswith("control.tar")), None)
    data_name = next((n for n in parts if n.startswith("data.tar")), None)
    if not ctl_name or not data_name:
        raise ConvertError(f"{path.name}: control/data fehlen")
    ctl_dir, tree = workdir / "control", workdir / "tree"
    ctl_dir.mkdir(parents=True)
    tree.mkdir(parents=True)
    extract_tar(decompress(ctl_name, parts[ctl_name]), ctl_dir)
    warnings: list[str] = []
    extract_tar(decompress(data_name, parts[data_name]), tree, warnings)
    f = parse_control((ctl_dir / "control").read_text(errors="replace"))
    desc = f.get("Description", "").split("\n")
    long_desc = "\n".join("" if l.strip() == "." else l for l in desc[1:]).strip()
    name = f["Package"]
    c = Converted(
        fmt="deb", name=norm_name(name), version=norm_version(f["Version"]),
        arch=f.get("Architecture", "all"), tree=tree, summary=desc[0], description=long_desc,
        url=f.get("Homepage", ""), packager=f.get("Maintainer", ""),
        depends=parse_rel(f.get("Pre-Depends", "")) + parse_rel(f.get("Depends", "")),
        optdepends=parse_rel(f.get("Recommends", "")) + parse_rel(f.get("Suggests", "")),
        conflicts=parse_rel(f.get("Conflicts", "")) + parse_rel(f.get("Breaks", "")),
        source_version=f["Version"], warnings=warnings,
    )
    c.license = deb_license(tree, name)
    for d in parse_rel(f.get("Provides", "")):
        a = d.alternatives[0]
        c.provides.append(f"{norm_name(a.name)}={a.version}" if a.version else norm_name(a.name))
    conflict_names = {a.name for d in c.conflicts for a in d.alternatives}
    c.replaces = [a.name for d in parse_rel(f.get("Replaces", "")) for a in d.alternatives
                  if a.name in conflict_names]
    conff = ctl_dir / "conffiles"
    if conff.exists():
        c.backup = [l.strip().removeprefix("remove-on-upgrade ") for l in conff.read_text().splitlines()
                    if l.strip()]
    hooks = {"preinst": [("pre_install", "install"), ("pre_upgrade", 'upgrade "$SYP_OLD_VERSION"')],
             "postinst": [("post_install", "configure"), ("post_upgrade", 'configure "$SYP_OLD_VERSION"')],
             "prerm": [("pre_remove", "remove")],
             "postrm": [("post_remove", "remove")]}
    for script, targets in hooks.items():
        p = ctl_dir / script
        if p.exists():
            body = p.read_text(errors="replace")
            for hook, args in targets:
                c.scripts[hook] = wrap_script(body, args)
    for t in ("triggers", "templates", "config"):
        if (ctl_dir / t).exists():
            c.warnings.append(f"dpkg-{t} vorhanden – wird von syp nicht ausgewertet")
    return c
