#!/bin/bash
# Unabhängiger Nachbau: veröffentlichte Pakete auf dem eigenen Rechner aus den Rezepten neu bauen und mit dem
# veröffentlichten Paket vergleichen – außerhalb der symfonOS-CI, von jedem, der dem Ergebnis der CI nicht
# einfach glauben will (wie die Rebuilder von Arch und Debian).
#
#   tools/nachbau.sh [PAKET ...]          # ohne Pakete: Stichprobe von N kleinen Paketen (rotierend je Tag)
#
# Ablauf: signierten Index des stable-Repos laden und mit distro/keys/symfonos-release.pub prüfen; Bauumgebung
# aus genau diesem Repo aufsetzen (alle Pakete, wie die Bauumgebung der CI); jedes Paket mit `syp build --chroot`
# bauen; das veröffentlichte Paket laden (sha256 aus dem signierten Index) und den Inhalt vergleichen
# (tools/pkg-compare.py: Pfade, Rechte, sha256, Verweise – nicht Signatur und Bauzeit).
#
# Umgebung:
#   NACHBAU_REPO   Repo-URL oder -Ordner (Standard: das stable-Release auf GitHub, Dateien flach)
#   NACHBAU_ROOT   Bauumgebung; Standard $WORK/root (wird angelegt, braucht Root-Rechte); »-«: auf dem Host bauen
#   NACHBAU_PUB    öffentlicher Schlüssel des Repos (Standard distro/keys/symfonos-release.pub)
#   WORK           Arbeitsordner (Standard ~/.cache/symfonos-nachbau; Quellen und Bauumgebung bleiben erhalten)
#   N              Größe der Stichprobe (Standard 5), MAX_MB Obergrenze der installierten Größe (Standard 30)
#   OUT            Ergebnisdatei (Standard nachbau.txt): Abschnitt zum Einfügen in ein Issue »nachbau: …«
#
# Rückgabe: 0 = alle nachgebauten Pakete gleich, 1 = mindestens eines weicht ab, 2 = nichts nachgebaut.
# Die Rezepte müssen zur veröffentlichten Version passen (sonst wird das Paket übersprungen und genannt):
# den Checkout auf den Stand des Releases bringen, z. B. `git checkout main && git pull`.
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
SYP="$ROOT/csrc/bin/syp"
RECIPES=${NACHBAU_RECIPES:-$ROOT/distro/recipes}
REPO=${NACHBAU_REPO:-https://github.com/SymfonOS/SymfonOS/releases/download/repo-x86_64}
PUB=${NACHBAU_PUB:-$ROOT/distro/keys/symfonos-release.pub}
WORK=${WORK:-${XDG_CACHE_HOME:-$HOME/.cache}/symfonos-nachbau}
N=${N:-5} MAX_MB=${MAX_MB:-30} OUT=${OUT:-nachbau.txt}
BROOT=${NACHBAU_ROOT:-$WORK/root}
[ -x "$SYP" ] || make -s -C "$ROOT/csrc"
for t in curl zstd python3; do command -v "$t" >/dev/null || { echo "!! $t fehlt" >&2; exit 2; }; done
mkdir -p "$WORK/repo" "$WORK/pkgs" "$WORK/out" "$WORK/sources"
WORK=$(cd "$WORK" && pwd)
export SYP_SRCCACHE="$WORK/sources"

# Datei aus dem Repo holen: von einer URL flach (GitHub-Release), aus einem Ordner mit Pfad
case $REPO in
  http://*|https://*) remote=1 ;;
  file://*) REPO=${REPO#file://}; remote=0 ;;
  *) remote=0 ;;
esac
[ "$remote" = 1 ] || REPO=$(cd "$REPO" && pwd)
get() {
  if [ "$remote" = 1 ]; then curl -fsSL --retry 3 -o "$2" "$REPO/$(basename "$1")"; else cp "$REPO/$1" "$2"; fi
}

# 1. Index laden, Signatur prüfen
echo ":: Index von $REPO"
get index.json.zst "$WORK/repo/index.json.zst"
get index.json.zst.sig "$WORK/repo/index.json.zst.sig"
"$SYP" repo-show "$WORK/repo" --pub "$PUB" > "$WORK/index.json" ||
  { echo "!! Index-Signatur ungültig (Schlüssel $PUB)" >&2; exit 2; }

# 2. Auswahl: Name, Version, Datei, sha256 – nur wo das Rezept dieselbe Version nennt
python3 -I - "$WORK/index.json" "$RECIPES" "$N" "$MAX_MB" "$@" > "$WORK/pick.tsv" <<'PY'
import json, os, sys, time, tomllib
idx, recipes, n, max_mb, want = json.load(open(sys.argv[1])), sys.argv[2], int(sys.argv[3]), int(sys.argv[4]), sys.argv[5:]
pub = {e["info"]["name"]: e for e in idx["packages"]}
def recipe(name):
    for d in (name, pub[name]["info"].get("origin", {}).get("recipe", name)):
        f = os.path.join(recipes, d, "SYPBUILD.toml")
        if os.path.exists(f):
            with open(f, "rb") as fh:
                return d, tomllib.load(fh)
    return None, None
