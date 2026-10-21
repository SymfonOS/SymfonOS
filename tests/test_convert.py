"""Tests für syp-convert (C): echte .deb-, .rpm- und Arch-Pakete aus pkgfactory."""
from __future__ import annotations

import json
import tarfile

import pytest

from helpers import CONVERT, keygen, make_pkg, publish, run, setup_root, syp
from pkgfactory import make_arch, make_deb, make_rpm

AVAILABLE = [("glibc", []), ("gtk3", []), ("python-numpy", []), ("python-requests", []),
             ("perl-foo-bar", []), ("bash", ["sh"]), ("ffmpeg", []), ("qt6-base", [])]


@pytest.fixture
def mapped_root(tmp_path):
    """System mit synchronisiertem Repo, dessen Pakete die Mapping-Ziele bereitstellen."""
    key, pub = keygen(tmp_path)
    repo = tmp_path / "repo"
    publish(repo, key, *[make_pkg(tmp_path, n, "99-1", key=key, provides=p) for n, p in AVAILABLE])
    root = setup_root(tmp_path, repo, pub)
    syp("-r", root, "sync")
    return root


def convert(pkg, out, *extra):
    r = run(CONVERT, pkg, "-o", out, "--json", *extra)
    return json.loads(r.stdout)[0]


def pkginfo(path):
    with tarfile.open(path) as tf:
        return (json.loads(tf.extractfile(".SYPINFO.json").read()),
                [f["path"] for f in json.loads(tf.extractfile(".FILES.json").read())])


def test_deb(tmp_path, mapped_root):
    rep = convert(make_deb(tmp_path), tmp_path / "o", "-r", mapped_root)
    info, files = pkginfo(rep["output"])
    assert info["name"] == "paintbox" and info["version"] == "1:2.1rc1-3ubuntu1"
    assert "glibc>=2.34" in info["depends"]
    assert "gtk3>=3.24" in info["depends"]
    assert "python-numpy" in info["depends"]
    assert info["license"] == "GPL-3.0-or-later"
    assert info["backup"] == ["etc/paintbox.conf"]
    assert info["categories"] == ["Graphics", "2DGraphics"]
    assert "ffmpeg" in info["optdepends"]
    assert [u["dep"] for u in rep["unresolved"]] == ["libfoo-missing1"]
    assert "debconf>=0.5" in rep["ignored"]
    assert "post_install" in info["scripts"] and info["origin"]["scripts_reviewed"] is False
    assert "usr/bin/paintbox" in files


def test_rpm(tmp_path, mapped_root):
    rep = convert(make_rpm(tmp_path), tmp_path / "o", "-r", mapped_root)
    info, files = pkginfo(rep["output"])
    assert info["version"] == "3.0beta2-1.fc43"
    assert "usr/lib/libcolorlab.so.1" in files and not any("lib64" in f for f in files)
    assert "colour-lab=3.0" in info["provides"]
    assert {"glibc", "gtk3>=3.24", "sh", "python-requests", "perl-foo-bar"} <= set(info["depends"])
    assert [u["dep"] for u in rep["unresolved"]] == ["nonexistent-lib"]
    assert info["backup"] == ["etc/colorlab.conf"]
    assert "post_upgrade" in info["scripts"]


def test_arch(tmp_path, mapped_root):
    rep = convert(make_arch(tmp_path), tmp_path / "o", "-r", mapped_root)
    info, _ = pkginfo(rep["output"])
    assert info["depends"][:2] == ["glibc", "qt6-base>=6.5"]
    assert info["provides"][0] == "brushes=1.4"
    assert rep["optional_missing"] == ["krita"]
    assert "post_install" in info["scripts"] and not rep["unresolved"]


def test_offline_mode_marks_unverified(tmp_path):
    rep = convert(make_deb(tmp_path), tmp_path / "o", "--offline", "-r", tmp_path / "none")
    assert rep["unverified"] and not rep["unresolved"]


def test_text_report_and_strict(tmp_path, mapped_root):
    r = run(CONVERT, make_deb(tmp_path), "-o", tmp_path / "o", "-r", mapped_root, "--strict", check=False)
    assert r.returncode == 2
    assert "✗ FEHLT: libfoo-missing1" in r.stdout and "Fehlende Pakete" in r.stdout


def test_convert_and_install_end_to_end(tmp_path):
    key, pub = keygen(tmp_path)
    repo = tmp_path / "repo"
    publish(repo, key,
            make_pkg(tmp_path, "glibc", "2.40-1", {"usr/lib/libc.so.6": "x"}, key, provides=["so:libc.so.6"]),
            make_pkg(tmp_path, "gtk3", "3.24.43-1", {"usr/lib/libgtk-3.so.0": "x"}, key),
            make_pkg(tmp_path, "python-numpy", "2.1-1", {"usr/lib/np": "x"}, key))
    root = setup_root(tmp_path, repo, pub)
    syp("-r", root, "sync")
    deb = make_deb(tmp_path, depends="libc6 (>= 2.34), libgtk-3-0, python3-numpy")
    run(CONVERT, deb, "-o", tmp_path / "conv", "-r", root, "-k", key, "--strict")
    pkg = tmp_path / "conv" / "paintbox-1~2.1rc1-3ubuntu1-x86_64.syp"
    out = syp("-r", root, "-y", "install", pkg).stdout
    assert (root / "usr/bin/paintbox").exists()
    assert "post_install von paintbox (konvertiert, ungeprüft) übersprungen" in out
