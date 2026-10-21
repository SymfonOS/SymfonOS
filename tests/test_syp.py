from __future__ import annotations

import json
import tarfile

import pytest

from syp import repo
from syp.cli import main
from syp.crypto import Keyring, SecretKey, SignatureError
from syp.deps import Atom
from syp.package import AtpPackage, PackageError, PackageInfo
from syp.repo import RepoPackage
from syp.solver import Solver, SolverError
from syp.version import vercmp

from helpers import make_pkg, setup_root


@pytest.mark.parametrize("a,b,r", [
    ("1.0", "1.0", 0), ("1.0", "1.1", -1), ("1.10", "1.9", 1), ("1.0a", "1.0", -1),
    ("1.0", "1.0.1", -1), ("1:1.0", "2.0", 1), ("1.0-2", "1.0-10", -1), ("1.0-1", "1.0", 0),
    ("2.0rc1", "2.0", -1), ("1.0.0", "1.0", 1), ("1.a", "1.1", -1),
])
def test_vercmp(a, b, r):
    assert vercmp(a, b) == r


def test_atom_versions():
    assert Atom.parse("foo>=1.2").matches_version("1.3-1")
    assert not Atom.parse("foo<1.2").matches_version("1.2")
    assert not Atom.parse("foo>=1").matches_version(None)


def rp(name, ver, **kw):
    return RepoPackage(PackageInfo(name=name, version=ver, **kw), "core", "", "", 0)


class Inst:
    def __init__(self, name, ver, reason="explicit", **kw):
        self.info = PackageInfo(name=name, version=ver, **kw)
        self.reason = reason

    name = property(lambda s: s.info.name)
    version = property(lambda s: s.info.version)


def test_solver_basic_and_provides():
    avail = [rp("app", "1-1", depends=["libx>=2", "sh"]), rp("libx", "1-1"), rp("libx", "2-1"),
             rp("bash", "5-1", provides=["sh"])]
    plan = Solver(avail, {}).install(["app"])
    got = {p.name: p.version for p in plan.install}
    assert got == {"app": "1-1", "libx": "2-1", "bash": "5-1"}
    assert plan.reasons["app"] == "explicit" and plan.reasons["libx"] == "dep"


def test_solver_backtracks_over_conflict():
    avail = [rp("app", "1-1", depends=["tool"]), rp("tool-a", "2-1", provides=["tool"],
             conflicts=["base"]), rp("tool-b", "1-1", provides=["tool"])]
    plan = Solver(avail, {"base": Inst("base", "1-1")}).install(["app"])
    assert {p.name for p in plan.install} == {"app", "tool-b"}


def test_solver_alternatives_and_missing():
    avail = [rp("app", "1-1", depends=["x | y"]), rp("y", "1-1")]
    assert {p.name for p in Solver(avail, {}).install(["app"]).install} == {"app", "y"}
    with pytest.raises(SolverError, match="Nichts stellt »nope«"):
        Solver([rp("app", "1-1", depends=["nope"])], {}).install(["app"])


def test_solver_upgrade_and_broken_revdeps():
    inst = {"lib": Inst("lib", "1-1", "dep"), "app": Inst("app", "1-1", depends=["lib<2"])}
    avail = [rp("lib", "2-1"), rp("lib", "1-1"), rp("app", "1-1", depends=["lib<2"]),
             rp("app", "2-1", depends=["lib>=2"])]
    plan = Solver(avail, inst).upgrade()
    assert {p.name: p.version for p in plan.install} == {"lib": "2-1", "app": "2-1"}


def test_solver_remove_and_orphans():
    inst = {"lib": Inst("lib", "1", "dep"), "app": Inst("app", "1", depends=["lib"]),
            "old": Inst("old", "1", "dep")}
    with pytest.raises(SolverError, match="app benötigt"):
        Solver([], inst).remove(["lib"])
    assert Solver([], inst).remove(["lib"], cascade=True).remove == ["app", "lib"]
    assert Solver([], inst).orphans() == ["old"]


def test_package_roundtrip_and_signature(tmp_path):
    key = SecretKey.generate("test")
    p = make_pkg(tmp_path, "hello", "1.0-1", signer=key)
    pkg = AtpPackage(p)
    kr = Keyring()
    with pytest.raises(SignatureError):
        pkg.verify(kr)
    key.save(tmp_path / "k.key", tmp_path / "k.pub")
    kr = Keyring.from_dir(tmp_path)
    assert pkg.verify(kr).id == key.id
    pkg.extract(tmp_path / "x")
    assert (tmp_path / "x/usr/share/hello/VERSION").read_text() == "1.0-1"


def test_tampered_package_rejected(tmp_path):
    key = SecretKey.generate()
    key.save(tmp_path / "k.key", tmp_path / "k.pub")
    p = make_pkg(tmp_path, "hello", "1.0-1", signer=key)
    with tarfile.open(p) as tf:
        members = {m.name: tf.extractfile(m).read() for m in tf.getmembers()}
    info = json.loads(members[".SYPINFO.json"])
    info["depends"] = ["evil"]
    members[".SYPINFO.json"] = json.dumps(info).encode()
    bad = tmp_path / "bad.syp"
    import io
    with tarfile.open(bad, "w") as tf:
        for n in (".SYPINFO.json", ".FILES.json", "data.tar.zst", ".SIGNATURE.json"):
            ti = tarfile.TarInfo(n)
            ti.size = len(members[n])
            tf.addfile(ti, io.BytesIO(members[n]))
    with pytest.raises(SignatureError):
        AtpPackage(bad).verify(Keyring.from_dir(tmp_path))


