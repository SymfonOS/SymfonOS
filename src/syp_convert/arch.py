"""Arch Linux .pkg.tar.(zst|xz|gz) -> Zwischenformat."""
from __future__ import annotations

import io
import tarfile
from pathlib import Path

from .common import ConvertError, Converted
from .deb import decompress
from .mapping import SourceAtom, SourceDep

META = {".PKGINFO", ".MTREE", ".BUILDINFO", ".INSTALL", ".CHANGELOG"}


def parse_pkginfo(text: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for line in text.splitlines():
        if line.startswith("#") or " = " not in line:
            continue
        k, v = line.split(" = ", 1)
        out.setdefault(k.strip(), []).append(v.strip())
    return out


def _atom(s: str) -> SourceAtom:
    s = s.split(":")[0].strip() if ": " in s else s.strip()    # optdepend-Beschreibung abtrennen
    for op in (">=", "<=", "=", "<", ">"):
        if op in s:
            n, v = s.split(op, 1)
            return SourceAtom(n, op, v)
    return SourceAtom(s)


def parse(path: Path, workdir: Path) -> Converted:
    blob = decompress(path.name, path.read_bytes())
    tree = workdir / "tree"
    tree.mkdir(parents=True)
    warnings: list[str] = []
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:") as tf:
        names = tf.getnames()
        if ".PKGINFO" not in names:
            raise ConvertError(f"{path.name}: .PKGINFO fehlt")
        info = parse_pkginfo(tf.extractfile(".PKGINFO").read().decode())
        install = tf.extractfile(".INSTALL").read().decode() if ".INSTALL" in names else ""
        members = [m for m in tf.getmembers() if m.name not in META]
        warnings += [f"setuid/setgid entfernt: /{m.name}" for m in members if m.mode & 0o6000]
        tf.extractall(tree, members=members, filter="tar")
    g = lambda k: (info.get(k) or [""])[0]  # noqa: E731
    c = Converted(
        fmt="arch", name=g("pkgname"), version=g("pkgver"), arch=g("arch") or "x86_64",
        tree=tree, summary=g("pkgdesc"), license=" AND ".join(info.get("license", [])) or "unknown",
        url=g("url"), packager=g("packager"),
        depends=[SourceDep((_atom(d),)) for d in info.get("depend", [])],
        optdepends=[SourceDep((_atom(d),)) for d in info.get("optdepend", [])],
        conflicts=[SourceDep((_atom(d),)) for d in info.get("conflict", [])],
        provides=info.get("provides", []), replaces=info.get("replaces", []),
        backup=info.get("backup", []), source_version=g("pkgver"), warnings=warnings,
    )
    if install:
        calls = {"pre_install": '"$SYP_NEW_VERSION"', "post_install": '"$SYP_NEW_VERSION"',
                 "pre_upgrade": '"$SYP_NEW_VERSION" "$SYP_OLD_VERSION"',
                 "post_upgrade": '"$SYP_NEW_VERSION" "$SYP_OLD_VERSION"',
                 "pre_remove": '"$SYP_OLD_VERSION"', "post_remove": '"$SYP_OLD_VERSION"'}
        for hook, args in calls.items():
            if f"{hook}()" in install.replace(" ()", "()"):
                c.scripts[hook] = f"{install}\n{hook} {args}\n"
    return c
