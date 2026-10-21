"""Tests für den Paketmanager syp (C)."""
from __future__ import annotations

import io
import os
import subprocess
import json
import tarfile

import pytest

from helpers import ROOT, SYP, keygen, make_pkg, publish, run, setup_root, syp


@pytest.mark.parametrize("a,b,r", [
    ("1.0", "1.0", 0), ("1.0", "1.1", -1), ("1.10", "1.9", 1), ("1.0a", "1.0", -1),
    ("1.0", "1.0.1", -1), ("1:1.0", "2.0", 1), ("1.0-2", "1.0-10", -1), ("1.0-1", "1.0", 0),
    ("2.0rc1", "2.0", -1), ("1.0.0", "1.0", 1), ("1.a", "1.1", -1), ("1.01", "1.1", 0),
])
def test_vercmp(a, b, r):
    assert int(syp("vercmp", a, b).stdout) == r


@pytest.fixture
def env(tmp_path):
    key, pub = keygen(tmp_path)
    repo_dir = tmp_path / "repo"
    root = setup_root(tmp_path, repo_dir, pub)

    def pub_and_sync(*pkgs):
        publish(repo_dir, key, *pkgs)
        syp("-r", root, "sync")

    def S(*args, **kw):
        return syp("-r", root, "-y", *args, **kw)

    def plan(*args):
        return json.loads(syp("-r", root, "-n", *args, "--json").stdout)

    def pk(name, ver, files=None, **info):
        return make_pkg(tmp_path, name, ver, files, key, **info)

    return tmp_path, key, root, pub_and_sync, S, plan, pk


def names(plan):
    return {p["name"]: p["version"] for p in plan["install"]}


def test_solver_basic_and_provides(env):
    tmp, key, root, publish_, S, plan, pk = env
    publish_(pk("app", "1-1", depends=["libx>=2", "sh"]), pk("libx", "1-1"), pk("libx", "2-1"),
             pk("bash", "5-1", provides=["sh"]))
    assert names(plan("install", "app")) == {"app": "1-1", "libx": "2-1", "bash": "5-1"}
    S("install", "app")
    info = {p["name"]: p["reason"] for p in json.loads(syp("-r", root, "list", "--json").stdout)}
    assert info == {"app": "explicit", "libx": "dep", "bash": "dep"}


def test_solver_backtracks_over_conflict(env):
    tmp, key, root, publish_, S, plan, pk = env
    base = pk("base", "1-1")
    S("install", base)
    publish_(pk("app", "1-1", depends=["tool"]), pk("tool-a", "2-1", provides=["tool"], conflicts=["base"]),
             pk("tool-b", "1-1", provides=["tool"]))
    assert names(plan("install", "app")) == {"app": "1-1", "tool-b": "1-1"}


def test_solver_alternatives_and_missing(env):
    tmp, key, root, publish_, S, plan, pk = env
    publish_(pk("app", "1-1", depends=["x | y"]), pk("y", "1-1"), pk("bad", "1-1", depends=["nope"]))
    assert names(plan("install", "app")) == {"app": "1-1", "y": "1-1"}
    r = syp("-r", root, "-n", "install", "bad", check=False)
    assert r.returncode == 1 and "Nichts stellt »nope« bereit (benötigt von bad)" in r.stderr


def test_solver_upgrade_and_broken_revdeps(env):
    tmp, key, root, publish_, S, plan, pk = env
    publish_(pk("lib", "1-1"), pk("app", "1-1", depends=["lib<2"]))
    S("install", "app")
    publish_(pk("lib", "2-1"), pk("app", "2-1", depends=["lib>=2"]))
    S("sync")
    assert names(plan("upgrade", "--no-sync")) == {"lib": "2-1", "app": "2-1"}


def test_remove_cascade_and_orphans(env):
    tmp, key, root, publish_, S, plan, pk = env
    publish_(pk("lib", "1"), pk("app", "1", depends=["lib"]), pk("old", "1"), pk("tool", "1", depends=["old"]))
    S("install", "app", "tool")
    r = syp("-r", root, "-y", "remove", "lib", check=False)
    assert r.returncode == 1 and "app benötigt »lib«" in r.stderr
    assert plan("remove", "lib", "--cascade")["remove"] == ["app", "lib"]
    S("remove", "tool")
    assert plan("autoremove")["remove"] == ["old"]


