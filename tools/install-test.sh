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
  SYMFONOS_PASSWORD=Blauer-Wal-7-Segelt SYMFONOS_NO_NVRAM=1 \
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

# UPGRADE=1: Update-Test im installierten System vorbereiten (tools/upgrade-test-guest.sh läuft dort als Dienst).
# Testpaket symfonos-upgrade-probe 1-1 wird vorab installiert, 2-1 liegt in einem signierten lokalen Repo.
if [[ -n ${UPGRADE:-} ]]; then
  log "Update-Test vorbereiten"
  T=$W/ovl/mnt/upg
  mkdir -p "$T" "$W/ovl/tmp/upg"
  mount -o subvol=@ "$ROOTPART" "$T"
  mount -o subvol=@cache "$ROOTPART" "$T/var/cache"
  install -Dm755 "$(dirname "$0")/upgrade-test-guest.sh" "$T/usr/lib/symfonos/upgrade-test"
  chroot "$W/ovl" /usr/bin/env -i PATH=/usr/bin:/usr/sbin /bin/bash -euo pipefail -s <<'EOF'
cd /tmp/upg
T=/mnt/upg R=/var/lib/symfonos-upgrade-test/repo G=usr/lib/systemd/system-generators
syp keygen upg --comment "symfonOS Update-Test" >/dev/null
for v in 1-1 2-1; do
  mkdir -p "tree$v/usr/bin" "tree$v/$G"
  printf '#!/bin/sh\necho %s\n' "$v" > "tree$v/usr/bin/symfonos-upgrade-probe"
  printf '#!/bin/sh\n# Update-Test %s: erzeugt nichts\nexit 0\n' "$v" > "tree$v/$G/symfonos-upgrade-probe"
  chmod 755 "tree$v/usr/bin/symfonos-upgrade-probe" "tree$v/$G/symfonos-upgrade-probe"
  printf '{"name":"symfonos-upgrade-probe","version":"%s","arch":"x86_64","summary":"symfonOS Update-Test"}' "$v" > "info$v.json"
  syp pack "tree$v" "info$v.json" -o "out$v" -k upg.key >/dev/null
