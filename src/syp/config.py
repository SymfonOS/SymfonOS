"""Konfiguration: <root>/etc/syp/syp.toml"""
from __future__ import annotations

import platform
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class RepoConf:
    name: str
    url: str
    priority: int = 50     # kleiner = bevorzugt
    enabled: bool = True


@dataclass
class Config:
    root: Path
    arch: str = field(default_factory=platform.machine)
    repos: list[RepoConf] = field(default_factory=list)
    snapshots: bool = True
    snapshot_backend: str = "snapper"
    keep_cache: int = 3
    run_scripts: bool = True
    require_signatures: bool = True

    @property
    def etc(self) -> Path:
        return self.root / "etc/syp"

    @property
    def keyring_dir(self) -> Path:
        return self.etc / "keys"

    @property
    def db_path(self) -> Path:
        return self.root / "var/lib/syp/syp.db"

    @property
    def sync_dir(self) -> Path:
        return self.root / "var/lib/syp/sync"

    @property
    def cache_dir(self) -> Path:
        return self.root / "var/cache/syp/pkg"

    @property
    def triggers_dirs(self) -> list[Path]:
        return [self.root / "usr/share/syp/triggers", self.etc / "triggers"]

    @classmethod
    def load(cls, root: Path | str = "/") -> "Config":
        root = Path(root)
        cfg = cls(root=root)
        f = root / "etc/syp/syp.toml"
        if f.exists():
            d = tomllib.loads(f.read_text())
            cfg.arch = d.get("arch", cfg.arch)
            cfg.repos = [RepoConf(**r) for r in d.get("repo", [])]
            snap = d.get("snapshots", {})
            cfg.snapshots = snap.get("enabled", True)
            cfg.snapshot_backend = snap.get("backend", "snapper")
            cfg.keep_cache = d.get("cache", {}).get("keep", 3)
            sec = d.get("security", {})
            cfg.run_scripts = sec.get("run_scripts", True)
            cfg.require_signatures = sec.get("require_signatures", True)
        cfg.repos.sort(key=lambda r: r.priority)
        return cfg