def test_package_signature_and_tamper(tmp_path):
    key, pub = keygen(tmp_path)
    p = make_pkg(tmp_path, "hello", "1.0-1", key=key)
    with tarfile.open(p) as tf:
        assert tf.getnames() == [".SYPINFO.json", ".FILES.json", "data.tar.zst", ".SIGNATURE.json"]
        members = {m.name: tf.extractfile(m).read() for m in tf.getmembers()}
    info = json.loads(members[".SYPINFO.json"])
    info["summary"] = "manipuliert"
    members[".SYPINFO.json"] = json.dumps(info).encode()
    bad = tmp_path / "bad.syp"
    with tarfile.open(bad, "w") as tf:
        for n in (".SYPINFO.json", ".FILES.json", "data.tar.zst", ".SIGNATURE.json"):
            ti = tarfile.TarInfo(n)
            ti.size = len(members[n])
            tf.addfile(ti, io.BytesIO(members[n]))
    root = setup_root(tmp_path, tmp_path / "repo", pub)
    syp("-r", root, "-y", "install", p)
    r = syp("-r", root, "-y", "install", bad, check=False)
    assert r.returncode == 1 and "Signatur ungültig" in r.stderr
    r = syp("-r", root, "-y", "install", make_pkg(tmp_path, "unsig", "1"), check=False)
    assert "nicht signiert" in r.stderr


def test_invalid_name(tmp_path):
    with pytest.raises(AssertionError, match="Ungültiger Paketname"):
        make_pkg(tmp_path, "Bad Name", "1")


def test_end_to_end(env):
    tmp, key, root, publish_, S, plan, pk = env
    publish_(pk("libcolor", "1.0-1", {"usr/lib/libcolor.so.1": "v1"}, provides=["so:libcolor.so.1"]),
             pk("paint", "1.0-1", {"usr/bin/paint": "paint1", "etc/paint.conf": "a=1\n"},
                depends=["so:libcolor.so.1"], backup=["etc/paint.conf"], categories=["Graphics"]))
    S("install", "paint")
    assert (root / "usr/bin/paint").read_text() == "paint1"
    assert syp("-r", root, "owns", root / "usr/bin/paint").stdout == "/usr/bin/paint gehört zu paint\n"
    search = json.loads(syp("-r", root, "search", "paint", "--json").stdout)
    assert search[0]["installed"] == "1.0-1" and search[0]["categories"] == ["Graphics"]

    (root / "etc/paint.conf").write_text("a=2\n")      # Nutzer ändert Konfig
    publish_(pk("paint", "2.0-1", {"usr/bin/paint": "paint2", "etc/paint.conf": "a=1\nb=1\n"},
                depends=["so:libcolor.so.1"], backup=["etc/paint.conf"]))
    S("upgrade")
    assert (root / "usr/bin/paint").read_text() == "paint2"
    assert (root / "etc/paint.conf").read_text() == "a=2\n"
    assert (root / "etc/paint.conf.sypnew").exists()
    S("verify")
    assert "upgrade" in S("history").stdout
    assert len(json.loads(syp("-r", root, "history", "--json").stdout)) == 2

    S("rollback")                                       # Zustand vor dem Upgrade
    assert (root / "usr/bin/paint").read_text() == "paint1"

    assert syp("-r", root, "-y", "remove", "libcolor", check=False).returncode == 1
    S("remove", "paint")
    assert not (root / "usr/bin/paint").exists()
    assert (root / "etc/paint.conf.sypsave").exists()
    S("autoremove")
    assert not (root / "usr/lib/libcolor.so.1").exists()
    assert "Keine Probleme" in syp("-r", root, "doctor", check=False).stdout


