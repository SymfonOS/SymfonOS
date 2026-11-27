#!/usr/bin/env python3
"""Upstream-Signaturen der Quellarchive prüfen (GPG), statt Prüfsummen nur beim ersten Laden festzuhalten.

distro/source-keys.toml nennt je Paket die Fingerabdrücke der Schlüssel, mit denen das Projekt seine
Archive signiert, und die Endung der Signaturdatei; die öffentlichen Schlüssel liegen unter
distro/keys/upstream/<FINGERABDRUCK>.asc. Ein Paket dort muss bei jedem Versionssprung eine gültige
Signatur eines dieser Schlüssel mitbringen – ein neuer Schlüssel ist eine bewusste Änderung im Repo.

    tools/source-sigs.py check [PAKET ...]        # Signaturen prüfen (alle Pakete mit Schlüsseln)
    tools/source-sigs.py check --changed REF      # nur Pakete, deren Rezept sich seit REF geändert hat
    tools/source-sigs.py discover [PAKET ...]     # Signaturen suchen, Schlüssel holen und eintragen

discover sucht neben der ersten Quelle eines Rezepts nach .sig/.asc/.sign (sonst nach einer signierten
Prüfsummenliste wie SHA256SUMS{.asc,.sig,.gpg}, in der das Archiv stehen muss), holt den Schlüssel zum
Aussteller der Signatur vom Keyserver und trägt ihn nur ein, wenn er die Signatur des Archivs bestätigt,
dessen sha256 schon im Rezept steht. Zwei unabhängige Wege (die früher festgehaltene Prüfsumme und die
Signatur) stimmen dann überein; jede spätere Version wird gegen den so festgeschriebenen Schlüssel geprüft.

Signierte Git-Tags (sig = "git-tag"): viele Projekte auf GitHub/GitLab signieren keine Archive, wohl aber ihre
Release-Tags. Ist die Quelle ein Tag-Archiv der Forge (…/archive/TAG…, …/-/archive/TAG/…), holt discover das
Tag-Objekt (git fetch --depth=1, ohne Dateiinhalte), prüft dessen Signatur und vergleicht das Archiv Datei für
Datei mit dem signierten Baum: jede Datei im Archiv muss dort mit gleichem Git-Hash und gleicher Art (Datei,
ausführbar, Verweis) stehen. Fehlen dürfen Dateien (export-ignore), hinzukommen oder abweichen nicht.

Archive kommen zuerst aus dem Lookaside-Cache (SYP_LOOKASIDE, benannt nach der sha256), Signaturen vom
Projektserver. Rückgabewert von check: 0 = alles gültig, 1 = ungültig oder fehlend, 2 = nur Netzfehler.
"""
from __future__ import annotations

import argparse
import hashlib
import bz2
import concurrent.futures
import gzip
import lzma
import os
import re
import subprocess
import sys
import tempfile
import time
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(os.environ.get("SOURCE_SIGS_ROOT") or Path(__file__).resolve().parent.parent)
RECIPES = ROOT / "distro" / "recipes"
KEYS_TOML = ROOT / "distro" / "source-keys.toml"
KEYDIR = ROOT / "distro" / "keys" / "upstream"
LOOKASIDE = os.environ.get("SYP_LOOKASIDE", "https://github.com/SymfonOS/SymfonOS/releases/download/sources")
SUFFIXES = (".sig", ".asc", ".sign")
# signierte Prüfsummenlisten neben dem Archiv (viele GitHub-Releases signieren nur diese)
SUMS_FILES = ("SHA256SUMS", "SHA256SUMS.txt", "sha256sums.txt", "checksums.txt", "SHA512SUMS")
SUMS_SIGS = (".asc", ".sig", ".gpg")
KEYSERVERS = tuple(os.environ.get("SOURCE_SIGS_KEYSERVERS", "").split()) or (
    "https://keyserver.ubuntu.com/pks/lookup?op=get&options=mr&search=0x{fpr}",
    "https://keys.openpgp.org/vks/v1/by-fingerprint/{fpr}")
UA = {"User-Agent": "symfonOS-source-sigs/1"}
# Forge-Adressen für Tag-Archive (Tests leiten sie auf lokale Repos um)
FORGES = {h: os.environ.get(f"SOURCE_SIGS_FORGE_{h.split('.')[-2].upper()}", f"https://{h}")
          for h in ("github.com", "gitlab.com", "gitlab.freedesktop.org", "invent.kde.org", "gitlab.gnome.org")}
