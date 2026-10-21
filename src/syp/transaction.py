"""Transaktionen: Herunterladen, Prüfen, Konfliktcheck, Anwenden, Historie, Rollback."""
from __future__ import annotations

import fcntl
import fnmatch
import hashlib
import os
import shutil
import subprocess
from contextlib import contextmanager
from pathlib import Path

from . import repo, snapshot, triggers
from .config import Config
from .crypto import Keyring
from .db import Database
from .package import SypPackage
from .repo import RepoPackage, sha256
from .solver import Plan


class TransactionError(Exception):
    pass


@contextmanager
def locked(cfg: Config):
    lock = cfg.root / "var/lib/syp/db.lck"
    lock.parent.mkdir(parents=True, exist_ok=True)
    with open(lock, "w") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise TransactionError("Eine andere syp-Instanz läuft bereits") from None
        yield


def local_package(path: Path) -> RepoPackage:
    p = SypPackage(path)
    return RepoPackage(p.info, "lokal", path.name, sha256(path), path.stat().st_size,
                       priority=0, local_path=path.resolve())


class Transaction:
    def __init__(self, cfg: Config, db: Database, keyring: Keyring, log=print,
                 overwrite: tuple[str, ...] = (), allow_unsigned: bool = False,
                 trust_scripts: bool = False, no_scripts: bool = False):
        self.cfg, self.db, self.keyring, self.log = cfg, db, keyring, log
        self.overwrite, self.allow_unsigned = overwrite, allow_unsigned
        self.trust_scripts = trust_scripts
        self.no_scripts = no_scripts or not cfg.run_scripts
        self.changed: set[str] = set()

    # --- Hilfsfunktionen --------------------------------------------------------
    def _p(self, rel: str) -> Path:
        return self.cfg.root / rel

    def _script(self, info, hook: str, **env) -> None:
        body = info.scripts.get(hook)
        if not body or self.no_scripts:
            return
        origin = info.origin or {}
        if origin.get("format", "native") != "native" and not origin.get("scripts_reviewed") \
                and not self.trust_scripts:
            self.log(f"   Hinweis: {hook} von {info.name} (konvertiert, ungeprüft) übersprungen; "
                     f"--trust-scripts zum Ausführen")
            return
        cmd = ["sh", "-ec", body, hook]
        if self.cfg.root != Path("/"):
            if os.geteuid() != 0 or not (self.cfg.root / "bin/sh").exists():
                self.log(f"   Hinweis: {hook} von {info.name} in --root ohne chroot übersprungen")
                return
            cmd = ["chroot", str(self.cfg.root)] + cmd
        self.log(f"   {hook}: {info.name}")
        r = subprocess.run(cmd, env={"PATH": "/usr/bin:/usr/sbin:/bin:/sbin", **env})
        if r.returncode and hook.startswith("pre_"):
            raise TransactionError(f"{hook} von {info.name} fehlgeschlagen ({r.returncode})")
        if r.returncode:
            self.log(f"   Warnung: {hook} von {info.name} beendet mit {r.returncode}")

    @staticmethod
    def _hash(p: Path) -> str | None:
        if not p.is_file() or p.is_symlink():
            return None
        h = hashlib.sha256()
        with open(p, "rb") as f:
            for c in iter(lambda: f.read(1 << 20), b""):
                h.update(c)
        return h.hexdigest()

    def _place(self, src: Path, dst: Path, e: dict) -> None:
        if e["type"] == "dir":
            if not dst.is_dir():
                dst.mkdir(parents=True, exist_ok=True)
                os.chmod(dst, e["mode"])
            return
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.is_dir() and not dst.is_symlink():
            raise TransactionError(f"Verzeichnis im Weg: /{e['path']}")
        tmp = dst.with_name(f".{dst.name}.syp-tmp")
        if tmp.exists() or tmp.is_symlink():
            tmp.unlink()
        if e["type"] == "link":
            os.symlink(e["target"], tmp)
        else:
            try:
                os.rename(src, tmp)
            except OSError:          # z. B. EXDEV über Btrfs-Subvolume hinweg
                shutil.copy2(src, tmp)
        os.replace(tmp, dst)
        self.changed.add(e["path"])

    # --- Prüfungen ---------------------------------------------------------------
    def _check_conflicts(self, pkgs: list[SypPackage], removing: set[str]) -> None:
        claimed: dict[str, str] = {}
        problems = []
        for pkg in pkgs:
            replacing = {pkg.info.name} | removing
            for e in pkg.files:
                if e["type"] == "dir":
                    continue
                path = e["path"]
                if path in claimed:
                    problems.append(f"/{path}: in {claimed[path]} und {pkg.info.name}")
                    continue
                claimed[path] = pkg.info.name
                owners = set(self.db.owners(path)) - replacing
                if owners:
                    problems.append(f"/{path} existiert bereits in {', '.join(sorted(owners))}")
                elif not self.db.owners(path):
                    target = self._p(path)
                    if (target.exists() or target.is_symlink()) and not target.is_dir() and \
                            not any(fnmatch.fnmatch("/" + path, g) for g in self.overwrite):
                        problems.append(f"/{path} existiert im Dateisystem (keinem Paket zugeordnet)")
        if problems:
            raise TransactionError("Dateikonflikte:\n  " + "\n  ".join(problems[:25]))

    # --- Entfernen / Installieren --------------------------------------------------
    def _remove_files(self, name: str, keep: set[str], info) -> None:
        backup = {b.lstrip("/") for b in info.backup}
        files = self.db.files_of(name)
        for f in sorted(files, key=lambda f: f["path"], reverse=True):
            path = f["path"]
            if path in keep or set(self.db.owners(path)) - {name}:
                continue
            target = self._p(path)
            if f["type"] == "dir":
                try:
                    target.rmdir()
                except OSError:
                    pass
                continue
            if not (target.exists() or target.is_symlink()):
                continue
            if path in backup and self._hash(target) != f["sha256"]:
                target.rename(target.with_name(target.name + ".sypsave"))
                self.log(f"   /{path} geändert -> gesichert als .sypsave")
                continue
            target.unlink()
            self.changed.add(path)

    def _remove(self, name: str) -> None:
        inst = self.db.get(name)
        self._script(inst.info, "pre_remove", SYP_OLD_VERSION=inst.version)
        self.log(f"-> Entferne {name} {inst.version}")
        self._remove_files(name, set(), inst.info)
        self.db.delete(name)
        self._script(inst.info, "post_remove", SYP_OLD_VERSION=inst.version)

    def _install(self, pkg: SypPackage, reason: str) -> None:
        info = pkg.info
        old = self.db.get(info.name)
        hook = "upgrade" if old else "install"
        env = {"SYP_NEW_VERSION": info.version, "SYP_OLD_VERSION": old.version if old else ""}
        verb = f"Aktualisiere {info.name} {old.version} -> {info.version}" if old else \
            f"Installiere {info.name} {info.version}"
        self.log(f"-> {verb}")
        staging = self.cfg.root / "var/cache/syp/staging" / info.name
        shutil.rmtree(staging, ignore_errors=True)
        pkg.extract(staging)
        self._script(info, f"pre_{hook}", **env)
        old_hashes = {f["path"]: f["sha256"] for f in self.db.files_of(info.name)} if old else {}
        backup = {b.lstrip("/") for b in info.backup}
        for e in pkg.files:
            dst = self._p(e["path"])
            if e["path"] in backup and dst.is_file():
                cur, new = self._hash(dst), e["sha256"]
                orig = old_hashes.get(e["path"])
                if cur == new:
                    continue
                if orig is not None and cur != orig:
                    self._place(staging / e["path"], dst.with_name(dst.name + ".sypnew"), e)
                    self.log(f"   /{e['path']} lokal geändert -> neue Version als .sypnew")
                    continue
                if orig is None:
                    shutil.copy2(dst, dst.with_name(dst.name + ".sypsave"))
            self._place(staging / e["path"], dst, e)
        if old:
            self._remove_files(info.name, {e["path"] for e in pkg.files}, old.info)
        self.db.put(info, pkg.files, old.reason if old else reason)
        shutil.rmtree(staging, ignore_errors=True)
        self._script(info, f"post_{hook}", **env)

    # --- Ablauf ------------------------------------------------------------------
    def execute(self, plan: Plan, cmdline: str) -> int | None:
        if plan.empty:
            self.log(" Nichts zu tun.")
            return None
        with locked(self.cfg):
            pkgs = []
            for rp in plan.install:
                path = repo.fetch_package(self.cfg, rp)
                if rp.local_path and rp.local_path.parent != self.cfg.cache_dir.resolve():
                    self.cfg.cache_dir.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(path, self.cfg.cache_dir / path.name)   # für Rollbacks
                pkg = SypPackage(path)
                if pkg.info.name != rp.name or pkg.info.version != rp.version:
                    raise TransactionError(f"{path.name}: Metadaten passen nicht zum Index")
                if self.cfg.require_signatures and not self.allow_unsigned:
                    pkg.verify(self.keyring)
                pkgs.append(pkg)
            pkgs = _order(pkgs)
            removing = set(plan.remove)
            self._check_conflicts(pkgs, removing)
            pre = snapshot.create(self.cfg, "pre", f"syp: {cmdline}")
            tx = self.db.tx_begin(cmdline, pre)
            try:
                for name in plan.remove:
                    self._remove(name)
                for pkg in pkgs:
                    self._install(pkg, plan.reasons.get(pkg.info.name, "explicit"))
                triggers.run(self.cfg, self.changed, self.log)
            except BaseException as e:
                self.db.tx_end(tx, "failed")
                hint = f" (Snapshot {pre}: `snapper rollback {pre}`)" if pre else ""
                raise TransactionError(f"Transaktion {tx} fehlgeschlagen: {e}. "
                                       f"Zurücksetzen mit `syp rollback {tx}`{hint}") from e
            post = snapshot.create(self.cfg, "post", f"syp: {cmdline}", pre)
            self.db.tx_end(tx, "done", post)
            return tx


