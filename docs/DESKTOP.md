# Desktop-Stufe aus Quellcode

Auf der Basis (LFS 12.4, rund 140 Pakete) baut die Desktop-Stufe KDE Plasma 6 unter Wayland, ebenfalls
vollständig aus Quellcode. Vorlage sind die Bauanleitungen von **BLFS 12.4 (systemd)**. Sie passen
Version für Version zur Basis und sind von den BLFS-Autoren gemeinsam getestet.

## Bestandteile

| Bereich | Pakete (Auswahl) |
|---|---|
| Grafik | LLVM 20 mit Clang, Mesa 25.1 (radeonsi, r600, iris, crocus, i915, nouveau, virgl, llvmpipe, zink; Vulkan: AMD, Intel, lavapipe, virtio), libdrm, libva, Vulkan-Loader, glslang, SPIR-V |
| Anzeige | Wayland 1.24, wayland-protocols, Xwayland 24.1 für X11-Programme, libxkbcommon, X11-Bibliotheken |
| SELinux | Reference Policy 2025.02 mit eigenem Modul `symfonos-selinux` (`distro/selinux/`): SDDM läuft als `xdm_t`, die Sitzung als `unconfined_t`. Live-System und installierter Desktop laufen im Modus *enforcing* (Standard, wie Fedora) ohne Verweigerungen (siehe [SICHERHEIT.md](SICHERHEIT.md)) |
| Netzwerk | NetworkManager 1.54 mit `plasma-nm` (WLAN über iwd, DNS über systemd-resolved), Mobilfunk über ModemManager (LTE/5G-Modems mit MBIM und QMI, Anbieterdaten aus mobile-broadband-provider-info), DSL (PPPoE über pppd 2.5), VPN mit WireGuard und OpenVPN 2.6; das Basissystem bleibt bei systemd-networkd. Bluetooth mit BlueZ 5.83 und `bluedevil`, Audio über PipeWire (SBC, Opus) |
| Laufwerke | UDisks2 2.10 mit libblockdev 3.3 (Partitionen, Dateisysteme, LUKS, NVMe): USB-Sticks und Platten erscheinen in Dolphin und im Infobereich und lassen sich ohne root einhängen (unter `/run/media/$USER`) |
| Drucken | CUPS 2.4 mit driverlosem Drucken (IPP Everywhere, AirPrint), Druckersuche über Avahi (Namen wie `drucker.local` über nss-mdns), cups-filters 2 (PDF über qpdf, Rasterung über Ghostscript), `print-manager` in den Systemeinstellungen, Qt-Druckdialog mit CUPS und Druck-Portal; Drucker verwalten dürfen Mitglieder von `wheel` |
| Eingabe | libinput mit libwacom (Grafiktabletts von Wacom, Huion und XP-Pen), libevdev, mtdev |
| Audio und Video | PipeWire 1.4 mit WirePlumber als Ersatz für PulseAudio und JACK (niedrige Latenz), ALSA, FFmpeg 7.1 mit AV1, VP9, H.264, H.265, Opus, VA-API und Vulkan; GStreamer 1.26 nur als Bibliothek für die Desktop-Portale |
| Qt | Qt 6.9.2, jedes Modul ein eigenes Paket (`qt6-base`, `qt6-declarative`, `qt6-wayland` …), Verzeichnisse wie bei Fedora und Arch |
| KDE | KDE Frameworks 6.17, Plasma 6.4.4 (KWin nur Wayland), SDDM 0.21 mit Wayland-Anmeldebildschirm über KWin |
| Programme installieren | Discover mit Flatpak 1.16 und **Flathub** als vorbelegter Quelle (`/etc/flatpak/remotes.d/`), KDE Store über KNewStuff; Berechtigungen der Flatpak-Programme in den Systemeinstellungen (`flatpak-kcm`). Programme von Flathub sind vorgebaut (nicht aus diesem Repository) und laufen in einer bubblewrap-Sandbox; Signaturen prüft ostree mit GnuPG. Systempakete bleiben bei `syp` |
| Programme | Konsole, Dolphin, Kate, Ark, Gwenview, Okular (PDF mit Poppler), Dragon Player (Video) und Elisa (Musik) über Qt Multimedia, Spectacle (Bildschirmfotos, Taste „Druck“), KCalc, KCharSelect, Systemmonitor, Systemeinstellungen |
| Schriften | Inter (Oberfläche), JetBrains Mono (Terminal), DejaVu, Noto Color Emoji |

Das Metapaket `symfonos-desktop` fasst alles zusammen. `symfonos-desktop-config` enthält die
symfonOS-Voreinstellungen: Farbschemata, globales Design, SDDM unter Wayland, PAM für Anmeldung und
Bildschirmsperre, systemd-Presets und `graphical.target`.

## Rezepte erzeugen

