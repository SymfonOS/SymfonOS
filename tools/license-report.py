#!/usr/bin/env python3
"""Lizenzbericht aller Pakete (installiertes System oder Repo-Index) als Markdown.

    tools/license-report.py                 # installiertes System (/)
    tools/license-report.py --root /mnt     # anderes System
    tools/license-report.py --apps-only     # nur Anwendungen mit Desktop-Kategorie
Nicht als frei bekannte Lizenzen werden markiert (Exit-Code 1), damit die CI sie auffängt.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from syp import repo  # noqa: E402
from syp.config import Config  # noqa: E402
from syp.db import Database  # noqa: E402

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
    cfg = Config.load(a.root)
    if a.repo:
        infos = {p.name: p.info for p in repo.load_synced(cfg)}
    else:
        infos = {n: p.info for n, p in Database(cfg.db_path).installed().items()}
    print("| Paket | Version | Lizenz | Herkunft | frei |\n|---|---|---|---|---|")
    bad = 0
    for n in sorted(infos):
        i = infos[n]
        if a.apps_only and not i.categories:
            continue
        free = is_free(i.license)
        bad += not free
        print(f"| {n} | {i.version} | {i.license} | {i.origin.get('format', '?')} | {'✓' if free else '⚠'} |")
    print(f"\n{len(infos)} Pakete, {bad} mit unklarer/unfreier Lizenz", file=sys.stderr)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
