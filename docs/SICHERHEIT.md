# Sicherheit in symfonOS

Ziel ist das Schutzniveau von Fedora Workstation: mehrere unabhängige Schichten, sichere Vorgaben
und Werkzeuge, die den Zustand prüfbar machen. Diese Seite beschreibt, was umgesetzt ist, wie es im
Vergleich zu Fedora dasteht und wo es noch Lücken gibt.

Schnellprüfung auf einem laufenden System:

```bash
symfonos-security status        # Übersicht mit ✓ / ! / ✗
sudo syp audit                  # installierte Pakete mit bekannten, behobenen Lücken
symfonos-secureboot status      # Firmware, Schlüssel, unsignierte EFI-Dateien
```

## Schichten

### 1. Pakete und Updates (`syp`)
| Maßnahme | Umsetzung |
|---|---|
| Signierte Pakete | Ed25519 über Metadaten, Dateiliste und Nutzdaten; ohne gültige Signatur keine Installation |
| Signierter Index | zusätzlich sha256 jedes Pakets im Index; Index und Paket müssen beide stimmen |
| Freeze-Schutz | Indizes laufen ab (`expires`, Standard 14 Tage), ein Spiegel kann keinen alten Stand „einfrieren“ |
| Rollback-Schutz | ein Index mit älterem `generated`-Zeitstempel als der zuletzt akzeptierte wird abgelehnt |
| Verwechslungsschutz | Name und Architektur im signierten Index müssen zur Repo-Konfiguration passen |
| Sicherheitshinweise | Feld `security = ["CVE-…"]` im Rezept; `syp audit`, `syp upgrade --security` |
| Automatische Sicherheitsupdates | optional: `symfonos-security autoupdate on` (täglich, nur mit Netzteil, nie im Prüfungsmodus) |
| Atomare Transaktionen | Konfliktprüfung vor jeder Änderung, Btrfs-Snapshot davor, `syp rollback` |

### 2. Gebaute Programme (Prüfung in `syp build`)
Jedes Paket wird nach dem Bauen geprüft, vergleichbar mit Fedoras *annocheck* und *rpmlint*:

- Compiler-Vorgaben: `-O2 -D_FORTIFY_SOURCE=3 -D_GLIBCXX_ASSERTIONS -fstack-protector-strong -fstack-clash-protection -fcf-protection`, PIE und SSP als Compiler-Standard (gcc mit `--enable-default-pie --enable-default-ssp`), Linker `-z relro -z now`
- **Fehler** (Bau bricht ab): ELF ohne PIE, ohne RELRO, ausführbarer Stack, Text-Relokationen, unsichere RPATH/RUNPATH, Dateien oder Verzeichnisse, in die jeder schreiben darf (ohne Sticky-Bit)
- **Bericht**: fehlendes BIND_NOW, Programme ohne Stack-Protector oder FORTIFY, alle setuid-/setgid-Dateien
- Ausnahmen nur per `[options] hardening = "warn"` mit Pflichtbegründung `hardening_reason`; das Ergebnis steht im Paket (`origin.hardening`)

### 3. Kernel (`distro/recipes/linux/symfonos.config`)
- **SELinux** als primäres MAC-LSM, dazu Yama, Lockdown, Landlock, BPF-LSM, IMA/EVM (Reihenfolge wie Fedora)
- Speicherhärtung nach KSPP: `INIT_ON_ALLOC`, `INIT_STACK_ALL_ZERO`, `ZERO_CALL_USED_REGS`, gehärtete und zufällige SLAB-Freelists, `HARDENED_USERCOPY`, `FORTIFY_SOURCE`, `LIST_HARDENED`, `RANDOMIZE_KSTACK_OFFSET`, `STRICT_KERNEL/MODULE_RWX`, `PAGE_TABLE_CHECK`, IOMMU strikt
- Angriffsfläche: kein `/dev/mem`, `/dev/port`, `/proc/kcore`; vsyscall aus; `TIOCSTI` aus; unprivilegiertes BPF aus
- **Modulsignaturen erzwungen** (SHA-512). Der Signaturschlüssel entsteht pro Kernelbau und wird nicht ausgeliefert. Damit kann niemand nachträglich Module für diesen Kernel signieren.
- TPM 2.0 (TIS/CRB) fest eingebaut
- Laufzeit (`/usr/lib/sysctl.d/50-symfonos-hardening.conf`): `kptr_restrict=2`, `dmesg_restrict`, `ptrace_scope=1`, `kexec_load_disabled`, BPF-JIT-Härtung, geschützte Links/FIFOs, keine ICMP-Redirects, Reverse-Path-Filter, SYN-Cookies, IPv6-Privacy-Adressen
- Selten genutzte Netzprotokolle und Dateisysteme werden nicht automatisch geladen (`modprobe.d`). KVM, FUSE und UDF bleiben bewusst nutzbar (Prüfungs-VM, AppImages, optische Medien).

