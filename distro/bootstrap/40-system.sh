#!/usr/bin/env bash
# Stufe 4 (LFS Kap. 8 und darüber hinaus): alle Rezepte als signierte .syp-Pakete in der Wurzel bauen.
# Jedes Paket wird sofort installiert und ersetzt die temporären Werkzeuge -> danach verwaltet syp alles.
#   SYP_KEY=release.key MAX_MINUTES=300 ./40-system.sh      (Exit 3 = Zeitbudget erschöpft, erneut starten)
# shellcheck source-path=SCRIPTDIR source=config.sh
. "$(dirname "$0")/config.sh"
: "${SYP_KEY:?SYP_KEY (geheimer Signaturschlüssel) setzen}"
mkdir -p "$PKGREPO"
# /usr/lib64 als echtes Verzeichnis verleitet Meson und Nettle dazu, dort zu installieren (pkg-config sucht
# nur in /usr/lib). Wie bei Arch: Inhalt nach /usr/lib, dann Verweis lib64 -> lib.
if [[ -d $SYM/usr/lib64 && ! -L $SYM/usr/lib64 ]]; then
  log "/usr/lib64 in der Bauwurzel zusammenführen"
  cp -a "$SYM/usr/lib64/." "$SYM/usr/lib/" && rm -rf --one-file-system "$SYM/usr/lib64" && ln -s lib "$SYM/usr/lib64"
fi
export SYP_SRCCACHE="$SRC" SYP_SOURCE_ROOT="$REPO_ROOT"
# Quellen zuerst aus dem Lookaside-Cache des Projekts (tools/lookaside-sync.sh), dann vom Projektserver;
# geprüft wird immer gegen die sha256 im Rezept. Leer setzen, um nur die Projektserver zu fragen.
export SYP_LOOKASIDE=${SYP_LOOKASIDE-https://github.com/SymfonOS/SymfonOS/releases/download/sources}
# Bauschritte ohne Netz (syp: eigener Netz-Namensraum); ein Rezept, das Netz braucht, sagt [options] network = true
export SYP_BUILD_NET=${SYP_BUILD_NET-strict}
args=(buildall "$RECIPES" --repo "$PKGREPO" --root "$SYM" -k "$SYP_KEY" --name symfonos --nocheck)
[[ -n "${MAX_MINUTES:-}" ]] && args+=(--max-minutes "$MAX_MINUTES")
[[ -n "${ONLY:-}" ]] && args+=(--only "$ONLY")
[[ -n "${KEEP_GOING:-}" ]] && args+=(--keep-going)
log "syp ${args[*]}"
"$SYP" "${args[@]}"
