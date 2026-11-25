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
import fnmatch, hashlib, os, re, shutil, sys
base = os.environ["FAKE_GH_DIR"]
a = [x for x in sys.argv[1:]]
while "--repo" in a:
    i = a.index("--repo"); del a[i:i + 2]
if a[0] == "api":                       # nur, was publish-repo.sh braucht (Ausgabe wie nach -q)
    if m := re.search(r"/releases/tags/([^/?]+)$", a[-3] if "-q" in a else a[-1]):
        sys.exit(0 if os.path.isdir(os.path.join(base, m.group(1))) and print(m.group(1)) is None else 1)
    m = re.search(r"/releases/([^/?]+)/assets", next(x for x in a if "/assets" in x))
    d = os.path.join(base, m.group(1))
    for f in sorted(os.listdir(d)):
        print(f, "sha256:" + hashlib.sha256(open(os.path.join(d, f), "rb").read()).hexdigest(), sep="\t")
    sys.exit(0)
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
    fail = os.environ.get("FAKE_GH_FAIL")   # Datei mit Zahl: so viele Uploads scheitern (wie HTTP 500)
    n = int(open(fail).read() or 0) if fail else 0
    if n > 0:
        open(fail, "w").write(str(n - 1))
        sys.exit("HTTP 500")
    for f in rest:   # FAKE_GH_DROP: Upload solcher Dateien geht stillschweigend verloren
        if not f.startswith("--") and not fnmatch.fnmatch(os.path.basename(f), os.environ.get("FAKE_GH_DROP", "-")):
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
    # promotion.json überlebt das Veröffentlichen von testing (sonst begänne die Wartezeit jedes Mal neu)
    assert "noch keine promotion.json" not in out, out
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
    # Neubau mit gleicher Version (neue Bau-Revision): gleicher Dateiname, anderer Inhalt – das Release muss
    # die neue Datei bekommen, sonst passt die Prüfsumme im signierten Index nicht zur Datei
    for f in src.rglob("libfoo-2-1-*.syp"):
        f.unlink()
    syp("repo-add", src, p("libfoo", "2-1", files={"usr/share/libfoo/VERSION": "2-1 neu gebaut"}),
        "--name", "symfonos", "-k", key)
    out = run_ci(MIN_DAYS="0")
    idx = json.loads(syp("repo-show", rel / "repo-x86_64-testing", "--pub", pub).stdout)
    import hashlib
    for e in idx["packages"]:
        got = hashlib.sha256((rel / "repo-x86_64-testing" / os.path.basename(e["file"])).read_bytes()).hexdigest()
        assert got == e["sha256"], f"{e['file']}: Release-Datei passt nicht zum Index"
    # scheitern Uploads vorübergehend (GitHub-Störung mit HTTP 500), helfen weitere Versuche
    for f in src.rglob("libfoo-2-1-*.syp"):
        f.unlink()
    syp("repo-add", src, p("libfoo", "2-1", files={"usr/share/libfoo/VERSION": "2-1 nach Störung"}),
        "--name", "symfonos", "-k", key)
    fail = tmp_path / "fail"
    fail.write_text("3")
    run_ci(MIN_DAYS="0", FAKE_GH_FAIL=str(fail), PUBLISH_RETRY_SLEEP="0")
    assert fail.read_text() == "0"
    assert b"nach St" in subprocess.run(["zstd", "-dcq"], input=__import__("tarfile").open(
        rel / "repo-x86_64-testing/libfoo-2-1-x86_64.syp").extractfile("data.tar.zst").read(),
        capture_output=True).stdout
    # geht ein Upload verloren, merkt es die Gegenprobe nach dem Veröffentlichen
    for f in src.rglob("libfoo-2-1-*.syp"):
        f.unlink()
    syp("repo-add", src, p("libfoo", "2-1", files={"usr/share/libfoo/VERSION": "2-1 noch einmal"}),
        "--name", "symfonos", "-k", key)
    r = subprocess.run([ROOT / "tools/promote-release.sh", src, key, pub], capture_output=True, text=True,
                       env=dict(env, FAKE_GH_DROP="libfoo-*"))
    assert r.returncode == 1 and "passen nicht zum Index: libfoo-2-1-x86_64.syp" in r.stdout, r.stdout + r.stderr