### 4. Zugriffskontrolle
- **SELinux** mit Reference Policy (MCS, systemd). Anmeldungen werden wie bei Fedora `unconfined_u` zugeordnet, Systemdienste sind eingeschränkt.
- **PAM** wie Fedoras `authselect local`: `pam_faillock` (5 Fehlversuche in 15 min → 10 min Sperre), `pam_pwquality` (mindestens 12 Zeichen, Wörterbuchprüfung, gilt auch für root), Passwort-Hashes mit **yescrypt**, kein `nullok`, SELinux-Kontext und `loginuid` bei der Anmeldung
- **root** ist gesperrt, die Verwaltung läuft über `sudo` (Gruppe `wheel`): `use_pty`, 5 min Gültigkeit, Protokoll in `/var/log/sudo.log`
- Home-Verzeichnisse `0700`, Standard-umask `022`

### 5. Netz
- **nftables**: eingehend alles gesperrt außer Antworten, Loopback, nötigem ICMP und DHCPv6; eigene Freigaben mit `sudo symfonos-security firewall allow 22/tcp`
- Ein Reload ersetzt nur die eigene Tabelle `inet symfonos`, nie das ganze Regelwerk. Tabellen des Prüfungsmodus, von libvirt oder podman bleiben erhalten.
- SSH-Server ist installiert, aber **aus**. Eingeschaltet gilt: kein root-Login, keine leeren Passwörter, keine Weiterleitung von X11 oder Agent
- DNS über systemd-resolved: DNSSEC `allow-downgrade`, DNS-over-TLS opportunistisch, LLMNR/mDNS aus

### 6. Kryptographie (`crypto-policies`)
Eine systemweite Richtlinie für OpenSSL (und damit curl, syp und Python) sowie OpenSSH, wie bei Fedora:

| Richtlinie | TLS | Schlüsseltausch | RSA mindestens | Sonstiges |
|---|---|---|---|---|
| `DEFAULT` | 1.2 / 1.3 | X25519MLKEM768 (Post-Quanten-Hybrid) zuerst, X25519, NIST-Kurven, FFDHE ≥ 2048 | 2048 | kein SHA-1, kein RC4/3DES/MD5 |
| `FUTURE` | 1.2 (nur AEAD) / 1.3 | nur Hybrid + X25519/X448 und starke Gruppen | 3072 | SECLEVEL 3, keine CBC-Suiten |
| `LEGACY` | 1.2 / 1.3 | zusätzlich DH-group14 | 2048 | SHA-1-Signaturen und CBC erlaubt, nur für Altgeräte |

Umschalten: `sudo update-crypto-policies --set FUTURE` (oder `symfonos-security crypto FUTURE`). TLS 1.0 und 1.1 gibt es in keiner Richtlinie.

### 7. Start und Datenträger
- **Festplattenverschlüsselung standardmäßig**: LUKS2, AES-256-XTS, mit systemd-boot **Argon2id** (speicherhart). Nur bei GRUB ist PBKDF2 nötig, weil GRUB kein Argon2 kann.
- **TPM 2.0** (`symfonos-install --tpm`): Entsperren gebunden an PCR 7 (Secure-Boot-Zustand), dazu ein **Wiederherstellungsschlüssel**
- **Secure Boot mit eigenen Schlüsseln** (`symfonos-secureboot`): erzeugt PK/KEK/db und trägt sie im Setup-Modus der Firmware ein. Signiert werden systemd-boot und jedes **Unified Kernel Image**. In einem UKI stecken Kernel, Initramfs und Kommandozeile in einer signierten Datei, Kernel-Parameter lassen sich beim Start also nicht unbemerkt ändern. Der Kernel-Hook baut und signiert nach jedem Kernel-Update automatisch. Die Microsoft-Zertifikate bleiben standardmäßig in der db (Option-ROMs, Windows-Dual-Boot).
- `symfonos-secureboot enroll` nimmt `lockdown=integrity` in die eingebettete Kommandozeile auf. Upstream-Kernel koppeln Lockdown nicht von selbst an Secure Boot (bei Fedora erledigt das ein eigener Patch). Danach kann auch root den laufenden Kernel nicht mehr verändern; als Folge entfällt der Ruhezustand (Hibernate), Standby funktioniert weiter.