def test_file_conflict_and_overwrite(env):
    tmp, key, root, publish_, S, plan, pk = env
    publish_(pk("a", "1-1", {"usr/bin/x": "a"}), pk("b", "1-1", {"usr/bin/x": "b"}),
             pk("c", "1-1", {"usr/bin/y": "c"}))
    S("install", "a")
    r = syp("-r", root, "-y", "install", "b", check=False)
    assert r.returncode == 1 and "existiert bereits in a" in r.stderr
    assert (root / "usr/bin/x").read_text() == "a"
    (root / "usr/bin/y").write_text("unverwaltet")
    assert syp("-r", root, "-y", "install", "c", check=False).returncode == 1
    S("install", "c", "--overwrite", "/usr/bin/y")
    assert (root / "usr/bin/y").read_text() == "c"


def test_unsigned_index_rejected(tmp_path):
    key, pub = keygen(tmp_path)
    other, _ = keygen(tmp_path, "other")
    repo_dir = tmp_path / "repo"
    root = setup_root(tmp_path, repo_dir, pub)
    publish(repo_dir, other, make_pkg(tmp_path, "a", "1-1", key=key))
    r = syp("-r", root, "sync", check=False)
    assert r.returncode == 1 and "Unbekannter Signaturschlüssel" in r.stderr


def test_build_recipe(tmp_path):
    (tmp_path / "hello.sh").write_text("#!/bin/sh\necho Hallo\n")
    (tmp_path / "SYPBUILD.toml").write_text('''
[package]
name = "hello"
version = "1.0-1"
arch = "any"
license = "MIT"
makedepends = ["gcc"]
[[source]]
path = "hello.sh"
[build]
check = 'test -x hello.sh || chmod +x hello.sh'
package = \'\'\'
install -Dm755 hello.sh "$DESTDIR/usr/bin/hello"
echo "$PKGNAME $PKGVER $PKGREL" > "$DESTDIR/usr/bin/.meta"
\'\'\'
[scripts]
post_install = "echo installiert"
''')
    syp("build", tmp_path / "SYPBUILD.toml", "-o", tmp_path / "o")
    p = tmp_path / "o/hello-1.0-1-any.syp"
    with tarfile.open(p) as tf:
        files = json.loads(tf.extractfile(".FILES.json").read())
        info = json.loads(tf.extractfile(".SYPINFO.json").read())
    assert [f["path"] for f in files] == ["usr", "usr/bin", "usr/bin/.meta", "usr/bin/hello"]
    assert files[-1]["mode"] == 0o755
    assert "makedepends" not in info and info["scripts"]["post_install"] == "echo installiert"


def test_buildall_order_and_resume(tmp_path):
    key, pub = keygen(tmp_path)
    rec = tmp_path / "recipes"
    for name, deps in (("zlib", []), ("libpng", ["zlib"]), ("app", ["libpng", "sh"]), ("bash", [])):
        d = rec / name
        d.mkdir(parents=True)
        extra = 'provides = ["sh"]\n' if name == "bash" else ""
        (d / "SYPBUILD.toml").write_text(
            f'[package]\nname = "{name}"\nversion = "1-1"\nmakedepends = {json.dumps(deps)}\n{extra}'
            f'[build]\npackage = \'mkdir -p "$DESTDIR/usr/share/{name}" && date +%s%N > "$DESTDIR/usr/share/{name}/b"\'\n')
    repo = tmp_path / "repo"
    out = syp("buildall", rec, "--repo", repo, "-k", key, "--name", "core").stdout
    order = [line.split()[2] for line in out.splitlines() if line.startswith("==> [")]
    assert order.index("zlib") < order.index("libpng") < order.index("app") and order.index("bash") < order.index("app")
    out2 = syp("buildall", rec, "--repo", repo, "-k", key, "--name", "core").stdout
    assert "gebaut 0, vorhanden 4" in out2
    root = setup_root(tmp_path, repo, pub)
    syp("-r", root, "sync")
    syp("-r", root, "-y", "install", "app")


def test_buildall_dry_run(tmp_path):
    rec = tmp_path / "r"
    (rec / "x").mkdir(parents=True)
    (rec / "x/SYPBUILD.toml").write_text('[package]\nname = "x"\nversion = "1"\n')
    r = syp("buildall", rec, "--repo", tmp_path / "repo", "-n")
    assert "würde bauen: x 1" in r.stdout