def test_invalid_name():
    with pytest.raises(PackageError):
        PackageInfo(name="Bad Name", version="1").validate()


@pytest.fixture
def env(tmp_path):
    key = SecretKey.generate("symfonOS Test")
    repo_dir = tmp_path / "repo"
    root = setup_root(tmp_path, repo_dir, key)

    def publish(*pkgs):
        for p in pkgs:
            repo.add_to_pool(repo_dir, p)
        repo.build_index(repo_dir, "core", "x86_64", key)
        assert main(["-r", str(root), "sync"]) == 0
    return tmp_path, key, root, publish


def test_end_to_end(env, capsys):
    tmp, key, root, publish = env
    R = ["-r", str(root), "-y"]
    publish(
        make_pkg(tmp, "libcolor", "1.0-1", {"usr/lib/libcolor.so.1": "v1"}, key,
                 provides=["so:libcolor.so.1"]),
        make_pkg(tmp, "paint", "1.0-1", {"usr/bin/paint": "paint1", "etc/paint.conf": "a=1\n"},
                 key, depends=["so:libcolor.so.1"], backup=["etc/paint.conf"]),
    )
    assert main(R + ["install", "paint"]) == 0
    assert (root / "usr/bin/paint").read_text() == "paint1"
    assert main(R + ["owns", str(root / "usr/lib/libcolor.so.1")]) in (0, 1)

    # Nutzer ändert Konfig -> Upgrade schreibt .sypnew
    (root / "etc/paint.conf").write_text("a=2\n")
    publish(make_pkg(tmp, "paint", "2.0-1", {"usr/bin/paint": "paint2", "etc/paint.conf": "a=1\nb=1\n"},
                     key, depends=["so:libcolor.so.1"], backup=["etc/paint.conf"]))
    assert main(R + ["upgrade"]) == 0
    assert (root / "usr/bin/paint").read_text() == "paint2"
    assert (root / "etc/paint.conf").read_text() == "a=2\n"
    assert (root / "etc/paint.conf.sypnew").exists()
    assert main(R + ["verify"]) == 0

    # Rollback auf Stand vor dem Upgrade
    capsys.readouterr()
    assert main(R + ["history"]) == 0
    assert "upgrade" in capsys.readouterr().out
    assert main(R + ["rollback"]) == 0
    assert (root / "usr/bin/paint").read_text() == "paint1"

    # Entfernen + verwaiste Abhängigkeiten
    assert main(R + ["remove", "libcolor"]) == 1      # paint hängt davon ab
    assert main(R + ["remove", "paint"]) == 0
    assert not (root / "usr/bin/paint").exists()
    assert (root / "etc/paint.conf.sypsave").exists()  # geänderte Konfig gesichert
    assert main(R + ["autoremove"]) == 0
    assert not (root / "usr/lib/libcolor.so.1").exists()


def test_file_conflict(env):
    tmp, key, root, publish = env
    R = ["-r", str(root), "-y"]
    publish(make_pkg(tmp, "a", "1-1", {"usr/bin/x": "a"}, key),
            make_pkg(tmp, "b", "1-1", {"usr/bin/x": "b"}, key))
    assert main(R + ["install", "a"]) == 0
    assert main(R + ["install", "b"]) == 1
    assert (root / "usr/bin/x").read_text() == "a"


def test_unsigned_index_rejected(tmp_path):
    key = SecretKey.generate()
    repo_dir = tmp_path / "repo"
    root = setup_root(tmp_path, repo_dir, key)
    repo.add_to_pool(repo_dir, make_pkg(tmp_path, "a", "1-1"))
    repo.build_index(repo_dir, "core", "x86_64", SecretKey.generate())  # fremder Schlüssel
    assert main(["-r", str(root), "sync"]) == 1


def test_build_recipe(tmp_path):
    (tmp_path / "hello.sh").write_text("#!/bin/sh\necho Hallo\n")
    (tmp_path / "SYPBUILD.toml").write_text('''
[package]
name = "hello"
version = "1.0-1"
arch = "any"
license = "MIT"
[[source]]
path = "hello.sh"
[build]
package = 'install -Dm755 hello.sh "$DESTDIR/usr/bin/hello"'
''')
    assert main(["build", str(tmp_path / "SYPBUILD.toml"), "-o", str(tmp_path / "o")]) == 0
    pkg = AtpPackage(tmp_path / "o/hello-1.0-1-any.syp")
    assert [f["path"] for f in pkg.files] == ["usr", "usr/bin", "usr/bin/hello"]
    assert pkg.files[-1]["mode"] == 0o755


def test_pacman_import(tmp_path):
    from syp.db import Database
    from syp.pacman_import import import_db
    root = tmp_path
    d = root / "var/lib/pacman/local/bash-5.2-1"
    d.mkdir(parents=True)
    (d / "desc").write_text("%NAME%\nbash\n\n%VERSION%\n5.2-1\n\n%PROVIDES%\nsh\n\n%LICENSE%\nGPL-3.0-or-later\n")
    (d / "files").write_text("%FILES%\nusr/\nusr/bin/\nusr/bin/bash\n")
    (root / "usr/bin").mkdir(parents=True)
    (root / "usr/bin/bash").write_text("x")
    db = Database(root / "db.sqlite")
    assert import_db(root, db, log=lambda *_: None) == 1
    assert db.owners("usr/bin/bash") == ["bash"]
    assert db.get("bash").info.provides == ["sh"]