def test_promotion_karma(tmp_path):
    """Karma: ab +3 genügt ein Tag, ab -2 bleibt das Paket in testing; --report nennt den Stand je Paket."""
    key, pub = keygen(tmp_path)
    testing, stable = tmp_path / "testing", tmp_path / "stable"
    p = lambda n, v, **kw: make_pkg(tmp_path, n, v, key=key, **kw)
    syp("repo-add", testing, p("gut", "1-1"), p("kaputt", "1-1"), p("still", "1-1"), "--name", "testing", "-k", key)
    karma, report = tmp_path / "karma.json", tmp_path / "report.json"
    karma.write_text(json.dumps({"gut 1-1": 3, "kaputt 1-1": -2}))
    t0 = 1_800_000_000.0
    promote(testing, stable, pub, key, t0)
    out = promote(testing, stable, pub, key, t0 + 1.5 * DAY, "--karma", karma, "--report", report)
    assert "→ stable: gut 1-1" in out and "still 1-1: erst 1.5 von 7 Tagen" in out
    assert "kaputt 1-1: von Testern abgelehnt (Karma -2)" in out
    r = json.loads(report.read_text())
    assert r["gut 1-1"]["status"] == "stable" and r["gut 1-1"]["karma"] == 3
    assert r["kaputt 1-1"]["status"] == "wartet" and "abgelehnt" in r["kaputt 1-1"]["reason"]
    # nach 8 Tagen kommt still, kaputt bleibt trotz Wartezeit draußen
    out = promote(testing, stable, pub, key, t0 + 8 * DAY, "--karma", karma)
    assert names(stable, pub) == ["gut 1-1", "still 1-1"]


def test_promotion_report_superseded(tmp_path):
    """Ältere wartende Versionen, zu denen testing eine neuere hat, gelten als überholt (kein eigenes Issue)."""
    key, pub = keygen(tmp_path)
    testing, stable = tmp_path / "testing", tmp_path / "stable"
    p = lambda n, v: make_pkg(tmp_path, n, v, key=key)
    syp("repo-add", testing, p("x", "1-1"), "--name", "testing", "-k", key)
    t0 = 1_800_000_000.0
    promote(testing, stable, pub, key, t0)
    syp("repo-add", testing, p("x", "1-2"), p("x", "1-3"), "--name", "testing", "-k", key)
    report = tmp_path / "report.json"
    out = promote(testing, stable, pub, key, t0 + DAY, "--report", report)
    assert "x 1-3: erst" in out and "x 1-1:" not in out and "x 1-2:" not in out, out
    assert "1 zurückgestellt, 2 ältere Versionen überholt" in out
    r = json.loads(report.read_text())
    assert r["x 1-3"]["status"] == "wartet"
    assert r["x 1-1"]["status"] == r["x 1-2"]["status"] == "ersetzt"
    # die älteste hat ihre Wartezeit hinter sich und kommt trotzdem nach stable
    out = promote(testing, stable, pub, key, t0 + 7.5 * DAY, "--report", report)
    assert "→ stable: x 1-1" in out and json.loads(report.read_text())["x 1-1"]["status"] == "stable"


