#!/usr/bin/env bash
# Installationstest (wie openQA bei Fedora, ohne Bildschirm): installiert aus dem Live-ISO mit
# symfonos-install auf eine Image-Datei und bootet das installierte System in QEMU.
#   sudo tools/install-test.sh out/symfonos-base-*.iso
# Der Installer läuft in einer beschreibbaren Kopie (Overlay) des Live-Systems, genau wie vom Medium.
set -euo pipefail
ISO=$(realpath "$1")
W=$(mktemp -d /var/tmp/symfonos-install-test.XXXXXX)
LOOP=""
log() { printf '\033[1;35m::\033[0m %s\n' "$*"; }

cleanup() {
  set +e
  # Erst alles aushängen, dann prüfen, dann löschen – nie rm -rf über eingehängte Verzeichnisse
  for m in $(findmnt -rn -o TARGET | grep "^$W" | sort -r); do umount -l "$m"; done
  [[ -n $LOOP ]] && losetup -d "$LOOP"
  if findmnt -rn -o TARGET | grep -q "^$W"; then
    echo "Warnung: unter $W ist noch etwas eingehängt – Verzeichnis bleibt stehen" >&2
  else
    rm -rf --one-file-system "$W"
  fi
}
trap cleanup EXIT

log "Live-System aus dem ISO bereitstellen"
xorriso -osirrox on -indev "$ISO" -extract /LiveOS/squashfs.img "$W/squashfs.img" >/dev/null 2>&1
mkdir -p "$W"/{sq,t,ovl,inst}
mount -o loop,ro "$W/squashfs.img" "$W/sq"
mount -t tmpfs -o size=4G tmpfs "$W/t"
mkdir -p "$W/t/up" "$W/t/work"
mount -t overlay overlay -o "lowerdir=$W/sq,upperdir=$W/t/up,workdir=$W/t/work" "$W/ovl"
mount --bind /dev "$W/ovl/dev"
mount --bind /dev/pts "$W/ovl/dev/pts"
mount -t proc proc "$W/ovl/proc"
mount -t sysfs sysfs "$W/ovl/sys"
mount -t tmpfs tmpfs "$W/ovl/run"
mkdir -p "$W/ovl/run/rootfsbase"
mount --bind "$W/sq" "$W/ovl/run/rootfsbase"        # wie dmsquash-live: unveränderte Live-Wurzel

# INSTALLER=pfad: anderen Installer testen (z. B. Arbeitsstand aus dem Repo gegen ein älteres ISO)
[[ -n ${INSTALLER:-} ]] && install -m755 "$INSTALLER" "$W/ovl/usr/bin/symfonos-install"

log "Zielplatte (16 GiB, BIOS + UEFI-Layout)"
truncate -s 16G "$W/disk.img"
LOOP=$(losetup -fP --show "$W/disk.img")

log "symfonos-install (unverschlüsselt, GRUB, ohne Firmware-Variablen)"
chroot "$W/ovl" /usr/bin/env -i PATH=/usr/bin:/usr/sbin HOME=/root TERM=dumb \
  SYMFONOS_PASSWORD=symfonos-test SYMFONOS_NO_NVRAM=1 \
  symfonos-install --disk "$LOOP" --no-encrypt --boot grub --user tester --hostname symfonos-test --yes

log "Installiertes System prüfen"
ROOTPART=${LOOP}p3
mount -o ro,subvol=@ "$ROOTPART" "$W/inst"
for f in etc/fstab etc/hostname boot/grub/grub.cfg usr/bin/syp; do
  [[ -e $W/inst/$f ]] || { echo "::error title=Installationstest::/$f fehlt im installierten System"; exit 1; }
done
grep -q '^tester:' "$W/inst/etc/passwd" || { echo "::error title=Installationstest::Benutzer fehlt"; exit 1; }
if grep -q '^live:' "$W/inst/etc/passwd"; then echo "::error title=Installationstest::Live-Benutzer im installierten System"; exit 1; fi
KVER=$(ls "$W/inst/usr/lib/modules" | head -1)
cp "$W/inst/boot/vmlinuz-$KVER" "$W/vmlinuz"
cp "$W/inst/boot/initramfs-$KVER.img" "$W/initrd"
RUUID=$(blkid -s UUID -o value "$ROOTPART")
pkgs=$(chroot "$W/ovl" syp -r /run/rootfsbase list 2>/dev/null | wc -l)
umount "$W/inst"
losetup -d "$LOOP"; LOOP=""

log "Installiertes System booten ($KVER, $pkgs Pakete)"
accel=tcg; [[ -w /dev/kvm ]] && accel=kvm
timeout "${TIMEOUT:-900}" qemu-system-x86_64 -machine q35,accel=$accel -m 4096 -smp 2 \
  -drive file="$W/disk.img",format=raw,if=virtio \
  -kernel "$W/vmlinuz" -initrd "$W/initrd" \
  -append "root=UUID=$RUUID rootflags=subvol=@ rw console=ttyS0,115200 systemd.journald.forward_to_console=1" \
  -nographic -no-reboot -serial file:"$W/serial.log" -monitor none &
qpid=$!
ok=0
for _ in $(seq "${TIMEOUT:-900}"); do
  if grep -aq "symfonos-test login:" "$W/serial.log" 2>/dev/null; then ok=1; break; fi
  grep -aqE "Kernel panic|emergency mode|Failed to mount" "$W/serial.log" 2>/dev/null && break
  kill -0 "$qpid" 2>/dev/null || break
  sleep 1
done
sleep "${SETTLE:-20}"
kill "$qpid" 2>/dev/null || true
wait "$qpid" 2>/dev/null || true
clean=$(sed 's/\x1b\[[0-9;]*m//g' "$W/serial.log")
cp "$W/serial.log" "${LOG:-install-serial.log}"
fails=$(echo "$clean" | grep -a "\[FAILED\]" || true)
crashes=$(echo "$clean" | grep -aE "segfault at|general protection fault|Oops|BUG:" || true)
if ((ok)) && [[ -z $fails && -z $crashes ]]; then
  log "Installiertes System bootet bis zum Anmeldeprompt"
  exit 0
fi
echo "::error title=Installationstest (Boot des installierten Systems)::$(echo "${crashes:-}${fails:-}" | head -10; echo "$clean" | tail -c 2500 | sed ':a;N;$!ba;s/%/%25/g;s/\n/%0A/g')"
exit 1
