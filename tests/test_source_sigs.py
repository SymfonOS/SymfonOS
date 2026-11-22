"""Upstream-Signaturen der Quellarchive (tools/source-sigs.py): Schlüssel finden und festschreiben,
gültige Signaturen annehmen, fremde Schlüssel, manipulierte Archive und fehlende Signaturen ablehnen."""
import gzip
import hashlib
import os
import shutil
import subprocess

import pytest

from helpers import ROOT

pytestmark = pytest.mark.skipif(not shutil.which("gpg"), reason="gpg fehlt")
TOOL = ROOT / "tools/source-sigs.py"


def mkkey(home, uid):
    home.mkdir(mode=0o700)
    subprocess.run(["gpg", "--homedir", home, "--batch", "--passphrase", "", "--quick-gen-key", uid,
                    "ed25519", "sign", "never"], check=True, capture_output=True)
    out = subprocess.run(["gpg", "--homedir", home, "--with-colons", "--list-keys"], capture_output=True,
                         text=True, check=True).stdout
    fpr = next(line.split(":")[9] for line in out.splitlines() if line.startswith("fpr:"))
    pub = subprocess.run(["gpg", "--homedir", home, "--armor", "--export", fpr], capture_output=True,
                         check=True).stdout
    return fpr, pub


def sign(home, path, out, armor=False):
    subprocess.run(["gpg", "--homedir", home, "--batch", "--yes", *(["--armor"] if armor else []),
                    "--detach-sign", "-o", out, path], check=True, capture_output=True)


def recipe(root, name, url, data):
    d = root / "distro/recipes" / name
    d.mkdir(parents=True)
    (d / "SYPBUILD.toml").write_text(
        f'[package]\nname = "{name}"\nversion = "1-1"\n[[source]]\nurl = "{url}"\n'
        f'sha256 = "{hashlib.sha256(data).hexdigest()}"\n')


def tool(root, *args, **env):
    e = dict(os.environ, SOURCE_SIGS_ROOT=str(root), SYP_LOOKASIDE=f"file://{root}/lookaside", **env)
    e.pop("GITHUB_ACTIONS", None)
    return subprocess.run([TOOL, *args], capture_output=True, text=True, env=e)


def test_discover_and_check(tmp_path):
    root, srv, ks = tmp_path / "repo", tmp_path / "srv", tmp_path / "ks"
    for d in (srv, ks, root / "lookaside"):
        d.mkdir(parents=True)
    fpr, pub = mkkey(tmp_path / "g1", "Upstream <up@example.invalid>")
    other, _ = mkkey(tmp_path / "g2", "Fremd <evil@example.invalid>")
    (ks / fpr).write_bytes(pub)
    # a: .sig neben dem Archiv; b: ASCII-Signatur .asc; c: unsigniert; k: .sign gilt dem entpackten .tar
    for n, suf in (("a", ".sig"), ("b", ".asc"), ("c", None)):
        data = f"archiv {n}\n".encode()
        (srv / f"{n}-1.tar").write_bytes(data)
        recipe(root, n, f"file://{srv}/{n}-1.tar", data)
        if suf:
            sign(tmp_path / "g1", srv / f"{n}-1.tar", srv / f"{n}-1.tar{suf}", armor=suf == ".asc")
    (srv / "k-1.tar").write_bytes(b"kern\n")
    sign(tmp_path / "g1", srv / "k-1.tar", srv / "k-1.tar.xz.sign")
    import lzma
    kx = lzma.compress(b"kern\n")
    (srv / "k-1.tar.xz").write_bytes(kx)
    recipe(root, "k", f"file://{srv}/k-1.tar.xz", kx)

    r = tool(root, "discover", SOURCE_SIGS_KEYSERVERS=f"file://{ks}/{{fpr}}")
    assert r.returncode == 0, r.stdout + r.stderr
    keys = (root / "distro/source-keys.toml").read_text()
    assert "[a]" in keys and "[b]" in keys and "[k]" in keys and "[c]" not in keys
    assert 'sig = ".asc"' in keys and "uncompressed = true" in keys
    assert (root / f"distro/keys/upstream/{fpr}.asc").exists()

    r = tool(root, "check")
    assert r.returncode == 0 and "3 gültig" in r.stdout, r.stdout + r.stderr

    # Archiv aus dem Lookaside-Cache, auch wenn der Projektserver es nicht mehr hat
    (root / "lookaside" / hashlib.sha256(b"archiv a\n").hexdigest()).write_bytes(b"archiv a\n")
    (srv / "a-1.tar").unlink()
    assert tool(root, "check", "a").returncode == 0

    # neue Version, von einem fremden Schlüssel signiert: abgelehnt
    data = b"archiv b, Version 2\n"
    (srv / "b-2.tar").write_bytes(data)
    sign(tmp_path / "g2", srv / "b-2.tar", srv / "b-2.tar.asc", armor=True)
    shutil.rmtree(root / "distro/recipes/b")
    recipe(root, "b", f"file://{srv}/b-2.tar", data)
    r = tool(root, "check", "b")
    assert r.returncode == 1 and "fremdem Schlüssel" in r.stdout, r.stdout

    # neue Version ohne Signaturdatei: abgelehnt (das Projekt signiert bisher immer)
    (srv / "b-2.tar.asc").unlink()
    r = tool(root, "check", "b")
    assert r.returncode == 1 and "FEHL" in r.stdout, r.stdout
    assert other not in (root / "distro/source-keys.toml").read_text()


def test_check_changed_only(tmp_path):
    root, srv = tmp_path / "repo", tmp_path / "srv"
    srv.mkdir()
    fpr, pub = mkkey(tmp_path / "g", "U <u@example.invalid>")
    data = gzip.compress(b"x")
    (srv / "x-1.tar.gz").write_bytes(data)
    sign(tmp_path / "g", srv / "x-1.tar.gz", srv / "x-1.tar.gz.sig")
    recipe(root, "x", f"file://{srv}/x-1.tar.gz", data)
    recipe(root, "y", f"file://{srv}/y-1.tar.gz", b"y")
    (root / "distro/keys/upstream").mkdir(parents=True)
    (root / f"distro/keys/upstream/{fpr}.asc").write_bytes(pub)
    (root / "distro/source-keys.toml").write_text(f'[x]\nkeys = ["{fpr}"]\nsig = ".sig"\n')
    git = ["git", "-C", root, "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-qm", "a"], check=True)
    r = tool(root, "check", "--changed", "HEAD")
    assert r.returncode == 0 and "keine geänderten Rezepte" in r.stdout
    # Archiv manipuliert, Rezept mit neuer Prüfsumme: geprüft und abgelehnt
    bad = gzip.compress(b"x, manipuliert")
    (srv / "x-1.tar.gz").write_bytes(bad)
    shutil.rmtree(root / "distro/recipes/x")
    recipe(root, "x", f"file://{srv}/x-1.tar.gz", bad)
    r = tool(root, "check", "--changed", "HEAD")
    assert r.returncode == 1 and "ungültig" in r.stdout, r.stdout