FPR = re.compile(r"^[0-9A-F]{40}$")


class NetError(Exception):
    pass


def fetch(url: str, tries: int = 3) -> bytes:
    if url.startswith("file://"):
        try:
            return Path(url[7:]).read_bytes()
        except FileNotFoundError:
            raise NetError(f"{url}: HTTP 404") from None
        except OSError as e:
            raise NetError(f"{url}: {e}") from None
    last = None
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code in (403, 404, 410):
                raise NetError(f"{url}: HTTP {e.code}") from None
            last = e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = e
        time.sleep(2 * (i + 1))
    raise NetError(f"{url}: {last}")


def recipe(name: str) -> dict:
    with open(RECIPES / name / "SYPBUILD.toml", "rb") as f:
        return tomllib.load(f)


def load_keys() -> dict:
    if not KEYS_TOML.exists():
        return {}
    with open(KEYS_TOML, "rb") as f:
        return tomllib.load(f)


def write_keys(data: dict) -> None:
    head = ("# Upstream-Signaturschlüssel der Quellarchive – Format und Ablauf: tools/source-sigs.py\n"
            "# keys: Fingerabdrücke (Hauptschlüssel), öffentliche Schlüssel unter distro/keys/upstream/\n"
            "# sig: Endung der Signaturdatei neben dem Archiv; uncompressed: Signatur gilt dem entpackten .tar\n"
            "# tar_sig: die Signatur liegt neben dem entpackten Namen (NAME.tar.sign statt NAME.tar.xz.sign)\n"
            "# sums: signiert ist eine Prüfsummenliste dieses Namens neben dem Archiv (sig gilt ihr)\n"
            "# sig = \"git-tag\": signiert ist der Release-Tag; das Archiv muss dem signierten Baum entsprechen\n")
    out = [head]
    for name in sorted(data):
        e = data[name]
        # Namen mit Punkt (cairomm1.0) in Anführungszeichen, sonst wären es verschachtelte Tabellen
        out.append(f"\n[{name}]\n" if re.fullmatch(r"[A-Za-z0-9_-]+", name) else f'\n["{name}"]\n')
        out.append("keys = [" + ", ".join(f'"{k}"' for k in e["keys"]) + "]\n")
        out.append(f'sig = "{e["sig"]}"\n')
        if e.get("sums"):
            out.append(f'sums = "{e["sums"]}"\n')
        if e.get("uncompressed"):
            out.append("uncompressed = true\n")
        if e.get("tar_sig"):
            out.append("tar_sig = true\n")
    KEYS_TOML.write_text("".join(out))


def main_source(r: dict) -> dict | None:
    for s in r.get("source", []):
        if s.get("extract", True) and s.get("url", "").startswith(("https://", "http://", "file://")):
            return s
    return None


def archive(src: dict) -> bytes:
    """Archiv laden (Lookaside zuerst) und gegen die sha256 des Rezepts prüfen."""
    want = src.get("sha256", "")
    if want == "TODO":
        return fetch(src["url"])           # neue Version, Prüfsumme trägt die CI erst ein: Signatur entscheidet
    if len(want) != 64:
        raise ValueError("keine sha256 im Rezept")
    data = None
    for url in (f"{LOOKASIDE.rstrip('/')}/{want}", src["url"]):
        try:
            data = fetch(url)
        except NetError:
            continue
        if hashlib.sha256(data).hexdigest() == want:
            return data
    raise NetError(f"Archiv nicht ladbar oder Prüfsumme falsch: {src['url']}")


def uncompress(name: str, data: bytes) -> bytes:
    if name.endswith((".xz", ".txz")):
        return lzma.decompress(data)
    if name.endswith((".gz", ".tgz")):
        return gzip.decompress(data)
    if name.endswith(".bz2"):
        return bz2.decompress(data)
    return data


def tar_url(url: str) -> str:
    """URL ohne Kompressionsendung (…tar.xz → …tar), sonst unverändert."""
    for ext in (".xz", ".gz", ".bz2"):
        if url.endswith(".tar" + ext):
            return url[:-len(ext)]
    return url