### 8. Protokollierung und Überwachung
- **auditd** mit Regeln nach OSPP/STIG-Vorbild: Identitäts- und Rechteänderungen, PAM, SSH, Firewall, SELinux-Richtlinie, Netzkonfiguration, Zeit, Kernel-Module, Paketverwaltung, `execve` mit Rechtewechsel, verweigerte Dateizugriffe von Benutzern. Die Regeln werden zuletzt gesperrt (`-e 2`) und lassen sich bis zum Neustart nicht ändern.
- `audit=1` und `audit_backlog_limit=8192` auf der Kernel-Kommandozeile, damit auch Prozesse vor auditd erfasst werden
- Journal dauerhaft (Ereignisse überstehen einen Neustart), keine Core-Dumps auf der Platte
- `symfonos-security-check.timer`: tägliche Soll-Ist-Prüfung, Abweichungen landen als `auth.warning` im Journal

## Vergleich mit Fedora Workstation

| Bereich | Fedora | symfonOS | Bewertung |
|---|---|---|---|
| MAC | SELinux *targeted*, **enforcing** | SELinux refpolicy, **permissive** zum Start | ⚠ Lücke (siehe unten) |
| Compiler-Härtung | `redhat-rpm-config`, annocheck | gleichwertige Flags, Prüfung bricht Bau ab | ✓ gleichwertig, strenger beim Abbruch |
| Kernel-Härtung | Fedora-Konfiguration | KSPP-nah, strengere Vorgaben (`INIT_ON_ALLOC`, `MODULE_SIG_FORCE`) | ✓ strenger |
| Paketsignaturen | GPG je RPM, signierte Repo-Metadaten (optional) | Ed25519 je Paket **und** signierter Index mit Ablauf/Rollback-Schutz | ✓ strenger |
| Krypto-Richtlinien | crypto-policies (inkl. GnuTLS, NSS, Java) | OpenSSL + OpenSSH | ◐ weniger Back-ends (GnuTLS/NSS folgen mit dem Desktop) |
| Firewall | firewalld, Zone *FedoraWorkstation* (Ports 1025–65535 offen) | nftables, eingehend gesperrt | ✓ strenger |
| Passwörter | pwquality, yescrypt, faillock (über authselect) | gleich, `enforce_for_root` | ✓ gleichwertig |
| Secure Boot | **shim mit Microsoft-Signatur**, läuft sofort | eigene Schlüssel, Einrichtung durch den Benutzer | ⚠ Lücke im Komfort |
| Verschlüsselung | optional im Installer | **Standard**, Argon2id, TPM optional | ✓ strenger |
| Audit | auditd mit wenigen Regeln | OSPP-nahe Regeln, gesperrt | ✓ strenger |
| Sicherheitsteam | Red-Hat-Produktsicherheit, Bodhi-Advisories | CVE-Feld im Rezept, `syp audit` | ⚠ Lücke im Prozess |
| Updates | Bodhi: updates-testing → stable mit Karma | `tools/promote.py`: testing → stable nach 7 Tagen, Sicherheits-Schnellspur, Sperrliste | ◐ ohne Karma-Abstimmung |

## Bekannte Lücken (offen benannt)

