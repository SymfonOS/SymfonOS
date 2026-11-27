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

    # Zeitbudget schon verbraucht: nichts durchsucht, nichts verloren
    r = tool(root, "discover", SOURCE_SIGS_KEYSERVERS=f"file://{ks}/{{fpr}}", SOURCE_SIGS_BUDGET="0.000001")
    assert r.returncode == 0 and "4 Pakete nicht durchsucht" in r.stdout, r.stdout + r.stderr

    r = tool(root, "discover", SOURCE_SIGS_KEYSERVERS=f"file://{ks}/{{fpr}}", SOURCE_SIGS_JOBS="3")
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


def test_expired_subkey_and_dotted_name(tmp_path):
    """Abgelaufener Schlüssel, der mit einem Unterschlüssel signiert hat (export-clean würfe ihn weg),
    und ein Paketname mit Punkt (wäre in TOML sonst eine verschachtelte Tabelle)."""
    import time
    root, srv, ks, home = tmp_path / "repo", tmp_path / "srv", tmp_path / "ks", tmp_path / "g"
    for d in (srv, ks, root / "lookaside"):
        d.mkdir(parents=True)
    home.mkdir(mode=0o700)
    g = ["gpg", "--homedir", home, "--batch", "--passphrase", ""]
    subprocess.run([*g, "--quick-gen-key", "Alt <alt@example.invalid>", "ed25519", "cert", "seconds=4"],
                   check=True, capture_output=True)
    out = subprocess.run([*g, "--with-colons", "--list-keys"], capture_output=True, text=True).stdout
    fpr = next(line.split(":")[9] for line in out.splitlines() if line.startswith("fpr:"))
    subprocess.run([*g, "--quick-add-key", fpr, "ed25519", "sign", "seconds=4"], check=True, capture_output=True)
    data = b"alt\n"
    (srv / "lib1.0-1.tar").write_bytes(data)
    sign(home, srv / "lib1.0-1.tar", srv / "lib1.0-1.tar.sig")
    pub = subprocess.run([*g, "--armor", "--export", fpr], capture_output=True).stdout
    out = subprocess.run([*g, "--with-colons", "--list-keys"], capture_output=True, text=True).stdout
    for f in (line.split(":")[9] for line in out.splitlines() if line.startswith("fpr:")):
        (ks / f).write_bytes(pub)                   # Keyserver findet den Schlüssel auch per Unterschlüssel
    recipe(root, "lib1.0", f"file://{srv}/lib1.0-1.tar", data)
    time.sleep(5)                                   # Schlüssel und Unterschlüssel sind jetzt abgelaufen
    r = tool(root, "discover", SOURCE_SIGS_KEYSERVERS=f"file://{ks}/{{fpr}}")
    assert "neu   lib1.0" in r.stdout, r.stdout + r.stderr
    assert '["lib1.0"]' in (root / "distro/source-keys.toml").read_text()
    r = tool(root, "check")
    assert r.returncode == 0 and "1 gültig" in r.stdout, r.stdout + r.stderr


def test_signed_checksum_list(tmp_path):
    """Projekt signiert nur SHA256SUMS: Schlüssel eintragen, wenn die Liste das Archiv nennt; danach prüfen."""
    root, srv, ks = tmp_path / "repo", tmp_path / "srv", tmp_path / "ks"
    for d in (srv / "v1", srv / "v2", ks, root / "lookaside"):
        d.mkdir(parents=True)
    fpr, pub = mkkey(tmp_path / "g", "Rel <rel@example.invalid>")
    (ks / fpr).write_bytes(pub)
    data = b"projekt 1\n"
    (srv / "v1/p-1.tar.gz").write_bytes(data)
    (srv / "v1/SHA256SUMS").write_text(f"{hashlib.sha256(b'anderes').hexdigest()}  q-1.tar.gz\n"
                                       f"{hashlib.sha256(data).hexdigest()}  p-1.tar.gz\n")
    sign(tmp_path / "g", srv / "v1/SHA256SUMS", srv / "v1/SHA256SUMS.asc", armor=True)
    recipe(root, "p", f"file://{srv}/v1/p-1.tar.gz", data)
    r = tool(root, "discover", SOURCE_SIGS_KEYSERVERS=f"file://{ks}/{{fpr}}")
    assert "neu   p: SHA256SUMS.asc" in r.stdout, r.stdout + r.stderr
    assert 'sums = "SHA256SUMS"' in (root / "distro/source-keys.toml").read_text()
    r = tool(root, "check")
    assert r.returncode == 0 and "1 gültig" in r.stdout, r.stdout + r.stderr

    # neue Version: Liste gültig signiert, nennt das Archiv aber mit anderer Summe – abgelehnt
    data2 = b"projekt 2\n"
    (srv / "v2/p-2.tar.gz").write_bytes(data2)
    (srv / "v2/SHA256SUMS").write_text(f"{hashlib.sha256(b'etwas anderes').hexdigest()}  p-2.tar.gz\n")
    sign(tmp_path / "g", srv / "v2/SHA256SUMS", srv / "v2/SHA256SUMS.asc", armor=True)
    shutil.rmtree(root / "distro/recipes/p")
    recipe(root, "p", f"file://{srv}/v2/p-2.tar.gz", data2)
    r = tool(root, "check", "p")
    assert r.returncode == 1 and "Summe falsch" in r.stdout, r.stdout
    # richtige Liste, aber von fremdem Schlüssel signiert – abgelehnt
    (srv / "v2/SHA256SUMS").write_text(f"SHA256 (p-2.tar.gz) = {hashlib.sha256(data2).hexdigest()}\n")
    mkkey(tmp_path / "f", "Fremd <f@example.invalid>")
    sign(tmp_path / "f", srv / "v2/SHA256SUMS", srv / "v2/SHA256SUMS.asc", armor=True)
    r = tool(root, "check", "p")
    assert r.returncode == 1 and "fremdem Schlüssel" in r.stdout, r.stdout
    # vom richtigen Schlüssel (BSD-Format): gültig
    sign(tmp_path / "g", srv / "v2/SHA256SUMS", srv / "v2/SHA256SUMS.asc", armor=True)
    assert tool(root, "check", "p").returncode == 0