done
install -Dm644 upg.pub "$T/etc/syp/keys/upgrade-test.pub"
mkdir -p "$T$R"
syp repo-add "$T$R" out2-1/*.syp --name symfonos -k upg.key >/dev/null
syp -r "$T" -y install out1-1/*.syp
EOF
  # Paketquelle »symfonos« auf das lokale Test-Repo umstellen (gleicher Name, daher gleiche Rollback-Prüfung)
  sed -i -e 's|^url = "https://github.com/SymfonOS/SymfonOS/releases/download/repo-x86_64"$|url = "file:///var/lib/symfonos-upgrade-test/repo"|' \
    -e '/^flat = true$/d' "$T/etc/syp/syp.toml"
  grep -q '^url = "file:///var/lib/symfonos-upgrade-test/repo"' "$T/etc/syp/syp.toml" ||
    { echo "::error title=Update-Test::Paketquelle in /etc/syp/syp.toml nicht gefunden"; exit 1; }
  cat > "$T/etc/systemd/system/symfonos-upgrade-test.service" <<'EOF'
[Unit]
Description=symfonOS Update-Test (CI)
After=local-fs.target

[Service]
Type=oneshot
ExecStart=/usr/lib/symfonos/upgrade-test
StandardOutput=journal+console
StandardError=journal+console

[Install]
WantedBy=multi-user.target
EOF
  mkdir -p "$T/etc/systemd/system/multi-user.target.wants"
  ln -sf ../symfonos-upgrade-test.service "$T/etc/systemd/system/multi-user.target.wants/"
  FC=/mnt/upg/etc/selinux/refpolicy/contexts/files/file_contexts
  if [[ -r $W/ovl$FC ]]; then
    chroot "$W/ovl" setfiles -r /mnt/upg -F "$FC" /mnt/upg/usr/lib/symfonos /mnt/upg/etc/systemd/system \
      /mnt/upg/etc/syp /mnt/upg/var/lib /mnt/upg/usr/bin/symfonos-upgrade-probe \
      /mnt/upg/usr/lib/systemd/system-generators /mnt/upg/var/cache/syp
  fi
  umount "$T/var/cache" "$T"
fi
losetup -d "$LOOP"; LOOP=""

accel=tcg; [[ -w /dev/kvm ]] && accel=kvm
# boot SERIAL-LOG MUSTER...: startet das installierte System, bis alle MUSTER im Log stehen oder die VM endet
# (-no-reboot: ein Neustart beendet QEMU). Danach noch SETTLE Sekunden laufen lassen, um späte Fehler mitzunehmen;
# mit WAIT_EXIT=1 stattdessen warten, bis die VM von selbst endet (Neustart nach dem Rollback).
boot() {
  local serial=$1 qpid ok=0 p
  shift
  : > "$serial"
  timeout "${TIMEOUT:-900}" qemu-system-x86_64 -machine q35,accel=$accel -m 4096 -smp 2 \
    -drive file="$W/disk.img",format=raw,if=virtio \
    -kernel "$W/vmlinuz" -initrd "$W/initrd" \
    -append "root=UUID=$RUUID rootflags=subvol=@ rw console=ttyS0,115200 systemd.journald.forward_to_console=1 ${APPEND:-}" \
    -nographic -no-reboot -serial file:"$serial" -monitor none &
  qpid=$!
  for _ in $(seq "${TIMEOUT:-900}"); do
    ok=1; for p in "$@"; do grep -aq "$p" "$serial" 2>/dev/null || ok=0; done
    ((ok)) && break
    grep -aqE "Kernel panic|emergency mode|Failed to mount|UPGRADE-TEST FAIL" "$serial" 2>/dev/null && break
    kill -0 "$qpid" 2>/dev/null || break
    sleep 1
  done
  if ((ok)) && [[ -n ${WAIT_EXIT:-} ]]; then
    for _ in $(seq 300); do kill -0 "$qpid" 2>/dev/null || break; sleep 1; done
  elif ((ok)); then
    sleep "${SETTLE:-20}"
  fi
  kill "$qpid" 2>/dev/null || true
  wait "$qpid" 2>/dev/null || true
  return $((!ok))
}
L=${LOG:-install-serial.log}
report() {  # report LOG TITEL: [FAILED]-Units und Abstürze im seriellen Log als Fehler melden
  local clean fails crashes
  clean=$(sed 's/\x1b\[[0-9;]*m//g' "$1")
  fails=$(echo "$clean" | grep -a "\[FAILED\]" || true)
  crashes=$(echo "$clean" | grep -aE "segfault at|general protection fault|Oops|BUG:|UPGRADE-TEST FAIL" || true)
  [[ -z $fails && -z $crashes && ${3:-1} == 1 ]] && return 0
  echo "::error title=$2::$(echo "${crashes:-}${fails:-}" | head -10; echo "$clean" | tail -c 2500 | sed ':a;N;$!ba;s/%/%25/g;s/\n/%0A/g')"
  return 1
}

if [[ -n ${UPGRADE:-} ]]; then
  log "Update-Test: Start 1 (syp upgrade, syp rollback --system, Neustart)"
  # nach »rollback eingeleitet« startet das System neu; QEMU endet dabei (-no-reboot)
  WAIT_EXIT=1 boot "$W/serial-upgrade.log" "UPGRADE-TEST rollback eingeleitet" && ok=1 || ok=0
  cp "$W/serial-upgrade.log" "${L%.log}-upgrade.log"
  grep -a "UPGRADE-TEST" "$W/serial-upgrade.log" | sed 's/\x1b\[[0-9;]*m//g' | while read -r l; do log "$l"; done
  # Meldungen beim Herunterfahren zählen nicht: nur bis zur Rollback-Zeile prüfen
  sed '/UPGRADE-TEST rollback eingeleitet/q' "$W/serial-upgrade.log" > "$W/serial-upgrade-run.log"
  report "$W/serial-upgrade-run.log" "Update-Test (Start 1)" "$ok" || exit 1
fi

log "Installiertes System booten ($KVER, $pkgs Pakete)"
want=("symfonos-test login:")
[[ -n ${UPGRADE:-} ]] && want+=("UPGRADE-TEST rollback OK")   # nach dem Rollback: alter Stand aktiv
boot "$W/serial.log" "${want[@]}" && ok=1 || ok=0
[[ -n ${UPGRADE:-} ]] && grep -a "UPGRADE-TEST" "$W/serial.log" | sed 's/\x1b\[[0-9;]*m//g' | while read -r l; do log "$l"; done
cp "$W/serial.log" "$L"
python3 "$(dirname "$0")/avc-summary.py" "$L" > "${L%.log}-avc.txt" 2>&1 || true
log "SELinux: $(tail -1 "${L%.log}-avc.txt")"
report "$W/serial.log" "Installationstest (Boot des installierten Systems)" "$ok" || exit 1
log "Installiertes System bootet bis zum Anmeldeprompt${UPGRADE:+ (Update und Rollback bestanden)}"
