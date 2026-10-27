#!/usr/bin/env python3
"""Findet ELF-Dateien in einer Systemwurzel, deren benötigte Bibliotheken (DT_NEEDED) dort fehlen.

    tools/check-libs.py WURZEL

Ausgabe: je fehlender Bibliothek eine Zeile mit den ersten Nutzern; Exit 1, wenn etwas fehlt.
Fehlende Laufzeitabhängigkeiten eines Pakets (z. B. libcanberra ohne libtool → libltdl.so.7)
fallen sonst erst auf, wenn ein Programm startet.
"""
import collections
import os
import subprocess
import sys

# Plugins, die nur ein Entwicklungswerkzeug lädt (Qt Designer), und Firmware (keine Linux-ELF-Programme)
IGNORE = ("usr/lib/qt6/plugins/designer/", "usr/lib/firmware/")


def libdirs(root):
    dirs = ["usr/lib"]
    conf = os.path.join(root, "etc/ld.so.conf.d")
    if os.path.isdir(conf):
        for f in sorted(os.listdir(conf)):
            with open(os.path.join(conf, f), errors="replace") as fh:
                dirs += [l.strip().lstrip("/") for l in fh if l.strip() and not l.startswith("#")]
    return dirs


def dynamic(path):
    out = subprocess.run(["readelf", "-d", path], capture_output=True, text=True).stdout
    needed, runpath = [], []
    for line in out.splitlines():
        if "(NEEDED)" in line:
            needed.append(line.split("[", 1)[1].rstrip("]"))
        elif "(RUNPATH)" in line or "(RPATH)" in line:
            runpath += line.split("[", 1)[1].rstrip("]").split(":")
    return needed, runpath


def main(root):
    have = set()
    for d in libdirs(root):
        p = os.path.join(root, d)
        if os.path.isdir(p):
            have.update(os.listdir(p))
    missing = collections.defaultdict(list)
    for top in ("usr/bin", "usr/lib"):
        for base, _, files in os.walk(os.path.join(root, top)):
            rel_base = os.path.relpath(base, root) + "/"
            if rel_base.startswith(IGNORE):
                continue
            for fn in files:
                path = os.path.join(base, fn)
                if os.path.islink(path):
                    continue
                try:
                    with open(path, "rb") as fh:
                        if fh.read(4) != b"\x7fELF":
                            continue
                except OSError:
                    continue
                needed, runpath = dynamic(path)
                for so in needed:
                    if so in have or so.startswith("ld-linux"):
                        continue
                    if so.startswith("/") and os.path.exists(os.path.join(root, so.lstrip("/"))):
                        continue
                    # private Bibliotheksordner (usr/lib/systemd): das Programm lädt die Geschwister über seinen RUNPATH
                    dirs = [base] + [r.replace("$ORIGIN", base) if "$ORIGIN" in r else os.path.join(root, r.lstrip("/"))
                                     for r in runpath]
                    if not any(os.path.exists(os.path.join(d, so)) for d in dirs):
                        missing[so].append(os.path.relpath(path, root))
    for so, users in sorted(missing.items()):
        print(f"{so} fehlt für {', '.join(users[:3])}{' …' if len(users) > 3 else ''}")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
