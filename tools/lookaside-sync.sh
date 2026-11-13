#!/bin/bash
# Lookaside-Cache für Quellarchive (wie bei Fedora): legt jede Quelle, die ein Rezept nennt, unter ihrer
# sha256 im GitHub-Release »sources« ab. syp lädt von dort zuerst (SYP_LOOKASIDE, distro/bootstrap/40-system.sh),
# damit Bauten nicht an fremden Servern hängen, die ausfallen, umziehen oder alte Versionen löschen.
#
#   tools/lookaside-sync.sh QUELL-CACHE [REZEPT-ORDNER] [TAG]
#
# Hochgeladen wird nur, was im Cache liegt und die Prüfsumme des Rezepts hat. Je Lauf höchstens MAX_MB
# (Standard 2500) und MAX_FILES (Standard 200), damit der Bau-Job im Zeitrahmen bleibt; der Rest folgt im
# nächsten Lauf. Quellen, die kein Rezept mehr nennt, werden entfernt.
set -euo pipefail
CACHE=${1:?Quell-Cache fehlt} RECIPES=${2:-$(cd "$(dirname "$0")/.." && pwd)/distro/recipes} TAG=${3:-sources}
MAX_MB=${MAX_MB:-2500} MAX_FILES=${MAX_FILES:-200}
REPO_SLUG=${GITHUB_REPOSITORY:?GITHUB_REPOSITORY fehlt}
w=$(mktemp -d)
trap 'rm -rf "$w"' EXIT

# sha256 und Dateiname aller Quellen aus den Rezepten
python3 -I - "$RECIPES" > "$w/want.tsv" <<'PY'
import os, sys, tomllib
seen = {}
for d in sorted(os.listdir(sys.argv[1])):
    f = os.path.join(sys.argv[1], d, "SYPBUILD.toml")
    if not os.path.exists(f):
        continue
    with open(f, "rb") as fh:
        r = tomllib.load(fh)
    for s in r.get("source", []):
        url, sum_ = s.get("url"), s.get("sha256", "")
        if url and len(sum_) == 64:
            seen.setdefault(sum_, s.get("filename") or url.rsplit("/", 1)[-1])
for k, v in sorted(seen.items()):
    print(k, v, sep="\t")
PY
[ -s "$w/want.tsv" ] || { echo "keine Quellen in $RECIPES gefunden" >&2; exit 1; }

gh release view "$TAG" --repo "$REPO_SLUG" >/dev/null 2>&1 ||
  gh release create "$TAG" --repo "$REPO_SLUG" --prerelease --title "symfonOS Quellarchive (Lookaside-Cache)" \
    --notes "Quellarchive aller Rezepte, benannt nach ihrer sha256 (syp: SYP_LOOKASIDE). Wird automatisch gepflegt; nicht von Hand ändern."
gh release view "$TAG" --repo "$REPO_SLUG" --json assets -q '.assets[].name' | sort > "$w/have.txt"

mkdir "$w/up"
files=0 bytes=0 missing=0 bad=0
while IFS=$'\t' read -r sum name; do
  grep -qx "$sum" "$w/have.txt" && continue
  f="$CACHE/$name"
  if [ ! -f "$f" ]; then missing=$((missing + 1)); continue; fi
  if [ "$(sha256sum "$f" | cut -d' ' -f1)" != "$sum" ]; then bad=$((bad + 1)); continue; fi
  size=$(stat -c %s "$f")
  if [ "$files" -ge "$MAX_FILES" ] || [ $(((bytes + size) / 1048576)) -gt "$MAX_MB" ]; then continue; fi
  ln -s "$(realpath "$f")" "$w/up/$sum"
  files=$((files + 1)) bytes=$((bytes + size))
done < "$w/want.tsv"

up=0
for f in "$w"/up/*; do
  [ -e "$f" ] || continue
  for _ in 1 2 3; do  # GitHub antwortet gelegentlich mit 502
    if gh release upload "$TAG" --repo "$REPO_SLUG" "$f"; then up=$((up + 1)); break; fi
    sleep 20
  done
done

# Quellen, die kein Rezept mehr nennt (nur Namen aus 64 Hex-Zeichen: andere Dateien gehören nicht dazu)
old=0
cut -f1 "$w/want.tsv" | sort > "$w/want.txt"
while read -r a; do
  [[ $a =~ ^[0-9a-f]{64}$ ]] || continue
  gh release delete-asset "$TAG" "$a" --repo "$REPO_SLUG" --yes && old=$((old + 1))
done < <(comm -23 "$w/have.txt" "$w/want.txt")

total=$(wc -l < "$w/want.tsv")
present=$(comm -12 "$w/have.txt" "$w/want.txt" | wc -l)
echo "::notice title=Lookaside-Cache::$((present + up)) von $total Quellen im Release $TAG ($up neu, $((bytes / 1048576)) MB; $old entfernt; $missing nicht im Cache; $bad mit falscher Summe)"