rows = []
for name in (want or sorted(pub)):
    if name not in pub:
        print(f"!! {name}: nicht im Repo", file=sys.stderr)
        continue
    e = pub[name]
    d, r = recipe(name)
    if not r:
        print(f"!! {name}: kein Rezept", file=sys.stderr)
        continue
    rv = r.get("package", {}).get("version")
    if rv != e["info"]["version"]:
        if want:
            print(f"!! {name}: Rezept {rv}, veröffentlicht {e['info']['version']} – übersprungen", file=sys.stderr)
        continue
    if not want and e["info"].get("installed_size", 0) > max_mb * 1048576:
        continue
    rows.append((name, d, e["info"]["version"], e["file"], e["sha256"]))
if not want and rows:
    start = (int(time.time()) // 86400 * n) % len(rows)
    rows = [rows[(start + i) % len(rows)] for i in range(min(n, len(rows)))]
for row in rows:
    print(*row, sep="\t")
PY
[ -s "$WORK/pick.tsv" ] || { echo "!! nichts nachzubauen"; exit 2; }

# 3. Bauumgebung aus dem Repo (alle Pakete, wie die Bauumgebung der CI), bei neuem Index aktualisiert
chroot=()
if [ "$BROOT" != - ]; then
  [ "$(id -u)" = 0 ] || { echo "!! die Bauumgebung braucht Root-Rechte (sudo -E $0 …) oder NACHBAU_ROOT=-" >&2; exit 2; }
  mkdir -p "$BROOT/etc/syp/keys"
  cp "$PUB" "$BROOT/etc/syp/keys/symfonos-release.pub"
  url=$REPO; [ "$remote" = 1 ] || url="file://$REPO"
  printf 'arch = "x86_64"\n[[repo]]\nname = "symfonos"\nurl = "%s"\nflat = %s\n[snapshots]\nenabled = false\n' \
    "$url" "$([ "$remote" = 1 ] && echo true || echo false)" > "$BROOT/etc/syp/syp.toml"
  stamp=$(sha256sum "$WORK/repo/index.json.zst" | cut -c1-64)
  if [ "$(cat "$BROOT/.nachbau-index" 2>/dev/null)" != "$stamp" ]; then
    echo ":: Bauumgebung $BROOT aus dem Repo aufsetzen (einmalig groß, danach nur Aktualisierungen)"
    "$SYP" -r "$BROOT" -y sync
    python3 -I -c 'import json,sys; print("\n".join(sorted({e["info"]["name"] for e in json.load(open(sys.argv[1]))["packages"]})))' \
      "$WORK/index.json" | xargs "$SYP" -r "$BROOT" -y install
    "$SYP" -r "$BROOT" -y upgrade --no-sync
    echo "$stamp" > "$BROOT/.nachbau-index"
  fi
  chroot=(--chroot "$BROOT")
fi

# 4. bauen und vergleichen
[ -f "$WORK/nachbau.key" ] || "$SYP" keygen "$WORK/nachbau" --comment "Nachbau (nur lokal)" >/dev/null
same=() diff=() fail=()
{
  echo "### Nachbau $(date -u +%Y-%m-%d)"
  echo "Rechner: $(uname -srm); syp $("$SYP" --version 2>/dev/null | head -1 || echo ?); Index $(sha256sum "$WORK/repo/index.json.zst" | cut -c1-16)"
  echo
} > "$OUT"
while IFS=$'\t' read -r name dir ver file sha; do
  echo ":: $name $ver"
  pub="$WORK/pkgs/$(basename "$file")"
  if ! { [ -f "$pub" ] && echo "$sha  $pub" | sha256sum -c --quiet 2>/dev/null; }; then
    get "$file" "$pub" && echo "$sha  $pub" | sha256sum -c --quiet ||
      { fail+=("$name"); echo "- $name $ver: veröffentlichtes Paket nicht ladbar oder Prüfsumme falsch" >> "$OUT"; continue; }
  fi
  rm -rf "$WORK/out/$name"
  if ! "$SYP" build "$RECIPES/$dir/SYPBUILD.toml" -o "$WORK/out/$name" -k "$WORK/nachbau.key" --nocheck "${chroot[@]}" \
      > "$WORK/out/$name.log" 2>&1; then
    fail+=("$name"); echo "- $name $ver: Bau fehlgeschlagen (Protokoll $WORK/out/$name.log)" >> "$OUT"; continue
  fi
  new=$(find "$WORK/out/$name" -name "$name-$ver-*.syp" | head -1)
  if [ -z "$new" ]; then fail+=("$name"); echo "- $name $ver: kein Paket gebaut" >> "$OUT"; continue; fi
  if python3 -I "$ROOT/tools/pkg-compare.py" "$pub" "$new" veröffentlicht nachgebaut > "$WORK/out/$name.diff"; then
    same+=("$name"); echo "- $name $ver: gleich" >> "$OUT"
  else
    diff+=("$name"); { echo "- $name $ver: **ABWEICHEND**"; echo '```'; head -40 "$WORK/out/$name.diff"; echo '```'; } >> "$OUT"
  fi
done < "$WORK/pick.tsv"
{
  echo
  echo "Ergebnis: ${#same[@]} gleich, ${#diff[@]} abweichend, ${#fail[@]} nicht gebaut"
} >> "$OUT"
cat "$OUT"
[ ${#diff[@]} -eq 0 ] || exit 1
[ ${#same[@]} -gt 0 ] || exit 2
exit 0
