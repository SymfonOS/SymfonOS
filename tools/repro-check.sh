#!/bin/bash
# Reproduzierbarkeit prüfen: baut einige Pakete ein zweites Mal in derselben Bauumgebung und vergleicht
# Inhalt (Pfad, Typ, Rechte, sha256, Verweisziel) mit dem veröffentlichten Paket.
#
#   tools/repro-check.sh REPO REZEPTE BAUROOT SCHLÜSSEL [ANZAHL]     (BAUROOT »-«: auf dem Host bauen)
#
# Gebaut wird zweimal: unterscheiden sich die beiden Bauten, ist das Paket nicht reproduzierbar; sind sie gleich,
# aber anders als das veröffentlichte Paket, ist dieses veraltet (ältere Bauumgebung, gleiche Version).
# Ausgewählt werden rotierend (je Tag andere) Rezepte, deren Quellen zusammen höchstens MAX_SRC_MB (Standard 30)
# groß sind, damit der Lauf kurz bleibt. Gebaut wird gegen eine Kopie des Repos (Pakete als Hardlinks, die
# Zielpakete entfernt), ohne in die Bauumgebung zu installieren. Das Ergebnis steht als Hinweis im Lauf;
# Abweichungen brechen nichts ab (noch kein Gate), die Liste steht in $OUT (Standard repro.txt).
set -euo pipefail
REPO=$(realpath "${1:?Repo fehlt}") RECIPES=$(realpath "${2:?Rezepte fehlen}") BROOT=${3:?Bauumgebung fehlt}
KEY=${4:?Schlüssel fehlt} N=${5:-4}
MAX_SRC_MB=${MAX_SRC_MB:-30} OUT=${OUT:-repro.txt} REPO_NAME=${REPO_NAME:-symfonos}  # Name wie im signierten Index
ROOT=$(cd "$(dirname "$0")/.." && pwd)
SYP="$ROOT/csrc/bin/syp"
CACHE=${SYP_SRCCACHE:-$BROOT/sources}
export SYP_SRCCACHE=$CACHE
w=$(mktemp -d)
trap 'rm -rf "$w"' EXIT

# Auswahl: Name, Paketdatei (wie im Pool)
python3 -I - "$RECIPES" "$CACHE" "$REPO/pool" "$N" "$MAX_SRC_MB" > "$w/pick.tsv" <<'PY'
import os, sys, time, tomllib
recipes, cache, pool, n, max_mb = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4]), int(sys.argv[5])
have = {f: os.path.join(d, f) for d, _, fs in os.walk(pool) for f in fs if f.endswith(".syp")}
cand = []
for d in sorted(os.listdir(recipes)):
    f = os.path.join(recipes, d, "SYPBUILD.toml")
    if not os.path.exists(f):
        continue
    with open(f, "rb") as fh:
        r = tomllib.load(fh)
    p = r.get("package", {})
    name, ver, arch = p.get("name", d), p.get("version", ""), p.get("arch", "x86_64")
    pkg = next((k for k in have if k.startswith(f"{name}-{ver}-") and k[len(name) + len(ver) + 2:] in
                (f"{arch}.syp", "any.syp", "x86_64.syp")), None)
    if not pkg:
        continue
    size = 0
    for s in r.get("source", []):
        fn = s.get("filename") or s.get("url", "").rsplit("/", 1)[-1]
        q = os.path.join(cache, fn)
        size += os.path.getsize(q) if os.path.exists(q) else 10**12
    if size <= max_mb * 1048576:
        cand.append((name, have[pkg]))
if cand:
    start = (int(time.time()) // 86400 * n) % len(cand)
    for i in range(min(n, len(cand))):
        print(*cand[(start + i) % len(cand)], sep="\t")
PY
[ -s "$w/pick.tsv" ] || { echo "::notice title=Reproduzierbarkeit::keine passenden Rezepte"; exit 0; }

# Repo-Kopie ohne die Zielpakete
# Zwei Repo-Kopien ohne die Zielpakete (Pakete als Hardlinks, Index und Metadaten als echte Kopien)
copy_repo() {
  mkdir -p "$1"
  (cd "$REPO" && find . -path ./pool -prune -o -type f -print) | while read -r f; do
    mkdir -p "$1/$(dirname "$f")"; cp -p "$REPO/$f" "$1/$f"
  done
  cp -al "$REPO/pool" "$1/pool"
  while IFS=$'\t' read -r _ orig; do rm -f "$1/pool/${orig#"$REPO/pool/"}"; done < "$w/pick.tsv"
}
copy_repo "$w/repo1"
copy_repo "$w/repo2"

names=$(cut -f1 "$w/pick.tsv" | paste -sd,)
rootarg=(--root "$BROOT")
[ "$BROOT" = - ] && rootarg=()  # ohne Bauumgebung (Tests)
# buildall richtet die Paketquelle der Bauumgebung auf die Repo-Kopie aus: danach wiederherstellen
conf="$BROOT/etc/syp/syp.toml"
if [ -f "$conf" ]; then cp -p "$conf" "$w/syp.toml"; trap 'cp -p "$w/syp.toml" "$conf"; rm -rf "$w"' EXIT; fi
for n in 1 2; do
  echo ":: Bau $n: $names"
  "$SYP" buildall "$RECIPES" --repo "$w/repo$n" "${rootarg[@]}" -k "$KEY" --name "$REPO_NAME" --nocheck --no-install \
    --only "$names" --keep-going 2>&1 | tail -n 20 || true
done

# Vergleich zweier Pakete: Inhalt laut .FILES.json, für kleine Textdateien ein unified diff
cat > "$w/compare.py" <<'PY'
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
pa, pb, la, lb = sys.argv[1:5]
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
PY

# gleich: beide Bauten und das veröffentlichte Paket identisch
# veraltet: beide Bauten gleich, das veröffentlichte Paket nicht (gebaut mit älterer Bauumgebung oder syp)
# nicht reproduzierbar: die beiden Bauten unterscheiden sich
same=() stale=() diff=() fail=()
: > "$OUT"
while IFS=$'\t' read -r name orig; do
  rel=${orig#"$REPO/pool/"} n1="$w/repo1/pool/${orig#"$REPO/pool/"}" n2="$w/repo2/pool/${orig#"$REPO/pool/"}"
  if [ ! -f "$n1" ] || [ ! -f "$n2" ]; then fail+=("$name"); echo "$name: Bau fehlgeschlagen" >> "$OUT"; continue; fi
  if ! python3 -I "$w/compare.py" "$n1" "$n2" bau1 bau2 >> "$OUT"; then
    diff+=("$name"); echo "$name: NICHT reproduzierbar (zwei Bauten verschieden, siehe oben)" >> "$OUT"
  elif ! python3 -I "$w/compare.py" "$orig" "$n1" veröffentlicht neu >> "$OUT"; then
    stale+=("$name"); echo "$name: reproduzierbar, aber veröffentlichtes Paket veraltet ($rel, siehe oben)" >> "$OUT"
  else
    same+=("$name")
  fi
done < "$w/pick.tsv"
total=$(wc -l < "$w/pick.tsv")
echo "::notice title=Reproduzierbarkeit::$(( ${#same[@]} + ${#stale[@]} )) von $total reproduzierbar (${same[*]} ${stale[*]}); veröffentlichtes Paket veraltet: ${stale[*]:-–}; nicht reproduzierbar: ${diff[*]:-–}; Bau fehlgeschlagen: ${fail[*]:-–}"
cat "$OUT"
