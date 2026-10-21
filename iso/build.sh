#!/usr/bin/env bash
# Baut das symfonOS-ISO. Läuft als root in Arch Linux (z. B. Container archlinux:latest, --privileged).
#   ./iso/build.sh [ausgabeverzeichnis]
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
OUT=${1:-$ROOT/out}
WORK=${WORK:-/tmp/symfonos-work}
LOCALREPO=/tmp/symfonos-localrepo
BUILDER=builder

pacman -Syu --noconfirm --needed archiso base-devel git python-build python-installer \
  python-setuptools python-wheel python-pytest pyside6 libarchive zstd libsodium sqlite curl \
  qt6-svg kpmcore boost yaml-cpp extra-cmake-modules qt6-tools
make -C "$ROOT/csrc" -j"$(nproc)"   # syp und syp-convert (C) für Schlüssel und Tests

id "$BUILDER" &>/dev/null || useradd -m "$BUILDER"
echo "$BUILDER ALL=(ALL) NOPASSWD: /usr/bin/pacman" > /etc/sudoers.d/90-builder
install -d -o "$BUILDER" "$LOCALREPO"
chown -R "$BUILDER" "$ROOT"

# Öffentlicher Release-Schlüssel (CI: aus Secret abgeleitet; lokal: Testschlüssel erzeugen)
if [[ -z ${SYP_RELEASE_PUB:-} ]]; then
  "$ROOT/csrc/bin/syp" keygen /tmp/symfonos-dev --comment "symfonOS Entwicklung (unsicher)"
  export SYP_RELEASE_PUB=/tmp/symfonos-dev.pub
  echo "WARNUNG: Entwicklungsschlüssel erzeugt – nicht für Releases verwenden" >&2
fi
chmod 644 "$SYP_RELEASE_PUB"

build_pkg() {  # $1 = Verzeichnis mit PKGBUILD
  sudo -u "$BUILDER" env SYP_RELEASE_PUB="$SYP_RELEASE_PUB" PKGDEST="$LOCALREPO" \
    bash -c "cd '$1' && makepkg --noconfirm --syncdeps --cleanbuild --force"
}

for p in symfonos-tools symfonos-branding symfonos-keyring; do
  build_pkg "$ROOT/packaging/$p"
done
while read -r aur; do
  [[ -z $aur || $aur == \#* ]] && continue
  d=/tmp/aur-$aur
  rm -rf "$d" && sudo -u "$BUILDER" git clone --depth 1 "https://aur.archlinux.org/$aur.git" "$d"
  build_pkg "$d"
  pacman -U --noconfirm "$LOCALREPO/$aur"-*.pkg.tar.zst   # Build-Abhängigkeit für Folgepakete
done < "$ROOT/packaging/aur.txt"

repo-add "$LOCALREPO/symfonos-local.db.tar.gz" "$LOCALREPO"/*.pkg.tar.zst

# Arbeitskopie des Profils; Pakete, die es (nicht mehr) gibt, werden gemeldet statt den Bau abzubrechen
PROFILE=/tmp/symfonos-profile
rm -rf "$PROFILE" && cp -a "$ROOT/iso" "$PROFILE"
DBP=/tmp/symfonos-pacdb && rm -rf "$DBP" && mkdir -p "$DBP"
pacman --config "$PROFILE/pacman.conf" --dbpath "$DBP" -Sy >/dev/null
grep -Ev '^\s*(#|$)' "$ROOT/iso/packages.x86_64" > /tmp/wanted.txt
echo symfonos-keyring >> /tmp/wanted.txt
: > "$PROFILE/packages.x86_64"
while read -r p; do
  if pacman --config "$PROFILE/pacman.conf" --dbpath "$DBP" -Si "$p" &>/dev/null; then
    echo "$p" >> "$PROFILE/packages.x86_64"
  else
    echo "::warning title=Paket fehlt::$p ist in keinem Repo vorhanden und wird ausgelassen"
  fi
done < /tmp/wanted.txt

rm -rf "$WORK"
mkarchiso -v -w "$WORK" -o "$OUT" "$PROFILE"
( cd "$OUT" && sha256sum symfonos-*.iso > SHA256SUMS )
echo ":: ISO fertig: $(ls "$OUT"/symfonos-*.iso)"