FAKE_GH_API = r'''#!/usr/bin/env python3
"""Ersatz für »gh api« mit Issues, Labels und Reaktionen in $FAKE_GH_STATE (JSON)."""
import json, os, re, sys
path = os.environ["FAKE_GH_STATE"]
st = json.load(open(path)) if os.path.exists(path) else {"issues": [], "labels": [], "reactions": {}}
a = sys.argv[1:]
assert a[0] == "api", a
url, method = a[1], "GET"
if "-X" in a:
    method = a[a.index("-X") + 1]
data = json.loads(sys.stdin.read()) if "--input" in a else None
out = None
if url.endswith("/labels") and method == "POST":
    if data["name"] in st["labels"]:
        sys.exit("422 already_exists")
    st["labels"].append(data["name"]); out = data
elif m := re.search(r"/issues/(\d+)/reactions", url):
    out = st["reactions"].get(m.group(1), [])
elif m := re.search(r"/issues/(\d+)$", url):
    i = next(x for x in st["issues"] if x["number"] == int(m.group(1)))
    i.update(data); out = i
elif re.search(r"/issues$", url) and method == "POST":
    i = {"number": len(st["issues"]) + 1, "state": "open", "title": data["title"], "body": data["body"],
         "labels": [{"name": n} for n in data["labels"]]}
    st["issues"].append(i); out = i
elif "/issues?" in url:
    page = int(re.search(r"page=(\d+)", url.split("per_page")[1]).group(1))
    out = [x for x in st["issues"] if x["state"] == "open"][(page - 1) * 100:page * 100]
else:
    sys.exit(f"unbekannt: {a}")
json.dump(st, open(path, "w"))
print(json.dumps(out))
'''


def test_testing_karma_issues(tmp_path):
    """tools/testing-karma.py: Issues je wartendem Paket, Karma aus 👍/👎 (eine Stimme je Person, ohne Bots),
    übernommene und verschwundene Versionen werden geschlossen."""
    bindir, state = tmp_path / "bin", tmp_path / "gh.json"
    bindir.mkdir()
    (bindir / "gh").write_text(FAKE_GH_API)
    (bindir / "gh").chmod(0o755)
    env = dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}", FAKE_GH_STATE=str(state), GITHUB_REPOSITORY="t/t")
    tool = lambda *a: subprocess.run([sys.executable, ROOT / "tools/testing-karma.py", *map(str, a)],
                                     capture_output=True, text=True, env=env)
    report = tmp_path / "report.json"
    report.write_text(json.dumps({
        "foo 2-1": {"name": "foo", "version": "2-1", "days": 1, "need": 7, "karma": 0, "status": "wartet",
                    "reason": "erst 1.0 von 7 Tagen in testing"},
        "bar 1-1": {"name": "bar", "version": "1-1", "days": 0, "need": 0, "karma": 0, "status": "stable"}}))
    r = tool("sync", report)
    assert r.returncode == 0 and "1 neu" in r.stdout, r.stdout + r.stderr
    s = json.loads(state.read_text())
    assert [i["title"] for i in s["issues"]] == ["testing: foo 2-1"] and s["labels"] == ["testing"]
    assert "erst 1.0 von 7 Tagen" in s["issues"][0]["body"]
    # Stimmen: zwei Personen 👍, eine 👎, ein Bot 👍, eine Person mit beidem (zählt nicht)
    u = lambda n, t="User": {"login": n, "type": t}
    s["reactions"]["1"] = [{"content": "+1", "user": u("a")}, {"content": "+1", "user": u("b")},
                           {"content": "-1", "user": u("c")}, {"content": "+1", "user": u("bot", "Bot")},
                           {"content": "+1", "user": u("d")}, {"content": "-1", "user": u("d")},
                           {"content": "heart", "user": u("e")}]
    state.write_text(json.dumps(s))
    karma = tmp_path / "karma.json"
    assert tool("collect", karma).returncode == 0
    assert json.loads(karma.read_text()) == {"foo 2-1": 1}
    # zweiter Lauf: unverändert → nichts Neues; dann foo übernommen → Issue geschlossen
    assert "0 neu, 0 aktualisiert, 0 geschlossen" in tool("sync", report).stdout
    rep = json.loads(report.read_text())
    rep["foo 2-1"]["status"] = "stable"
    report.write_text(json.dumps(rep))
    assert "1 geschlossen" in tool("sync", report).stdout
    assert json.loads(state.read_text())["issues"][0]["state"] == "closed"
    # eine Version, die nicht mehr im Bericht steht, wird ebenfalls geschlossen
    rep = {"baz 1-1": {"name": "baz", "version": "1-1", "days": 0, "need": 7, "karma": 0, "status": "wartet"}}
    report.write_text(json.dumps(rep))
    tool("sync", report)
    rep = {}
    report.write_text(json.dumps(rep))
    assert "1 geschlossen" in tool("sync", report).stdout
