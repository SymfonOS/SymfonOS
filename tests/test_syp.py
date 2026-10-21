"""Tests für den Paketmanager syp (C)."""
from __future__ import annotations

import io
import json
import tarfile

import pytest

from helpers import keygen, make_pkg, publish, setup_root, syp


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
    out = syp("buildall", rec, "--repo", repo, "-k", key).stdout
    order = [line.split()[2] for line in out.splitlines() if line.startswith("==> [")]
    assert order.index("zlib") < order.index("libpng") < order.index("app") and order.index("bash") < order.index("app")
    out2 = syp("buildall", rec, "--repo", repo, "-k", key).stdout
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
