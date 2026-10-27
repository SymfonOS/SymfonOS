#!/usr/bin/env bash
# Stufe 5: bootfähiges Live-/Installations-ISO ausschließlich aus dem eigenen syp-Repository.
#   SYP_PUB=release.pub [VARIANT=base|desktop] ./50-image.sh [ausgabe.iso]
# base: Konsole (symfonos-base); desktop: KDE Plasma unter Wayland mit SDDM (symfonos-desktop)
# shellcheck source-path=SCRIPTDIR source=config.sh
. "$(dirname "$0")/config.sh"
: "${SYP_PUB:?SYP_PUB (öffentlicher Release-Schlüssel) setzen}"
VARIANT=${VARIANT:-base}
case $VARIANT in
  base) META=symfonos-base ;;
  desktop) META=symfonos-desktop ;;
  *) echo "VARIANT muss base oder desktop sein" >&2; exit 2 ;;
esac
OUTISO=${1:-$REPO_ROOT/out/symfonos-$VARIANT-$(date +%Y.%m.%d)-x86_64.iso}
WORK=$SYM/var/tmp/image            # liegt in der Werkzeugwurzel, damit mksquashfs/grub-mkrescue dort laufen
IMG=$WORK/rootfs ISO=$WORK/iso
LABEL=SYMFONOS
# Reste eines abgebrochenen Laufs: erst aushängen, dann prüfen, dann löschen (nie rm -rf über /dev-Bindungen)
if [[ -d $WORK ]]; then
  # grep ohne Treffer (nichts eingehängt, z. B. nach dem Basis-Image) darf mit pipefail nicht abbrechen
  findmnt -rn -o TARGET | { grep "^$WORK" || true; } | sort -r | while read -r m; do umount -l "$m"; done
  if findmnt -rn -o TARGET | grep -q "^$WORK"; then echo "unter $WORK ist noch etwas eingehängt" >&2; exit 1; fi
  rm -rf --one-file-system "$WORK"
fi
mkdir -p "$IMG/etc/syp/keys" "$ISO"/{boot,LiveOS}
for m in dev proc sys run; do mkdir -p "$SYM/$m"; mountpoint -q "$SYM/$m" || mount --bind "/$m" "$SYM/$m"; done
trap 'for m in run sys proc dev; do mountpoint -q "$IMG/$m" && umount -l "$IMG/$m"; mountpoint -q "$SYM/$m" && umount -l "$SYM/$m"; done; true' EXIT

# Der Installer kopiert das Live-System (samt Paketdatenbank); ein zusätzliches Paket-Repository auf dem
# Medium würde jedes Programm doppelt enthalten. INCLUDE_REPO=1 legt es trotzdem bei (Offline-Paketauswahl).
if [[ ${INCLUDE_REPO:-0} == 1 ]]; then
  log "Repository ins ISO übernehmen"
  cp -a "$PKGREPO"/. "$ISO/repo/"
  cp "$SYP_PUB" "$ISO/repo/symfonos-release.pub"
fi

log "Live-System mit syp aufbauen"
cat > "$IMG/etc/syp/syp.toml" <<CONF
arch = "x86_64"
[[repo]]
name = "symfonos"
url = "file://$PKGREPO"
[snapshots]
enabled = false
# Live-Medium schlank halten (eine Download-Datei unter 2 GiB): Doku, fremde Übersetzungen und Firmware
# reiner Server- und ARM-Hardware werden nicht ausgepackt, bleiben aber den Paketen zugeordnet.
# Das installierte System bekommt die normale Konfiguration; »syp reinstall« holt Ausgelassenes nach.
[install]
exclude = ["/usr/share/doc/*", "/usr/share/gtk-doc/*", "/usr/share/info/*", "/usr/share/help/*",
           "/usr/share/locale/*", "/usr/share/qt6/translations/*", "/usr/share/man/??/*", "/usr/share/man/??_??/*",
           "/usr/lib/firmware/netronome/*", "/usr/lib/firmware/liquidio/*", "/usr/lib/firmware/mellanox/*",
           "/usr/lib/firmware/mlxsw_spectrum/*", "/usr/lib/firmware/cxgb4/*", "/usr/lib/firmware/bnx2x/*",
           "/usr/lib/firmware/qed/*", "/usr/lib/firmware/qcom/*", "/usr/lib/firmware/mrvl/prestera/*",
           "/usr/lib/firmware/dpaa2/*", "/usr/lib/firmware/ti-keystone/*", "/usr/lib/firmware/meson/*",
           "/usr/lib/firmware/imx/*", "/usr/lib/firmware/arm/*", "/usr/lib/firmware/cnm/*",
           "/usr/lib/firmware/inside-secure/*", "/usr/lib/firmware/ql2*", "/usr/lib/firmware/phanfw.bin*"]
