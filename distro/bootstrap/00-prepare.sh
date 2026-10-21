#!/usr/bin/env bash
# Stufe 0: Host prüfen, syp bauen, Verzeichnisstruktur von $SYM anlegen
# shellcheck source-path=SCRIPTDIR source=config.sh
. "$(dirname "$0")/config.sh"
log "Host prüfen"
need=(bash gcc g++ make bison gawk m4 patch perl python3 tar xz gzip bzip2 makeinfo sed grep diff find pkg-config)
missing=()
for t in "${need[@]}"; do command -v "$t" >/dev/null || missing+=("$t"); done
((${#missing[@]})) && { echo "Fehlende Host-Werkzeuge: ${missing[*]}" >&2; exit 1; }
[[ $(id -u) -eq 0 ]] || { echo "Bitte als root ausführen (chroot, mount)" >&2; exit 1; }
log "syp für den Host bauen"
make -C "$REPO_ROOT/csrc" -j"$NPROC"
log "Verzeichnisse in $SYM"
mkdir -p "$SYM"/{etc,var,usr/{bin,lib,sbin},tools,sources,build}
for d in bin lib sbin; do [[ -e "$SYM/$d" ]] || ln -s "usr/$d" "$SYM/$d"; done
[[ -e "$SYM/lib64" ]] || ln -s usr/lib "$SYM/lib64"
mark prepare
