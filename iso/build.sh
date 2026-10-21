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
  python-setuptools python-wheel python-pytest python-zstandard python-cryptography pyside6 \
  qt6-svg kpmcore boost yaml-cpp extra-cmake-modules qt6-tools

id "$BUILDER" &>/dev/null || useradd -m "$BUILDER"
echo "$BUILDER ALL=(ALL) NOPASSWD: /usr/bin/pacman" > /etc/sudoers.d/90-builder
install -d -o "$BUILDER" "$LOCALREPO"
chown -R "$BUILDER" "$ROOT"

# Öffentlicher Release-Schlüssel (CI: aus Secret abgeleitet; lokal: Testschlüssel erzeugen)
if [[ -z ${SYP_RELEASE_PUB:-} ]]; then
  PYTHONPATH="$ROOT/src" python -m syp keygen /tmp/symfonos-dev --comment "symfonOS Entwicklung (unsicher)"
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
grep -qx symfonos-keyring "$ROOT/iso/packages.x86_64" || echo symfonos-keyring >> "$ROOT/iso/packages.x86_64"

rm -rf "$WORK"
mkarchiso -v -w "$WORK" -o "$OUT" "$ROOT/iso"
( cd "$OUT" && sha256sum symfonos-*.iso > SHA256SUMS )
echo ":: ISO fertig: $(ls "$OUT"/symfonos-*.iso)"
