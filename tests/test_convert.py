from __future__ import annotations

import pytest

from syp import repo
from syp.cli import main as syp
from syp.crypto import SecretKey
from syp.package import SypPackage
from syp.repo import RepoPackage
from syp.package import PackageInfo
from syp_convert import Mapper, convert, detect
from syp_convert.common import norm_version
from syp_convert.cli import main as convert_cli
from syp_convert.elf import dynamic_info

from helpers import make_pkg, setup_root
from pkgfactory import ELF_SRC, make_arch, make_deb, make_rpm


def avail(*specs):
    out = []
    for s in specs:
        name, _, rest = s.partition(" ")
        prov = rest.split(",") if rest else []
        out.append(RepoPackage(PackageInfo(name=name, version="99-1", provides=prov), "core", "", "", 0))
    return out


REPO = avail("glibc", "gtk3", "python-numpy", "python-requests", "perl-foo-bar", "bash sh",
             "ffmpeg", "qt6-base")


@pytest.mark.parametrize("src,exp", [("1:2.1~rc1-3ubuntu1", "1:2.1rc1-3ubuntu1"),
                                     ("1.0~1", "1.0pre1"), ("1.0^20240101-2.fc40", "1.0.0.20240101-2.fc40"),
                                     ("2.0-1-2", "2.0_1-2")])
def test_norm_version(src, exp):
    assert norm_version(src) == exp


def test_elf_reader():
    needed, _ = dynamic_info(ELF_SRC)
    assert any(n.startswith("libc.so") for n in needed)


def test_deb(tmp_path):
    deb = make_deb(tmp_path)
    assert detect(deb) == "deb"
    out, rep = convert(deb, tmp_path / "o", Mapper(REPO))
    info = SypPackage(out).info
    assert info.name == "paintbox" and info.version == "1:2.1rc1-3ubuntu1"
    assert "glibc>=2.34" in info.depends
    assert "gtk3>=3.24" in info.depends
    assert "python-numpy" in info.depends
    assert info.license == "GPL-3.0-or-later"
    assert info.backup == ["etc/paintbox.conf"]
    assert info.categories == ["Graphics", "2DGraphics"]
    assert "ffmpeg" in info.optdepends
    assert [u["dep"] for u in rep.unresolved] == ["libfoo-missing1"]
    assert "debconf>=0.5" in rep.ignored
    assert "post_install" in info.scripts and info.origin["scripts_reviewed"] is False


def test_rpm(tmp_path):
    rpm = make_rpm(tmp_path)
    out, rep = convert(rpm, tmp_path / "o", Mapper(REPO))
    info = SypPackage(out).info
    files = [f["path"] for f in SypPackage(out).files]
    assert info.version == "3.0beta2-1.fc43"
    assert "usr/lib/libcolorlab.so.1" in files and not any("lib64" in f for f in files)
    assert "colour-lab=3.0" in info.provides
    assert {"glibc", "gtk3>=3.24", "sh", "python-requests", "perl-foo-bar"} <= set(info.depends)
    assert [u["dep"] for u in rep.unresolved] == ["nonexistent-lib"]
    assert info.backup == ["etc/colorlab.conf"]
    assert "post_upgrade" in info.scripts


def test_arch(tmp_path):
    out, rep = convert(make_arch(tmp_path), tmp_path / "o", Mapper(REPO))
    info = SypPackage(out).info
    assert info.depends[:2] == ["glibc", "qt6-base>=6.5"]
    assert info.provides[0] == "brushes=1.4"
    assert rep.optional_missing == ["krita"]
    assert "post_install" in info.scripts and rep.ok


def test_offline_mode_marks_unverified(tmp_path):
    _, rep = convert(make_deb(tmp_path), tmp_path / "o", Mapper(None))
    assert rep.unverified and not rep.unresolved


def test_convert_and_install_end_to_end(tmp_path, capsys):
    key = SecretKey.generate("convert")
    repo_dir = tmp_path / "repo"
    root = setup_root(tmp_path, repo_dir, key)
    base = [make_pkg(tmp_path, "glibc", "2.40-1", {"usr/lib/libc.so.6": "x"}, key, provides=["so:libc.so.6"]),
            make_pkg(tmp_path, "gtk3", "3.24.43-1", {"usr/lib/libgtk-3.so.0": "x"}, key),
            make_pkg(tmp_path, "python-numpy", "2.1-1", {"usr/lib/np": "x"}, key)]
    for p in base:
        repo.add_to_pool(repo_dir, p)
    repo.build_index(repo_dir, "core", "x86_64", key)
    assert syp(["-r", str(root), "sync"]) == 0
    deb = make_deb(tmp_path, depends="libc6 (>= 2.34), libgtk-3-0, python3-numpy")
    rc = convert_cli([str(deb), "-o", str(tmp_path / "conv"), "-r", str(root), "-k", str(tmp_path / "test.key"),
                      "--strict"])
    assert rc == 0, capsys.readouterr().out
    pkg = tmp_path / "conv" / "paintbox-1~2.1rc1-3ubuntu1-x86_64.syp"
    assert syp(["-r", str(root), "-y", "install", str(pkg)]) == 0
    assert (root / "usr/bin/paintbox").exists()
    out = capsys.readouterr().out
    assert "post_install von paintbox (konvertiert, ungeprüft) übersprungen" in out
