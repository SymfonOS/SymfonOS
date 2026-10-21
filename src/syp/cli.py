"""syp – der symfonOS-Paketmanager (CLI)."""
from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
import time
from pathlib import Path

from . import __version__, pacman_import, repo, snapshot
from .build import build
from .config import Config
from .crypto import Keyring, SecretKey, SignatureError
from .db import Database
from .package import SypPackage, PackageError
from .repo import RepoError
from .solver import Plan, Solver, SolverError
from .transaction import (Transaction, TransactionError, clean_cache, local_package,
                          rollback_plan)
from .version import Version

TTY = sys.stdout.isatty()


def c(code: str, s: str) -> str:
    return f"\033[{code}m{s}\033[0m" if TTY else s


def bold(s):
    return c("1", s)


def err(msg: str) -> None:
    print(c("31;1", "Fehler: ") + msg, file=sys.stderr)


class Ctx:
    def __init__(self, a):
        self.args = a
        self.cfg = Config.load(a.root)
        self.db = Database(self.cfg.db_path)
        self.keyring = Keyring.from_dir(self.cfg.keyring_dir)

    def available(self):
        return repo.load_synced(self.cfg)

    def tx(self) -> Transaction:
        a = self.args
        return Transaction(self.cfg, self.db, self.keyring,
                           overwrite=tuple(getattr(a, "overwrite", []) or []),
                           allow_unsigned=getattr(a, "allow_unsigned", False),
                           trust_scripts=getattr(a, "trust_scripts", False),
                           no_scripts=getattr(a, "no_scripts", False))

    def need_root(self):
        if self.cfg.root == Path("/") and os.geteuid() != 0:
            raise TransactionError("Diese Aktion benötigt Root-Rechte (sudo / pkexec)")


def show_plan(plan: Plan, installed) -> None:
    if plan.remove:
        print(bold(f"Zu entfernen ({len(plan.remove)}):"), " ".join(plan.remove))
    if plan.install:
        print(bold(f"Zu installieren/aktualisieren ({len(plan.install)}):"))
        total = 0
        for p in sorted(plan.install, key=lambda p: p.name):
            old = installed.get(p.name)
            arrow = f"{old.version} -> {p.version}" if old else p.version
            print(f"  {p.repo}/{p.name}  {arrow}")
            total += p.size if not p.local_path else 0
        if total:
            print(f"Download: {total / 1e6:.1f} MB")


def confirm(a, q: str = "Fortfahren?") -> bool:
    if a.yes:
        return True
    try:
        return input(f"{q} [J/n] ").strip().lower() in ("", "j", "ja", "y", "yes")
    except EOFError:
        return False


def run_plan(ctx: Ctx, plan: Plan) -> int:
    if plan.empty:
        print(" Nichts zu tun.")
        return 0
    show_plan(plan, ctx.db.installed())
    if ctx.args.dry_run or not confirm(ctx.args):
        return 0
    ctx.need_root()
    tx = ctx.tx().execute(plan, "syp " + shlex.join(ctx.args.argv))
    print(c("32;1", f":: Transaktion {tx} abgeschlossen"))
    return 0


# --- Befehle -----------------------------------------------------------------------
def cmd_sync(ctx: Ctx) -> int:
    ctx.need_root()
    repo.sync(ctx.cfg, ctx.keyring)
    print(f":: {len(ctx.available())} Pakete in {len(ctx.cfg.repos)} Repositories")
    return 0


def cmd_install(ctx: Ctx) -> int:
    local, reqs = [], []
    for t in ctx.args.targets:
        if t.endswith(".syp") and Path(t).exists():
            local.append(local_package(Path(t)))
        else:
            reqs.append(t)
    s = Solver(ctx.available(), ctx.db.installed())
    plan = s.install(reqs, local)
    if ctx.args.as_dep:
        plan.reasons = {k: "dep" for k in plan.reasons}
    return run_plan(ctx, plan)


def cmd_remove(ctx: Ctx) -> int:
    s = Solver([], ctx.db.installed())
    plan = s.remove(ctx.args.targets, ctx.args.cascade)
    return run_plan(ctx, plan)


def cmd_upgrade(ctx: Ctx) -> int:
    if not ctx.args.no_sync:
        cmd_sync(ctx)
    plan = Solver(ctx.available(), ctx.db.installed()).upgrade()
    return run_plan(ctx, plan)


def cmd_autoremove(ctx: Ctx) -> int:
    orphans = Solver([], ctx.db.installed()).orphans()
    return run_plan(ctx, Plan(remove=orphans))


def _pkg_json(info, installed=None, repo_name=None) -> dict:
    d = {"name": info.name, "version": info.version, "summary": info.summary,
         "license": info.license, "categories": info.categories, "url": info.url,
         "origin": info.origin.get("format")}
    if repo_name:
        d["repo"] = repo_name
    d["installed"] = installed.version if installed else None
    return d


