#!/usr/bin/env bash
# Spiegelt Arch-Binärpakete als signierte syp-Repositories (core, extra).
# Läuft in Arch Linux. Standard: alle Pakete des ISOs inkl. Abhängigkeiten; ALL=1 für komplette Repos.
#   SYP_KEY=/pfad/release.key ./tools/repo-sync.sh <ausgabe>
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
OUT=${1:-$ROOT/out/repo}
: "${SYP_KEY:?SYP_KEY (geheimer Signaturschlüssel) nicht gesetzt}"
export SYP_ZSTD_LEVEL=${SYP_ZSTD_LEVEL:-15}
[[ -x $ROOT/csrc/bin/syp ]] || make -C "$ROOT/csrc" -j"$(nproc)"
SYP=$ROOT/csrc/bin/syp SYP_CONVERT=$ROOT/csrc/bin/syp-convert
CACHE=${CACHE:-/var/cache/symfonos-sync}
mkdir -p "$CACHE" "$OUT"
pacman -Sy --noconfirm >/dev/null

if [[ ${ALL:-0} == 1 ]]; then
  mapfile -t targets < <(pacman -Slq core extra)
else
  mapfile -t targets < <(grep -Ev '^\s*(#|$)' "$ROOT/iso/packages.x86_64" |
                         grep -Ev '^(symfonos-|calamares$|ckbcomp$)')
fi
# Download-URLs inkl. aller Abhängigkeiten auflösen, Repo aus URL ableiten
pacman -Sp --noconfirm "${targets[@]}" | sort -u > "$CACHE/urls.txt"
echo ":: $(wc -l < "$CACHE/urls.txt") Pakete"
while read -r url; do
  repo=$(sed -E 's#.*/(core|extra|multilib)/os/.*#\1#' <<<"$url")
  f="$CACHE/$(basename "$url")"
  [[ -e $f ]] || curl -fsSL --retry 3 -o "$f" "$url"
  conv="$OUT/$repo/x86_64/pool"
  mkdir -p "$conv"
  name=$(basename "$f" | sed -E 's/-[^-]+-[^-]+-[^-]+\.pkg\.tar\.zst$//')
  ls "$conv"/*/"$name"-*.syp &>/dev/null && continue
  "$SYP_CONVERT" "$f" -o "$conv/${name:0:1}" -k "$SYP_KEY" --offline --mark-scripts-reviewed \
    --json > "$CACHE/$name.report.json" || echo "!! $name fehlgeschlagen" >&2
done < "$CACHE/urls.txt"

for repo in core extra multilib; do
  [[ -d $OUT/$repo/x86_64 ]] || continue
  "$SYP" repo-add "$OUT/$repo/x86_64" --name "$repo" --arch x86_64 -k "$SYP_KEY"
done
echo ":: Repos unter $OUT"