@pytest.mark.skipif(os.geteuid() != 0, reason="chroot/mount brauchen Root")
def test_chroot_build(tmp_path):
    """Bau per chroot: Host steuert, Schritte laufen isoliert in der Wurzel."""
    r = tmp_path / "chr"
    for d in ("usr", "etc/alternatives", "tmp", "var/tmp"):
        (r / d).mkdir(parents=True)
    for link in ("bin", "sbin", "lib", "lib64"):
        (r / link).symlink_to(f"usr/{link}")
    mounts = [("/usr", r / "usr"), ("/etc/alternatives", r / "etc/alternatives")]
    try:
        for src, dst in mounts:
            if subprocess.run(["mount", "--bind", "-o", "ro", src, str(dst)]).returncode:
                pytest.skip("bind-mount nicht möglich")
        rec = tmp_path / "rec"
        rec.mkdir()
        (rec / "hello.c").write_text('#include <stdio.h>\nint main(void){puts("chroot");return 0;}\n')
        (rec / "SYPBUILD.toml").write_text('''
[package]
name = "hello"
version = "1.0-1"
[[source]]
path = "hello.c"
[build]
build = 'cc -o hello hello.c; test ! -e "$RECIPEDIR/../../../../../home/user/symfonos"; test -f "$RECIPEDIR/hello.c"'
check = './hello | grep -q chroot'
package = 'install -Dm755 hello "$DESTDIR/usr/bin/hello"'
''')
        syp("build", rec / "SYPBUILD.toml", "--chroot", r, "-o", tmp_path / "out")
    finally:
        for _, dst in reversed(mounts):
            subprocess.run(["umount", str(dst)])
    assert not list((r / "var/tmp/syp-build").iterdir())
    key, pub = keygen(tmp_path)
    root = setup_root(tmp_path, tmp_path / "repo", pub)
    syp("-r", root, "-y", "install", "--allow-unsigned", tmp_path / "out/hello-1.0-1-x86_64.syp")
    assert subprocess.run([root / "usr/bin/hello"], capture_output=True, text=True).stdout == "chroot\n"


def test_build_style_make_and_source_dir(tmp_path):
    proj = tmp_path / "src/tool-2.0"
    proj.mkdir(parents=True)
    (proj / "tool.c").write_text('int main(void){return 0;}\n')
    (proj / "Makefile").write_text(
        "all:\n\tcc $(CFLAGS) -o tool tool.c\ninstall:\n\tinstall -Dm755 tool $(DESTDIR)$(PREFIX)/bin/tool\n")
    rec = tmp_path / "rec"
    rec.mkdir()
    with tarfile.open(rec / "tool-2.0.tar.gz", "w:gz") as tf:
        tf.add(proj, arcname="tool-2.0")
    (rec / "SYPBUILD.toml").write_text('''
[package]
name = "tool"
version = "2.0-1"
[[source]]
path = "tool-2.0.tar.gz"
[build]
style = "make"
pre_configure = 'echo "S=$S" > /dev/null; test -f tool.c'
post_install = 'install -Dm644 /dev/null "$DESTDIR/usr/share/doc/tool/OK"'
''')
    # lokale Archive werden kopiert, nicht entpackt -> Quelle per URL simulieren
    syp("build", rec / "SYPBUILD.toml", "-o", tmp_path / "o", check=False)
    (rec / "SYPBUILD.toml").write_text((rec / "SYPBUILD.toml").read_text().replace(
        'path = "tool-2.0.tar.gz"', f'url = "file://{rec}/tool-2.0.tar.gz"\nsha256 = "TODO"'))
    syp("build", rec / "SYPBUILD.toml", "-o", tmp_path / "o", "--update-sums")
    assert 'sha256 = "' in (rec / "SYPBUILD.toml").read_text() and "TODO" not in (rec / "SYPBUILD.toml").read_text()
    with tarfile.open(tmp_path / "o/tool-2.0-1-x86_64.syp") as tf:
        files = [f["path"] for f in json.loads(tf.extractfile(".FILES.json").read())]
    assert "usr/bin/tool" in files and "usr/share/doc/tool/OK" in files


