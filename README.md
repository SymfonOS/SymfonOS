# symfonOS

Eine schlanke, moderne Linux-Distribution für kreative Arbeit: Grafik, Illustration, Foto, Video, Audio und 3D. Sie bringt ein eigenes Paketsystem mit (`syp`), einen Konverter für Fremdpakete, ein zentrales Design-Center, einen lokalen KI-Assistenten und einen Prüfungsmodus.

| | |
|---|---|
| **Basis** | Zwei Wege: (1) **Quellcode-Distribution** – eigene Toolchain nach LFS, alle Pakete aus `distro/recipes/` mit `syp buildall` gebaut; (2) Arch-basiertes Live-ISO (konvertierte Pakete) als Übergang, bis der Desktop aus Quellcode steht |
| **Sicherheit** | SELinux, erzwungene Kernel-Modulsignaturen, KSPP-Härtung, systemweite Krypto-Richtlinien, nftables (eingehend zu), auditd, LUKS2 + TPM2, Secure Boot mit eigenen Schlüsseln und UKI – siehe [SICHERHEIT.md](docs/SICHERHEIT.md) |
| **Qualität** | Kanäle testing → stable mit Wartezeit, CI-Gates (Werror, ASan/UBSan, Härtungsprüfung je Paket), Freigabekriterien – siehe [QUALITAET.md](docs/QUALITAET.md) |
| **Release-Modell** | Rolling Release (nächtlicher Abgleich, Snapshots vor jeder Änderung) |
| **Kernel** | `linux-zen` (niedrige Latenz für Audio/Stift), Fallback `linux-lts` |
| **Init** | systemd |
| **Bootloader** | GRUB + grub-btrfs (Booten in Snapshots), Live-Medium: systemd-boot/syslinux |
| **Dateisystem** | Btrfs (`@`, `@home`, `@snapshots`, `@var_log`, `@cache`, `@ollama`, `@ai_models`), zstd, snapper |
| **Desktop** | KDE Plasma 6 (Wayland), PipeWire/JACK, Inter + JetBrains Mono + Noto |
| **Installer** | Calamares (Btrfs-Standardlayout, optional LUKS) |
| **Paketsystem** | `syp` (Format `.syp`, Ed25519-signiert, SQLite-Datenbank, Rollback) |

## Architektur

```mermaid
flowchart TB
  subgraph Build["Build & Verteilung (CI)"]
    ARCH[(Arch core/extra)] -->|tools/repo-sync.sh| CONV[syp-convert --mark-scripts-reviewed]
    CONV -->|Ed25519| REPO[(syp-Repos: symfonos / core / extra<br/>GitHub Pages)]
    SRC[Dieses Repo] -->|PKGBUILD / SYPBUILD| LOCAL[(Lokales Repo:<br/>symfonos-tools, -branding, -keyring, calamares)]
    LOCAL --> ISO[mkarchiso → symfonos-*.iso]
    ARCH --> ISO
  end
  subgraph System["Installiertes System"]
    CAL[Calamares] -->|postinstall: syp import-pacman| SYP
    SYP[syp CLI] --> SOLVER[Solver<br/>Backtracking, provides, Konflikte]
    SYP --> TX[Transaktionen<br/>Konfliktcheck, Trigger, Konfig-Schutz]
    TX --> SNAP[snapper Pre/Post-Snapshot]
    TX --> DB[(SQLite: Pakete, Dateien, Historie)]
    STORE[Software-Center] -->|pkexec| SYP
    CGUI[Konverter-GUI] --> CONVL[syp-convert] --> SYP
    DESIGN[Design-Center] --> PLASMA[KDE Plasma 6]
    ASSIST[Assistent] -->|nur lokal| OLLAMA[Ollama + Qwen3 4B]
    ASSIST -->|RAG| DOCS[(Handbuch /usr/share/doc/symfonos)]
    ASSIST -->|nach Bestätigung| SHELL[Befehl]
    EXAM[symfonos-exam] --> NFT[nftables-Allowlist] & CAGE[cage-Kiosk: Firefox / seb-linux / VM]
  end
  REPO --> SYP
  ISO --> CAL
```

## Roadmap

