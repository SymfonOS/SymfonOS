#!/usr/bin/env python3
"""Upstream-Signaturen der Quellarchive prüfen (GPG), statt Prüfsummen nur beim ersten Laden festzuhalten.

distro/source-keys.toml nennt je Paket die Fingerabdrücke der Schlüssel, mit denen das Projekt seine
Archive signiert, und die Endung der Signaturdatei; die öffentlichen Schlüssel liegen unter
distro/keys/upstream/<FINGERABDRUCK>.asc. Ein Paket dort muss bei jedem Versionssprung eine gültige
Signatur eines dieser Schlüssel mitbringen – ein neuer Schlüssel ist eine bewusste Änderung im Repo.

    tools/source-sigs.py check [PAKET ...]        # Signaturen prüfen (alle Pakete mit Schlüsseln)
    tools/source-sigs.py check --changed REF      # nur Pakete, deren Rezept sich seit REF geändert hat
    tools/source-sigs.py discover [PAKET ...]     # Signaturen suchen, Schlüssel holen und eintragen

discover sucht neben der ersten Quelle eines Rezepts nach .sig/.asc/.sign, holt den Schlüssel zum
Aussteller der Signatur vom Keyserver und trägt ihn nur ein, wenn er die Signatur des Archivs bestätigt,
dessen sha256 schon im Rezept steht. Zwei unabhängige Wege (die früher festgehaltene Prüfsumme und die
Signatur) stimmen dann überein; jede spätere Version wird gegen den so festgeschriebenen Schlüssel geprüft.

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
KEYSERVERS = tuple(os.environ.get("SOURCE_SIGS_KEYSERVERS", "").split()) or (
    "https://keyserver.ubuntu.com/pks/lookup?op=get&options=mr&search=0x{fpr}",
    "https://keys.openpgp.org/vks/v1/by-fingerprint/{fpr}")
UA = {"User-Agent": "symfonOS-source-sigs/1"}
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
            "# sig: Endung der Signaturdatei neben dem Archiv; uncompressed: Signatur gilt dem entpackten .tar\n")
    out = [head]
    for name in sorted(data):
        e = data[name]
        # Namen mit Punkt (cairomm1.0) in Anführungszeichen, sonst wären es verschachtelte Tabellen
        out.append(f"\n[{name}]\n" if re.fullmatch(r"[A-Za-z0-9_-]+", name) else f'\n["{name}"]\n')
        out.append("keys = [" + ", ".join(f'"{k}"' for k in e["keys"]) + "]\n")
        out.append(f'sig = "{e["sig"]}"\n')
        if e.get("uncompressed"):
            out.append("uncompressed = true\n")
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
        data = archive(src)
        sig = fetch(src["url"] + e["sig"])
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
    for suf in SUFFIXES:
        try:
            sig = fetch(url + suf, tries=2)
        except NetError:
            continue
        if b"PGP SIGNATURE" not in sig and (not sig or sig[0] & 0x80 == 0):
            continue                                       # Fehlerseite statt Signatur
        try:
            data = archive(src)
        except (NetError, ValueError) as ex:
            return None, None, f"netz  {name}: {ex}"
        for unc in (False, True) if suf == ".sign" else (False,):
            payload = uncompress(url, data) if unc else data
            for iss in issuers(sig):
                key = fetch_key(iss)
                if not key:
                    continue
                fpr = verify(sig, payload, [key])
                if fpr:
                    entry = {"keys": [fpr], "sig": suf, **({"uncompressed": True} if unc else {})}
                    # export-clean wirft bei abgelaufenen Schlüsseln die Unterschlüssel weg: dann den
                    # Schlüssel so speichern, wie ihn der Keyserver geliefert hat
                    small = export_minimal(key, fpr)
                    return entry, small if verify(sig, payload, [small]) == fpr else key, \
                        f"neu   {name}: {url.rsplit('/', 1)[-1]}{suf} – Schlüssel {fpr}"
        return None, None, f"ohne  {name}: {url.rsplit('/', 1)[-1]}{suf} vorhanden, aber kein passender Schlüssel"
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