def test_signature_next_to_uncompressed_name(tmp_path):
    """kernel.org: NAME.tar.sign signiert das entpackte NAME.tar, ausgeliefert wird NAME.tar.xz."""
    import lzma
    root, srv, ks = tmp_path / "repo", tmp_path / "srv", tmp_path / "ks"
    for d in (srv, ks, root / "lookaside"):
        d.mkdir(parents=True)
    fpr, pub = mkkey(tmp_path / "g", "Kern <k@example.invalid>")
    (ks / fpr).write_bytes(pub)
    (srv / "bz-1.tar").write_bytes(b"bluez\n")
    sign(tmp_path / "g", srv / "bz-1.tar", srv / "bz-1.tar.sign")
    (srv / "bz-1.tar").unlink()                    # nur die komprimierte Fassung wird ausgeliefert
    data = lzma.compress(b"bluez\n")
    (srv / "bz-1.tar.xz").write_bytes(data)
    recipe(root, "bz", f"file://{srv}/bz-1.tar.xz", data)
    r = tool(root, "discover", SOURCE_SIGS_KEYSERVERS=f"file://{ks}/{{fpr}}")
    assert "neu   bz: bz-1.tar.sign" in r.stdout, r.stdout + r.stderr
    keys = (root / "distro/source-keys.toml").read_text()
    assert "tar_sig = true" in keys and "uncompressed = true" in keys
    r = tool(root, "check")
    assert r.returncode == 0 and "1 gültig" in r.stdout, r.stdout + r.stderr
    # manipulierter Inhalt (gleicher Name, neue Prüfsumme im Rezept): abgelehnt
    bad = lzma.compress(b"bluez, manipuliert\n")
    (srv / "bz-1.tar.xz").write_bytes(bad)
    shutil.rmtree(root / "distro/recipes/bz")
    recipe(root, "bz", f"file://{srv}/bz-1.tar.xz", bad)
    r = tool(root, "check")
    assert r.returncode == 1 and "ungültig" in r.stdout, r.stdout


def mktag_repo(forge, path, gpghome, fpr, tag, files, sign_tag=True):
    """Repo unter forge/path mit (signiertem) Tag; files: {Pfad: Inhalt}, Pfad mit »*« am Ende = ausführbar."""
    repo = forge / path
    repo.mkdir(parents=True)
    env = dict(os.environ, GNUPGHOME=str(gpghome), GIT_CONFIG_GLOBAL="/dev/null", GIT_AUTHOR_NAME="Up", GIT_AUTHOR_EMAIL="up@example.invalid",
               GIT_COMMITTER_NAME="Up", GIT_COMMITTER_EMAIL="up@example.invalid")
    run = lambda *a: subprocess.run(["git", "-C", repo, *a], check=True, capture_output=True, env=env)
    run("init", "-q")
    for p, content in files.items():
        f = repo / p.rstrip("*")
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(content)
        if p.endswith("*"):
            f.chmod(0o755)
    (repo / "link").symlink_to("README")
    run("add", "-A")
    run("commit", "-qm", "release")
    run("-c", f"user.signingkey={fpr}", "tag", *(["-s"] if sign_tag else ["-a"]), tag, "-m", f"release {tag}")
    return repo, run