def sums_lookup(text: str, fname: str) -> str | None:
    """Prüfsumme für fname aus einer Liste im GNU- (»hex  name«) oder BSD-Format (»SHA256 (name) = hex«)."""
    for line in text.splitlines():
        m = re.match(r"^([0-9a-fA-F]{64}|[0-9a-fA-F]{128})\s+\*?(\S+)$", line.strip())
        if m and m.group(2).rsplit("/", 1)[-1] == fname:
            return m.group(1).lower()
        m = re.match(r"^SHA(?:256|512) \((.+)\) = ([0-9a-fA-F]+)$", line.strip())
        if m and m.group(1).rsplit("/", 1)[-1] == fname:
            return m.group(2).lower()
    return None


def sums_match(sums: bytes, url: str, data: bytes) -> bool:
    want = sums_lookup(sums.decode(errors="replace"), url.rsplit("/", 1)[-1])
    if not want:
        return False
    h = hashlib.sha256 if len(want) == 64 else hashlib.sha512
    return h(data).hexdigest() == want


def gpg(home: str, *args: str, data: bytes | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["gpg", "--homedir", home, "--batch", "--no-tty", "--status-fd", "1", *args],
                          input=data, capture_output=True)


def verify(sig: bytes, data: bytes, keyfiles: list[bytes]) -> str | None:
    """Fingerabdruck des Hauptschlüssels bei gültiger Signatur, sonst None."""
    with tempfile.TemporaryDirectory() as home:
        for k in keyfiles:
            gpg(home, "--import", data=k)
        Path(home, "s").write_bytes(sig)
        Path(home, "d").write_bytes(data)
        r = gpg(home, "--verify", str(Path(home, "s")), str(Path(home, "d")))
        for line in r.stdout.decode(errors="replace").splitlines():
            p = line.split()
            if len(p) >= 3 and p[:2] == ["[GNUPG:]", "VALIDSIG"]:
                return p[-1].upper()
        return None


def issuers(sig: bytes) -> list[str]:
    with tempfile.TemporaryDirectory() as home:
        r = gpg(home, "--list-packets", data=sig)
        txt = (r.stdout + r.stderr).decode(errors="replace")
    fprs = re.findall(r"issuer fpr v\d ([0-9A-F]{40})", txt)
    ids = re.findall(r"keyid ([0-9A-F]{16})", txt)
    return list(dict.fromkeys(fprs + ids))


def keyfile(fpr: str) -> bytes:
    return (KEYDIR / f"{fpr}.asc").read_bytes()


def tag_source(url: str) -> tuple[str, str] | None:
    """(Repo-URL, Tag) für ein Tag-Archiv der Forge, sonst None.
    github.com/O/R/archive/[refs/tags/]TAG(.tar.gz|/NAME.tar.gz) · HOST/GRUPPE/R/-/archive/TAG/NAME.tar.*"""
    m = re.match(r"^https://(github\.com)/([^/]+/[^/]+)/archive/(?:refs/tags/)?([^/]+?)(?:\.tar\.(?:gz|xz|bz2)|\.zip)?(?:/[^/]+)?$", url)
    if not m:
        m = re.match(r"^https://([^/]+)/(.+?)/-/archive/([^/]+)/[^/]+$", url)
    if not m or m.group(1) not in FORGES:
        return None
    tag = m.group(3)
    if re.fullmatch(r"[0-9a-f]{40}", tag):
        return None                                        # Commit statt Tag: nichts Signiertes
    return f"{FORGES[m.group(1)]}/{m.group(2)}", tag


def git(d: str, *args: str) -> bytes:
    r = subprocess.run(["git", "-C", d, *args], capture_output=True, timeout=600,
                       env=dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_CONFIG_NOSYSTEM="1"))
    if r.returncode:
        raise NetError(f"git {args[0]}: {r.stderr.decode(errors='replace').strip()[-300:]}")
    return r.stdout


