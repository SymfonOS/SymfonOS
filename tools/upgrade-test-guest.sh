#!/bin/bash
# Läuft im installierten System (Dienst symfonos-upgrade-test, eingerichtet von tools/install-test.sh mit UPGRADE=1).
# Start 1: »syp sync && syp -y upgrade« gegen ein lokales, signiertes Test-Repo, Ergebnis prüfen
#          (neue Version, SELinux-Label, Btrfs-Snapshot), dann »syp rollback --system« und Neustart.
# Start 2: prüfen, dass der zurückgesetzte Stand läuft (alte Version).
# Ergebniszeilen beginnen mit »UPGRADE-TEST« und landen über die Konsole im seriellen Log.
set -uo pipefail
PATH=/usr/bin:/usr/sbin
PHASE=/var/log/symfonos-upgrade-test.phase   # /var/log ist ein eigenes Subvolume: übersteht den Rollback
GEN=/usr/lib/systemd/system-generators/symfonos-upgrade-probe   # Label laut Richtlinie: systemd_generator_exec_t,
# das Verzeichnis selbst ist bin_t – ohne restorecon nach dem Update bekäme die neue Datei das falsche Label
fail() {
  # SELinux-Verweigerungen landen bei laufendem auditd nicht auf der Konsole; vor der FAIL-Zeile ausgeben,
  # denn der Host beendet die VM, sobald er sie sieht
  grep -a 'avc: *denied' /var/log/audit/audit.log 2>/dev/null | tail -8 | sed 's/^/UPGRADE-TEST avc: /'
  echo "UPGRADE-TEST FAIL: $*"
  systemctl poweroff; exit 1
}

case $(cat "$PHASE" 2>/dev/null) in
  "")
    echo upgrade > "$PHASE"; sync
    [[ $(symfonos-upgrade-probe) == 1-1 ]] || fail "Ausgangsversion fehlt"
    # wie im Alltag: über den Dienst der automatischen Sicherheitsupdates (syp als systemd-Dienst, init_t);
    # ohne Sicherheitshinweise im Index spielt »upgrade --security« alle Updates ein
    systemctl start syp-security-update.service
    res=$(systemctl show -p Result --value syp-security-update.service)
    journalctl -u syp-security-update.service -o cat --no-pager | sed 's/^/UPGRADE-TEST dienst: /'
    [[ $res == success ]] || fail "syp-security-update.service: $res"
    [[ $(symfonos-upgrade-probe) == 2-1 ]] || fail "neue Version nicht aktiv: $(symfonos-upgrade-probe)"
    syp list | grep -qx 'symfonos-upgrade-probe 2-1' || fail "Paketdatenbank: $(syp list | grep probe)"
    if [[ -e /sys/fs/selinux/enforce ]]; then
      ctx=$(stat -c %C "$GEN")
      [[ $ctx == *:systemd_generator_exec_t:* ]] || fail "SELinux-Label nach dem Update: $GEN hat $ctx"
      echo "UPGRADE-TEST label $GEN $ctx"
    fi
    snap=$(ls -d /.snapshots/syp-*-pre 2>/dev/null | tail -1)
    [[ -n $snap ]] || fail "kein Snapshot vor dem Update in /.snapshots"
    echo "UPGRADE-TEST upgrade OK ($snap)"
    echo rollback > "$PHASE"; sync
    syp -y rollback --system || fail "syp rollback --system"
    echo "UPGRADE-TEST rollback eingeleitet, Neustart"
    systemctl reboot
    ;;
  rollback)
    echo "done" > "$PHASE"; sync
    v=$(symfonos-upgrade-probe)
    [[ $v == 1-1 ]] || fail "nach dem Rollback läuft $v statt 1-1"
    syp list | grep -qx 'symfonos-upgrade-probe 1-1' || fail "Paketdatenbank nach dem Rollback: $(syp list | grep probe)"
    old=$(btrfs subvolume list / 2>/dev/null | grep -c '@\.vor-rollback-' || true)
    [[ $old -ge 1 ]] || fail "vorheriger Stand @.vor-rollback-* fehlt"
    echo "UPGRADE-TEST rollback OK"
    ;;
  *) echo "UPGRADE-TEST bereits gelaufen" ;;
esac