def cmd_search(ctx: Ctx) -> int:
    q = [t.lower() for t in ctx.args.terms]
    inst = ctx.db.installed()
    newest = {}
    for p in ctx.available():
        if p.name not in newest or Version.parse(p.version) > Version.parse(newest[p.name].version):
            newest[p.name] = p
    hits = [p for p in newest.values()
            if all(t in (p.name + " " + p.info.summary + " " + " ".join(p.info.categories)).lower()
                   for t in q)]
    hits.sort(key=lambda p: p.name)
    if ctx.args.json:
        print(json.dumps([_pkg_json(p.info, inst.get(p.name), p.repo) for p in hits],
                         ensure_ascii=False))
        return 0
    for p in hits:
        tag = c("36", " [installiert]") if p.name in inst else ""
        print(f"{c('35;1', p.repo)}/{bold(p.name)} {c('32', p.version)}{tag}\n    {p.info.summary}")
    return 0


def cmd_info(ctx: Ctx) -> int:
    inst = ctx.db.installed()
    avail = ctx.available()
    rc = 0
    for name in ctx.args.targets:
        if name.endswith(".syp") and Path(name).exists():
            info, where = SypPackage(Path(name)).info, "Datei"
        elif name in inst:
            info, where = inst[name].info, "installiert"
        else:
            cands = [p for p in avail if p.name == name]
            if not cands:
                err(f"Paket nicht gefunden: {name}")
                rc = 1
                continue
            best = max(cands, key=lambda p: Version.parse(p.version))
            info, where = best.info, best.repo
        if ctx.args.json:
            print(json.dumps(info.to_dict(), ensure_ascii=False, indent=2))
            continue
        rows = [("Name", info.name), ("Version", info.version), ("Quelle", where),
                ("Beschreibung", info.summary), ("Lizenz", info.license), ("URL", info.url),
                ("Architektur", info.arch), ("Hängt ab von", ", ".join(info.depends) or "–"),
                ("Optional", ", ".join(info.optdepends) or "–"),
                ("Stellt bereit", ", ".join(info.provides) or "–"),
                ("Konflikte", ", ".join(info.conflicts) or "–"),
                ("Größe", f"{info.installed_size / 1e6:.1f} MB"),
                ("Herkunft", json.dumps(info.origin, ensure_ascii=False)),
                ("Gebaut", time.strftime("%Y-%m-%d %H:%M", time.localtime(info.build_date)))]
        if name in inst:
            rows.append(("Grund", "explizit" if inst[name].reason == "explicit" else "Abhängigkeit"))
        for k, v in rows:
            print(f"{bold(k.ljust(14))}: {v}")
        print()
    return rc


def cmd_list(ctx: Ctx) -> int:
    inst = ctx.db.installed()
    for n in sorted(inst):
        p = inst[n]
        if ctx.args.explicit and p.reason != "explicit":
            continue
        print(f"{n} {p.version}")
    return 0


def cmd_files(ctx: Ctx) -> int:
    for f in ctx.db.files_of(ctx.args.target):
        print("/" + f["path"])
    return 0


def cmd_owns(ctx: Ctx) -> int:
    rc = 0
    for path in ctx.args.paths:
        rel = os.path.abspath(path).lstrip("/")
        owners = ctx.db.owners(rel)
        if owners:
            print(f"/{rel} gehört zu {', '.join(owners)}")
        else:
            print(f"/{rel} gehört zu keinem Paket")
            rc = 1
    return rc


def cmd_verify(ctx: Ctx) -> int:
    bad = 0
    names = ctx.args.targets or sorted(ctx.db.installed())
    backups = {}
    for n, p in ctx.db.installed().items():
        backups[n] = {b.lstrip("/") for b in p.info.backup}
    for n in names:
        for f in ctx.db.files_of(n):
            p = ctx.cfg.root / f["path"]
            if not (p.exists() or p.is_symlink()):
                print(f"{n}: fehlt /{f['path']}")
                bad += 1
            elif f["type"] == "file" and f["path"] not in backups.get(n, ()):
                if Transaction._hash(p) != f["sha256"]:
                    print(f"{n}: verändert /{f['path']}")
                    bad += 1
    print(f":: {bad} Abweichungen" if bad else ":: Alle Dateien intakt")
    return 1 if bad else 0


