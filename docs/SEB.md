# Safe Exam Browser (SEB) unter symfonOS

## Stand der Linux-Unterstützung (Recherche Oktober 2026)

- **Kein offizieller Linux-Client.** SEB wird offiziell nur für Windows, macOS und iOS entwickelt. Der Hauptentwickler schrieb im Dezember 2025 in der Projekt-Diskussion, die technische Machbarkeit sei bestenfalls unklar und die organisatorischen Voraussetzungen seien vorerst nicht gegeben ([Discussion #1360](https://github.com/SafeExamBrowser/seb-win-refactoring/discussions/1360)). Der frühere Linux-Client (SEB 2.x, XULRunner) ist seit Jahren eingestellt.
- **Hochschulen** führen Linux ausdrücklich als nicht unterstützt, z. B. [NC State Libraries](https://www.lib.ncsu.edu/software/safe-exam-browser) und [APU IT Helpdesk](https://apiit.atlassian.net/wiki/spaces/ITSM/pages/2925101136/Safe+Exam+Browser+SEB+FAQ+and+Installation+Guide).
- **Community-Ports (inoffiziell):**
  - [Jvr2022/seb-linux](https://github.com/Jvr2022/seb-linux): nativer Qt-6-Client, öffnet `.seb`-Dateien und `seb://`-Links, verteilt als AppImage.
  - [Jitheswar/seb-linux](https://github.com/Jitheswar/seb-linux): .NET-8-Port mit System-Chromium. Liest echte `.seb`-Konfigurationen, hat aber **kein Lockdown** und erzwingt die Prüfungsbeschränkungen daher nicht.
- **Wine/Proton:** Nutzerberichte zeigen, dass die Windows-Version unter Wine scheitert ([Linux Mint Forum](https://forums.linuxmint.com/viewtopic.php?t=408901), [Manjaro Forum](https://forum.manjaro.org/t/how-to-install-safe-exam-browser-for-student-examination/157984)). SEB hängt von .NET, WebView2 und Windows-Kernel-Schnittstellen ab (Kiosk, Prozessüberwachung).
- **Prüfungsplattformen** wie Moodle können den *Browser Exam Key* bzw. *Config Key* prüfen. Ein Client wird dann nur akzeptiert, wenn er diese Schlüssel korrekt berechnet und die Prüfungsorganisation ihn zulässt.

## Integrationsoptionen in symfonOS

| Option | Umsetzung | Eignung |
|---|---|---|
| **A. Prüfungsmodus + Kiosk-Firefox** (Standard) | `sudo symfonos-exam start --url URL --allow DOMAIN` | Prüfungen **ohne** SEB-Pflicht (Moodle-Quiz ohne SEB-Schlüssel, Online-Klausuren mit Domain-Allowlist) |
| **B. Community-Client seb-linux** | AppImage nach `/opt/seb-linux/SafeExamBrowser.AppImage`, dann `symfonos-exam start --seb pruefung.seb` – läuft im selben gesperrten Kiosk | nur, wenn die Prüfungsorganisation den inoffiziellen Client ausdrücklich zulässt |
| **C. Windows-VM mit offiziellem SEB** | `symfonos-exam vm --image win11.qcow2 --allow DOMAIN` (QEMU/KVM im Kiosk) | nur, wenn die SEB-Konfiguration virtuelle Maschinen erlaubt; SEB erkennt VMs und blockiert sie sonst. Windows-Lizenz nötig |
| **D. Wine/Proton** | – | **nicht unterstützt** (nicht lauffähig bzw. von SEB abgelehnt) |
| **E. Dual-Boot Windows** | Calamares richtet GRUB mit os-prober ein | volle Kompatibilität, wenn eine offizielle Plattform zwingend ist |

## Was der Prüfungsmodus einschränkt (`exam/symfonos-exam`)

- **Fenster:** eigener Benutzer `exam`, Sitzung im Kiosk-Compositor `cage` mit genau einem Vollbildfenster. Kein Taskwechsel, kein Panel, kein VT-Wechsel (cage ohne `-s`), SysRq aus. Wird die App geschlossen, startet sie neu.
- **Netzwerk:** nftables-Tabelle `inet symfonos_exam` mit Policy *drop*. Erlaubt sind nur loopback, DHCP, DNS zu den Systemresolvern und HTTP(S) zu den aufgelösten Adressen der freigegebenen Domains. Die Adressen werden alle 5 Minuten neu aufgelöst (CDNs).
- **Browser:** Firefox-Enterprise-Policies (Website-Filter mit Allowlist, keine DevTools, kein about:config, keine Add-ons, keine Downloads/Drucken, keine Passwortspeicherung).
- **Hintergrundprozesse:** Bluetooth, SSH, Ollama/Assistent, Avahi, CUPS, KDE Connect und gängige Fernwartungsdienste werden gestoppt und danach wiederhergestellt.
- **Geräte:** `usb_storage`/`uas` gesperrt (Neuladen verhindert).
- **Ende:** `sudo symfonos-exam stop` oder automatisch nach `--minutes N`. Der Normalzustand wird wiederhergestellt und das Prüfungs-Home gelöscht.
- **Grenzen:** Lokale Root-Rechte heben jede Sperre auf. Der Modus schützt vor Bedienung im laufenden Betrieb, nicht vor einem Besitzer mit Admin-Rechten oder einem Live-USB-Stick. Für beaufsichtigte Prüfungen an Leihgeräten sollte das Gerät ohne Admin-Konto für Prüflinge betrieben werden.

## SEB-Kompatibilität und Sicherheitsschicht

Die Härtung (siehe [SICHERHEIT.md](SICHERHEIT.md)) ist auf alle fünf Optionen abgestimmt. Die
automatischen Tests in `tests/test_security.py` prüfen das:

| Option | Was sie braucht | Wie die Härtung damit umgeht |
|---|---|---|
| A. Kiosk-Firefox | eigene nftables-Tabelle | Die Tabelle `inet symfonos_exam` läuft zusätzlich zur Basis-Firewall (beide müssen ein Paket erlauben). Ein Firewall-Reload löscht sie nicht. |
| B. seb-linux (AppImage) | FUSE, User-Namespaces für die Browser-Sandbox | FUSE ist nicht gesperrt, und User-Namespaces bleiben aktiv |
| C. Windows-VM mit offiziellem SEB | KVM, QEMU | KVM ist nicht gesperrt; SELinux ordnet Benutzer `unconfined_u` zu, damit QEMU auch im Modus *enforcing* läuft |
| E. Dual-Boot Windows | Windows startet mit Secure Boot | `symfonos-secureboot init` behält die Microsoft-Zertifikate in der db. Viele SEB-Konfigurationen verlangen unter Windows Secure Boot oder ein TPM. |

Zusätzlich während einer Prüfung:
- Es werden keine automatischen Updates eingespielt (`ConditionPathExists=!/run/symfonos-exam/active`).
- Erzwungene Modulsignaturen und, mit Secure Boot, Kernel-Lockdown verhindern, dass sich zur Laufzeit ein fremdes Kernel-Modul einschleust, um Bildschirm oder Eingaben abzugreifen.
- Die tägliche Sicherheitsprüfung wertet das vom Prüfungsmodus abgeschaltete SysRq nicht als Abweichung.

In der Quellcode-Distribution steckt der Prüfungsmodus im Paket `symfonos-exam` (Teil von `symfonos-base`).
Die grafischen Bestandteile `cage`, `firefox`, `sddm`, `qemu-desktop` und `fuse2` sind optionale
Abhängigkeiten. Sie kommen mit der Desktop-Stufe. Bis dahin liefert das Arch-basierte ISO den
vollständigen Prüfungsmodus.
