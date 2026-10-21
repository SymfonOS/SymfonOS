#!/bin/sh
# Installiert die symfonOS-Sicherheitskonfiguration nach DESTDIR (Paket symfonos-security).
#   ./install.sh /pfad/zum/pkgdir
# Krypto-Richtlinien liegen im eigenen Paket crypto-policies (Abhängigkeit von openssl/openssh).
set -eu
D=${1:?Ziel (DESTDIR) fehlt}
cd "$(dirname "$0")"

i() { install -D -m "$1" "$2" "$D/$3"; }

# PAM (wie Fedora/authselect "local": faillock, pwquality, yescrypt, SELinux-Kontext, loginuid)
for f in pam.d/*; do i 644 "$f" "etc/pam.d/${f#pam.d/}"; done
i 644 pwquality.conf etc/security/pwquality.conf.d/50-symfonos.conf
i 644 faillock.conf etc/security/faillock.conf
install -d -m 755 "$D/var/log/faillock"

# Kernel-Laufzeitparameter und gesperrte Module (Vorgaben unter /usr/lib, lokale Änderungen in /etc)
i 644 sysctl.d/50-symfonos-hardening.conf usr/lib/sysctl.d/50-symfonos-hardening.conf
i 644 modprobe.d/50-symfonos-blacklist.conf usr/lib/modprobe.d/50-symfonos-blacklist.conf

# Firewall
i 644 nftables.conf etc/nftables.conf
install -d -m 755 "$D/etc/nftables.d"
i 644 systemd/nftables.service usr/lib/systemd/system/nftables.service

# Audit
i 640 audit/50-symfonos.rules etc/audit/rules.d/50-symfonos.rules

# SSH (Server- und Client-Drop-ins; die Algorithmen kommen aus der Krypto-Richtlinie)
i 644 ssh/50-symfonos.conf etc/ssh/sshd_config.d/50-symfonos.conf
i 644 ssh/50-crypto-policy.conf etc/ssh/ssh_config.d/50-crypto-policy.conf

# sudo: Pseudo-Terminal erzwingen, kurze Gültigkeit, Protokoll
install -d -m 750 "$D/etc/sudoers.d"
cat > "$D/etc/sudoers.d/00-symfonos" <<'X'
# symfonOS-Vorgaben für sudo
Defaults use_pty
Defaults env_reset
Defaults timestamp_timeout=5
Defaults passwd_tries=3
Defaults !visiblepw
Defaults logfile="/var/log/sudo.log"
Defaults lecture="never"
X
chmod 440 "$D/etc/sudoers.d/00-symfonos"

# Speicherabbilder nicht dauerhaft ablegen (enthalten oft Geheimnisse)
install -d "$D/usr/lib/systemd/coredump.conf.d"
printf '[Coredump]\nStorage=none\nProcessSizeMax=0\n' > "$D/usr/lib/systemd/coredump.conf.d/50-symfonos.conf"

# DNS: DNSSEC prüfen, wo möglich; verschlüsseltes DNS opportunistisch
install -d "$D/usr/lib/systemd/resolved.conf.d"
printf '[Resolve]\nDNSSEC=allow-downgrade\nDNSOverTLS=opportunistic\nLLMNR=no\nMulticastDNS=no\n' \
  > "$D/usr/lib/systemd/resolved.conf.d/50-symfonos.conf"

# Journal dauerhaft speichern, damit Sicherheitsereignisse einen Neustart überstehen
install -d "$D/usr/lib/systemd/journald.conf.d"
printf '[Journal]\nStorage=persistent\nSystemMaxUse=1G\nForwardToSyslog=no\n' \
  > "$D/usr/lib/systemd/journald.conf.d/50-symfonos.conf"

# Standarddienste (systemctl preset): Firewall und Audit an, SSH-Server aus
install -d "$D/usr/lib/systemd/system-preset"
cat > "$D/usr/lib/systemd/system-preset/50-symfonos-security.preset" <<'X'
enable nftables.service
enable auditd.service
enable systemd-resolved.service
enable systemd-timesyncd.service
enable symfonos-security-check.timer
enable symfonos-autorelabel.service
disable sshd.service
disable sshd.socket
X

# Tägliche Prüfung: offene Sicherheitslücken (syp audit) und Abweichungen der Konfiguration ins Journal
cat > "$D/usr/lib/systemd/system/symfonos-security-check.service" <<'X'
[Unit]
Description=symfonOS-Sicherheitsprüfung (Lücken, Konfiguration)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/bin/symfonos-security check --quiet
Nice=10
IOSchedulingClass=idle
ProtectSystem=strict
ProtectHome=read-only
PrivateTmp=yes
NoNewPrivileges=yes
ReadWritePaths=-/var/lib/syp -/var/cache/syp
X
cat > "$D/usr/lib/systemd/system/symfonos-security-check.timer" <<'X'
[Unit]
Description=Tägliche symfonOS-Sicherheitsprüfung

[Timer]
OnBootSec=15min
OnUnitActiveSec=1d
RandomizedDelaySec=1h
Persistent=true

[Install]
WantedBy=timers.target
X

# Optionale automatische Sicherheitsupdates (symfonos-security autoupdate on), wie dnf-automatic:
# nur Pakete mit Sicherheitshinweis, Snapshot und Rollback über syp, nicht im Akkubetrieb
cat > "$D/usr/lib/systemd/system/syp-security-update.service" <<'X'
[Unit]
Description=Sicherheitsupdates einspielen (syp upgrade --security)
After=network-online.target
Wants=network-online.target
ConditionACPower=true
ConditionPathExists=!/run/symfonos-exam/active

[Service]
Type=oneshot
ExecStart=/usr/bin/syp -y upgrade --security
Nice=10
IOSchedulingClass=idle
X
cat > "$D/usr/lib/systemd/system/syp-security-update.timer" <<'X'
[Unit]
Description=Tägliche Sicherheitsupdates

[Timer]
OnCalendar=daily
RandomizedDelaySec=2h
Persistent=true

[Install]
WantedBy=timers.target
X

# Gemeinsame Härtungsparameter der Kernel-Kommandozeile (Installer: /etc/kernel/cmdline bzw. GRUB)
install -d "$D/usr/lib/symfonos"
echo "quiet loglevel=3 audit=1 audit_backlog_limit=8192 init_on_alloc=1 slab_nomerge page_alloc.shuffle=1 randomize_kstack_offset=on vsyscall=none" \
  > "$D/usr/lib/symfonos/kernel-cmdline"

# SELinux-Dateikontexte beim Start neu setzen, wenn /.autorelabel existiert (Installer, symfonos-security)
cat > "$D/usr/lib/systemd/system/symfonos-autorelabel.service" <<'X'
[Unit]
Description=SELinux-Dateikontexte neu setzen
DefaultDependencies=no
Requires=local-fs.target
After=local-fs.target
Before=sysinit.target shutdown.target
Conflicts=shutdown.target
ConditionPathExists=/.autorelabel
ConditionSecurity=selinux

[Service]
Type=oneshot
ExecStart=/usr/sbin/setfiles -e /proc -e /sys -e /dev -e /run -F /etc/selinux/refpolicy/contexts/files/file_contexts /
ExecStart=/usr/bin/rm -f /.autorelabel
ExecStart=/usr/bin/systemctl --no-block reboot
StandardOutput=journal+console

[Install]
WantedBy=sysinit.target
X

# Verwaltungswerkzeuge
i 755 bin/symfonos-security usr/bin/symfonos-security
i 755 bin/symfonos-secureboot usr/bin/symfonos-secureboot
