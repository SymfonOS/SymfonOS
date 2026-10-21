"""syp-convert – Kommandozeile."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from syp import repo
from syp.config import Config
from syp.crypto import SecretKey
from syp.db import Database

from . import ConvertError, Mapper, convert


def make_mapper(root: str, offline: bool, extra_map: str | None) -> Mapper:
    cfg = Config.load(root)
    available = None if offline else repo.load_synced(cfg)
    if available is not None and not available:
        available = None          # kein synchronisierter Index -> ungeprüfter Modus
    db = Database(cfg.db_path) if cfg.db_path.exists() else None
    extra = Path(extra_map) if extra_map else cfg.etc / "convert-map.toml"
    return Mapper(available, db, extra)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="syp-convert",
                                 description="Pakete aus Debian/Ubuntu, Fedora und Arch nach .syp umwandeln")
    ap.add_argument("packages", nargs="+", type=Path, help=".deb, .rpm oder .pkg.tar.zst")
    ap.add_argument("-o", "--out", type=Path, default=Path("."))
    ap.add_argument("-k", "--key", help="Signaturschlüssel (.key)")
    ap.add_argument("-r", "--root", default="/", help="System, dessen Repos/DB für das Mapping genutzt werden")
    ap.add_argument("--offline", action="store_true", help="Kein Abgleich mit Repo-Index")
    ap.add_argument("--map", help="Zusätzliche Mapping-Tabelle (TOML)")
    ap.add_argument("--license", help="Lizenz überschreiben (SPDX)")
    ap.add_argument("--name", help="Paketnamen überschreiben")
    ap.add_argument("--keep-versions", action="store_true",
                    help="Versionsbedingungen fremder Distros unverändert übernehmen")
    ap.add_argument("--mark-scripts-reviewed", action="store_true",
                    help="Skripte als geprüft markieren (nur für vertrauenswürdige Quellen)")
    ap.add_argument("--strict", action="store_true", help="Fehlende Abhängigkeiten = Fehler")
    ap.add_argument("--json", action="store_true", help="Bericht als JSON")
    a = ap.parse_args(argv)
    mapper = make_mapper(a.root, a.offline, a.map)
    signer = SecretKey.load(Path(a.key)) if a.key else None
    reports, rc = [], 0
    for p in a.packages:
        try:
            _, rep = convert(p, a.out, mapper, signer, a.mark_scripts_reviewed, a.keep_versions,
                             {"license": a.license, "name": a.name})
        except (ConvertError, OSError, KeyError, ValueError) as e:
            print(f"Fehler bei {p}: {e}", file=sys.stderr)
            rc = 1
            continue
        reports.append(rep)
        if not rep.ok and a.strict:
            rc = 2
        if not a.json:
            print(rep.text() + "\n")
    if a.json:
        print(json.dumps([r.to_dict() for r in reports], ensure_ascii=False, indent=2))
    missing = sorted({u["dep"] for r in reports for u in r.unresolved})
    if missing and not a.json:
        print("Fehlende Pakete (bitte bereitstellen oder in convert-map.toml zuordnen):")
        for m in missing:
            print(f"  - {m}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
