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
#   NACHBAU_ENV_REPO  Repo, aus dem die Bauumgebung entsteht (Standard: NACHBAU_REPO). Die CI baut gegen den Stand
#                  von testing; mit dem testing-Repo (…/repo-x86_64-testing) gleicht die Umgebung der der CI, auch wenn
#                  eine neuere Bauabhängigkeit (z. B. binutils) noch nicht in stable angekommen ist.
#   NACHBAU_ROOT   Bauumgebung; Standard $WORK/root (wird angelegt, braucht Root-Rechte); »-«: auf dem Host bauen
#   NACHBAU_PUB    öffentlicher Schlüssel des Repos (Standard distro/keys/symfonos-release.pub)
#   WORK           Arbeitsordner (Standard ~/.cache/symfonos-nachbau; Quellen und Bauumgebung bleiben erhalten)
#   N              Größe der Stichprobe (Standard 5), MAX_MB Obergrenze der installierten Größe (Standard 30)
#   SYP_LOOKASIDE  Lookaside-Cache für Quellen (Standard: Release »sources«; leer = nur Projektserver)
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
ENVREPO=${NACHBAU_ENV_REPO:-$REPO}
PUB=${NACHBAU_PUB:-$ROOT/distro/keys/symfonos-release.pub}
WORK=${WORK:-${XDG_CACHE_HOME:-$HOME/.cache}/symfonos-nachbau}
N=${N:-5} MAX_MB=${MAX_MB:-30} OUT=${OUT:-nachbau.txt}
BROOT=${NACHBAU_ROOT:-$WORK/root}
[ -x "$SYP" ] || make -s -C "$ROOT/csrc"
for t in curl zstd python3; do command -v "$t" >/dev/null || { echo "!! $t fehlt" >&2; exit 2; }; done
mkdir -p "$WORK/repo" "$WORK/envrepo" "$WORK/pkgs" "$WORK/out" "$WORK/sources"
WORK=$(cd "$WORK" && pwd)
export SYP_SRCCACHE="$WORK/sources"
# Quellen zuerst aus dem Lookaside-Cache (GitHub-Release »sources«), dann vom Projektserver; zählt nur mit der
# sha256 aus dem Rezept, ist also nicht mehr Vertrauen als der Projektserver. SYP_LOOKASIDE= schaltet ab.
export SYP_LOOKASIDE=${SYP_LOOKASIDE-https://github.com/SymfonOS/SymfonOS/releases/download/sources}

# Datei aus einem Repo holen: von einer URL flach (GitHub-Release), aus einem Ordner mit Pfad
canon() {   # Repo-Angabe vereinheitlichen: URL bleibt, Ordner (auch file://) wird absolut
  case $1 in
    http://*|https://*) echo "$1" ;;
    *) (cd "${1#file://}" && pwd) ;;
  esac
}
REPO=$(canon "$REPO") ENVREPO=$(canon "$ENVREPO")
fetch() {   # fetch REPO DATEI ZIEL
  case $1 in
    http://*|https://*) curl -fsSL --retry 3 -o "$3" "$1/$(basename "$2")" ;;
    *) cp "$1/$2" "$3" ;;
  esac
}
get() { fetch "$REPO" "$@"; }

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
    slot = int(os.environ.get("GITHUB_RUN_NUMBER") or int(time.time()) // 86400)   # je CI-Lauf eine neue Stichprobe
    start = (slot * n) % len(rows)
    rows = [rows[(start + i) % len(rows)] for i in range(min(n, len(rows)))]
for row in rows:
    print(*row, sep="\t")
PY
[ -s "$WORK/pick.tsv" ] || { echo "!! nichts nachzubauen"; exit 2; }

# 3. Bauumgebung aus dem Repo (alle Pakete, wie die Bauumgebung der CI), bei neuem Index aktualisiert
chroot=()
if [ "$BROOT" != - ]; then
  [ "$(id -u)" = 0 ] || { echo "!! die Bauumgebung braucht Root-Rechte (sudo -E $0 …) oder NACHBAU_ROOT=-" >&2; exit 2; }
  case $ENVREPO in http://*|https://*) url=$ENVREPO flat=true ;; *) url="file://$ENVREPO" flat=false ;; esac
  envidx="$WORK/repo/index.json.zst"
  if [ "$ENVREPO" != "$REPO" ]; then   # eigener Index für die Bauumgebung, ebenfalls mit Signaturprüfung
    echo ":: Bauumgebung aus $ENVREPO"
    fetch "$ENVREPO" index.json.zst "$WORK/envrepo/index.json.zst"
    fetch "$ENVREPO" index.json.zst.sig "$WORK/envrepo/index.json.zst.sig"
    "$SYP" repo-show "$WORK/envrepo" --pub "$PUB" > "$WORK/envindex.json" ||
      { echo "!! Index-Signatur der Bauumgebung ungültig (Schlüssel $PUB)" >&2; exit 2; }
    envidx="$WORK/envrepo/index.json.zst"
  else
    cp "$WORK/index.json" "$WORK/envindex.json"
  fi
  mkdir -p "$BROOT/etc/syp/keys"
  cp "$PUB" "$BROOT/etc/syp/keys/symfonos-release.pub"
  # Paketskripte und Bauten im chroot brauchen /dev, /proc und /sys (syp build --chroot hängt nichts ein)
  for m in dev proc sys; do mkdir -p "$BROOT/$m"; done
  mountpoint -q "$BROOT/dev" || mount --bind /dev "$BROOT/dev"
  mountpoint -q "$BROOT/proc" || mount -t proc proc "$BROOT/proc"
  mountpoint -q "$BROOT/sys" || mount --bind /sys "$BROOT/sys"
  trap 'for m in sys proc dev; do mountpoint -q "$BROOT/$m" && umount -l "$BROOT/$m"; done' EXIT
  write_conf() {   # Paketquelle der Bauumgebung (syp bringt eine eigene syp.toml mit: nach der Installation erneut)
    printf 'arch = "x86_64"\n[[repo]]\nname = "symfonos"\nurl = "%s"\nflat = %s\n[snapshots]\nenabled = false\n' \
      "$url" "$flat" > "$BROOT/etc/syp/syp.toml"
    cp "$PUB" "$BROOT/etc/syp/keys/symfonos-release.pub"
  }
  write_conf
  stamp=$(sha256sum "$envidx" | cut -c1-64)
  if [ "$(cat "$BROOT/.nachbau-index" 2>/dev/null)" != "$stamp" ]; then
    echo ":: Bauumgebung $BROOT aus dem Repo aufsetzen (einmalig groß, danach nur Aktualisierungen)"
    "$SYP" -r "$BROOT" -y sync
    python3 -I -c 'import json,sys; print("\n".join(sorted({e["info"]["name"] for e in json.load(open(sys.argv[1]))["packages"]})))' \
      "$WORK/envindex.json" | xargs "$SYP" -r "$BROOT" -y install --overwrite '/etc/syp/*'
    write_conf
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
  echo "Rechner: $(uname -srm); syp $("$SYP" --version 2>/dev/null | head -1 || echo ?); Index $(sha256sum "$WORK/repo/index.json.zst" | cut -c1-16)$([ "$ENVREPO" = "$REPO" ] || echo "; Bauumgebung aus $ENVREPO")"
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
    diff+=("$name"); cp "$new" "$WORK/out/"   # zum Nachsehen (Workflow lädt es als Artefakt hoch)
    { echo "- $name $ver: **ABWEICHEND**"; echo '```'; head -40 "$WORK/out/$name.diff"; echo '```'; } >> "$OUT"
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
