"""Software-Center-Backend und Lizenzbericht gegen ein echtes syp-System."""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

from helpers import ROOT, SYP, keygen, make_pkg, publish, setup_root, syp


@pytest.fixture
def system(tmp_path, monkeypatch):
    monkeypatch.setenv("SYP_BIN", SYP)
    key, pub = keygen(tmp_path)
    repo = tmp_path / "repo"
    publish(repo, key,
            make_pkg(tmp_path, "krita", "5.2.9-1", key=key, license="GPL-3.0-or-later",
                     categories=["Graphics", "RasterGraphics"], summary="Malen"),
            make_pkg(tmp_path, "ardour", "8.12-1", key=key, license="GPL-2.0-or-later", categories=["Audio"]),
            make_pkg(tmp_path, "libfoo", "1-1", key=key, license="MIT"),
            make_pkg(tmp_path, "closed", "1-1", key=key, license="LicenseRef-Proprietary", categories=["Utility"]))
    root = setup_root(tmp_path, repo, pub)
    syp("-r", root, "sync")
    syp("-r", root, "-y", "install", "krita")
    publish(repo, key, make_pkg(tmp_path, "krita", "5.3.0-1", key=key, license="GPL-3.0-or-later",
                                categories=["Graphics"], summary="Malen"))
    syp("-r", root, "sync")
    return root


def test_store_backend(system):
    sys.path.insert(0, str(ROOT / "src"))
    import importlib

    import syp_store.backend as b
    importlib.reload(b)
    be = b.Backend(str(system))
    items = {i.name: i for i in be.items()}
    assert set(items) == {"krita", "ardour", "closed"}          # libfoo ohne Kategorie = kein App-Eintrag
    assert items["krita"].installed == "5.2.9-1" and items["krita"].update
    assert items["krita"].section() == "Grafik & Illustration"
    assert items["ardour"].section() == "Audio & Musik"
    ups, err = be.updates()
    assert ups == [("krita", "5.2.9-1", "5.3.0-1")] and err is None
    assert be.history()[0]["cmd"].endswith("install krita")
    assert be.command("install", "x")[:2] == ["pkexec", SYP]


def test_license_report(system):
    env = dict(os.environ, SYP_BIN=SYP)
    r = subprocess.run([sys.executable, str(ROOT / "tools/license-report.py"), "--root", str(system), "--repo"],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 1                                      # proprietäres Paket wird gemeldet
    assert "| closed | 1-1 | LicenseRef-Proprietary | native | ⚠ |" in r.stdout
    assert "| krita | 5.3.0-1 | GPL-3.0-or-later | native | ✓ |" in r.stdout
