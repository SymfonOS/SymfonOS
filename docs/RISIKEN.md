# Risiken, Aufwand, Lizenzfragen

## Risiken

| Risiko | Auswirkung | Gegenmaßnahme |
|---|---|---|
| **Abhängigkeit von Arch-Binärpaketen** | Upstream-Brüche (z. B. Python-/Qt-Sprünge) kommen ungefiltert an | Staging-Kanal `testing` → `stable` nach 7 Tagen und QEMU-Smoke-Tests (Roadmap Phase 7); Snapshots und `syp rollback` |
| **Eigener Paketmanager** | Bugs treffen das ganze System; kein Ökosystem wie pacman/dnf | 54 Tests, Transaktionshistorie, Pre/Post-Snapshots, `syp doctor`; Python-Referenz später als Rust-Port mit gleichem Format |
| **Konvertierung fremder Binärpakete** | ABI-Mismatch (glibc/Qt/ICU-Versionen), fehlende Abhängigkeiten, Distro-Skripte (debconf, alternatives) | ELF-Sonamen-Prüfung, Mapping-Bericht mit „FEHLT“, `--strict`; Skripte standardmäßig deaktiviert; für Proprietäres Flatpak empfehlen |
| **Pakete hinter Arch-Mechanismen** | pacman-Hooks (`/usr/share/libalpm/hooks`) laufen unter syp nicht | syp-Trigger decken die wichtigsten Hooks ab; CI vergleicht Hook-Liste mit Triggern (offen) |
| **`/etc/os-release`, `filesystem`, `base`** | Branding überschreibt Dateien eines Arch-Pakets (`syp verify` meldet sie) | eigener `filesystem`/`base`-Rebuild (Phase 8) |
| **Sicherheit der Signaturschlüssel** | Kompromittierter CI-Schlüssel = signierte Schadpakete | Schlüssel nur als GitHub-Secret, getrennter Offline-Root-Schlüssel + Rotation über `symfonos-keyring` (offen), reproduzierbare Pakete |
| **Secure Boot** | Viele Geräte booten das ISO nicht ohne Deaktivierung von Secure Boot | shim + MOK bzw. sbctl (Phase 8) |
| **Calamares aus dem AUR** | Build kann bei Upstream-Änderungen brechen | Version im lokalen Repo pinnen, CI-Bau |
| **KI-Assistent** | Halluzinierte Befehle; Prompt-Injection über Logs | Befehle nur vorgeschlagen, Einzelbestätigung, Risikoklasse „gefährlich“ verlangt AUSFÜHREN; Logs als Daten markiert; nur lokale Endpunkte erlaubt |
| **Modell-Download** | URL/Datei ändert sich, Integrität | sha256 im Skript pinnen (offen), Spiegel im eigenen Repo |
| **SEB** | Kein offizieller Linux-Support; Prüfungsorganisationen lehnen Alternativen ab | Prüfungsmodus klar als „ohne SEB-Pflicht“ kennzeichnen; VM/Dual-Boot dokumentiert |
| **Rechtliches Branding** | „symfonOS“ ist nicht markenrechtlich geprüft | vor öffentlichem Release recherchieren (DPMA/EUIPO) |

## Aufwandsschätzung

| Phase | Umfang | Aufwand (Personenmonate) |
|---|---|---|
| 1–3 Referenzimplementierung (dieses Repo) | syp, Konverter, Store, Design-Center, Assistent | 2–3 PM bis zur Beta-Reife |
| 4 ISO + Installer bis zum stabilen Release | Hardwaretests, Calamares-Feinschliff, Barrierefreiheit | 2 PM |
| 6–7 Repo-Infrastruktur + Staging + Smoke-Tests | ~15 000 Pakete spiegeln, Speicher/CDN, QEMU-Tests | 2 PM, laufend ca. 0,3 PM/Monat |
| 8 Eigenständigkeit (eigene Basis, Secure Boot, Rust-Port syp) | | 4–6 PM |
| 9 Store-Ausbau (AppStream, Screenshots, Flatpak) | | 2 PM |
| **Gesamt bis 1.0** | | **ca. 12–15 PM** (z. B. 3 Personen × 5 Monate) |
| **Betrieb** | Sicherheitsupdates, Support, Repo | 0,5–1 Vollzeitstelle |

Infrastruktur: GitHub Pages reicht für den Start. Bei vollständigen Repos (~60 GB je Snapshot) sind Object Storage und CDN nötig (geschätzt 20–100 €/Monat je nach Traffic).