| Phase | Inhalt | Stand |
|---|---|---|
| 0 | Architektur, Basis, Name, Branding | ✅ |
| 1 | Paketsystem `syp`: Format, Signaturen, Repos, Solver, Transaktionen, Trigger, Historie, Rollback, Build-Rezepte | ✅ getestet |
| 2 | `syp-convert` für deb/rpm/arch inkl. ELF-Sonamen-Mapping, CLI + GUI | ✅ getestet |
| 3 | Software-Center, Design-Center, Assistent (RAG, Log-Analyse, Bestätigungspflicht) | ✅ getestet (Offscreen-Screenshots) |
| 4 | ISO: archiso-Profil, Calamares (Btrfs/LUKS/GRUB), Live-Benutzer, Barrierefreiheit beim Boot | ✅ konfiguriert, Bau in CI |
| 5 | Prüfungsmodus (nftables, Kiosk, Dienste/USB), SEB-Varianten | ✅ Trockenlauf getestet |
| 6 | Repo-Spiegel aus Arch, CI/CD, Releases | ✅ Workflow, Secret nötig |
| 7 | Kanäle `testing` → `stable` (`tools/promote.py`: 7 Tage, Sicherheits-Schnellspur, Sperrliste, Abhängigkeitsprüfung) | ✅ getestet, Anbindung an die Bau-CI offen |
| 8 | Kern in C (`syp`, `syp-convert`), Quellcode-Distribution: LFS-Toolchain, 140 Rezepte, `syp buildall` in der Chroot, Live-/Installations-ISO | ✅ ISO `source-nightly`, Boot-Test in QEMU bestanden (Firewall, Audit, SELinux, WLAN-Dienst laufen); Test auf echter Hardware offen |
| 8a | Sicherheit auf Fedora-Niveau: SELinux, Krypto-Richtlinien, PAM, Audit, Firewall, Secure Boot (eigene Schlüssel, UKI), TPM2 | ✅ umgesetzt; SELinux zunächst permissive, kein MS-signierter shim |
| 8b | Desktop aus Quellcode (Mesa, Wayland, Qt 6, KDE Plasma, PipeWire), GnuTLS/NSS in den Krypto-Richtlinien | offen |
| 9 | Delta-Updates, Software-Center mit Screenshots/AppStream, Flatpak-Integration im Store | offen |

## Schnellstart (Entwicklung)

```bash
pip install -e ".[gui,test]"
make -C csrc                           # syp und syp-convert (C11; libarchive, libzstd, libsodium, sqlite3, libcurl)
make test                              # ~75 Tests: Paketsystem, Konverter, Sicherheitsschicht, Kanäle, Prüfungsmodus, Assistent
syp keygen ~/dev --comment test        # Schlüsselpaar
syp build SYPBUILD.toml -k ~/dev.key   # symfonos-tools als .syp bauen
syp-convert paket.deb --offline        # Fremdpaket umwandeln
syp -r /tmp/testroot list             # beliebiges Wurzelverzeichnis (Tests, Bootstrap)
sudo ./iso/build.sh out                # ISO bauen (Arch Linux / Container archlinux:latest --privileged)
```

## Verzeichnisse

| Pfad | Inhalt |
|---|---|
| `csrc/` | `syp` und `syp-convert` in C: Format, Signaturen, Solver, Transaktionen, Bau, Härtungsprüfung, Konverter |
| `distro/recipes/` | Rezepte (`SYPBUILD.toml`) der Quellcode-Distribution |
| `distro/bootstrap/` | LFS-Stufen: Cross-Toolchain, temporäre Werkzeuge, Chroot, `syp buildall`, Image |
| `distro/security/` | Sicherheitskonfiguration, `symfonos-security`, `symfonos-secureboot`, Krypto-Richtlinien |
| `distro/installer/` | Konsolen-Installer (LUKS2, TPM2, systemd-boot/UKI oder GRUB, SELinux-Relabel) |
| `src/syp_convert` | Konverter-GUI (nutzt das C-Werkzeug) |
| `src/syp_store` | Software-Center |
| `src/symfonos_design` | Design-Center + Plasma-Layouts |
| `src/symfonos_assist` | Lokaler Assistent: RAG, Ollama/llama.cpp, Log-Regeln, Sicherheitslogik |
| `src/symfonos_ui` | Gemeinsamer Qt-Stil (Hell/Dunkel, Akzentfarbe) |
| `system/` | Systemdateien: Units, Polkit, Skripte, Konfiguration |
| `desktop/` | Farbschemata, globale Designs, Logo, Calamares-Branding, snapper |
| `iso/` | archiso-Profil inkl. Calamares-Konfiguration |
| `exam/` | Prüfungsmodus |
| `packaging/` | PKGBUILDs (ISO-Bau) |
| `docs/` | [Paketformat](docs/PAKETFORMAT.md), [Sicherheit](docs/SICHERHEIT.md), [Qualität](docs/QUALITAET.md), [Apps & Lizenzen](docs/APPS.md), [SEB](docs/SEB.md), [Risiken & Aufwand](docs/RISIKEN.md), [Handbuch](docs/handbuch/) |

Lizenz: Code GPL-3.0-or-later, Artwork CC-BY-SA-4.0.

## Screenshots

| Software-Center | Design-Center | Paket-Konverter |
|---|---|---|
| ![Software](docs/screenshots/software.png) | ![Design-Center](docs/screenshots/design-center.png) | ![Konverter](docs/screenshots/konverter.png) |
