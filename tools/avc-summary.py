#!/usr/bin/env python3
"""Fasst SELinux-Verweigerungen (AVC) aus einem Konsolen- oder Journal-Log zusammen.

    tools/avc-summary.py serial.log            # eine Zeile je Quelle/Ziel/Klasse mit allen Rechten
    tools/avc-summary.py --allow serial.log    # dieselben Fälle als allow-Regeln (Ausgangspunkt für ein Modul)

Im Modus permissive meldet der Kernel jede Verweigerung nur einmal je Regel; der Desktop-Test bootet dafür ohne
auditd und ohne printk-Ratenlimit, damit alle Meldungen auf der seriellen Konsole landen. Erfasst werden auch
Meldungen von Objektverwaltern im Userspace (dbus-daemon, systemd).
"""
import collections
import re
import sys

AVC = re.compile(r"avc:\s+denied\s+\{ ([^}]+) \} for (.*?)scontext=(\S+) tcontext=(\S+) tclass=(\S+)")
FIELD = re.compile(r'\b(comm|name|path|service)="?([^" ]+)')


def typ(ctx):
    parts = ctx.split(":")
    return parts[2] if len(parts) > 2 else ctx


def parse(text):
    seen = collections.OrderedDict()
    for m in AVC.finditer(text):
        perms, rest, s, t, cls = m.groups()
        key = (typ(s), typ(t), cls)
        e = seen.setdefault(key, {"perms": set(), "hints": collections.OrderedDict()})
        e["perms"].update(perms.split())
        for k, v in FIELD.findall(rest):
            e["hints"].setdefault(f"{k}={v}", None)
    return seen


def main(argv):
    allow = "--allow" in argv
    files = [a for a in argv if not a.startswith("--")]
    text = "".join(open(f, errors="replace").read() for f in files) if files else sys.stdin.read()
    # Zeilenumbrüche, die die Konsole mitten in Meldungen einfügt, und Farbcodes entfernen
    text = re.sub(r"\x1b\[[0-9;]*m", "", text)
    seen = parse(text)
    for (s, t, cls), e in seen.items():
        perms = " ".join(sorted(e["perms"]))
        if allow:
            print(f"allow {s} {t}:{cls} {{ {perms} }};")
        else:
            hints = " ".join(list(e["hints"])[:3])
            print(f"{s} -> {t} : {cls} {{ {perms} }}  {hints}")
    print(f"{len(seen)} verschiedene Verweigerungen", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