1. **SELinux startet permissive.** Die Reference Policy deckt Desktop-Programme weniger gut ab als Fedoras *targeted*-Richtlinie. Vor dem Umschalten auf *enforcing* braucht es eigene Richtlinienmodule, die aus den AVC-Meldungen realer Installationen entstehen (`symfonos-security selinux denials`). Ziel: *enforcing* als Standard, sobald die Testmatrix (Installation, Desktop, Audio, Prüfungsmodus) ohne Verweigerungen läuft. Einzelne Systeme können schon jetzt mit `sudo symfonos-security selinux enforcing` umschalten. Das Werkzeug verweigert den Wechsel, solange es Verweigerungen seit dem Start gibt.
2. **Kein von Microsoft signierter shim.** Dafür ist ein Review durch das shim-review-Board und eine Signatur durch Microsoft nötig, was eine Organisation mit Sicherheitsprozess voraussetzt. Bis dahin gilt: Secure Boot mit eigenen Schlüsseln, oder Secure Boot aus.
3. **Echtheit der Quellen.** Prüfsummen werden beim ersten Laden festgehalten (Trust on first use). Die Prüfung upstream-signierter Tarballs (GPG-Signaturen von GNU, kernel.org und anderen) fehlt noch.
4. **Reproduzierbarkeit.** `SOURCE_DATE_EPOCH` wird gesetzt, ein unabhängiger Zweitbau mit Bit-Vergleich läuft aber noch nicht in der CI.
5. **IMA/EVM** sind im Kernel aktiv, laden aber noch keine Appraisal-Richtlinie. Es gibt außerdem weder fapolicyd noch USBGuard.
6. **Noch nicht auf echter Hardware gebootet.** Die Quellcode-Distro wird in der CI gebaut. Kernel-Konfiguration, UKI-Start und TPM-Entsperrung müssen erst auf Testgeräten bestätigt werden, bevor sie als stabil gelten.

## Zusammenspiel mit dem Prüfungsmodus (SEB)

Die Härtung ist so ausgelegt, dass alle Wege aus [SEB.md](SEB.md) funktionieren:

- Die Firewall-Tabelle des Prüfungsmodus (`inet symfonos_exam`) läuft **zusätzlich** zur Basis-Firewall. Ein Paket muss beide passieren, die Allowlist kann die Basis also nur weiter einschränken. Ein Reload der Basis-Firewall löscht sie nicht (getestet in `tests/test_security.py`).
- Während einer Prüfung spielen automatische Updates nichts ein, und die tägliche Prüfung wertet das bewusst abgeschaltete SysRq nicht als Abweichung.
- Option C (Windows-VM mit offiziellem SEB): KVM bleibt verfügbar. SELinux ordnet Benutzer `unconfined_u` zu, damit QEMU auch im Modus *enforcing* läuft.
- Option B (seb-linux als AppImage): FUSE bleibt verfügbar, und User-Namespaces werden nicht abgeschaltet, weil die Sandbox von Chromium bzw. Qt WebEngine sie braucht.
- Option E (Dual-Boot mit Windows): Mit `symfonos-secureboot init` bleiben die Microsoft-Zertifikate in der db. Windows startet weiter mit Secure Boot, und das verlangen viele SEB-Prüfungskonfigurationen.
- Modulsignaturen und Lockdown verhindern, dass während einer Prüfung unbemerkt Kernel-Module zum Mitschneiden oder Umgehen geladen werden.

## Dateien

| Pfad im Repo | Installiert nach | Paket |
|---|---|---|
| `distro/security/pam.d/` | `/etc/pam.d/` | symfonos-security |
| `distro/security/sysctl.d/`, `modprobe.d/` | `/usr/lib/sysctl.d/`, `/usr/lib/modprobe.d/` | symfonos-security |
| `distro/security/nftables.conf` | `/etc/nftables.conf`, Freigaben in `/etc/nftables.d/` | symfonos-security |
| `distro/security/audit/` | `/etc/audit/rules.d/` | symfonos-security |
| `distro/security/ssh/` | `/etc/ssh/sshd_config.d/`, `/etc/ssh/ssh_config.d/` | symfonos-security |
| `distro/security/crypto/` | `/usr/share/crypto-policies/`, `/etc/crypto-policies/` | crypto-policies |
| `distro/security/bin/` | `/usr/bin/symfonos-security`, `symfonos-secureboot`, `update-crypto-policies` | symfonos-security / crypto-policies |
| `csrc/lib/harden.c` | Prüfung in `syp build` | syp |