def test_index_freeze_rollback_and_mixup_protection(tmp_path):
    import shutil
    import time
    key, pub = keygen(tmp_path)
    repo = tmp_path / "repo"
    root = setup_root(tmp_path, repo, pub)
    publish(repo, key, make_pkg(tmp_path, "a", "1-1", key=key))
    syp("-r", root, "sync")
    old = tmp_path / "old"
    shutil.copytree(repo, old)
    time.sleep(1.1)
    publish(repo, key, make_pkg(tmp_path, "a", "2-1", key=key))
    syp("-r", root, "sync")
    # Spiegel liefert wieder den alten (gültig signierten) Index aus -> Rollback-Angriff
    for f in ("index.json.zst", "index.json.zst.sig"):
        shutil.copy(old / f, repo / f)
    r = syp("-r", root, "sync", check=False)
    assert r.returncode == 1 and "Rollback-Angriff" in r.stderr
    # abgelaufener Index -> Freeze-Angriff
    env = dict(os.environ, SYP_INDEX_VALID_DAYS="-1")
    time.sleep(1.1)
    syp("repo-add", repo, "--name", "core", "-k", key, env=env)
    r = syp("-r", root, "sync", check=False)
    assert r.returncode == 1 and "abgelaufen" in r.stderr
    # Index eines anderen Repos
    syp("repo-add", repo, "--name", "extra", "-k", key)
    r = syp("-r", root, "sync", check=False)
    assert r.returncode == 1 and "Verwechslungsangriff" in r.stderr
    syp("repo-add", repo, "--name", "core", "-k", key)
    syp("-r", root, "sync")


def test_hardening_check(tmp_path):
    rec = tmp_path / "rec"
    rec.mkdir()
    (rec / "x.c").write_text("int main(void){char b[64]; return b[0];}\n")
    def recipe(flags, extra=""):
        (rec / "SYPBUILD.toml").write_text(f'''
[package]
name = "x"
version = "1-1"
[[source]]
path = "x.c"
[build]
build = 'cc {flags} -o x x.c'
package = 'install -Dm755 x "$DESTDIR/usr/bin/x"'
{extra}
''')
    recipe("$CFLAGS $LDFLAGS -fPIE -pie")
    out = syp("build", rec / "SYPBUILD.toml", "-o", tmp_path / "ok").stdout
    assert "ohne PIE 0" in out and "ausf. Stack 0" in out
    with tarfile.open(tmp_path / "ok/x-1-1-x86_64.syp") as tf:
        h = json.loads(tf.extractfile(".SYPINFO.json").read())["origin"]["hardening"]
    assert h["elf"] == 1 and h["no_pie"] == 0 and h["no_bind_now"] == 0
    recipe("-no-pie -Wl,-z,execstack -Wl,-z,norelro")
    r = syp("build", rec / "SYPBUILD.toml", "-o", tmp_path / "bad", check=False)
    assert r.returncode == 1 and "Härtungsprüfung fehlgeschlagen" in r.stderr
    assert "kein PIE" in r.stdout and "ausführbarer Stack" in r.stdout
    recipe("-no-pie", '[options]\nhardening = "warn"\nhardening_reason = "Test"')
    syp("build", rec / "SYPBUILD.toml", "-o", tmp_path / "warn")


def test_hardening_file_modes(tmp_path):
    rec = tmp_path / "rec"
    rec.mkdir()
    def recipe(pkg):
        (rec / "SYPBUILD.toml").write_text(
            '[package]\nname = "m"\nversion = "1-1"\narch = "any"\n[build]\n'
            "package = '''\n" + pkg + "\n'''\n")
    recipe('install -d -m1777 "$DESTDIR/var/tmp"\ninstall -Dm4755 /dev/null "$DESTDIR/usr/bin/helper"')
    out = syp("build", rec / "SYPBUILD.toml", "-o", tmp_path / "ok").stdout
    assert "Privilegiert: /usr/bin/helper (setuid)" in out              # Übersicht, kein Fehler
    with tarfile.open(tmp_path / "ok/m-1-1-any.syp") as tf:
        h = json.loads(tf.extractfile(".SYPINFO.json").read())["origin"]["hardening"]
    assert h["setuid"] == ["/usr/bin/helper (setuid)"] and h["world_writable"] == 0
    recipe('install -Dm666 /dev/null "$DESTDIR/etc/offen.conf"\ninstall -d -m777 "$DESTDIR/var/spool/x"')
    r = syp("build", rec / "SYPBUILD.toml", "-o", tmp_path / "bad", check=False)
    assert r.returncode == 1 and "Härtungsprüfung fehlgeschlagen (2 kritische" in r.stderr
    assert "/etc/offen.conf: für alle beschreibbar (Modus 0666)" in r.stdout


