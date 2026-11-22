#!/usr/bin/env python3
"""Zwei .syp-Pakete inhaltlich vergleichen (Pfad, Typ, Rechte, sha256, Verweisziel, Größe laut .FILES.json);
für kleine Textdateien zusätzlich ein unified diff. Signatur und Bauzeit zählen nicht.

    tools/pkg-compare.py A.syp B.syp [BEZEICHNUNG-A BEZEICHNUNG-B]     # 0 = gleich, 1 = verschieden
"""
import difflib, io, json, subprocess, sys, tarfile
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
                data = tf.extractfile(m).read()
                if b"\0" not in data:
                    out[m.name.lstrip("./")] = data.decode(errors="replace").splitlines()
    return out
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
        for line in list(difflib.unified_diff(ca[p], cb[p], f"{la}/{p}", f"{lb}/{p}", lineterm=""))[:30]:
            print("    " + line)
sys.exit(1 if bad else 0)