def test_signed_git_tag(tmp_path):
    """GitHub-Tag-Archiv ohne Signaturdatei: der signierte Release-Tag zählt, wenn das Archiv dem Baum entspricht."""
    import io
    import tarfile
    root, ks, forge = tmp_path / "repo", tmp_path / "ks", tmp_path / "forge"
    for d in (ks, root / "lookaside"):
        d.mkdir(parents=True)
    fpr, pub = mkkey(tmp_path / "g", "Upstream <up@example.invalid>")
    other, _ = mkkey(tmp_path / "g2", "Fremd <evil@example.invalid>")
    (ks / fpr).write_bytes(pub)
    files = {"README": b"hallo\n", "src/main.c": b"int main(){return 0;}\n", "configure*": b"#!/bin/sh\n"}
    repo, _ = mktag_repo(forge, "up/tool", tmp_path / "g", fpr, "v1.0", files)
    mktag_repo(forge, "up/plain", tmp_path / "g", fpr, "v2", files, sign_tag=False)

    def archive(repo, tag, prefix):
        return subprocess.run(["git", "-C", repo, "archive", "--format=tar.gz", f"--prefix={prefix}/", tag],
                              check=True, capture_output=True).stdout

    def put(name, url, data):
        if (root / "distro/recipes" / name).exists():
            shutil.rmtree(root / "distro/recipes" / name)
        recipe(root, name, url, data)
        (root / "lookaside" / hashlib.sha256(data).hexdigest()).write_bytes(data)

    good = archive(repo, "v1.0", "tool-1.0")
    put("tool", "https://github.com/up/tool/archive/v1.0/tool-1.0.tar.gz", good)
    put("plain", "https://github.com/up/plain/archive/refs/tags/v2.tar.gz", archive(forge / "up/plain", "v2", "plain-2"))
    env = dict(SOURCE_SIGS_KEYSERVERS=f"file://{ks}/{{fpr}}", SOURCE_SIGS_FORGE_GITHUB=f"file://{forge}")
    r = tool(root, "discover", **env)
    assert r.returncode == 0 and "neu   tool: signierter Tag v1.0" in r.stdout, r.stdout + r.stderr
    keys = (root / "distro/source-keys.toml").read_text()
    assert 'sig = "git-tag"' in keys and "[plain]" not in keys        # unsignierter Tag: nichts eingetragen
    r = tool(root, "check", **env)
    assert r.returncode == 0 and "ok    tool (signierter Tag v1.0" in r.stdout, r.stdout + r.stderr

    # Archiv mit untergeschobener bzw. veränderter Datei: abgelehnt, obwohl der Tag gültig signiert ist
    for change in ({"tool-1.0/src/extra.c": b"evil\n"}, {"tool-1.0/src/main.c": b"int main(){return 1;}\n"}):
        buf = io.BytesIO()
        with tarfile.open(fileobj=io.BytesIO(good)) as src, tarfile.open(fileobj=buf, mode="w:gz") as dst:
            for m in src:
                if m.name in change:
                    continue
                dst.addfile(m, src.extractfile(m) if m.isfile() else None)
            for n, content in change.items():
                ti = tarfile.TarInfo(n)
                ti.size = len(content)
                dst.addfile(ti, io.BytesIO(content))
        put("tool", "https://github.com/up/tool/archive/v1.0/tool-1.0.tar.gz", buf.getvalue())
        r = tool(root, "check", **env)
        assert r.returncode == 1 and "FEHL  tool: src/" in r.stdout, r.stdout + r.stderr

    # neue Version, Tag von einem fremden Schlüssel signiert: abgelehnt
    subprocess.run(["git", "-C", repo, "commit", "-q", "--allow-empty", "-m", "2"], check=True, capture_output=True,
                   env=dict(os.environ, GIT_CONFIG_GLOBAL="/dev/null", GIT_AUTHOR_NAME="X", GIT_AUTHOR_EMAIL="x@x", GIT_COMMITTER_NAME="X",
                            GIT_COMMITTER_EMAIL="x@x"))
    subprocess.run(["git", "-C", repo, "-c", f"user.signingkey={other}", "tag", "-s", "v1.1", "-m", "1.1"], check=True,
                   capture_output=True, env=dict(os.environ, GNUPGHOME=str(tmp_path / "g2"), GIT_CONFIG_GLOBAL="/dev/null",
                                                  GIT_COMMITTER_NAME="X",
                                                  GIT_COMMITTER_EMAIL="x@x"))
    put("tool", "https://github.com/up/tool/archive/v1.1/tool-1.1.tar.gz", archive(repo, "v1.1", "tool-1.1"))
    r = tool(root, "check", **env)
    assert r.returncode == 1 and "fremdem Schlüssel" in r.stdout, r.stdout + r.stderr


def test_tag_source_urls():
    import importlib.util
    spec = importlib.util.spec_from_file_location("ss", TOOL)
    ss = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ss)
    t = ss.tag_source
    assert t("https://github.com/systemd/systemd/archive/v257.8/systemd-257.8.tar.gz") == \
        ("https://github.com/systemd/systemd", "v257.8")
    assert t("https://github.com/o/r/archive/refs/tags/v1.2.tar.gz") == ("https://github.com/o/r", "v1.2")
    assert t("https://github.com/rhboot/efivar/archive/39.tar.gz") == ("https://github.com/rhboot/efivar", "39")
    assert t("https://gitlab.freedesktop.org/pipewire/pipewire/-/archive/1.4.7/pipewire-1.4.7.tar.bz2") == \
        ("https://gitlab.freedesktop.org/pipewire/pipewire", "1.4.7")
    assert t("https://gitlab.com/o/r/-/archive/" + "a" * 40 + "/r.tar.gz") is None      # Commit, kein Tag
    assert t("https://github.com/o/r/releases/download/v1/r-1.tar.gz") is None          # Release-Datei
    assert t("https://example.org/r-1.tar.gz") is None
