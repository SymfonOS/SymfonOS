#!/usr/bin/env bash
# Grafischer Boot-Test des Desktop-ISO (wie openQA bei Fedora, vereinfacht): bootet in QEMU mit
# virtio-GPU, wartet auf SDDM, KWin und die Plasma-Sitzung des Live-Benutzers und hält Bildschirmfotos fest.
#   tools/desktop-test.sh out/symfonos-desktop-*.iso [ausgabeverzeichnis]
# Erfolg: graphical.target erreicht, kwin_wayland und plasmashell laufen, kein Absturz (Coredump/Segfault).
set -euo pipefail
ISO=$(realpath "$1")
OUT=$(realpath -m "${2:-desktop-test}")
mkdir -p "$OUT"
T=$(mktemp -d)
qpid=""
cleanup() { [[ -n $qpid ]] && kill "$qpid" 2>/dev/null; rm -rf "$T"; }
trap cleanup EXIT
label=$(isoinfo -d -i "$ISO" 2>/dev/null | awk -F': ' '/Volume id/ {print $2}') ||
  label=$(blkid -o value -s LABEL "$ISO")
xorriso -osirrox on -indev "$ISO" -extract /boot/vmlinuz "$T/vmlinuz" -extract /boot/initramfs.img "$T/initrd" >/dev/null 2>&1
accel=tcg; [[ -w /dev/kvm ]] && accel=kvm
echo ":: Boote $ISO grafisch (Label $label, $accel)"
timeout "${TIMEOUT:-1500}" qemu-system-x86_64 -machine q35,accel=$accel -m 6144 -smp 4 \
  -cdrom "$ISO" -kernel "$T/vmlinuz" -initrd "$T/initrd" \
  -append "root=live:CDLABEL=$label rd.live.image console=ttyS0,115200 systemd.journald.forward_to_console=1" \
  -device virtio-vga -display none -device virtio-tablet-pci -device virtio-keyboard-pci \
  -audiodev none,id=snd0 -device ich9-intel-hda -device hda-output,audiodev=snd0 \
  -serial file:"$T/serial.log" -monitor unix:"$T/mon.sock",server,nowait -no-reboot &
qpid=$!

shot() {   # Bildschirmfoto über den QEMU-Monitor (PPM, dann PNG falls möglich)
  printf 'screendump %s\n' "$T/$1.ppm" | socat - UNIX-CONNECT:"$T/mon.sock" >/dev/null 2>&1 || return 0
  sleep 1
  if command -v convert >/dev/null; then convert "$T/$1.ppm" "$OUT/$1.png" 2>/dev/null || cp "$T/$1.ppm" "$OUT/"
  else cp "$T/$1.ppm" "$OUT/"; fi
}
clean() { sed 's/\x1b\[[0-9;]*m//g' "$T/serial.log" 2>/dev/null; }

graphical=0 session=0
for i in $(seq "${TIMEOUT:-1500}"); do
  log=$(clean)
  ((graphical)) || { grep -aq "Reached target.*Graphical Interface" <<<"$log" && { graphical=1; echo ":: graphical.target nach ${i}s"; }; }
  if grep -aq "plasmashell" <<<"$log" && grep -aqE "kwin_wayland|KWin" <<<"$log"; then session=1; echo ":: Plasma-Sitzung nach ${i}s"; break; fi
  grep -aqE "Kernel panic|emergency mode|Failed to mount" <<<"$log" && break
  kill -0 "$qpid" 2>/dev/null || break
  ((i % 120 == 0)) && shot "zwischenstand-$i"
  sleep 1
done
sleep "${SETTLE:-60}"   # Sitzung fertig aufbauen lassen, späte Abstürze erfassen
shot desktop
kill "$qpid" 2>/dev/null || true
qpid=""
clean > "$OUT/serial.log"
log=$(cat "$OUT/serial.log")
crashes=$(grep -aE "segfault at|general protection fault|Oops|BUG:|dumped core|Process [0-9]+ \(.*\) of user [0-9]+ dumped core" <<<"$log" | awk '!s[$0]++' | head -15 || true)
fails=$(grep -a "\[FAILED\]" <<<"$log" | awk '!s[$0]++' | head -15 || true)
echo ":: graphical=$graphical session=$session"
if ((graphical && session)) && [[ -z $crashes && ( -z $fails || ${STRICT:-1} == 0 ) ]]; then
  echo ":: Desktop startet: SDDM → KWin (Wayland) → Plasma"
  exit 0
fi
ctx=$(grep -aiE "sddm|kwin|plasma|\[FAILED\]|segfault|dumped core|error" <<<"$log" | awk '!s[$0]++' | tail -40)
echo "::error title=Desktop-Test (graphical=$graphical, Sitzung=$session)::$( { echo "$crashes"; echo "$fails"; echo "$ctx"; } | tail -c 3500 | sed ':a;N;$!ba;s/%/%25/g;s/\r//g;s/\n/%0A/g')"
exit 1
