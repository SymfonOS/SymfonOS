"""Trigger: einmal pro Transaktion ausgeführte Befehle (ldconfig, Icon-Cache, ...).

Format (TOML, /usr/share/syp/triggers/*.toml oder /etc/syp/triggers/*.toml):
    description = "Icon-Cache aktualisieren"
    paths = ["usr/share/icons/*"]
    exec = ["gtk-update-icon-cache", "-q", "-t", "-f", "/usr/share/icons/hicolor"]
"""
from __future__ import annotations

import fnmatch
import shutil
import subprocess
import tomllib
from importlib import resources
from pathlib import Path

from .config import Config


def load(cfg: Config) -> list[dict]:
    found: dict[str, dict] = {}
    builtin = resources.files("syp") / "triggers"
    for t in sorted(builtin.iterdir(), key=lambda p: p.name):
        if t.name.endswith(".toml"):
            found[t.name] = tomllib.loads(t.read_text())
    for d in cfg.triggers_dirs:
        for t in sorted(d.glob("*.toml")) if d.is_dir() else []:
            found[t.name] = tomllib.loads(t.read_text())   # /etc überschreibt gleichnamige
    return [dict(v, id=k) for k, v in sorted(found.items())]


def run(cfg: Config, changed: set[str], log=print) -> None:
    for t in load(cfg):
        if not any(fnmatch.fnmatch(p, pat) for p in changed for pat in t.get("paths", [])):
            continue
        cmd = t["exec"]
        if shutil.which(cmd[0], path=str(cfg.root / "usr/bin")) is None:
            continue
        log(f":: Trigger: {t.get('description', t['id'])}")
        if cfg.root != Path("/"):
            if not shutil.which("chroot"):
                continue
            cmd = ["chroot", str(cfg.root)] + cmd
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode:
            log(f"   Warnung: Trigger {t['id']} fehlgeschlagen: {r.stderr.strip()[:300]}")