include = ["/usr/share/locale/de/*", "/usr/share/locale/de_*", "/usr/share/locale/en/*", "/usr/share/locale/en_*",
           "/usr/share/locale/locale.alias", "/usr/share/qt6/translations/*_de.qm", "/usr/share/qt6/translations/*_en.qm",
           "/usr/share/help/de/*", "/usr/share/help/C/*", "/usr/share/doc/HTML/de/*"]
CONF
cp "$SYP_PUB" "$IMG/etc/syp/keys/symfonos-release.pub"
"$SYP" -r "$IMG" sync
"$SYP" -r "$IMG" -y install --overwrite /etc/syp/syp.toml "$META" symfonos-installer   # vorläufige Konfiguration ersetzen
# Für das installierte System zeigt die Konfiguration wieder auf die Online-Repos
cp "$REPO_ROOT/system/etc/syp/syp.toml" "$IMG/etc/syp/syp.toml"
# Fehlende Laufzeitabhängigkeiten (Bibliothek nicht installiert) zeigen sich sonst erst beim Programmstart
if ! libs=$(python3 -I "$REPO_ROOT/tools/check-libs.py" "$IMG"); then
  log "Warnung: Bibliotheken fehlen im Live-System"
  while read -r l; do log "  $l"; echo "::warning title=Fehlende Bibliothek ($VARIANT)::$l"; done <<<"$libs"
fi

log "Live-Konfiguration"
in_img() { chroot "$IMG" /usr/bin/env -i PATH=/usr/bin:/usr/sbin HOME=/root /bin/bash -euo pipefail -c "$1"; }
for m in dev proc sys run; do mkdir -p "$IMG/$m"; mount --bind "/$m" "$IMG/$m"; done
in_img "systemd-sysusers; systemd-tmpfiles --create || true
  useradd -m -G wheel,audio,video,input -s /bin/bash -c 'symfonOS Live' live; passwd -d live
  echo 'live ALL=(ALL:ALL) NOPASSWD: ALL' > /etc/sudoers.d/90-live; chmod 440 /etc/sudoers.d/90-live
  systemctl preset-all >/dev/null 2>&1 || true   # Firewall, Audit, Zeit, DNS wie bei einer Installation
  systemctl --global preset-all >/dev/null 2>&1 || true   # Benutzerdienste (PipeWire, WirePlumber)
  systemctl enable systemd-networkd systemd-resolved systemd-timesyncd iwd
  : > /etc/machine-id"
if [[ $VARIANT == desktop ]]; then
  # Live-Benutzer meldet sich automatisch in Plasma (Wayland) an; der Installer entfernt das wieder
  mkdir -p "$IMG/etc/sddm.conf.d"
  cat > "$IMG/etc/sddm.conf.d/20-live.conf" <<'CONF'
[Autologin]
User=live
Session=plasma
Relogin=false
CONF
else
  mkdir -p "$IMG/etc/systemd/system/getty@tty1.service.d"
  cat > "$IMG/etc/systemd/system/getty@tty1.service.d/autologin.conf" <<'CONF'
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin live --noclear %I $TERM
CONF
fi
cat > "$IMG/etc/motd" <<CONF

  symfonOS Live ($([[ $VARIANT == desktop ]] && echo "Desktop" || echo "Basissystem"))
  Installieren:  sudo symfonos-install
  Netzwerk:      iwctl  (WLAN)  ·  networkctl status
  Hilfe:         man syp  ·  https://github.com/thomasrohm23-bit/symfonOS

