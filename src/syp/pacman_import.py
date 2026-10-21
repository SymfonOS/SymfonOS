"""Übernimmt eine per pacstrap/archiso erzeugte pacman-Datenbank in die syp-Datenbank.

Wird einmalig im Installer (Calamares shellprocess) ausgeführt; danach verwaltet
ausschließlich syp das System und pacman wird entfernt.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

from .db import Database
from .package import PackageInfo

_LIST = {"DEPENDS": "depends", "OPTDEPENDS": "optdepends", "PROVIDES": "provides",
         "CONFLICTS": "conflicts", "REPLACES": "replaces", "GROUPS": "groups"}


def parse_sections(text: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    cur = None
    for line in text.splitlines():
        if line.startswith("%") and line.endswith("%") and len(line) > 2:
            cur = line.strip("%")
            out[cur] = []
        elif cur and line:
            out[cur].append(line)
    return out


def import_db(root: Path, db: Database, log=print) -> int:
    local = root / "var/lib/pacman/local"
    n = 0
    for d in sorted(p for p in local.iterdir() if p.is_dir()) if local.is_dir() else []:
        desc = parse_sections((d / "desc").read_text())
        files = parse_sections((d / "files").read_text()) if (d / "files").exists() else {}
        first = lambda k, dflt="": (desc.get(k) or [dflt])[0]  # noqa: E731
        info = PackageInfo(
            name=first("NAME"), version=first("VERSION"), arch=first("ARCH", "x86_64"),
            summary=first("DESC"), url=first("URL"), packager=first("PACKAGER"),
            license=" AND ".join(desc.get("LICENSE", [])) or "unknown",
            build_date=int(first("BUILDDATE", "0")), installed_size=int(first("SIZE", "0")),
            backup=[b.split("\t")[0] for b in files.get("BACKUP", [])],
            origin={"format": "pacman-import", "scripts_reviewed": True},
            **{v: desc.get(k, []) for k, v in _LIST.items()},
        )
        info.optdepends = [o.split(":")[0].strip() for o in info.optdepends]
        entries = []
        for rel in files.get("FILES", []):
            p = root / rel
            rel = rel.rstrip("/")
            if p.is_symlink():
                entries.append({"path": rel, "type": "link", "sha256": None})
            elif p.is_dir():
                entries.append({"path": rel, "type": "dir", "sha256": None})
            elif p.is_file():
                h = hashlib.sha256()
                with open(p, "rb") as f:
                    for c in iter(lambda: f.read(1 << 20), b""):
                        h.update(c)
                entries.append({"path": rel, "type": "file", "sha256": h.hexdigest()})
        db.put(info, entries, "dep" if first("REASON", "0") == "1" else "explicit")
        n += 1
    log(f":: {n} Pakete aus pacman übernommen")
    return n


def remove_pacman_state(root: Path) -> None:
    """Optional: pacman-Datenbank archivieren, damit nur noch syp gilt."""
    local = root / "var/lib/pacman"
    if local.exists():
        os.replace(local, root / "var/lib/pacman.imported-by-syp")