Die rund 330 Rezepte schreibt `tools/desktop-recipes.py`. Paketfamilien teilen sich dort eine
Bauvorschrift: Xorg-Bibliotheken, Qt-Module, Frameworks, Plasma und KDE Gear. Ein Versionssprung ist
dadurch eine Änderung an einer einzigen Stelle, zum Beispiel `KF = "6.17.0"`.

```sh
tools/desktop-recipes.py            # Rezepte schreiben bzw. aktualisieren (Prüfsummen bleiben erhalten)
tools/desktop-recipes.py --check    # weichen Rezepte vom Generator ab? (Test in der CI)
```

- **Neue Quellen** bekommen `sha256 = "TODO"`. Die CI trägt die Summe ein (Workflow »Rezepte«), sobald die Änderung auf `main` liegt.
- **Von Hand gepflegte Rezepte** haben keine `GENERATED`-Kopfzeile. Der Generator überschreibt sie nie.
- **Quellen aus diesem Repository** (`path = "@/…"`, z. B. Installer, syp, Desktop-Voreinstellungen): Nach einer Änderung braucht das Paket eine neue Version, sonst bleibt das alte im Pool. `tools/local-sources.py` hält Version und Inhalt in `distro/recipes/local-sources.lock` fest; ein Test meldet geänderte Quellen bei gleicher Version.

## Live-Medium

`VARIANT=desktop distro/bootstrap/50-image.sh` baut das Desktop-ISO.

- **Anmeldung:** Der Live-Benutzer meldet sich über SDDM automatisch in Plasma an. Der Installer entfernt diese automatische Anmeldung wieder.
- **Größe:** Das ISO ist rund 2,5 GiB groß. Um es klein zu halten, packt syp auf dem Medium Doku, Übersetzungen außer Deutsch und Englisch sowie Firmware reiner Server- und ARM-Hardware nicht aus. Dafür sorgt `[install] exclude`. GitHub nimmt je Datei höchstens 2 GiB an, deshalb veröffentlicht die CI das ISO in Teilen; `cat symfonos-desktop-*.iso.part* > symfonos-desktop.iso` setzt es wieder zusammen.
- **Installiertes System:** Die ausgelassenen Dateien bleiben den Paketen zugeordnet. Das installierte System hat die normale Konfiguration. `syp reinstall <paket>` holt Ausgelassenes nach.

## Tests

| Test | Prüft |
|---|---|
| `tools/desktop-test.sh` | ISO bootet in QEMU mit virtio-GPU bis `graphical.target`, KWin (Wayland) und `plasmashell` laufen, kein Absturz; Bildschirmfotos über den QEMU-Monitor |
| `tools/install-test.sh` | Installation aus dem Desktop-ISO auf eine virtuelle Platte, das installierte System startet bis zur Anmeldung |
| `tools/avc-summary.py` | Fasst die SELinux-Verweigerungen je Regel zusammen (`avc.txt` auf `ci-diag`). Der Haupt-Desktop-Test bootet dafür mit `enforcing=0`; ein zweiter Desktop-Test bootet das Live-System mit `enforcing=1` bis in die Plasma-Sitzung, der Desktop-Installationstest startet das installierte System mit `enforcing=1`; beide sind Bedingung für die Veröffentlichung |
| `tools/check-libs.py` | Beim Bau des Live-Systems: Jede ELF-Datei findet ihre Bibliotheken (fehlende Laufzeitabhängigkeiten erscheinen als Warnung) |

Die CI veröffentlicht das ISO erst, wenn beide Tests bestanden sind, als Release `desktop-nightly`.
Vollständige Logs und die Bildschirmfotos jedes Laufs liegen auf dem Branch `ci-diag`.

## Noch nicht enthalten

- **Weitere VPN-Erweiterungen:** OpenConnect (Cisco AnyConnect, GlobalProtect …) – die Oberfläche in plasma-nm braucht QtWebEngine; L2TP/IPsec – NetworkManager-l2tp braucht NSS, dazu strongSwan und xl2tpd.
- **Webbrowser aus dem Quellcode:** Firefox braucht Rust und Node.js, Falkon braucht QtWebEngine; beide passen nicht in eine Bauetappe der CI. Bis dahin kommt ein Browser über Flathub (z. B. `flatpak install flathub org.mozilla.firefox` oder in Discover).
- **Flatpak im enforcing-Test:** Programme laufen als `unconfined_t` in der bubblewrap-Sandbox, der Systemhelfer wie bei Fedora in der uneingeschränkten Domäne `flatpak_helper_t` (Modul `symfonos-selinux`). Eine Installation von Flathub ist in der CI noch nicht abgedeckt.
- **Kreativprogramme** (Krita, Blender, Ardour …) und PySide6 für die symfonOS-Apps folgen als nächste Stufe.
