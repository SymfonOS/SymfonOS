# shellcheck shell=bash
# Gemeinsame Einstellungen für den symfonOS-Bootstrap (wird von allen Stufen eingebunden)
# shellcheck disable=SC2034
set -euo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "$HERE/../.." && pwd)
SYM=${SYM:-/mnt/symfonos}                    # Wurzel des entstehenden Systems
TGT=x86_64-symfonos-linux-gnu                # Cross-Triplet der Werkzeugkette
RECIPES=${RECIPES:-$REPO_ROOT/distro/recipes}
SRC=${SRC:-$SYM/sources}                     # Quellarchive (geteilt mit syp, Prüfsummen aus den Rezepten)
BLD=${BLD:-$SYM/build}
PKGREPO=${PKGREPO:-$REPO_ROOT/out/repo/symfonos}
SYP=${SYP:-$REPO_ROOT/csrc/bin/syp}
NPROC=$(nproc)
export MAKEFLAGS="-j$NPROC" LC_ALL=POSIX
export PATH="$SYM/tools/bin:/usr/bin:/usr/sbin:/bin"
export CONFIG_SITE="$SYM/usr/share/config.site"

log() { printf '\033[1;35m==>\033[0m %s\n' "$*"; }
stamp() { [[ -e "$SYM/.stamps/$1" ]]; }
mark() { mkdir -p "$SYM/.stamps"; touch "$SYM/.stamps/$1"; }
# step <name> <meldung> <befehl> [argumente…]: führt einen Schritt genau einmal aus – in einer Subshell
# mit aktivem set -e – und setzt den Stempel erst nach Erfolg. Bewusst nicht "stamp x || { …; mark x; }":
# in einer ||-Liste ignoriert bash set -e, ein fehlgeschlagenes make würde dann als erledigt markiert.
step() {
  local name=$1 msg=$2; shift 2
  if stamp "$name"; then return 0; fi
  log "$msg"
  ( "$@" )
  mark "$name"
}

# Quelle eines Rezepts laden (Prüfsumme aus dem Rezept), entpacken und dorthin wechseln.
# unpack <rezept> [<nr. der Quelle, Standard 1>]
unpack() {
  local recipe=$RECIPES/$1/SYPBUILD.toml n=${2:-1} file dir
  mkdir -p "$SRC" "$BLD"
  file=$("$SYP" fetch "$recipe" -o "$SRC" | sed -n "${n}p")
  [[ -n "$file" ]] || { echo "Keine Quelle für $1" >&2; exit 1; }
  dir=$( (tar -tf "$file" 2>/dev/null || true) | head -1 | cut -d/ -f1)
  rm -rf "${BLD:?}/$dir"
  tar -xf "$file" -C "$BLD"
  cd "$BLD/$dir"
}
src_path() {  # Pfad der n-ten Quelle eines Rezepts (z. B. Patches)
  "$SYP" fetch "$RECIPES/$1/SYPBUILD.toml" -o "$SRC" | sed -n "${2:-1}p"
}
version_of() { sed -n 's/^version = "\(.*\)-[0-9]*"$/\1/p' "$RECIPES/$1/SYPBUILD.toml" | head -1; }