## Lizenzfragen

- **Eigener Code**: GPL-3.0-or-later. Artwork: CC-BY-SA-4.0.
- **Weiterverteilung von Arch-Binärpaketen**: Erlaubt bei freien Lizenzen. GPL/LGPL verlangen, dass der *korrespondierende Quellcode* verfügbar ist. Deshalb sollten die Arch-PKGBUILDs und Quell-Tarballs zu jeder Version gespiegelt werden (Quellcode-Spiegel ist Pflicht vor dem öffentlichen Release, offen). Zusätzlich dürfen keine Arch-Marken im Branding auftauchen.
- **Lizenzprüfung**: `tools/license-report.py` markiert unklare oder unfreie Lizenzen. In der CI blockiert der Bericht neue Unfrei-Pakete im Standard-Image.
- **Proprietäre Software** ist nicht vorinstalliert:
  - *NVIDIA*: Die Kernelmodule sind offen (MIT/GPL), der Userspace ist proprietär. Er wird erst auf dem Zielgerät per `symfonos-hwsetup` installiert, analog zu Fedora/Ubuntu (Opt-in).
  - *DaVinci Resolve, Steam, Spotify*: keine Weiterverteilung erlaubt bzw. nicht nötig → Flathub oder Herstellerinstaller. `syp-convert` erzeugt nur lokale Pakete für den eigenen Gebrauch, keine Redistribution.
  - *Microsoft Core Fonts*: EULA verbietet Weitergabe → nicht enthalten; Liberation-Schriften sind metrisch kompatibel.
- **Patentbelastete Codecs** (H.264/H.265/AAC über FFmpeg/x264/x265): in der EU in der Regel unproblematisch. Für Verteilung in den USA oder mit kommerziellem Vertrieb rechtlich prüfen (Fedora liefert sie deshalb nicht aus).
- **KI-Modell**: Qwen3-4B-Instruct-2507 und Qwen3-1.7B stehen unter Apache-2.0 und dürfen weitergegeben werden. Alternativen wie Gemma (eigene Nutzungsbedingungen) oder Llama (Community-Lizenz mit Einschränkungen) wären für die Weitergabe im ISO ungünstiger.
- **SEB**: Der Windows-SEB steht unter MPL-2.0. Für die VM-Variante braucht jedes Gerät eine Windows-Lizenz. Community-Ports haben eigene Lizenzen und werden nicht mitgeliefert.
- **Calamares** (GPL-3.0-or-later), **archiso** (GPL-3.0-or-later): unproblematisch.

## Eigenständig ergänzt (über die Anforderungen hinaus)

| Ergänzung | Warum |
|---|---|
| Konfigdatei-Schutz (`.sypnew`/`.sypsave`), `syp verify`, `syp doctor` | Updates dürfen Nutzeranpassungen nicht zerstören; Manipulation/Beschädigung erkennen |
| Trigger-System (ldconfig, Icons, initramfs, GRUB …) | konvertierte Pakete brauchen die Nacharbeiten, die sonst pacman-Hooks erledigen |
| Skripte fremder Pakete standardmäßig aus, setuid-Bits entfernt | Sicherheit: fremde Maintainer-Skripte laufen als root |
| Firewall (ufw, eingehend gesperrt), AppArmor aktiv, zram, fstrim | sichere und flotte Grundkonfiguration |
| Eigene Btrfs-Subvolumes für KI-Modelle | mehrere GB pro Modell nicht in jedem Snapshot |
| Live-Boot mit Bildschirmleser, „sichere Grafik“, Reduzierte Bewegung | Barrierefreiheit und Hardware-Kompatibilität ab dem ersten Start |
| Farbneutraler „Studio“-Modus, ICC-Workflow, Tablet-Treiber-Erkennung | kreative Arbeit: farbtreue Darstellung, Stiftgeräte |
| `symfonos-hwsetup` (NVIDIA, ROCm/CUDA für den Assistenten, Huion/XP-Pen) | Treiber automatisch passend zur Hardware |
| Update-Hinweise (Timer + Benachrichtigung), tägliches `syp sync` | Updates sichtbar, ohne ungefragt zu installieren |
| Lizenzbericht, CI mit Tests/Lint/ISO/Repo/Release | Nachvollziehbarkeit, Lizenz-Compliance, reproduzierbare Releases |
| Handbuch (12 Kapitel) | Grundlage für Nutzer und für die RAG-Suche des Assistenten |
