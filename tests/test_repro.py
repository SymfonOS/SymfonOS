"""tools/repro-check.sh: zweiter Bau, Vergleich des Inhalts."""
from __future__ import annotations

import os
import subprocess

from helpers import ROOT, keygen, syp


def test_repro_check(tmp_path):
    recs = tmp_path / "recipes"
    for name, step in (("gleich", "echo fest"), ("anders", "date +%s%N")):
        (recs / name).mkdir(parents=True)
        (recs / name / "SYPBUILD.toml").write_text(
            f'[package]\nname = "{name}"\nversion = "1-1"\narch = "any"\n'
            f"[build]\npackage = 'mkdir -p \"$DESTDIR/usr/share/{name}\"; {step} > \"$DESTDIR/usr/share/{name}/x\"'\n")
    key, _ = keygen(tmp_path)
    repo = tmp_path / "repo"
    syp("buildall", recs, "--repo", repo, "-k", key, "--name", "symfonos")
    out = tmp_path / "repro.txt"
    r = subprocess.run(["bash", ROOT / "tools/repro-check.sh", repo, recs, "-", key, "5"], capture_output=True,
                       text=True, env=dict(os.environ, SYP_SRCCACHE=str(tmp_path / "cache"), OUT=str(out)))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "1 von 2 gleich (gleich)" in r.stdout and "abweichend: anders" in r.stdout
    assert "usr/share/anders/x" in out.read_text()
    # Originalpakete bleiben unangetastet
    assert len(list((repo / "pool").rglob("*.syp"))) == 2