def cmd_history(ctx: Ctx) -> int:
    for t in reversed(ctx.db.history(ctx.args.limit)):
        before, after = t["before"], t["after"] or {}
        added = sorted(set(after) - set(before))
        removed = sorted(set(before) - set(after))
        changed = sorted(n for n in set(before) & set(after) if before[n] != after[n])
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(t["ts"]))
        snap = f" snapshot={t['snapshot_pre']}" if t["snapshot_pre"] else ""
        print(f"{bold(str(t['id']).rjust(4))}  {when}  {t['status']:<7} {t['cmdline']}{snap}")
        for label, items in (("+", added), ("-", removed), ("~", changed)):
            if items:
                print(f"        {label} {' '.join(items[:12])}{' …' if len(items) > 12 else ''}")
    return 0


def cmd_rollback(ctx: Ctx) -> int:
    a = ctx.args
    if a.system:
        if not snapshot.available(ctx.cfg):
            raise TransactionError("Keine Btrfs/snapper-Snapshots verfügbar")
        tx = ctx.db.tx(a.tx) if a.tx else next(
            (t for t in ctx.db.history() if t["snapshot_pre"]), None)
        if not tx or not tx["snapshot_pre"]:
            raise TransactionError("Kein Snapshot zu dieser Transaktion")
        print(f"System wird auf Snapshot {tx['snapshot_pre']} zurückgesetzt (Neustart nötig).")
        if confirm(a):
            ctx.need_root()
            os.execvp("snapper", ["snapper", "--ambit", "classic", "rollback", tx["snapshot_pre"]])
        return 0
    tx_id = a.tx or next((t["id"] for t in ctx.db.history() if t["status"] == "done"), None)
    if tx_id is None:
        raise TransactionError("Keine Transaktion zum Zurücksetzen")
    print(f":: Stelle Zustand vor Transaktion {tx_id} wieder her")
    return run_plan(ctx, rollback_plan(ctx.cfg, ctx.db, tx_id, ctx.available()))


def cmd_clean(ctx: Ctx) -> int:
    ctx.need_root()
    n = clean_cache(ctx.cfg, ctx.db, ctx.args.keep)
    print(f":: {n} alte Pakete aus dem Cache entfernt")
    return 0


def cmd_build(ctx: Ctx) -> int:
    signer = SecretKey.load(Path(ctx.args.key)) if ctx.args.key else None
    out = build(Path(ctx.args.recipe), Path(ctx.args.out), signer, run_check=not ctx.args.nocheck)
    print(c("32;1", f":: Gebaut: {out}") + ("" if signer else c("33", " (unsigniert)")))
    return 0


def cmd_keygen(ctx: Ctx) -> int:
    k = SecretKey.generate(ctx.args.comment)
    out = Path(ctx.args.out)
    k.save(out.with_suffix(".key"), out.with_suffix(".pub"))
    print(f":: Schlüssel {k.id}: {out.with_suffix('.key')} (geheim), {out.with_suffix('.pub')}")
    return 0


def cmd_repo_add(ctx: Ctx) -> int:
    rdir = Path(ctx.args.repo)
    signer = SecretKey.load(Path(ctx.args.key)) if ctx.args.key else None
    for p in ctx.args.packages:
        print(f"  + {repo.add_to_pool(rdir, Path(p)).relative_to(rdir)}")
    idx = repo.build_index(rdir, ctx.args.name, ctx.args.arch, signer)
    print(f":: Index geschrieben: {idx}" + ("" if signer else " (UNSIGNIERT)"))
    return 0


def cmd_import_pacman(ctx: Ctx) -> int:
    pacman_import.import_db(ctx.cfg.root, ctx.db)
    if ctx.args.retire:
        pacman_import.remove_pacman_state(ctx.cfg.root)
    return 0


