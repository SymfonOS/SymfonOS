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
    # Umgekehrt: libbar 2 würde »libbar<2« eines Pakets brechen, das schon in stable liegt
    stable3, testing3 = tmp_path / "stable3", tmp_path / "testing3"
    syp("repo-add", stable3, p("libbar", "1-1"), p("old", "1-1", depends=["libbar<2"]), "--name", "stable", "-k", key)
    syp("repo-add", testing3, p("libbar", "2-1"), p("other", "1-1"), "--name", "testing", "-k", key)
    promote(testing3, stable3, pub, key, t0)
    out = promote(testing3, stable3, pub, key, t0 + 8 * DAY)
    assert "libbar 2-1: bricht old 1-1: libbar<2" in out
    assert names(stable3, pub) == ["libbar 1-1", "old 1-1", "other 1-1"]
    # … bis eine neue Version von old mitkommt
    syp("repo-add", testing3, p("old", "2-1", depends=["libbar>=2"]), "--name", "testing", "-k", key)
    promote(testing3, stable3, pub, key, t0 + 8 * DAY)
    out = promote(testing3, stable3, pub, key, t0 + 16 * DAY)
    assert "→ stable: libbar 2-1" in out and "→ stable: old 2-1" in out


def test_promotion_requires_valid_signature(tmp_path):
    key, pub = keygen(tmp_path)
    other_key, other_pub = keygen(tmp_path / "x")
    testing = tmp_path / "testing"
    syp("repo-add", testing, make_pkg(tmp_path, "a", "1-1", key=key), "--name", "testing", "-k", key)
    r = subprocess.run([sys.executable, PROMOTE, "--testing", testing, "--stable", tmp_path / "s",
                        "--pub", other_pub, "-k", key], capture_output=True, text=True, env=dict(os.environ, SYP_BIN=SYP))
    assert r.returncode != 0 and "Signatur" in r.stderr


FAKE_GH = r'''#!/usr/bin/env python3
"""Ersatz für »gh release …«: Releases sind Ordner unter $FAKE_GH_DIR/<tag>."""
import fnmatch, os, shutil, sys
base = os.environ["FAKE_GH_DIR"]
a = [x for x in sys.argv[1:]]
while "--repo" in a:
    i = a.index("--repo"); del a[i:i + 2]
assert a[0] == "release", a
cmd, tag, rest = a[1], a[2], a[3:]
d = os.path.join(base, tag)
if cmd == "view":
    if not os.path.isdir(d):
        sys.exit(1)
    if "--json" in rest:
        print("\n".join(sorted(os.listdir(d))))
elif cmd == "create":
    os.makedirs(d, exist_ok=True)
elif cmd == "download":
    pats, out = [], "."
    while rest:
        x = rest.pop(0)
        if x == "--pattern": pats.append(rest.pop(0))
        elif x == "--dir": out = rest.pop(0)
    hits = [f for f in (os.listdir(d) if os.path.isdir(d) else []) if any(fnmatch.fnmatch(f, p) for p in pats)]
    if not hits:
        sys.exit("no assets match")
    os.makedirs(out, exist_ok=True)
    for f in hits:
        shutil.copy(os.path.join(d, f), out)
elif cmd == "upload":
    for f in rest:
        if not f.startswith("--"):
            shutil.copy(f, d)
elif cmd == "delete-asset":
    os.remove(os.path.join(d, rest[0]))
else:
    sys.exit(f"unbekannt: {a}")
'''


def test_promote_release_ci(tmp_path):
    """tools/promote-release.sh: testing veröffentlichen, Wartezeit über Läufe hinweg, stable schrumpft nie."""
    key, pub = keygen(tmp_path)
    bindir, rel, src = tmp_path / "bin", tmp_path / "releases", tmp_path / "out/repo/symfonos"
    bindir.mkdir()
    (bindir / "gh").write_text(FAKE_GH)
    (bindir / "gh").chmod(0o755)
    env = dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}", FAKE_GH_DIR=str(rel), GITHUB_REPOSITORY="t/t")
    p = lambda n, v, **kw: make_pkg(tmp_path, n, v, key=key, **kw)

    def run_ci(**extra):
        r = subprocess.run([ROOT / "tools/promote-release.sh", src, key, pub], capture_output=True, text=True,
                           env=dict(env, **extra))
        assert r.returncode == 0, r.stdout + r.stderr
        return r.stdout

    syp("repo-add", src, p("libfoo", "1-1"), p("app", "1-1", depends=["libfoo"]), "--name", "symfonos", "-k", key)
    out = run_ci()                                   # erster Lauf: nur testing, Wartezeit beginnt
    assert "stable ist leer" in out
    assert {"index.json.zst", "promotion.json"} <= set(os.listdir(rel / "repo-x86_64-testing"))
    assert not (rel / "repo-x86_64").exists()
    out = run_ci(MIN_DAYS="0")                       # Wartezeit vorbei: beide nach stable
    assert sorted(f for f in os.listdir(rel / "repo-x86_64") if f.endswith(".syp")) == \
        ["app-1-1-x86_64.syp", "libfoo-1-1-x86_64.syp"]
    # neue Version gebaut, alte Datei bleibt im lokalen Pool; app ist lokal verschwunden (muss vom Release kommen)
    syp("repo-add", src, p("libfoo", "2-1"), "--name", "symfonos", "-k", key)
    for f in src.rglob("app-1-1-*.syp"):
        f.unlink()
    syp("repo-add", src, "--name", "symfonos", "-k", key)
    out = run_ci()                                   # libfoo 2-1 wartet noch
    assert "libfoo 2-1: erst 0.0 von 7 Tagen" in out and "(1 vom Release geladen)" in out
    out = run_ci(MIN_DAYS="0")
    assert sorted(f for f in os.listdir(rel / "repo-x86_64") if f.endswith(".syp")) == \
        ["app-1-1-x86_64.syp", "libfoo-2-1-x86_64.syp"]
    assert sorted(f for f in os.listdir(rel / "repo-x86_64-testing") if f.endswith(".syp")) == ["libfoo-2-1-x86_64.syp"]
    assert names(rel / "repo-x86_64", pub) == ["app 1-1", "libfoo 2-1"]