def fetch_tag(repo: str, tag: str, d: str) -> tuple[bytes, bytes, dict]:
    """Signierten Tag holen: (Nutzdaten, Signatur, {Pfad: (Modus, Blob-Hash)} des Baums)."""
    git(d, "init", "-q")
    git(d, "fetch", "-q", "--depth=1", "--filter=blob:none", "--no-tags", repo, f"refs/tags/{tag}:refs/tags/t")
    if git(d, "cat-file", "-t", "t").strip() != b"tag":
        raise ValueError(f"{tag} ist kein annotierter Tag")
    raw = git(d, "cat-file", "tag", "t")
    i = raw.find(b"-----BEGIN PGP SIGNATURE-----")
    if i < 0:
        raise ValueError(f"Tag {tag} ist nicht (mit OpenPGP) signiert")
    payload, sig = raw[:i], raw[i:]
    head = dict(line.split(b" ", 1) for line in payload.split(b"\n\n", 1)[0].splitlines() if b" " in line)
    # der signierte Inhalt muss genau diesen Tag auf einen Commit nennen (kein umbenannter fremder Tag)
    if head.get(b"tag") != tag.encode() or head.get(b"type") != b"commit":
        raise ValueError(f"Tag-Objekt nennt {head.get(b'tag')!r}/{head.get(b'type')!r} statt {tag}/commit")
    tree = {}
    for line in git(d, "ls-tree", "-r", "-z", "--full-tree", head[b"object"].decode()).split(b"\0"):
        if not line:
            continue
        meta, path = line.split(b"\t", 1)
        mode, typ, sha = meta.split()
        if typ == b"blob":
            tree[path.decode(errors="surrogateescape")] = (mode.decode(), sha.decode())
    return payload, sig, tree


def archive_matches_tree(url: str, data: bytes, tree: dict) -> str | None:
    """None, wenn jede Datei des Archivs im signierten Baum gleich vorkommt; sonst die erste Abweichung."""
    import io
    import tarfile
    import zipfile
    files = []                                             # (Pfad, Modus, Inhalt)
    if url.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for i in z.infolist():
                if i.is_dir():
                    continue
                attr = i.external_attr >> 16
                mode = "120000" if attr & 0o170000 == 0o120000 else "100755" if attr & 0o111 else "100644"
                files.append((i.filename, mode, z.read(i)))
    else:
        with tarfile.open(fileobj=io.BytesIO(data)) as t:
            for m in t:
                if m.isdir() or m.name == "pax_global_header":
                    continue
                if m.issym():
                    files.append((m.name, "120000", m.linkname.encode(errors="surrogateescape")))
                elif m.isfile():
                    files.append((m.name, "100755" if m.mode & 0o111 else "100644", t.extractfile(m).read()))
                else:
                    return f"{m.name}: weder Datei noch Verweis"
    if not files:
        return "Archiv ist leer"
    # oberstes Verzeichnis (NAME-VERSION/) abschneiden; alle Einträge müssen darunter liegen
    top = files[0][0].split("/", 1)[0] + "/"
    for path, mode, content in files:
        if not path.startswith(top):
            return f"{path}: außerhalb von {top}"
        rel = path[len(top):]
        want = tree.get(rel)
        if not want:
            return f"{rel}: nicht im signierten Tag"
        sha = hashlib.sha1(b"blob %d\0" % len(content) + content).hexdigest()
        if want != (mode, sha):
            return f"{rel}: weicht vom signierten Tag ab ({mode} {sha[:12]} statt {want[0]} {want[1][:12]})"
    return None


def verify_tag(src: dict, keyfiles: list[bytes] | None) -> tuple[str | None, list[str], str | None]:
    """Signierten Tag zur Quelle prüfen: (Fingerabdruck, Aussteller, Fehler). Ohne keyfiles: Schlüssel vom
    Keyserver zu den Ausstellern der Signatur (discover)."""
    repo, tag = tag_source(src["url"])
    with tempfile.TemporaryDirectory() as d:
        payload, sig, tree = fetch_tag(repo, tag, d)
    data = archive(src)
    bad = archive_matches_tree(src["url"], data, tree)
    if bad:
        return None, [], bad
    iss = issuers(sig)
    if keyfiles is not None:
        return verify(sig, payload, keyfiles), iss, None
    for i in iss:
        key = fetch_key(i)
        if key:
            fpr = verify(sig, payload, [key])
            if fpr:
                small = export_minimal(key, fpr)
                return fpr, [small if verify(sig, payload, [small]) == fpr else key], None
    return None, iss, None


