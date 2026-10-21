import json
import shutil
import subprocess
from pathlib import Path

import pytest

EXAM = Path(__file__).resolve().parents[1] / "exam/symfonos-exam"


def render(*args):
    return subprocess.run([str(EXAM), "render", *args], capture_output=True, text=True, check=True).stdout


def test_policy_allowlist():
    out = render("--url", "https://127.0.0.1/quiz", "--allow", "127.0.0.2")
    pol = json.loads(out[out.index("{", out.index("# Firefox")):])["policies"]
    assert pol["WebsiteFilter"]["Block"] == ["<all_urls>"]
    assert "*://127.0.0.1/*" in pol["WebsiteFilter"]["Exceptions"]
    assert pol["DisableDeveloperTools"] is True


def test_nft_ruleset_valid(tmp_path):
    out = render("--allow", "127.0.0.1")
    nft = out[out.index("table inet"):out.index("# Firefox")]
    assert "policy drop" in nft and "127.0.0.1" in nft
    if shutil.which("nft") is None:
        pytest.skip("nft nicht installiert")
    f = tmp_path / "r.nft"
    f.write_text(nft)
    r = subprocess.run(["nft", "-c", "-f", str(f)], capture_output=True, text=True)
    if r.returncode and "Operation not permitted" in r.stderr:
        pytest.skip("keine Rechte für nft -c")
    assert r.returncode == 0, r.stderr


def test_requires_root_for_start():
    r = subprocess.run([str(EXAM), "status"], capture_output=True, text=True)
    assert r.returncode == 0 and "nicht aktiv" in r.stdout
