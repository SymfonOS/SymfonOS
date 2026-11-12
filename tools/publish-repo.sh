#!/bin/bash
# Veröffentlicht das aus Quellcode gebaute Paket-Repo als GitHub-Release, damit installierte Systeme Updates bekommen.
#
#   tools/publish-repo.sh REPO-ORDNER SCHLÜSSEL [TAG]
#
# GitHub Pages taugt dafür nicht (1 GB je Seite, 100 MB je Datei). Ein Release nimmt Dateien bis 2 GB, kennt aber
# keine Unterordner: die Pakete liegen flach neben index.json.zst, Clients setzen dafür »flat = true« (syp.toml).
# Veröffentlicht wird je Paketname nur die neueste Datei; erst Pakete, dann der frisch signierte Index (neue
# Ablaufzeit), zuletzt werden Pakete entfernt, die der Index nicht mehr nennt.
set -euo pipefail
SRC=${1:?Repo-Ordner fehlt} KEY=${2:?Signaturschlüssel fehlt} TAG=${3:-repo-x86_64}
ROOT=$(cd "$(dirname "$0")/.." && pwd)
SYP="$ROOT/csrc/bin/syp"
REPO_SLUG=${GITHUB_REPOSITORY:?GITHUB_REPOSITORY fehlt}
pub=$(mktemp -d)/symfonos
trap 'rm -rf "$(dirname "$pub")"' EXIT

# neueste Datei je Paketname (gleicher Name, mehrere Versionen aus früheren Läufen)
zstd -dc "$SRC/index.json.zst" | python3 -I -c '
import json, os, sys
src = sys.argv[1]
best = {}
for e in json.load(sys.stdin)["packages"]:
    n, f = e["info"]["name"], os.path.join(src, e["file"])
    if os.path.exists(f) and (n not in best or os.path.getmtime(f) > os.path.getmtime(best[n])):
        best[n] = f
print("\n".join(sorted(best.values())))' "$SRC" > "$(dirname "$pub")/files.txt"
mkdir -p "$pub/pool"
while read -r f; do
  d="$pub/pool/$(basename "$(dirname "$f")")"
  mkdir -p "$d"
  ln "$f" "$d/" 2>/dev/null || cp "$f" "$d/"
done < "$(dirname "$pub")/files.txt"
"$SYP" repo-add "$pub" --name symfonos --arch x86_64 -k "$KEY" >/dev/null
n=$(wc -l < "$(dirname "$pub")/files.txt")
echo ":: $n Pakete für $TAG"

gh release view "$TAG" --repo "$REPO_SLUG" >/dev/null 2>&1 ||
  gh release create "$TAG" --repo "$REPO_SLUG" --prerelease --title "symfonOS Paket-Repo (x86_64)" \
    --notes "Paketquelle für »syp sync«/»syp upgrade« installierter Systeme. Wird automatisch aktualisiert; nicht von Hand ändern."
have=$(gh release view "$TAG" --repo "$REPO_SLUG" --json assets -q '.assets[].name' | sort)
want=$(find "$pub/pool" -name '*.syp' -printf '%f\n' | sort)

upload() {  # GitHub antwortet gelegentlich mit 502: bis zu drei Versuche
  for _ in 1 2 3; do gh release upload "$TAG" --repo "$REPO_SLUG" "$@" && return 0; sleep 30; done; return 1
}
new=0
while read -r f; do
  [ -n "$f" ] || continue
  upload "$(find "$pub/pool" -name "$f" -print -quit)"
  new=$((new + 1))
done < <(comm -13 <(echo "$have") <(echo "$want"))
upload --clobber "$pub/index.json.zst" "$pub/index.json.zst.sig"
old=0
while read -r f; do
  [ -n "$f" ] || continue
  case $f in index.json.zst|index.json.zst.sig) continue ;; esac
  gh release delete-asset "$TAG" "$f" --repo "$REPO_SLUG" --yes && old=$((old + 1))
done < <(comm -23 <(echo "$have") <(echo "$want"))
echo "::notice title=Paket-Repo veröffentlicht::$TAG: $n Pakete ($new neu, $old entfernt)"
