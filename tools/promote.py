#!/usr/bin/env python3
"""Übernimmt Pakete aus dem Kanal »testing« nach »stable« (vergleichbar mit Fedoras Bodhi).

Ein Paket wird übernommen, wenn
  * es mindestens --min-days Tage in testing liegt (Sicherheitsupdates: --security-days, Standard 0),
  * es nicht gesperrt ist (Datei testing/hold: je Zeile »name« oder »name version«, # Kommentar),
  * alle Abhängigkeiten in stable plus den ebenfalls übernommenen Paketen erfüllbar sind
    (sonst würde stable brechen; das Paket wartet dann auf seine Abhängigkeiten) und
  * stable keine gleiche oder neuere Version hat.

Seit wann ein Paket in testing liegt, steht in testing/promotion.json (vom Werkzeug gepflegt).
Der Index von testing wird nur mit gültiger Signatur (--pub) gelesen; stable wird mit -k neu signiert.

  tools/promote.py --testing out/repo/testing --stable out/repo/stable --pub release.pub -k release.key
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

SYP = os.environ.get("SYP_BIN", "syp")
DEP = re.compile(r"^\s*([^<>=\s]+)\s*(>=|<=|=|<|>)?\s*(\S+)?\s*$")


def syp(*args: str) -> str:
    r = subprocess.run([SYP, *map(str, args)], capture_output=True, text=True)
    if r.returncode:
        sys.exit(f"promote: syp {' '.join(map(str, args[:2]))}: {r.stderr.strip()}")
    return r.stdout


_vc: dict[tuple[str, str], int] = {}


def vercmp(a: str, b: str) -> int:
    if (a, b) not in _vc:
        _vc[(a, b)] = int(syp("vercmp", a, b).strip())
    return _vc[(a, b)]


def load(repo: Path, pub: str | None) -> list[dict]:
    if not (repo / "index.json.zst").exists():
        return []
    args = ["repo-show", repo] + (["--pub", pub] if pub else [])
    return json.loads(syp(*args)).get("packages", [])


class Universe:
    """Was nach der Übernahme in stable stünde: je Paketname die neueste Version + provides."""

    def __init__(self, entries: list[dict]):
        self.pkgs: dict[str, dict] = {}
        for e in entries:
            self.add(e["info"])

    def add(self, info: dict) -> None:
        cur = self.pkgs.get(info["name"])
        if cur is None or vercmp(info["version"], cur["version"]) > 0:
            self.pkgs[info["name"]] = info

    def satisfies(self, dep: str) -> bool:
        return any(self._one(alt) for alt in dep.split("|"))

    def _one(self, dep: str) -> bool:
        m = DEP.match(dep)
        if not m:
            return False
        name, op, ver = m.groups()
        cands = []
        for p in self.pkgs.values():
            if p["name"] == name:
                cands.append(p["version"])
            for prov in p.get("provides", []):
                pm = DEP.match(prov)
                if pm and pm.group(1) == name:
                    cands.append(pm.group(3) or p["version"])
        if not op:
            return bool(cands)
        for v in cands:
            c = vercmp(v, ver)
            if {"=": c == 0, ">=": c >= 0, "<=": c <= 0, ">": c > 0, "<": c < 0}[op]:
                return True
        return False


def read_hold(path: Path) -> set[tuple[str, str | None]]:
    held = set()
    if path.exists():
        for line in path.read_text().splitlines():
            parts = line.split("#", 1)[0].split()
            if parts:
                held.add((parts[0], parts[1] if len(parts) > 1 else None))
    return held


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--testing", type=Path, required=True)
    ap.add_argument("--stable", type=Path, required=True)
    ap.add_argument("--pub", help="öffentlicher Schlüssel für den testing-Index (Pflicht außer mit --insecure)")
    ap.add_argument("--insecure", action="store_true", help="testing-Index ohne Signaturprüfung lesen (nur Tests)")
    ap.add_argument("-k", "--key", help="Signaturschlüssel für den stable-Index")
    ap.add_argument("--name", default="symfonos")
    ap.add_argument("--arch", default="x86_64")
    ap.add_argument("--min-days", type=float, default=7)
    ap.add_argument("--security-days", type=float, default=0)
    ap.add_argument("--now", type=float, default=None, help=argparse.SUPPRESS)
    ap.add_argument("-n", "--dry-run", action="store_true")
    a = ap.parse_args()
    if not a.pub and not a.insecure:
        ap.error("--pub fehlt (oder --insecure)")
    now = a.now if a.now is not None else time.time()

    testing = load(a.testing, a.pub)
    stable = load(a.stable, None)
    state_file = a.testing / "promotion.json"
    seen = json.loads(state_file.read_text()) if state_file.exists() else {}
    for e in testing:
        seen.setdefault(e["file"], now)
    seen = {f: t for f, t in seen.items() if any(e["file"] == f for e in testing)}
    if not a.dry_run:
        state_file.write_text(json.dumps(seen, indent=1, sort_keys=True) + "\n")

    held = read_hold(a.testing / "hold")
    have = {e["info"]["name"]: e["info"]["version"] for e in stable}
    reasons: dict[str, str] = {}
    cands = []
    for e in testing:
        i = e["info"]
        tag = f"{i['name']} {i['version']}"
        days = (now - seen[e["file"]]) / 86400
        need = a.security_days if i.get("security") else a.min_days
        if i["name"] in have and vercmp(i["version"], have[i["name"]]) <= 0:
            continue
        if (i["name"], None) in held or (i["name"], i["version"]) in held:
            reasons[tag] = "gesperrt (hold)"
        elif days < need:
            reasons[tag] = f"erst {days:.1f} von {need:g} Tagen in testing"
        else:
            cands.append(e)
    # Je Name nur die neueste Version
    best: dict[str, dict] = {}
    for e in cands:
        n = e["info"]["name"]
        if n not in best or vercmp(e["info"]["version"], best[n]["info"]["version"]) > 0:
            best[n] = e
    cands = list(best.values())
    # Fixpunkt: Kandidaten mit unerfüllbaren Abhängigkeiten zurückstellen
    while True:
        uni = Universe(stable)
        for e in cands:
            uni.add(e["info"])
        broken = [(e, d) for e in cands for d in e["info"].get("depends", []) if not uni.satisfies(d)]
        if not broken:
            break
        drop = {id(e) for e, _ in broken}
        for e, d in broken:
            reasons[f"{e['info']['name']} {e['info']['version']}"] = f"Abhängigkeit »{d}« in stable nicht erfüllbar"
        cands = [e for e in cands if id(e) not in drop]

    order = sorted(cands, key=lambda e: e["info"]["name"])
    for e in order:
        print(f"  → stable: {e['info']['name']} {e['info']['version']}{'  [Sicherheit]' if e['info'].get('security') else ''}")
    for tag, why in sorted(reasons.items()):
        print(f"  ⏸ {tag}: {why}")
    if order and not a.dry_run:
        files = [a.testing / e["file"] for e in order]
        args = ["repo-add", a.stable, *files, "--name", a.name, "--arch", a.arch] + (["-k", a.key] if a.key else [])
        syp(*args)
    print(f":: {len(order)} übernommen, {len(reasons)} zurückgestellt{' (Trockenlauf)' if a.dry_run else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
