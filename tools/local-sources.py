#!/usr/bin/env python3
"""Rezepte mit Quellen aus diesem Repository (path = "@/…"): Inhalt und Version festhalten.

syp buildall baut ein Paket nur neu, wenn sich seine Version ändert. Ändert sich die Quelle im Repository
(Installer, Desktop-Voreinstellungen, syp selbst) ohne neue Version, bleibt das alte Paket im Pool und landet
unbemerkt im ISO.

    tools/local-sources.py           # distro/recipes/local-sources.lock schreiben
    tools/local-sources.py --check   # Quelle geändert, Version gleich? (Test in der CI)
"""
import hashlib
import os
import subprocess
import sys
import tomllib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RECIPES = os.path.join(ROOT, "distro", "recipes")
LOCK = os.path.join(RECIPES, "local-sources.lock")


def tree_hash(paths):
    """Nur von git verfolgte Dateien: Bauergebnisse (csrc/bin, *.o) dürfen das Ergebnis nicht ändern."""
    out = subprocess.run(["git", "-C", ROOT, "ls-files", "-z", "--", *paths], capture_output=True, check=True)
    h = hashlib.sha256()
    for rel in sorted(f for f in out.stdout.decode().split("\0") if f):
        f = os.path.join(ROOT, rel)
        if os.path.islink(f) or not os.path.isfile(f):
            continue
        h.update(rel.encode() + b"\0")
        with open(f, "rb") as fh:
            h.update(hashlib.sha256(fh.read()).digest())
    return h.hexdigest()


def current():
    out = {}
    for name in sorted(os.listdir(RECIPES)):
        p = os.path.join(RECIPES, name, "SYPBUILD.toml")
        if not os.path.isfile(p):
            continue
        with open(p, "rb") as fh:
            r = tomllib.load(fh)
        paths = [s["path"][2:] for s in r.get("source", []) if str(s.get("path", "")).startswith("@/")]
        if paths:
            out[r["package"]["name"]] = (r["package"]["version"], tree_hash(paths))
    return out


def read_lock():
    lock = {}
    if os.path.exists(LOCK):
        for line in open(LOCK):
            if line.strip() and not line.startswith("#"):
                name, version, digest = line.split()
                lock[name] = (version, digest)
    return lock


def check():
    lock, now, errors = read_lock(), current(), []
    for name, (version, digest) in now.items():
        old = lock.get(name)
        if old is None:
            errors.append(f"{name}: fehlt in local-sources.lock (tools/local-sources.py ausführen)")
        elif old[0] == version and old[1] != digest:
            errors.append(f"{name}: Quelle geändert, Version {version} gleich – Paketversion erhöhen, "
                          "dann tools/local-sources.py ausführen")
        elif old != (version, digest):
            errors.append(f"{name}: local-sources.lock veraltet (tools/local-sources.py ausführen)")
    errors += [f"{name}: in local-sources.lock, aber kein Rezept mit @/-Quelle" for name in lock if name not in now]
    return errors


def main():
    if "--check" in sys.argv[1:]:
        errors = check()
        for e in errors:
            print(e)
        return 1 if errors else 0
    with open(LOCK, "w") as fh:
        fh.write("# Rezept Version sha256 der @/-Quellen – erzeugt von tools/local-sources.py\n")
        for name, (version, digest) in current().items():
            fh.write(f"{name} {version} {digest}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
