#!/usr/bin/env python3
"""Lizenzbericht aller Pakete (installiertes System oder Repo-Index) als Markdown.

    tools/license-report.py                 # installiertes System (/)
    tools/license-report.py --root /mnt     # anderes System
    tools/license-report.py --apps-only     # nur Anwendungen mit Desktop-Kategorie
Nicht als frei bekannte Lizenzen werden markiert (Exit-Code 1), damit die CI sie auffängt.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys

SYP = os.environ.get("SYP_BIN", "syp")
FREE = re.compile(r"^(L?GPL|AGPL|MPL|MIT|BSD|Apache|ISC|Zlib|CC0|CC-BY|OFL|Unlicense|PSF|Python|"
                  r"Artistic|BSL-1.0|CECILL|EPL|ImageMagick|FTL|curl|X11|0BSD|WTFPL|Vim|Ruby|PHP|"
                  r"LicenseRef-(Arch|custom)|custom)", re.I)


def is_free(expr: str) -> bool:
    parts = [p.strip("() ") for p in re.split(r"\s+(?:AND|OR|WITH)\s+|,", expr) if p.strip("() ")]
    return bool(parts) and all(FREE.match(p) or p.endswith("-exception") for p in parts)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="/")
    ap.add_argument("--repo", action="store_true", help="Repo-Index statt installierter Pakete")
    ap.add_argument("--apps-only", action="store_true")
    a = ap.parse_args()
    cmd = [SYP, "-r", a.root] + (["search", "--json"] if a.repo else ["list", "--json"])
    rows = json.loads(subprocess.run(cmd, capture_output=True, text=True, check=True).stdout)
    print("| Paket | Version | Lizenz | Herkunft | frei |\n|---|---|---|---|---|")
    bad = 0
    for i in sorted(rows, key=lambda r: r["name"]):
        if a.apps_only and not i.get("categories"):
            continue
        origin = i["origin"] if isinstance(i.get("origin"), str) else i.get("origin", {}).get("format", "?")
        free = is_free(i.get("license", ""))
        bad += not free
        print(f"| {i['name']} | {i['version']} | {i.get('license', '')} | {origin} | {'✓' if free else '⚠'} |")
    print(f"\n{len(rows)} Pakete, {bad} mit unklarer/unfreier Lizenz", file=sys.stderr)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