def _order(pkgs: list[SypPackage]) -> list[SypPackage]:
    """Topologische Reihenfolge: Abhängigkeiten zuerst (Zyklen werden toleriert)."""
    from .deps import Dep
    by_prov = {}
    for p in pkgs:
        for n, _ in p.info.provides_items():
            by_prov.setdefault(n, p)
    out, seen = [], set()

    def visit(p, stack=()):
        if p.info.name in seen or p.info.name in stack:
            return
        for d in p.info.depends:
            for a in Dep.parse(d).alternatives:
                q = by_prov.get(a.name)
                if q is not None and q is not p:
                    visit(q, stack + (p.info.name,))
                    break
        seen.add(p.info.name)
        out.append(p)

    for p in sorted(pkgs, key=lambda p: p.info.name):
        visit(p)
    return out


def rollback_plan(cfg: Config, db: Database, tx_id: int, available: list[RepoPackage]) -> Plan:
    """Plan, der den Paketzustand *vor* Transaktion tx_id wiederherstellt."""
    tx = db.tx(tx_id)
    if tx is None:
        raise TransactionError(f"Transaktion {tx_id} unbekannt")
    target: dict[str, str] = tx["before"]
    current = db.installed()
    plan = Plan(remove=sorted(set(current) - set(target)))
    missing = []
    for name, ver in sorted(target.items()):
        if name in current and current[name].version == ver:
            continue
        found = _from_cache(cfg, name, ver) or next(
            (p for p in available if p.name == name and p.version == ver), None)
        if found is None:
            missing.append(f"{name} {ver}")
        else:
            plan.install.append(found)
            plan.reasons[name] = current[name].reason if name in current else "dep"
    if missing:
        raise TransactionError("Für den Rollback fehlen Pakete (weder im Cache noch im Repo): "
                               + ", ".join(missing) + ". Alternative: `syp rollback --system`.")
    return plan


def _from_cache(cfg: Config, name: str, ver: str) -> RepoPackage | None:
    if not cfg.cache_dir.is_dir():
        return None
    for f in cfg.cache_dir.glob(f"{name}-*.syp"):
        try:
            p = local_package(f)
        except Exception:
            continue
        if p.name == name and p.version == ver:
            return p
    return None


def clean_cache(cfg: Config, db: Database, keep: int) -> int:
    """Behält je Paket die `keep` neuesten Versionen (für Rollbacks)."""
    from .version import Version
    groups: dict[str, list] = {}
    for f in cfg.cache_dir.glob("*.syp") if cfg.cache_dir.is_dir() else []:
        try:
            p = local_package(f)
        except Exception:
            f.unlink()
            continue
        groups.setdefault(p.name, []).append(p)
    removed = 0
    installed = db.state()
    for name, ps in groups.items():
        ps.sort(key=lambda p: Version.parse(p.version), reverse=True)
        for p in ps[keep:]:
            if installed.get(name) != p.version:
                p.local_path.unlink()
                removed += 1
    return removed
