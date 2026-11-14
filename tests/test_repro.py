"""tools/repro-check.sh: zweimal bauen, Bauten untereinander und mit dem veröffentlichten Paket vergleichen."""
from __future__ import annotations

import os
import subprocess

from helpers import ROOT, keygen, syp


def test_repro_check(tmp_path):
    recs = tmp_path / "recipes"
    steps = {"gleich": "echo fest", "anders": "date +%s%N", "alt": 'cat "$RECIPEDIR/v"'}
    for name, step in steps.items():
        (recs / name).mkdir(parents=True)
        (recs / name / "SYPBUILD.toml").write_text(
            f'[package]\nname = "{name}"\nversion = "1-1"\narch = "any"\n'
            f"[build]\npackage = 'mkdir -p \"$DESTDIR/usr/share/{name}\"; {step} > \"$DESTDIR/usr/share/{name}/x\"'\n")
    (recs / "alt/v").write_text("vorher\n")
    key, _ = keygen(tmp_path)
    repo = tmp_path / "repo"
    syp("buildall", recs, "--repo", repo, "-k", key, "--name", "symfonos")
    (recs / "alt/v").write_text("nachher\n")  # Bauumgebung geändert, Version gleich
    out = tmp_path / "repro.txt"
    r = subprocess.run(["bash", ROOT / "tools/repro-check.sh", repo, recs, "-", key, "5"], capture_output=True,
                       text=True, env=dict(os.environ, SYP_SRCCACHE=str(tmp_path / "cache"), OUT=str(out)))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "2 von 3 reproduzierbar" in r.stdout
    assert "veraltet: alt;" in r.stdout and "nicht reproduzierbar: anders;" in r.stdout
    log = out.read_text()
    assert "+++ bau2/usr/share/anders/x" in log
    assert "-vorher" in log and "+nachher" in log
    # Originalpakete bleiben unangetastet
    assert len(list((repo / "pool").rglob("*.syp"))) == 3
