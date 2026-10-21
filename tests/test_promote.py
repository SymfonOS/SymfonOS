"""Kanal testing → stable (tools/promote.py): Wartezeit, Sicherheits-Schnellspur, Sperren,
Abhängigkeitsprüfung und Signaturpflicht."""
import json
import os
import subprocess
import sys

from helpers import ROOT, SYP, keygen, make_pkg, syp

PROMOTE = ROOT / "tools/promote.py"
DAY = 86400.0


def promote(testing, stable, pub, key, now, *extra):
    r = subprocess.run([sys.executable, PROMOTE, "--testing", testing, "--stable", stable, "--pub", pub,
                        "-k", key, "--name", "stable", "--now", str(now), *extra],
                       capture_output=True, text=True, env=dict(os.environ, SYP_BIN=SYP))
    assert r.returncode == 0, r.stderr
    return r.stdout


def names(repo, pub):
    idx = json.loads(syp("repo-show", repo, "--pub", pub).stdout)
    return sorted(f"{e['info']['name']} {e['info']['version']}" for e in idx["packages"])


def test_promotion_rules(tmp_path):
    key, pub = keygen(tmp_path)
    testing, stable = tmp_path / "testing", tmp_path / "stable"
    p = lambda n, v, **kw: make_pkg(tmp_path, n, v, key=key, **kw)
    syp("repo-add", stable, p("libfoo", "1-1"), "--name", "stable", "-k", key)
    syp("repo-add", testing, p("libfoo", "2-1", provides=["so:libfoo.so.2"]),
        p("app", "1-1", depends=["so:libfoo.so.2"]), p("held", "1-1"),
        p("openssl", "3.5.2-1", security=["CVE-2025-9230"]), "--name", "testing", "-k", key)
    (testing / "hold").write_text("# Fehlerbericht offen\nheld\n")
    t0 = 1_800_000_000.0
    out = promote(testing, stable, pub, key, t0)                       # Tag 0: nur das Sicherheitsupdate
    assert "→ stable: openssl 3.5.2-1  [Sicherheit]" in out and "app 1-1: erst 0.0 von 7 Tagen" in out
    assert names(stable, pub) == ["libfoo 1-1", "openssl 3.5.2-1"]
    # Tag 8: libfoo 2 und app gemeinsam (app braucht so:libfoo.so.2), held bleibt gesperrt
    out = promote(testing, stable, pub, key, t0 + 8 * DAY)
    assert "held 1-1: gesperrt (hold)" in out
    assert names(stable, pub) == ["app 1-1", "libfoo 1-1", "libfoo 2-1", "openssl 3.5.2-1"]
    # Ohne den Anbieter in testing würde app stable brechen
    stable2, testing2 = tmp_path / "stable2", tmp_path / "testing2"
    syp("repo-add", stable2, p("libfoo", "1-1"), "--name", "stable", "-k", key)
    syp("repo-add", testing2, p("app2", "1-1", depends=["libfoo>=2"]), "--name", "testing", "-k", key)
    promote(testing2, stable2, pub, key, t0)
    out = promote(testing2, stable2, pub, key, t0 + 8 * DAY)
    assert "app2 1-1: Abhängigkeit »libfoo>=2« in stable nicht erfüllbar" in out
    assert names(stable2, pub) == ["libfoo 1-1"]


def test_promotion_requires_valid_signature(tmp_path):
    key, pub = keygen(tmp_path)
    other_key, other_pub = keygen(tmp_path / "x")
    testing = tmp_path / "testing"
    syp("repo-add", testing, make_pkg(tmp_path, "a", "1-1", key=key), "--name", "testing", "-k", key)
    r = subprocess.run([sys.executable, PROMOTE, "--testing", testing, "--stable", tmp_path / "s",
                        "--pub", other_pub, "-k", key], capture_output=True, text=True, env=dict(os.environ, SYP_BIN=SYP))
    assert r.returncode != 0 and "Signatur" in r.stderr
