"""Lookaside-Cache in der CI (tools/lookaside-sync.sh): Quellen unter ihrer sha256 ablegen, Grenzen je Lauf,
nicht mehr genannte Quellen entfernen – und syp lädt sie von dort (SYP_LOOKASIDE)."""
import hashlib
import os
import subprocess

from helpers import ROOT, SYP, run
from test_promote import FAKE_GH


def test_lookaside_sync(tmp_path):
    bindir, rel, cache, recs = tmp_path / "bin", tmp_path / "releases", tmp_path / "cache", tmp_path / "recipes"
    bindir.mkdir()
    (bindir / "gh").write_text(FAKE_GH)
    (bindir / "gh").chmod(0o755)
    cache.mkdir()
    sums = {}
    for n in ("a", "b", "c"):
        data = f"quelle {n}\n".encode()
        (cache / f"{n}-1.tar").write_bytes(data)
        sums[n] = hashlib.sha256(data).hexdigest()
        (recs / n).mkdir(parents=True)
        (recs / n / "SYPBUILD.toml").write_text(
            f'[package]\nname = "{n}"\nversion = "1-1"\n[[source]]\nurl = "https://example.invalid/{n}-1.tar"\n'
            f'sha256 = "{sums[n]}"\n')
    (cache / "c-1.tar").write_text("verändert\n")          # falsche Summe: nicht hochladen
    env = dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}", FAKE_GH_DIR=str(rel), GITHUB_REPOSITORY="t/t")

    def sync(**extra):
        r = subprocess.run([ROOT / "tools/lookaside-sync.sh", cache, recs], capture_output=True, text=True,
                           env=dict(env, **extra))
        assert r.returncode == 0, r.stdout + r.stderr
        return r.stdout

    out = sync(MAX_FILES="1")
    assert "1 von 3 Quellen" in out and "1 mit falscher Summe" in out
    out = sync()
    assert sorted(os.listdir(rel / "sources")) == sorted([sums["a"], sums["b"]])
    # Rezept a entfällt: seine Quelle verschwindet aus dem Release
    (recs / "a/SYPBUILD.toml").unlink()
    assert "1 entfernt" in sync()
    assert sorted(os.listdir(rel / "sources")) == [sums["b"]]
    # syp holt b über den Lookaside-Cache, obwohl der Projektserver nicht existiert
    r = run(SYP, "fetch", recs / "b/SYPBUILD.toml", "-o", tmp_path / "neu",
            env=dict(os.environ, SYP_LOOKASIDE=f"file://{rel}/sources"))
    assert (tmp_path / "neu/b-1.tar").read_text() == "quelle b\n" and "Lookaside" in r.stderr