def check_one(name: str, e: dict) -> tuple[str, str]:
    """Ein Paket prüfen: ("ok"|"bad"|"net", Meldung)."""
    try:
        r = recipe(name)
    except FileNotFoundError:
        return "bad", f"{name}: kein Rezept, aber Eintrag in source-keys.toml"
    src = main_source(r)
    if not src:
        return "bad", f"{name}: keine Quelle im Rezept"
    try:
        missing = [k for k in e["keys"] if not (KEYDIR / f"{k}.asc").exists()]
        if missing:
            raise ValueError(f"Schlüssel fehlt in distro/keys/upstream: {', '.join(missing)}")
        if e["sig"] == "git-tag":
            if not tag_source(src["url"]):
                return "bad", f"{name}: Quelle ist kein Tag-Archiv der Forge mehr ({src['url']})"
            got, _, err = verify_tag(src, [keyfile(k) for k in e["keys"]])
            if err:
                return "bad", f"{name}: {err}"
            if got and got in e["keys"]:
                return "ok", f"{name} (signierter Tag {tag_source(src['url'])[1]}, Schlüssel {got[-16:]})"
            return "bad", f"{name}: Tag {tag_source(src['url'])[1]} ungültig oder von fremdem Schlüssel signiert"
        data = archive(src)
        if e.get("sums"):
            # signierte Prüfsummenliste: Signatur über die Liste, Archiv muss darin mit seiner Summe stehen
            base = src["url"].rsplit("/", 1)[0]
            sums = fetch(f"{base}/{e['sums']}")
            sig = fetch(f"{base}/{e['sums']}{e['sig']}")
            got = verify(sig, sums, [keyfile(k) for k in e["keys"]])
            if got and not sums_match(sums, src["url"], data):
                return "bad", f"{name}: {src['url'].rsplit('/', 1)[-1]} fehlt in {e['sums']} oder Summe falsch"
        else:
            sig = fetch((tar_url(src["url"]) if e.get("tar_sig") else src["url"]) + e["sig"])
            if e.get("uncompressed"):
                data = uncompress(src["url"], data)
            got = verify(sig, data, [keyfile(k) for k in e["keys"]])
    except NetError as ex:
        # fehlende Signaturdatei (404) wiegt wie eine ungültige: das Projekt hat sie bisher immer geliefert
        return ("bad" if "HTTP 404" in str(ex) or "HTTP 410" in str(ex) else "net"), f"{name}: {ex}"
    except (ValueError, OSError, lzma.LZMAError, EOFError) as ex:
        return "bad", f"{name}: {ex}"
    if got and got in e["keys"]:
        return "ok", f"{name} ({src['url'].rsplit('/', 1)[-1]}, Schlüssel {got[-16:]})"
    return "bad", f"{name}: Signatur von {src['url'].rsplit('/', 1)[-1]} ungültig oder von fremdem Schlüssel"


def check(names: list[str]) -> int:
    """Pakete parallel prüfen (SOURCE_SIGS_JOBS, Standard 16); Ausgabe in Namensreihenfolge."""
    keys = load_keys()
    todo = [n for n in names if n in keys]
    jobs = max(1, int(os.environ.get("SOURCE_SIGS_JOBS", "16")))
    with concurrent.futures.ThreadPoolExecutor(jobs) as pool:
        results = list(pool.map(lambda n: check_one(n, keys[n]), todo))
    bad = [m for k, m in results if k == "bad"]
    neterr = [m for k, m in results if k == "net"]
    ok = 0
    for k, m in results:
        if k == "ok":
            ok += 1
            print(f"ok    {m}")
    for m in neterr:
        print(f"netz  {m}")
    for m in bad:
        print(f"FEHL  {m}")
    print(f"{ok} gültig, {len(bad)} ungültig/fehlend, {len(neterr)} Netzfehler")
    if os.environ.get("GITHUB_ACTIONS"):
        if bad:
            print(f"::error title=Quellsignaturen ({len(bad)})::" + "%0A".join(bad)[:3800])
        elif neterr:
            print(f"::warning title=Quellsignaturen: Netzfehler ({len(neterr)})::" + "%0A".join(neterr)[:3800])
        else:
            print(f"::notice title=Quellsignaturen::{ok} Archive mit gültiger Upstream-Signatur")
    return 1 if bad else 2 if neterr else 0


def fetch_key(issuer: str) -> bytes | None:
    for tmpl in KEYSERVERS:
        if len(issuer) != 40 and "openpgp" in tmpl:
            tmpl = "https://keys.openpgp.org/vks/v1/by-keyid/{fpr}"
        try:
            k = fetch(tmpl.format(fpr=issuer), tries=2)
        except NetError:
            continue
        if b"BEGIN PGP PUBLIC KEY BLOCK" in k:
            return k
    return None


