"""Repositories: signierter Index (index.json.zst + .sig) und Paket-Pool.

Layout eines Repos:
    <repo>/index.json.zst        {"name", "arch", "generated", "packages": [ {info..., "file", "sha256", "size"} ]}
    <repo>/index.json.zst.sig    Ed25519-Signatur
    <repo>/pool/<a>/<name>-<ver>-<arch>.syp
"""
from __future__ import annotations

import hashlib
import json
import shutil
import time
import urllib.request
from pathlib import Path

import zstandard

from .config import Config, RepoConf
from .crypto import Keyring, SecretKey, sign_file, verify_file
from .package import SypPackage, PackageInfo


class RepoError(Exception):
    pass


def _fetch(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    if url.startswith("file://") or "://" not in url:
        shutil.copyfile(url.removeprefix("file://"), tmp)
    else:
        req = urllib.request.Request(url, headers={"User-Agent": "syp/1"})
        with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
            shutil.copyfileobj(r, f)
    tmp.replace(dest)


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class RepoPackage:
    """Paketeintrag aus einem Repo-Index (oder einer lokalen Datei)."""

    def __init__(self, info: PackageInfo, repo: str, file: str, sha: str, size: int,
                 priority: int = 50, local_path: Path | None = None):
        self.info, self.repo, self.file, self.sha256, self.size = info, repo, file, sha, size
        self.priority, self.local_path = priority, local_path

    @property
    def name(self) -> str:
        return self.info.name

    @property
    def version(self) -> str:
        return self.info.version

    def __repr__(self) -> str:
        return f"<{self.repo}/{self.name} {self.version}>"


def load_synced(cfg: Config) -> list[RepoPackage]:
    out = []
    for rc in cfg.repos:
        if not rc.enabled:
            continue
        f = cfg.sync_dir / f"{rc.name}.index.json.zst"
        if not f.exists():
            continue
        idx = json.loads(zstandard.ZstdDecompressor().decompressobj().decompress(f.read_bytes()))
        for e in idx["packages"]:
            out.append(RepoPackage(PackageInfo.from_dict(e["info"]), rc.name, e["file"],
                                   e["sha256"], e["size"], rc.priority))
    return out


def sync(cfg: Config, keyring: Keyring, log=print) -> None:
    for rc in cfg.repos:
        if not rc.enabled:
            continue
        dest = cfg.sync_dir / f"{rc.name}.index.json.zst"
        tmp = cfg.sync_dir / f".{rc.name}.new"
        _fetch(f"{rc.url}/index.json.zst", tmp)
        _fetch(f"{rc.url}/index.json.zst.sig", tmp.with_name(tmp.name + ".sig"))
        if cfg.require_signatures:
            k = verify_file(keyring, tmp)
            log(f":: {rc.name}: Index signiert von {k.id} ({k.comment})")
        tmp.replace(dest)
        tmp.with_name(tmp.name + ".sig").replace(dest.with_name(dest.name + ".sig"))


def fetch_package(cfg: Config, pkg: RepoPackage) -> Path:
    """Lädt ein Paket in den Cache und prüft die Index-Prüfsumme."""
    if pkg.local_path:
        return pkg.local_path
    rc = next(r for r in cfg.repos if r.name == pkg.repo)
    dest = cfg.cache_dir / Path(pkg.file).name
    if not dest.exists() or sha256(dest) != pkg.sha256:
        _fetch(f"{rc.url}/{pkg.file}", dest)
    if sha256(dest) != pkg.sha256:
        dest.unlink()
        raise RepoError(f"{pkg.name}: Prüfsumme stimmt nicht mit dem signierten Index überein")
    return dest


def build_index(repo_dir: Path, name: str, arch: str, signer: SecretKey | None) -> Path:
    """Erzeugt index.json.zst aus allen .syp im Pool (für `syp repo-index`)."""
    entries = []
    for p in sorted((repo_dir / "pool").rglob("*.syp")):
        pkg = SypPackage(p)
        if pkg.info.arch not in (arch, "any"):
            continue
        entries.append({"info": pkg.info.to_dict(), "file": p.relative_to(repo_dir).as_posix(),
                        "sha256": sha256(p), "size": p.stat().st_size})
    idx = {"name": name, "arch": arch, "generated": int(time.time()), "packages": entries}
    out = repo_dir / "index.json.zst"
    out.write_bytes(zstandard.ZstdCompressor(level=19).compress(
        json.dumps(idx, ensure_ascii=False).encode()))
    if signer:
        sign_file(signer, out)
    return out


def add_to_pool(repo_dir: Path, pkg_path: Path) -> Path:
    name = SypPackage(pkg_path).info.name
    dest = repo_dir / "pool" / name[0] / pkg_path.name
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(pkg_path, dest)
    return dest
