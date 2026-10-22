#!/usr/bin/env bash
# Bootet das ISO in QEMU (serielle Konsole) und prüft, ob das Live-System hochfährt.
#   tools/smoke-test.sh out/symfonos-*.iso
set -euo pipefail
ISO=$(realpath "$1")
T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT
label=$(isoinfo -d -i "$ISO" 2>/dev/null | awk -F': ' '/Volume id/ {print $2}') ||
  label=$(blkid -o value -s LABEL "$ISO")
# Zwei Layouts: Quellcode-ISO (/boot/vmlinuz, dracut-Live) und archiso (/symfonos/boot/x86_64)
if xorriso -osirrox on -indev "$ISO" -extract /boot/vmlinuz "$T/vmlinuz" -extract /boot/initramfs.img "$T/initrd" >/dev/null 2>&1; then
  APPEND="root=live:CDLABEL=$label rd.live.image"
else
  xorriso -osirrox on -indev "$ISO" \
    -extract /symfonos/boot/x86_64/vmlinuz-linux-zen "$T/vmlinuz" \
    -extract /symfonos/boot/x86_64/initramfs-linux-zen.img "$T/initrd" >/dev/null 2>&1
  APPEND="archisobasedir=symfonos archisolabel=$label"
fi
accel=tcg; [[ -w /dev/kvm ]] && accel=kvm
echo ":: Boote $ISO (Label $label, $accel)"
timeout "${TIMEOUT:-900}" qemu-system-x86_64 -machine q35,accel=$accel -m 4096 -smp 2 \
  -cdrom "$ISO" -kernel "$T/vmlinuz" -initrd "$T/initrd" \
  -append "$APPEND console=ttyS0,115200 systemd.unit=multi-user.target systemd.journald.forward_to_console=1" \
  -nographic -no-reboot -serial file:"$T/serial.log" -monitor none &
qpid=$!
ok=0
for _ in $(seq "${TIMEOUT:-900}"); do
  if grep -q "Reached target.*Multi-User System" "$T/serial.log" 2>/dev/null; then ok=1; break; fi
  grep -qE "Kernel panic|emergency mode|Failed to mount" "$T/serial.log" 2>/dev/null && break
  kill -0 "$qpid" 2>/dev/null || break
  sleep 1
done
sleep "${SETTLE:-30}"   # späte Abstürze (z. B. Autologin) noch erfassen
kill "$qpid" 2>/dev/null || true
cp "$T/serial.log" "${LOG:-smoke-serial.log}"
# Erst Farbcodes entfernen: systemd schreibt "[\e[0;1;31mFAILED\e[0m]", roh wäre "[FAILED]" nie zu finden
fails=$(sed 's/\x1b\[[0-9;]*m//g' "$T/serial.log" | grep -a "\[FAILED\]" || true)
# Abstürze sind immer ein Fehler; nach dem Ziel noch kurz weiterlaufen lassen (Autologin, späte Dienste)
crashes=$(sed 's/\x1b\[[0-9;]*m//g' "$T/serial.log" | grep -aE "segfault at|general protection fault|Oops|BUG:" || true)
if ((ok)) && [[ -n $crashes ]]; then
  echo "::error title=Absturz im Boot-Test::$(echo "$crashes" | head -5 | sed ':a;N;$!ba;s/\n/%0A/g')"
  exit 1
fi
# STRICT=1 (Quellcode-ISO): fehlgeschlagene Units sind ein Fehler, mit den zugehörigen Journalzeilen
if ((ok)) && [[ -n $fails && ${STRICT:-0} == 1 ]]; then
  ctx=$(sed 's/\x1b\[[0-9;]*m//g' "$T/serial.log" | grep -aiE "\[FAILED\]|failed|error" | grep -av "Failed to read SELinux context" | awk '!s[$0]++' | head -25)
  echo "::error title=Fehlgeschlagene Units::$(echo "$ctx" | sed ':a;N;$!ba;s/%/%25/g;s/\n/%0A/g')"
  exit 1
fi
if ((ok)); then
  echo ":: Live-System erreicht multi-user.target"
  grep -q "symfonOS Live-Benutzer anlegen" "$T/serial.log" && echo ":: Live-Benutzer-Einrichtung gelaufen" || true
  [[ -n $fails ]] && echo "::warning title=Fehlgeschlagene Units::$(echo "$fails" | sed ':a;N;$!ba;s/\n/%0A/g')"
  exit 0
fi
echo "::error title=Boot-Test fehlgeschlagen::$(sed 's/\x1b\[[0-9;]*m//g' "$T/serial.log" | tail -c 3000 | sed ':a;N;$!ba;s/%/%25/g;s/\r//g;s/\n/%0A/g')"
exit 1
