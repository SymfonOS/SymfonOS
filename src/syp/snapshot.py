"""Btrfs-Snapshots vor/nach Transaktionen (snapper, Fallback: btrfs subvolume snapshot)."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .config import Config


def _is_btrfs(path: Path) -> bool:
    try:
        out = subprocess.run(["stat", "-f", "-c", "%T", str(path)], capture_output=True,
                             text=True, check=True).stdout.strip()
        return out == "btrfs"
    except (OSError, subprocess.CalledProcessError):
        return False


def available(cfg: Config) -> bool:
    return cfg.snapshots and cfg.root == Path("/") and _is_btrfs(cfg.root) and (
        shutil.which("snapper") is not None or cfg.snapshot_backend == "btrfs")


def create(cfg: Config, kind: str, desc: str, pre_id: str | None = None) -> str | None:
    if not available(cfg):
        return None
    if cfg.snapshot_backend == "snapper" and shutil.which("snapper"):
        cmd = ["snapper", "-c", "root", "create", "--type", kind, "--print-number",
               "--cleanup-algorithm", "number", "--description", desc,
               "--userdata", "important=yes" if kind == "pre" else "important=no"]
        if kind == "post" and pre_id:
            cmd += ["--pre-number", pre_id]
        r = subprocess.run(cmd, capture_output=True, text=True)
        return r.stdout.strip() or None if r.returncode == 0 else None
    import time
    name = f"/.snapshots/syp-{int(time.time())}-{kind}"
    r = subprocess.run(["btrfs", "subvolume", "snapshot", "-r", "/", name], capture_output=True)
    return name if r.returncode == 0 else None
