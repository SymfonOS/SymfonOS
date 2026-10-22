#!/usr/bin/env bash
# Stufe 5: bootfähiges Live-/Installations-ISO ausschließlich aus dem eigenen syp-Repository.
#   SYP_PUB=release.pub ./50-image.sh [ausgabe.iso]
# shellcheck source-path=SCRIPTDIR source=config.sh
. "$(dirname "$0")/config.sh"
: "${SYP_PUB:?SYP_PUB (öffentlicher Release-Schlüssel) setzen}"
OUTISO=${1:-$REPO_ROOT/out/symfonos-base-$(date +%Y.%m.%d)-x86_64.iso}
WORK=$SYM/var/tmp/image            # liegt in der Werkzeugwurzel, damit mksquashfs/grub-mkrescue dort laufen
IMG=$WORK/rootfs ISO=$WORK/iso
LABEL=SYMFONOS
rm -rf "$WORK"; mkdir -p "$IMG/etc/syp/keys" "$ISO"/{boot,LiveOS,repo}
for m in dev proc sys run; do mkdir -p "$SYM/$m"; mountpoint -q "$SYM/$m" || mount --bind "/$m" "$SYM/$m"; done
trap 'for m in run sys proc dev; do mountpoint -q "$SYM/$m" && umount -l "$SYM/$m"; done; true' EXIT

log "Repository ins ISO übernehmen (Offline-Installation)"
cp -a "$PKGREPO"/. "$ISO/repo/"
cp "$SYP_PUB" "$ISO/repo/symfonos-release.pub"

log "Live-System mit syp aufbauen"
cat > "$IMG/etc/syp/syp.toml" <<CONF
arch = "x86_64"
[[repo]]
name = "symfonos"
url = "file://$ISO/repo"
[snapshots]
enabled = false
CONF
cp "$SYP_PUB" "$IMG/etc/syp/keys/symfonos-release.pub"
"$SYP" -r "$IMG" sync
"$SYP" -r "$IMG" -y install --overwrite /etc/syp/syp.toml symfonos-base symfonos-installer   # vorläufige Konfiguration ersetzen
# Für das installierte System zeigt die Konfiguration wieder auf die Online-Repos
cp "$REPO_ROOT/system/etc/syp/syp.toml" "$IMG/etc/syp/syp.toml"

log "Live-Konfiguration"
in_img() { chroot "$IMG" /usr/bin/env -i PATH=/usr/bin:/usr/sbin HOME=/root /bin/bash -euo pipefail -c "$1"; }
for m in dev proc sys run; do mkdir -p "$IMG/$m"; mount --bind "/$m" "$IMG/$m"; done
in_img "systemd-sysusers; systemd-tmpfiles --create || true
  useradd -m -G wheel,audio,video,input -s /bin/bash -c 'symfonOS Live' live; passwd -d live
  echo 'live ALL=(ALL:ALL) NOPASSWD: ALL' > /etc/sudoers.d/90-live; chmod 440 /etc/sudoers.d/90-live
  systemctl preset-all >/dev/null 2>&1 || true   # Firewall, Audit, Zeit, DNS wie bei einer Installation
  systemctl enable systemd-networkd systemd-resolved systemd-timesyncd iwd
  : > /etc/machine-id"
mkdir -p "$IMG/etc/systemd/system/getty@tty1.service.d"
cat > "$IMG/etc/systemd/system/getty@tty1.service.d/autologin.conf" <<'CONF'
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin live --noclear %I $TERM
CONF
cat > "$IMG/etc/motd" <<'CONF'

  symfonOS Live (Basissystem)
  Installieren:  sudo symfonos-install
  Netzwerk:      iwctl  (WLAN)  ·  networkctl status
  Hilfe:         man syp  ·  https://github.com/thomasrohm23-bit/symfonOS

CONF
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
fi

log "SquashFS"
chroot "$SYM" mksquashfs "/var/tmp/image/rootfs" "/var/tmp/image/iso/LiveOS/squashfs.img" \
  -comp zstd -Xcompression-level 19 -b 1M -noappend -no-progress

log "GRUB (UEFI + BIOS) und ISO"
mkdir -p "$ISO/boot/grub"
cat > "$ISO/boot/grub/grub.cfg" <<CONF
set timeout=5
set default=0
insmod all_video
menuentry "symfonOS Live" {
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