def cmd_doctor(ctx: Ctx) -> int:
    problems = 0
    for t in ctx.db.history(5):
        if t["status"] in ("running", "failed"):
            print(f"! Transaktion {t['id']} ist {t['status']} – `syp rollback {t['id']}` empfohlen")
            problems += 1
    plan_broken = Solver([], ctx.db.installed())._broken(ctx.db.installed())
    for d, who in plan_broken:
        print(f"! {who}: Abhängigkeit »{d}« nicht erfüllt")
        problems += 1
    if not ctx.keyring.keys:
        print(f"! Keine vertrauenswürdigen Schlüssel in {ctx.cfg.keyring_dir}")
        problems += 1
    for rc in ctx.cfg.repos:
        f = ctx.cfg.sync_dir / f"{rc.name}.index.json.zst"
        if not f.exists():
            print(f"! Repo {rc.name} nie synchronisiert – `syp sync`")
            problems += 1
        elif time.time() - f.stat().st_mtime > 14 * 86400:
            print(f"! Repo {rc.name} seit über 14 Tagen nicht synchronisiert")
            problems += 1
    for p in ctx.cfg.root.glob("etc/**/*.sypnew"):
        print(f"! Neue Konfiguration wartet auf Abgleich: /{p.relative_to(ctx.cfg.root)}")
    print(":: Keine Probleme gefunden" if not problems else f":: {problems} Problem(e)")
    return 1 if problems else 0


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="syp", description="symfonOS-Paketmanager")
    ap.add_argument("--version", action="version", version=f"syp {__version__}")
    ap.add_argument("-r", "--root", default=os.environ.get("SYP_ROOT", "/"),
                    help="Alternatives Wurzelverzeichnis")
    ap.add_argument("-y", "--yes", action="store_true", help="Nicht nachfragen")
    ap.add_argument("-n", "--dry-run", action="store_true", help="Nur Plan anzeigen")
    sp = ap.add_subparsers(dest="cmd", required=True)

    def add(name, fn, help, aliases=()):
        p = sp.add_parser(name, help=help, aliases=list(aliases))
        p.set_defaults(fn=fn)
        return p

    def tx_opts(p):
        p.add_argument("--overwrite", action="append", metavar="GLOB",
                       help="Unverwaltete Dateien überschreiben (z. B. '/usr/lib/foo*')")
        p.add_argument("--allow-unsigned", action="store_true")
        p.add_argument("--trust-scripts", action="store_true",
                       help="Skripte konvertierter Pakete ausführen")
        p.add_argument("--no-scripts", action="store_true")

    add("sync", cmd_sync, "Repository-Indizes aktualisieren")
    p = add("install", cmd_install, "Pakete installieren", ["in"])
    p.add_argument("targets", nargs="+")
    p.add_argument("--as-dep", action="store_true")
    tx_opts(p)
    p = add("remove", cmd_remove, "Pakete entfernen", ["rm"])
    p.add_argument("targets", nargs="+")
    p.add_argument("--cascade", action="store_true", help="Abhängige Pakete mitentfernen")
    tx_opts(p)
    p = add("upgrade", cmd_upgrade, "System aktualisieren", ["up"])
    p.add_argument("--no-sync", action="store_true")
    tx_opts(p)
    p = add("autoremove", cmd_autoremove, "Verwaiste Abhängigkeiten entfernen")
    tx_opts(p)
    p = add("search", cmd_search, "Pakete suchen", ["s"])
    p.add_argument("terms", nargs="*")
    p.add_argument("--json", action="store_true")
    p = add("info", cmd_info, "Paketdetails")
    p.add_argument("targets", nargs="+")
    p.add_argument("--json", action="store_true")
    p = add("list", cmd_list, "Installierte Pakete", ["ls"])
    p.add_argument("--explicit", action="store_true")
    p = add("files", cmd_files, "Dateien eines Pakets")
    p.add_argument("target")
    p = add("owns", cmd_owns, "Zu welchem Paket gehört eine Datei?")
    p.add_argument("paths", nargs="+")
    p = add("verify", cmd_verify, "Installierte Dateien gegen Prüfsummen prüfen")
    p.add_argument("targets", nargs="*")
    p = add("history", cmd_history, "Transaktionshistorie")
    p.add_argument("--limit", type=int, default=30)
    p = add("rollback", cmd_rollback, "Transaktion zurücknehmen")
    p.add_argument("tx", type=int, nargs="?")
    p.add_argument("--system", action="store_true", help="Btrfs-Snapshot statt Paketebene")
    tx_opts(p)
    p = add("clean", cmd_clean, "Paket-Cache aufräumen")
    p.add_argument("--keep", type=int, default=3)
    p = add("build", cmd_build, "Paket aus SYPBUILD.toml bauen")
    p.add_argument("recipe", nargs="?", default="SYPBUILD.toml")
    p.add_argument("-o", "--out", default=".")
    p.add_argument("-k", "--key", help="Geheimer Signaturschlüssel")
    p.add_argument("--nocheck", action="store_true")
    p = add("keygen", cmd_keygen, "Signaturschlüssel erzeugen")
    p.add_argument("out")
    p.add_argument("--comment", default="")
    p = add("repo-add", cmd_repo_add, "Pakete ins Repo aufnehmen und Index signieren")
    p.add_argument("repo")
    p.add_argument("packages", nargs="*")
    p.add_argument("--name", default="symfonos")
    p.add_argument("--arch", default="x86_64")
    p.add_argument("-k", "--key")
    p = add("import-pacman", cmd_import_pacman, "pacman-Datenbank übernehmen (Installer)")
    p.add_argument("--retire", action="store_true")
    add("doctor", cmd_doctor, "Systemzustand prüfen")
    return ap


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    a = parser().parse_args(argv)
    a.argv = argv
    try:
        return a.fn(Ctx(a))
    except (SolverError, TransactionError, PackageError, SignatureError, RepoError) as e:
        err(str(e))
        return 1
    except KeyboardInterrupt:
        err("Abgebrochen")
        return 130


if __name__ == "__main__":
    sys.exit(main())
