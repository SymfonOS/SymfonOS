#!/bin/bash
# Reproduzierbarkeit prüfen: baut einige Pakete ein zweites Mal in derselben Bauumgebung und vergleicht
# Inhalt (Pfad, Typ, Rechte, sha256, Verweisziel) mit dem veröffentlichten Paket.
#
#   tools/repro-check.sh REPO REZEPTE BAUROOT SCHLÜSSEL [ANZAHL]     (BAUROOT »-«: auf dem Host bauen)
#
# Ausgewählt werden rotierend (je Tag andere) Rezepte, deren Quellen zusammen höchstens MAX_SRC_MB (Standard 30)
# groß sind, damit der Lauf kurz bleibt. Gebaut wird gegen eine Kopie des Repos (Pakete als Hardlinks, die
# Zielpakete entfernt), ohne in die Bauumgebung zu installieren. Das Ergebnis steht als Hinweis im Lauf;
# Abweichungen brechen nichts ab (noch kein Gate), die Liste steht in $OUT (Standard repro.txt).
set -euo pipefail
REPO=$(realpath "${1:?Repo fehlt}") RECIPES=$(realpath "${2:?Rezepte fehlen}") BROOT=${3:?Bauumgebung fehlt}
KEY=${4:?Schlüssel fehlt} N=${5:-4}
MAX_SRC_MB=${MAX_SRC_MB:-30} OUT=${OUT:-repro.txt}
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
mkdir -p "$w/repo"
(cd "$REPO" && find . -path ./pool -prune -o -type f -print) | while read -r f; do
  mkdir -p "$w/repo/$(dirname "$f")"; cp -p "$REPO/$f" "$w/repo/$f"
done
cp -al "$REPO/pool" "$w/repo/pool"
while IFS=$'\t' read -r _ orig; do rm -f "$w/repo/pool/${orig#"$REPO/pool/"}"; done < "$w/pick.tsv"

names=$(cut -f1 "$w/pick.tsv" | paste -sd,)
echo ":: zweiter Bau: $names"
rootarg=(--root "$BROOT")
[ "$BROOT" = - ] && rootarg=()  # ohne Bauumgebung (Tests)
"$SYP" buildall "$RECIPES" --repo "$w/repo" "${rootarg[@]}" -k "$KEY" --name repro --nocheck --no-install \
  --only "$names" --keep-going 2>&1 | tail -n 40 || true

same=() diff=() fail=()
: > "$OUT"
while IFS=$'\t' read -r name orig; do
  new="$w/repo/pool/${orig#"$REPO/pool/"}"
  if [ ! -f "$new" ]; then fail+=("$name"); echo "$name: zweiter Bau fehlgeschlagen" >> "$OUT"; continue; fi
  if python3 -I - "$orig" "$new" >> "$OUT" <<'PY'
import json, sys, tarfile
def files(p):
    with tarfile.open(p) as tf:
        return {f["path"]: {k: v for k, v in f.items() if k in ("type", "mode", "sha256", "target", "size")}
                for f in json.loads(tf.extractfile(".FILES.json").read())}
a, b = files(sys.argv[1]), files(sys.argv[2])
bad = sorted(p for p in a.keys() | b.keys() if a.get(p) != b.get(p))
for p in bad[:20]:
    print(f"  {p}: {a.get(p)} != {b.get(p)}")
sys.exit(1 if bad else 0)
PY
  then same+=("$name")
  else diff+=("$name"); echo "$name: abweichend (siehe oben)" >> "$OUT"
  fi
done < "$w/pick.tsv"
echo "::notice title=Reproduzierbarkeit::${#same[@]} von $(wc -l < "$w/pick.tsv") gleich (${same[*]:-–}); abweichend: ${diff[*]:-–}; zweiter Bau fehlgeschlagen: ${fail[*]:-–}"
cat "$OUT"
