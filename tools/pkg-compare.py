#!/usr/bin/env python3
"""Zwei .syp-Pakete inhaltlich vergleichen (Pfad, Typ, Rechte, sha256, Verweisziel, Größe laut .FILES.json);
für kleine Textdateien zusätzlich ein unified diff, für kleine Binärdateien die erste abweichende Stelle
(bei ar-Archiven wie .a: die abweichenden Glieder mit Kopfzeile). Signatur und Bauzeit zählen nicht.

    tools/pkg-compare.py A.syp B.syp [BEZEICHNUNG-A BEZEICHNUNG-B]     # 0 = gleich, 1 = verschieden
"""
import difflib, hashlib, io, json, subprocess, sys, tarfile
def files(p):
    with tarfile.open(p) as tf:
        return {f["path"]: {k: v for k, v in f.items() if k in ("type", "mode", "sha256", "target", "size")}
                for f in json.loads(tf.extractfile(".FILES.json").read())}
def content(p, want):
    with tarfile.open(p) as tf:
        raw = subprocess.run(["zstd", "-dcq"], input=tf.extractfile("data.tar.zst").read(),
                             capture_output=True, check=True).stdout
    out = {}
    with tarfile.open(fileobj=io.BytesIO(raw)) as tf:
        for m in tf:
            if m.name.lstrip("./") in want and m.isfile():
                out[m.name.lstrip("./")] = tf.extractfile(m).read()
    return out
def ar_members(data):
    """ar-Archiv: [(Name, Kopfzeile ohne Name, sha256 des Inhalts)]"""
    out, i = [], 8
    while i + 60 <= len(data):
        h = data[i:i + 60]
        try:
            size = int(h[48:58])
        except ValueError:
            break
        body = data[i + 60:i + 60 + size]
        out.append((h[:16].decode(errors="replace").strip(), h[16:58].decode(errors="replace").split(),
                    hashlib.sha256(body).hexdigest()[:16]))
        i += 60 + size + (size & 1)
    return out
def binary_diff(x, y):
    if x.startswith(b"!<arch>\n") and y.startswith(b"!<arch>\n"):
        ma, mb = ar_members(x), ar_members(y)
        if [m[0] for m in ma] != [m[0] for m in mb]:
            return [f"ar-Glieder: {[m[0] for m in ma]} != {[m[0] for m in mb]}"]
        return [f"ar-Glied {u[0]!r}: Kopf {u[1]} Inhalt {u[2]} != Kopf {v[1]} Inhalt {v[2]}"
                for u, v in zip(ma, mb) if u != v] or ["ar-Glieder gleich (Abweichung in Kopf oder Füllung)"]
    n = next((k for k in range(min(len(x), len(y))) if x[k] != y[k]), min(len(x), len(y)))
    return [f"erste Abweichung bei Byte {n} (Länge {len(x)} / {len(y)}): "
            f"{x[max(0, n - 8):n + 8].hex()} != {y[max(0, n - 8):n + 8].hex()}"]
pa, pb = sys.argv[1:3]
la, lb = (sys.argv[3:5] + ["a", "b"][len(sys.argv[3:5]):])
a, b = files(pa), files(pb)
bad = sorted(p for p in a.keys() | b.keys() if a.get(p) != b.get(p))
for p in bad[:20]:
    print(f"  {p}: {a.get(p)} != {b.get(p)}")
small = {p for p in bad[:5] if all((x.get(p) or {}).get("size", 1 << 30) < 65536 for x in (a, b))}
if small:
    ca, cb = content(pa, small), content(pb, small)
    for p in sorted(small & ca.keys() & cb.keys()):
        if any(b"\0" in d or d.startswith(b"!<arch>\n") for d in (ca[p], cb[p])):
            lines = binary_diff(ca[p], cb[p])
        else:
            lines = difflib.unified_diff(ca[p].decode(errors="replace").splitlines(),
                                         cb[p].decode(errors="replace").splitlines(), f"{la}/{p}", f"{lb}/{p}",
                                         lineterm="")
        for line in list(lines)[:30]:
            print("    " + line)
sys.exit(1 if bad else 0)
