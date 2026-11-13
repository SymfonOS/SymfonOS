#!/bin/bash
# Kanäle in der CI: veröffentlicht den frisch getesteten Stand als »testing« und übernimmt daraus nach den Regeln
# von tools/promote.py (7 Tage, Sicherheits-Schnellspur, Sperrliste, Abhängigkeiten) Pakete nach »stable«.
#
#   tools/promote-release.sh REPO-ORDNER SCHLÜSSEL ÖFFENTLICHER-SCHLÜSSEL
#
# testing = GitHub-Release $TESTING_TAG (Standard repo-x86_64-testing): jeder Bau, der alle ISO-Tests bestanden hat.
# stable  = GitHub-Release $STABLE_TAG (Standard repo-x86_64): die Paketquelle installierter Systeme.
# Seit wann ein Paket in testing liegt, steht in promotion.json am testing-Release; die Sperrliste ist
# distro/channels/hold im Repository (Änderungen per Pull Request, nachvollziehbar).
# stable wird bei jedem Lauf neu signiert, auch ohne Übernahmen: der Index läuft sonst nach 14 Tagen ab.
set -euo pipefail
SRC=${1:?Repo-Ordner fehlt} KEY=${2:?Signaturschlüssel fehlt} PUB=${3:?öffentlicher Schlüssel fehlt}
TESTING_TAG=${TESTING_TAG:-repo-x86_64-testing} STABLE_TAG=${STABLE_TAG:-repo-x86_64}
REPO_SLUG=${GITHUB_REPOSITORY:?GITHUB_REPOSITORY fehlt}
ROOT=$(cd "$(dirname "$0")/.." && pwd)
SYP="$ROOT/csrc/bin/syp"
SRC=$(cd "$SRC" && pwd)
w=$(mktemp -d)
trap 'rm -rf "$w"' EXIT
st=$w/stable
mkdir -p "$st"

# 1. testing veröffentlichen
"$ROOT/tools/publish-repo.sh" "$SRC" "$KEY" "$TESTING_TAG"

# 2. Stand von stable laden. Gibt es das Release, MUSS der Index geladen und gültig signiert sein: ein leerer
#    Stand würde beim Veröffentlichen alle Pakete aus stable löschen.
if gh release view "$STABLE_TAG" --repo "$REPO_SLUG" >/dev/null 2>&1; then
  gh release download "$STABLE_TAG" --repo "$REPO_SLUG" --pattern index.json.zst --pattern index.json.zst.sig --dir "$st"
  "$SYP" repo-show "$st" --pub "$PUB" > "$w/stable.json"
  # Paketdateien: aus dem lokalen Pool (dort liegen auch ältere Versionen), sonst vom stable-Release
  python3 -I -c '
import json, os, sys
idx, pool = json.load(open(sys.argv[1])), sys.argv[2]
local = {f: os.path.join(d, f) for d, _, fs in os.walk(pool) for f in fs if f.endswith(".syp")}
for e in idx["packages"]:
    b = os.path.basename(e["file"])
    print(e["file"], local.get(b, "-"), sep="\t")' "$w/stable.json" "$SRC/pool" > "$w/stable-files.txt"
  before=$(wc -l < "$w/stable-files.txt")
  fetched=0
  while IFS=$'\t' read -r rel loc; do
    mkdir -p "$st/$(dirname "$rel")"
    if [ "$loc" != - ]; then
      ln "$loc" "$st/$rel" 2>/dev/null || cp -p "$loc" "$st/$rel"
    else
      gh release download "$STABLE_TAG" --repo "$REPO_SLUG" --pattern "$(basename "$rel")" --dir "$st/$(dirname "$rel")"
      fetched=$((fetched + 1))
    fi
  done < "$w/stable-files.txt"
  echo ":: stable: $before Pakete ($fetched vom Release geladen)"
else
  before=0
  echo ":: stable ($STABLE_TAG) gibt es noch nicht, wird angelegt"
fi

# 3. übernehmen
gh release download "$TESTING_TAG" --repo "$REPO_SLUG" --pattern promotion.json --dir "$SRC" --clobber 2>/dev/null ||
  echo ":: noch keine promotion.json am testing-Release: Wartezeit beginnt jetzt für alle Pakete"
hold="$ROOT/distro/channels/hold"
SYP_BIN="$SYP" python3 "$ROOT/tools/promote.py" --testing "$SRC" --stable "$st" --pub "$PUB" -k "$KEY" \
  --hold "$hold" ${MIN_DAYS:+--min-days "$MIN_DAYS"} | tee "$w/promote.log"
# Übernahme-Stand sofort sichern: die Wartezeit zählt auch, wenn noch nichts übernommen wurde
gh release upload "$TESTING_TAG" --repo "$REPO_SLUG" --clobber "$SRC/promotion.json"
[ -f "$st/index.json.zst" ] || { echo ":: stable ist leer, nichts zu veröffentlichen"; exit 0; }
"$SYP" repo-show "$st" --pub "$PUB" > "$w/stable-neu.json"
after=$(python3 -I -c 'import json,sys; print(len({e["info"]["name"] for e in json.load(open(sys.argv[1]))["packages"]}))' "$w/stable-neu.json")
if [ "$after" -lt "$before" ]; then
  echo "::error title=stable::$before Pakete vorher, $after nachher – nicht veröffentlicht"
  exit 1
fi

# 4. stable veröffentlichen (neu signiert)
"$ROOT/tools/publish-repo.sh" "$st" "$KEY" "$STABLE_TAG"
n=$(grep -c '→ stable' "$w/promote.log" || true)
echo "::notice title=testing → stable::$n Pakete übernommen; $(tail -1 "$w/promote.log" | sed 's/^:: //')"