def export_minimal(key: bytes, fpr: str) -> bytes:
    with tempfile.TemporaryDirectory() as home:
        gpg(home, "--import", data=key)
        r = gpg(home, "--armor", "--export-options", "export-minimal,export-clean", "--export", fpr)
        return r.stdout if b"BEGIN PGP" in r.stdout else key


def discover_one(name: str) -> tuple[dict | None, bytes | None, str | None]:
    """Signatur und Schlüssel für ein Paket suchen: (Eintrag, Schlüssel, Meldung)."""
    try:
        src = main_source(recipe(name))
    except FileNotFoundError:
        return None, None, None
    if not src or len(src.get("sha256", "")) != 64:
        return None, None, None
    url = src["url"]
    # Kandidaten: Signatur neben dem Archiv; bei komprimierten Archiven auch neben dem entpackten Namen
    # (kernel.org: bluez-5.83.tar.sign gilt bluez-5.83.tar, ausgeliefert wird bluez-5.83.tar.xz)
    cands = [(url + suf, suf, False) for suf in SUFFIXES]
    tar = tar_url(url)
    if tar != url:
        cands += [(tar + suf, suf, True) for suf in SUFFIXES]
    for sigurl, suf, on_tar in cands:
        try:
            sig = fetch(sigurl, tries=2)
        except NetError:
            continue
        if b"PGP SIGNATURE" not in sig and (not sig or sig[0] & 0x80 == 0):
            continue                                       # Fehlerseite statt Signatur
        try:
            data = archive(src)
        except (NetError, ValueError) as ex:
            return None, None, f"netz  {name}: {ex}"
        for unc in (True,) if on_tar else (False, True) if suf == ".sign" else (False,):
            payload = uncompress(url, data) if unc else data
            for iss in issuers(sig):
                key = fetch_key(iss)
                if not key:
                    continue
                fpr = verify(sig, payload, [key])
                if fpr:
                    entry = {"keys": [fpr], "sig": suf, **({"uncompressed": True} if unc else {}),
                             **({"tar_sig": True} if on_tar else {})}
                    # export-clean wirft bei abgelaufenen Schlüsseln die Unterschlüssel weg: dann den
                    # Schlüssel so speichern, wie ihn der Keyserver geliefert hat
                    small = export_minimal(key, fpr)
                    return entry, small if verify(sig, payload, [small]) == fpr else key, \
                        f"neu   {name}: {sigurl.rsplit('/', 1)[-1]} – Schlüssel {fpr}"
        return None, None, f"ohne  {name}: {sigurl.rsplit('/', 1)[-1]} vorhanden, aber kein passender Schlüssel"
    entry, key, msg = discover_sums(name, src)
    if entry or msg or not tag_source(url):
        return entry, key, msg
    return discover_tag(name, src)


def discover_tag(name: str, src: dict) -> tuple[dict | None, bytes | None, str | None]:
    """Signierter Release-Tag: Schlüssel nur, wenn er den Tag bestätigt UND das Archiv (mit der schon
    festgeschriebenen sha256) dem signierten Baum entspricht."""
    tag = tag_source(src["url"])[1]
    try:
        fpr, keys, err = verify_tag(src, None)
    except NetError as ex:
        return None, None, (f"netz  {name}: {ex}" if "HTTP" in str(ex) else None)
    except ValueError:
        return None, None, None                            # Tag unsigniert oder nicht annotiert
    if err:
        return None, None, f"ohne  {name}: Tag {tag} signiert, Archiv passt nicht ({err})"
    if not fpr:
        return None, None, f"ohne  {name}: Tag {tag} signiert, aber kein passender Schlüssel"
    return {"keys": [fpr], "sig": "git-tag"}, keys[0], f"neu   {name}: signierter Tag {tag} – Schlüssel {fpr}"


