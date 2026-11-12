"""Lizenz-Gate: jedes Rezept nennt eine freie Lizenz (SPDX-Ausdruck) oder eine ausdrücklich zugelassene
weitergebbare (linux-firmware). Ohne dieses Gate käme ein unfreies Paket unbemerkt ins Repo."""
import importlib.util
import re

from helpers import ROOT

spec = importlib.util.spec_from_file_location("license_report", ROOT / "tools/license-report.py")
lr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lr)


def test_spdx_evaluation():
    assert lr.is_free("AFL-2.1 OR GPL-2.0-or-later")
    assert lr.is_free("GPL-3.0-or-later WITH GCC-exception-3.1")
    assert lr.is_free("(MIT OR LicenseRef-Proprietary) AND BSD-3-Clause")
    assert not lr.is_free("MIT AND LicenseRef-Proprietary")
    assert not lr.is_free("custom") and not lr.is_free("") and not lr.is_free("MITx")
    assert not lr.is_free("LicenseRef-linux-firmware")
    assert lr.is_free("LicenseRef-linux-firmware", redistributable=True)


def test_every_recipe_has_free_license():
    bad = []
    for f in sorted((ROOT / "distro/recipes").glob("*/SYPBUILD.toml")):
        m = re.search(r'^license\s*=\s*"([^"]*)"', f.read_text(), re.M)
        lic = m.group(1) if m else ""
        if not lr.is_free(lic, redistributable=True):
            bad.append(f"{f.parent.name}: {lic or '(keine Lizenz)'}")
    assert not bad, "Rezepte ohne freie Lizenz:\n" + "\n".join(bad)