CONF
# Größenübersicht (das ISO muss als eine Datei unter 2 GiB bleiben)
log "Größe des Live-Systems: $(du -sxm "$IMG" | cut -f1) MiB"
# head schließt die Pipe vorzeitig; sort bekommt dann SIGPIPE, das darf mit pipefail nicht abbrechen
{ du -xm --max-depth=3 "$IMG/usr" 2>/dev/null | sort -rn || true; } | head -25 | while read -r mb d; do log "Größe: ${mb} MiB ${d#"$IMG"}"; done
KVER=$(ls "$IMG/usr/lib/modules" | head -1)
log "Initramfs (dracut, dmsquash-live) für Kernel $KVER"
in_img "dracut --force --no-hostonly --no-hostonly-cmdline --add 'dmsquash-live' --kver '$KVER' /boot/initramfs-live.img"
cp "$IMG/usr/lib/modules/$KVER/vmlinuz" "$ISO/boot/vmlinuz"
mv "$IMG/boot/initramfs-live.img" "$ISO/boot/initramfs.img"
for m in run sys proc dev; do umount -l "$IMG/$m"; done

# SELinux-Dateikontexte setzen (wie Fedora-Live-Medien); SquashFS übernimmt die xattrs
FC=$IMG/etc/selinux/refpolicy/contexts/files/file_contexts
if [[ -r $FC ]]; then
  log "SELinux-Kontexte setzen"
  chroot "$IMG" setfiles -F /etc/selinux/refpolicy/contexts/files/file_contexts / \
    -e /proc -e /sys -e /dev -e /run || log "Warnung: setfiles fehlgeschlagen – Live-System läuft permissive ohne Labels"
  # Stichprobe: tragen die Dateien wirklich ein Label? (sonst erscheinen sie zur Laufzeit als unlabeled_t)
  for f in usr/bin/bash usr/lib/systemd/systemd etc/passwd; do
    lbl=$(getfattr --absolute-names -n security.selinux --only-values "$IMG/$f" 2>/dev/null | tr -d '\0')
    log "SELinux-Label /$f: ${lbl:-FEHLT}"
  done
else
  log "Warnung: keine file_contexts unter $FC – Richtlinie nicht im Store"
fi

log "SquashFS"
chroot "$SYM" mksquashfs "/var/tmp/image/rootfs" "/var/tmp/image/iso/LiveOS/squashfs.img" \
  -comp xz -Xbcj x86 -b 1M -noappend -no-progress   # xz + x86-Filter: deutlich kleiner als zstd

log "GRUB (UEFI + BIOS) und ISO"
mkdir -p "$ISO/boot/grub"
cat > "$ISO/boot/grub/grub.cfg" <<CONF
set timeout=5
set default=0
insmod all_video
menuentry "symfonOS Live ($VARIANT)" {
  linux /boot/vmlinuz root=live:CDLABEL=$LABEL rd.live.image quiet
  initrd /boot/initramfs.img
}
menuentry "symfonOS Live – in den Arbeitsspeicher kopieren" {
  linux /boot/vmlinuz root=live:CDLABEL=$LABEL rd.live.image rd.live.ram=1 quiet
  initrd /boot/initramfs.img
}
menuentry "symfonOS Live – sichere Grafik" {
  linux /boot/vmlinuz root=live:CDLABEL=$LABEL rd.live.image nomodeset
  initrd /boot/initramfs.img
}
CONF
# Ohne freetype gibt es keine unicode.pf2; das Textmenü braucht weder Schrift noch Theme
chroot "$SYM" grub-mkrescue --fonts= --locales= --themes= -o "/var/tmp/image/out.iso" "/var/tmp/image/iso" -- -volid "$LABEL"
mkdir -p "$(dirname "$OUTISO")"
mv "$WORK/out.iso" "$OUTISO"
( cd "$(dirname "$OUTISO")" && sha256sum "$(basename "$OUTISO")" > "$(basename "$OUTISO").sha256" )
log "Fertig: $OUTISO"