def discover_sums(name: str, src: dict) -> tuple[dict | None, bytes | None, str | None]:
    """Signierte Prüfsummenliste neben dem Archiv: Schlüssel nur, wenn er die Liste bestätigt UND die Liste
    das Archiv mit der schon festgeschriebenen sha256 nennt."""
    url = src["url"]
    base = url.rsplit("/", 1)[0]
    for sf in SUMS_FILES:
        try:
            sums = fetch(f"{base}/{sf}", tries=2)
        except NetError:
            continue
        for suf in SUMS_SIGS:
            try:
                sig = fetch(f"{base}/{sf}{suf}", tries=2)
            except NetError:
                continue
            if b"PGP SIGNATURE" not in sig and (not sig or sig[0] & 0x80 == 0):
                continue
            try:
                data = archive(src)
            except (NetError, ValueError) as ex:
                return None, None, f"netz  {name}: {ex}"
            if not sums_match(sums, url, data):
                return None, None, f"ohne  {name}: {sf}{suf} signiert, nennt {url.rsplit('/', 1)[-1]} aber nicht passend"
            for iss in issuers(sig):
                key = fetch_key(iss)
                if not key:
                    continue
                fpr = verify(sig, sums, [key])
                if fpr:
                    small = export_minimal(key, fpr)
                    return ({"keys": [fpr], "sig": suf, "sums": sf},
                            small if verify(sig, sums, [small]) == fpr else key,
                            f"neu   {name}: {sf}{suf} – Schlüssel {fpr}")
            return None, None, f"ohne  {name}: {sf}{suf} vorhanden, aber kein passender Schlüssel"
    return None, None, None


def discover(names: list[str]) -> int:
    """Pakete parallel durchsuchen (SOURCE_SIGS_JOBS, Standard 16). Nach SOURCE_SIGS_BUDGET Sekunden
    werden keine weiteren Pakete begonnen; Gefundenes wird laufend gespeichert, ein Abbruch verliert nichts."""
    keys = load_keys()
    KEYDIR.mkdir(parents=True, exist_ok=True)
    todo = [n for n in names if n not in keys]
    budget = float(os.environ.get("SOURCE_SIGS_BUDGET", "0") or 0)
    deadline = time.monotonic() + budget if budget else None
    found = skipped = 0
    jobs = max(1, int(os.environ.get("SOURCE_SIGS_JOBS", "16")))
    queue = list(reversed(todo))
    with concurrent.futures.ThreadPoolExecutor(jobs) as pool:
        futs: dict = {}
        while queue or futs:
            while queue and len(futs) < 2 * jobs:
                if deadline and time.monotonic() > deadline:
                    skipped, queue = len(queue), []
                    break
                n = queue.pop()
                futs[pool.submit(discover_one, n)] = n
            if not futs:
                break
            done, _ = concurrent.futures.wait(futs, return_when=concurrent.futures.FIRST_COMPLETED)
            for f in done:
                name = futs.pop(f)
                entry, key, msg = f.result()
                if msg:
                    print(msg, flush=True)
                if entry:
                    (KEYDIR / f"{entry['keys'][0]}.asc").write_bytes(key)
                    keys[name] = entry
                    found += 1
                    write_keys(keys)
    # Schlüsseldateien, die kein Paket mehr nennt, entfernen
    used = {k for e in keys.values() for k in e["keys"]}
    for f in KEYDIR.glob("*.asc"):
        if f.stem not in used:
            f.unlink()
    write_keys(keys)
    if skipped:
        print(f"Zeitbudget erschöpft: {skipped} Pakete nicht durchsucht (beim nächsten Lauf)")
    print(f"{found} Pakete neu eingetragen, {len(keys)} insgesamt mit Upstream-Signatur")
    return 0


def changed_since(ref: str) -> list[str]:
    out = subprocess.run(["git", "-C", str(ROOT), "diff", "--name-only", ref, "--", "distro/recipes",
                          "distro/source-keys.toml"], capture_output=True, text=True, check=True).stdout
    names = {p.split("/")[2] for p in out.split() if p.startswith("distro/recipes/")}
    if "distro/source-keys.toml" in out.split():
        names |= set(load_keys())          # geänderte Schlüsselliste: alles neu prüfen
    return sorted(names)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=("check", "discover"))
    ap.add_argument("pkgs", nargs="*")
    ap.add_argument("--changed", metavar="REF", help="nur Pakete mit geändertem Rezept seit REF")
    a = ap.parse_args()
    names = a.pkgs or (changed_since(a.changed) if a.changed else sorted(p.name for p in RECIPES.iterdir()
                                                                         if (p / "SYPBUILD.toml").exists()))
    if a.cmd == "check":
        if a.changed and not names:
            print("keine geänderten Rezepte")
            return 0
        return check(names)
    return discover(names)


if __name__ == "__main__":
    sys.exit(main())