def test_security_audit_and_upgrade(env):
    tmp, key, root, publish_, S, plan, pk = env
    publish_(pk("openssl", "3.5.1-1"), pk("curl", "8.14.0-1"), pk("libfoo", "1-1"))
    S("install", "openssl", "curl", "libfoo")
    publish_(pk("openssl", "3.5.2-1", security=["CVE-2025-9230", "CVE-2025-9231"]),
             pk("curl", "8.15.0-1"), pk("libfoo", "2-1", security=["CVE-2025-0001"]))
    S("sync")
    r = syp("-r", root, "audit", check=False)
    assert r.returncode == 1 and "openssl 3.5.1-1 -> 3.5.2-1: CVE-2025-9230 CVE-2025-9231" in r.stdout
    audit = json.loads(syp("-r", root, "audit", "--json", check=False).stdout)
    assert {a["name"] for a in audit} == {"openssl", "libfoo"}
    assert names(plan("upgrade", "--security", "--no-sync")) == {"openssl": "3.5.2-1", "libfoo": "2-1"}
    S("upgrade", "--security", "--no-sync")
    assert "Keine bekannten Sicherheitslücken" in syp("-r", root, "audit").stdout
    assert json.loads(syp("-r", root, "info", "curl", "--json").stdout)["version"] == "8.14.0-1"


def test_fetch_retry_and_sums_keep_layout(tmp_path):
    import http.server
    import threading
    hits = {"flaky": 0, "missing": 0}
    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass
        def do_GET(self):
            name = self.path.strip("/").split(".")[0]
            hits[name] = hits.get(name, 0) + 1
            if name == "flaky" and hits[name] == 1:
                self.send_error(503)
            elif name == "missing":
                self.send_error(404)
            else:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"quelle\n")
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    rec = tmp_path / "SYPBUILD.toml"
    text = ('[package]\nname = "f"\nversion = "1-1"\n\n[[source]]\nurl = "%s/flaky.txt"\nsha256 = "TODO"\n'
            'extract = false\n\n[build]\n'
            "package = '''\n  true \\\n    --eingerueckt\n'''\n" % base)   # eingerückte Zeilen (früher: free() auf verschobenen Zeiger)
    rec.write_text(text)
    env = dict(os.environ, SYP_SRCCACHE=str(tmp_path / "cache"), SYP_FETCH_RETRIES="3",
               NO_PROXY="127.0.0.1", no_proxy="127.0.0.1")
    r = syp("sums", rec, env=env)
    assert hits["flaky"] == 2 and "Versuch 1/3" in r.stderr
    import hashlib
    assert rec.read_text() == text.replace("TODO", hashlib.sha256(b"quelle\n").hexdigest())  # Leerzeilen bleiben
    rec.write_text(text.replace("flaky", "missing"))
    r = syp("sums", rec, env=env, check=False)
    assert r.returncode != 0 and hits["missing"] == 1                # 404: kein sinnloser Neuversuch
    srv.shutdown()


def test_fetch_stdout_is_only_paths(tmp_path):
    """Bootstrap liest die Pfade aus stdout von "syp fetch" – Fortschritt darf dort nicht landen."""
    src = tmp_path / "quelle.txt"
    src.write_text("inhalt\n")
    import hashlib
    rec = tmp_path / "SYPBUILD.toml"
    digest = hashlib.sha256(src.read_bytes()).hexdigest()
    rec.write_text('[package]\nname = "f"\nversion = "1-1"\n\n[[source]]\n'
                   f'url = "file://{src}"\nsha256 = "{digest}"\n')
    r = syp("fetch", rec, "-o", tmp_path / "out")
    assert r.stdout.splitlines() == [str(tmp_path / "out/quelle.txt")]
    assert "Lade" in r.stderr


