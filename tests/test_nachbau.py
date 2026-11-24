"""tools/nachbau.sh: veröffentlichte Pakete unabhängig nachbauen und vergleichen (Index-Signatur geprüft)."""
from __future__ import annotations

import os
import subprocess

from helpers import ROOT, keygen, syp


def run(tmp_path, repo, recs, pub, *pkgs, **env):
    e = dict(os.environ, NACHBAU_REPO=str(repo), NACHBAU_ROOT=env.pop("NACHBAU_ROOT", "-"), NACHBAU_PUB=str(pub), NACHBAU_RECIPES=str(recs),
             WORK=str(tmp_path / "work"), OUT=str(tmp_path / "nachbau.txt"), **env)
    return subprocess.run(["bash", ROOT / "tools/nachbau.sh", *pkgs], capture_output=True, text=True, env=e)


def test_nachbau(tmp_path):
    recs = tmp_path / "recipes"
    steps = {"fest": "echo fest", "zufall": "date +%s%N", "alt": 'cat "$RECIPEDIR/v"'}
    for name, step in steps.items():
        (recs / name).mkdir(parents=True)
        (recs / name / "SYPBUILD.toml").write_text(
            f'[package]\nname = "{name}"\nversion = "1-1"\narch = "any"\n'
            f"[build]\npackage = 'mkdir -p \"$DESTDIR/usr/share/{name}\"; {step} > \"$DESTDIR/usr/share/{name}/x\"'\n")
    (recs / "alt/v").write_text("vorher\n")
    key, pub = keygen(tmp_path)
    repo = tmp_path / "repo"
    syp("buildall", recs, "--repo", repo, "-k", key, "--name", "symfonos",
        env=dict(os.environ, SYP_SRCCACHE=str(tmp_path / "cache")))
    (recs / "alt/v").write_text("nachher\n")

    r = run(tmp_path, repo, recs, pub, "fest")
    assert r.returncode == 0 and "fest 1-1: gleich" in r.stdout, r.stdout + r.stderr
    r = run(tmp_path, repo, recs, pub, "fest", "zufall", "alt")
    assert r.returncode == 1, r.stdout + r.stderr
    out = (tmp_path / "nachbau.txt").read_text()
    assert "Ergebnis: 1 gleich, 2 abweichend, 0 nicht gebaut" in out
    assert "alt 1-1: **ABWEICHEND**" in out and "-vorher" in out and "+nachher" in out

    # Rezept passt nicht zur veröffentlichten Version: übersprungen, nicht als Abweichung gezählt
    (recs / "fest/SYPBUILD.toml").write_text((recs / "fest/SYPBUILD.toml").read_text().replace("1-1", "1-2"))
    r = run(tmp_path, repo, recs, pub, "fest")
    assert r.returncode == 2 and "Rezept 1-2, veröffentlicht 1-1" in r.stderr, r.stdout + r.stderr

    # fremder Schlüssel: der Index wird nicht geglaubt
    _, other = keygen(tmp_path, "fremd")
    r = run(tmp_path, repo, recs, other, "zufall")
    assert r.returncode == 2 and "Index-Signatur ungültig" in r.stderr, r.stdout + r.stderr

    # eigenes Repo für die Bauumgebung (z. B. testing): dessen Index wird ebenfalls nur mit gültiger Signatur geglaubt
    if os.geteuid() == 0:
        fremd = tmp_path / "fremd-repo"
        syp("buildall", recs, "--repo", fremd, "-k", tmp_path / "fremd.key", "--name", "symfonos",
            env=dict(os.environ, SYP_SRCCACHE=str(tmp_path / "cache")))
        r = run(tmp_path, repo, recs, pub, "zufall", NACHBAU_ROOT=str(tmp_path / "root"), NACHBAU_ENV_REPO=str(fremd))
        assert r.returncode == 2 and "Index-Signatur der Bauumgebung ungültig" in r.stderr, r.stdout + r.stderr
        assert not (tmp_path / "root/proc").exists()
