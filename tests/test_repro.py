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
    # Gate: mit STRICT=1 macht ein nicht reproduzierbares Paket den Lauf rot
    r = subprocess.run(["bash", ROOT / "tools/repro-check.sh", repo, recs, "-", key, "5"], capture_output=True,
                       text=True, env=dict(os.environ, SYP_SRCCACHE=str(tmp_path / "cache"), OUT=str(out), STRICT="1"))
    assert r.returncode == 1 and "::error title=Reproduzierbarkeit::nicht reproduzierbar: anders" in r.stdout


def test_repro_check_only(tmp_path):
    # ONLY=a,b prüft genau diese Pakete, unabhängig von Tagesauswahl und Größengrenze
    recs = tmp_path / "recipes"
    for name in ("eins", "zwei", "drei"):
        (recs / name).mkdir(parents=True)
        (recs / name / "SYPBUILD.toml").write_text(
            f'[package]\nname = "{name}"\nversion = "1-1"\narch = "any"\n[build]\n'
            f'package = \'install -Dm644 /dev/null "$DESTDIR/usr/share/{name}/x"\'\n')
    key, _ = keygen(tmp_path)
    repo = tmp_path / "repo"
    syp("buildall", recs, "--repo", repo, "-k", key, "--name", "symfonos")
    r = subprocess.run(["bash", ROOT / "tools/repro-check.sh", repo, recs, "-", key, "1"], capture_output=True,
                       text=True, env=dict(os.environ, SYP_SRCCACHE=str(tmp_path / "cache"),
                                           OUT=str(tmp_path / "r.txt"), ONLY="zwei,drei", MAX_SRC_MB="0"))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "2 von 2 reproduzierbar" in r.stdout and "eins" not in r.stdout


def test_repro_sample_rotates_per_run(tmp_path):
    # je CI-Lauf (GITHUB_RUN_NUMBER) eine andere Stichprobe, nicht den ganzen Tag dieselbe
    recs = tmp_path / "recipes"
    names = ("a1", "b2", "c3")
    for name in names:
        (recs / name).mkdir(parents=True)
        (recs / name / "SYPBUILD.toml").write_text(
            f'[package]\nname = "{name}"\nversion = "1-1"\narch = "any"\n[build]\n'
            f'package = \'install -Dm644 /dev/null "$DESTDIR/usr/share/{name}/x"\'\n')
    key, _ = keygen(tmp_path)
    repo = tmp_path / "repo"
    syp("buildall", recs, "--repo", repo, "-k", key, "--name", "symfonos")
    seen = []
    for run in ("7", "8", "9"):
        r = subprocess.run(["bash", ROOT / "tools/repro-check.sh", repo, recs, "-", key, "1"], capture_output=True,
                           text=True, env=dict(os.environ, SYP_SRCCACHE=str(tmp_path / "cache"),
                                               OUT=str(tmp_path / "r.txt"), GITHUB_RUN_NUMBER=run))
        assert r.returncode == 0, r.stdout + r.stderr
        seen.append(next(n for n in names if f"({n} " in r.stdout))
    assert sorted(seen) == sorted(names), seen


def test_repro_sample_skips_large_packages(tmp_path):
    # große gebaute Pakete (lange Bauzeit) kommen nicht in die Stichprobe, nur per ONLY
    recs = tmp_path / "recipes"
    # deterministisch, aber schlecht komprimierbar (Pseudozufall mit festem Startwert)
    for name, n in (("klein", 1), ("gross", 50000)):
        (recs / name).mkdir(parents=True)
        step = ('mkdir -p "$DESTDIR/usr/share/NAME"; perl -e \'srand(1); print map { chr int rand 256 } 1..N\' '
                '> "$DESTDIR/usr/share/NAME/x"')
        step = step.replace("NAME", name).replace("..N", f"..{n}")
        (recs / name / "SYPBUILD.toml").write_text(
            f'[package]\nname = "{name}"\nversion = "1-1"\narch = "any"\n[build]\npackage = \'\'\'{step}\'\'\'\n')
    key, _ = keygen(tmp_path)
    repo = tmp_path / "repo"
    syp("buildall", recs, "--repo", repo, "-k", key, "--name", "symfonos")
    env = dict(os.environ, SYP_SRCCACHE=str(tmp_path / "cache"), OUT=str(tmp_path / "r.txt"), MAX_PKG_MB="0.01")
    r = subprocess.run(["bash", ROOT / "tools/repro-check.sh", repo, recs, "-", key, "5"], capture_output=True,
                       text=True, env=env)
    assert r.returncode == 0 and "1 von 1 reproduzierbar (klein" in r.stdout, r.stdout + r.stderr
    r = subprocess.run(["bash", ROOT / "tools/repro-check.sh", repo, recs, "-", key, "5"], capture_output=True,
                       text=True, env=dict(env, ONLY="gross"))
    assert r.returncode == 0 and "1 von 1 reproduzierbar (gross" in r.stdout, r.stdout + r.stderr