def test_distro_recipe_graph_has_no_cycles(tmp_path):
    r = syp("buildall", ROOT / "distro/recipes", "--repo", tmp_path / "repo", "-n")
    assert "Abhängigkeitszyklen" not in r.stdout + r.stderr


@pytest.mark.skipif(os.geteuid() != 0, reason="chroot/mount brauchen Root")
def test_buildall_root_installs_without_syp_in_path(tmp_path):
    """buildall --root spielt Gebautes in die Bauumgebung ein und ruft dafür sich selbst auf (kein PATH nötig)."""
    r = tmp_path / "chr"
    for d in ("usr", "etc/alternatives", "tmp", "var/tmp"):
        (r / d).mkdir(parents=True)
    for link in ("bin", "sbin", "lib", "lib64"):
        (r / link).symlink_to(f"usr/{link}")
    recs = tmp_path / "recipes"
    for name, dep in (("a", None), ("b", "a")):
        (recs / name).mkdir(parents=True)
        (recs / name / "SYPBUILD.toml").write_text(
            f'[package]\nname = "{name}"\nversion = "1-1"\narch = "any"\n'
            + (f'depends = ["{dep}"]\n' if dep else "")
            + f"[build]\npackage = 'install -Dm644 /dev/null \"$DESTDIR/etc/{name}.txt\"'\n")
    key, _ = keygen(tmp_path)
    mounts = [("/usr", r / "usr"), ("/etc/alternatives", r / "etc/alternatives")]
    try:
        for src, dst in mounts:
            if subprocess.run(["mount", "--bind", "-o", "ro", src, str(dst)]).returncode:
                pytest.skip("bind-mount nicht möglich")
        out = run(SYP, "buildall", recs, "--repo", tmp_path / "repo", "--root", r, "-k", key, "--name", "t",
                  env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)})
    finally:
        for _, dst in reversed(mounts):
            subprocess.run(["umount", "-l", str(dst)])
    assert "gebaut 2" in out.stdout
    assert (r / "etc/a.txt").exists() and (r / "etc/b.txt").exists()
    # Fortsetzen: gebaut, aber nicht (mehr) installiert -> wird nachinstalliert statt nur übersprungen
    run(SYP, "-r", r, "-y", "remove", "b")
    out = run(SYP, "buildall", recs, "--repo", tmp_path / "repo", "--root", r, "-k", key, "--name", "t",
              env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)})
    assert "vorhanden 2" in out.stdout and (r / "etc/b.txt").exists()


def test_build_extracts_non_ascii_names_in_c_locale(tmp_path):
    """gcc-Quellen enthalten UTF-8-Dateinamen; der Bootstrap läuft mit LC_ALL=POSIX."""
    import hashlib
    import io
    tar = tmp_path / "q-1.tar.gz"
    with tarfile.open(tar, "w:gz", format=tarfile.PAX_FORMAT) as tf:
        for name in ("q-1/doc/Grüße.txt", "q-1/a.txt"):
            ti = tarfile.TarInfo(name)
            ti.size = 2
            tf.addfile(ti, io.BytesIO(b"x\n"))
    digest = hashlib.sha256(tar.read_bytes()).hexdigest()
    (tmp_path / "SYPBUILD.toml").write_text(
        f'[package]\nname = "q"\nversion = "1-1"\narch = "any"\n[[source]]\nurl = "file://{tar}"\nsha256 = "{digest}"\n'
        "[build]\npackage = 'test -f \"$S/doc/Grüße.txt\"; install -Dm644 \"$S/a.txt\" \"$DESTDIR/usr/share/q/a.txt\"'\n")
    run(SYP, "build", tmp_path / "SYPBUILD.toml", "-o", tmp_path / "out",
        env=dict(os.environ, LC_ALL="C", LANG="C", HOME=str(tmp_path)))
