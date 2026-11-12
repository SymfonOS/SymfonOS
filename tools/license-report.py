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
# Freie Lizenzen (SPDX-Kennungen, Präfixe). »custom« ohne Angabe ist unklar und wird gemeldet.
FREE = re.compile(r"^(L?GPL|AGPL|MPL|MIT|BSD|Apache|ISC|Zlib|CC0|CC-BY|CC-PDDC|OFL|Unlicense|PSF|Python|"
                  r"Artistic|BSL|CECILL|EPL|ImageMagick|FTL|curl|X11|0BSD|WTFPL|Vim|Ruby|PHP|"
                  r"AFL|Bitstream-Vera|HPND|Unicode|IJG|libpng|libtiff|OLDAP|SGI-B|CDDL|bzip2|"
                  r"NTP|OpenSSL|SSLeay|PostgreSQL|Sendmail|TCL|UCD|W3C|Xfig|blessing|"
                  r"LicenseRef-Public-Domain|Public-Domain|LicenseRef-Arch)([-.].*)?$", re.I)
# Nicht frei, aber weitergebbar und für Hardware nötig: nur ausdrücklich zugelassen
REDISTRIBUTABLE = {"LicenseRef-linux-firmware"}


def _tokens(expr: str) -> list[str]:
    return re.findall(r"\(|\)|[^\s()]+", expr.replace(",", " AND "))


def _eval(tok: list[str], allowed) -> bool:
    """SPDX-Ausdruck auswerten: OR = eine Alternative genügt, AND = alle, »X WITH Ausnahme« zählt wie X."""
    def atom() -> bool:
        t = tok.pop(0) if tok else ""
        if t == "(":
            v = expr()
            if tok and tok[0] == ")":
                tok.pop(0)
            return v
        v = allowed(t)
        if tok and tok[0].upper() == "WITH":
            tok.pop(0)
            if tok:
                tok.pop(0)
        return v

    def conj() -> bool:
        v = atom()
        while tok and tok[0].upper() == "AND":
            tok.pop(0)
            v = atom() and v
        return v

    def expr() -> bool:
        v = conj()
        while tok and tok[0].upper() == "OR":
            tok.pop(0)
            v = conj() or v
        return v

    return bool(tok) and expr()


def is_free(expr: str, redistributable: bool = False) -> bool:
    def ok(t: str) -> bool:
        t = t.rstrip("+")
        return bool(FREE.match(t)) or (redistributable and t in REDISTRIBUTABLE)
    return _eval(_tokens(expr or ""), ok)


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
        lic = i.get("license", "")
        free = is_free(lic)
        redis = not free and is_free(lic, redistributable=True)
        bad += not (free or redis)
        mark = "✓" if free else "weitergebbar" if redis else "⚠"
        print(f"| {i['name']} | {i['version']} | {lic} | {origin} | {mark} |")
    print(f"\n{len(rows)} Pakete, {bad} mit unklarer/unfreier Lizenz", file=sys.stderr)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
