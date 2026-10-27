# Desktop-Stufe aus Quellcode

Auf der Basis (LFS 12.4, rund 140 Pakete) baut die Desktop-Stufe KDE Plasma 6 unter Wayland, ebenfalls
vollständig aus Quellcode. Vorlage sind die Bauanleitungen von **BLFS 12.4 (systemd)**. Sie passen
Version für Version zur Basis und sind von den BLFS-Autoren gemeinsam getestet.

## Bestandteile

| Bereich | Pakete (Auswahl) |
|---|---|
| Grafik | LLVM 20 mit Clang, Mesa 25.1 (radeonsi, r600, iris, crocus, i915, nouveau, virgl, llvmpipe, zink; Vulkan: AMD, Intel, lavapipe, virtio), libdrm, libva, Vulkan-Loader, glslang, SPIR-V |
| Anzeige | Wayland 1.24, wayland-protocols, Xwayland 24.1 für X11-Programme, libxkbcommon, X11-Bibliotheken |
| Eingabe | libinput mit libwacom (Grafiktabletts von Wacom, Huion und XP-Pen), libevdev, mtdev |
| Audio und Video | PipeWire 1.4 mit WirePlumber als Ersatz für PulseAudio und JACK (niedrige Latenz), ALSA, FFmpeg 7.1 mit AV1, VP9, H.264, H.265, Opus, VA-API und Vulkan; GStreamer 1.26 nur als Bibliothek für die Desktop-Portale |
| Qt | Qt 6.9.2, jedes Modul ein eigenes Paket (`qt6-base`, `qt6-declarative`, `qt6-wayland` …), Verzeichnisse wie bei Fedora und Arch |
| KDE | KDE Frameworks 6.17, Plasma 6.4.4 (KWin nur Wayland), SDDM 0.21 mit Wayland-Anmeldebildschirm über KWin |
| Programme | Konsole, Dolphin, Kate, Ark, Gwenview, KCalc, KCharSelect, Systemmonitor, Systemeinstellungen |
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

Die CI veröffentlicht das ISO erst, wenn beide Tests bestanden sind, als Release `desktop-nightly`.
Vollständige Logs und die Bildschirmfotos jedes Laufs liegen auf dem Branch `ci-diag`.

## Noch nicht enthalten

- **NetworkManager und `plasma-nm`:** Das Netzwerk läuft über systemd-networkd und iwd, WLAN wird mit `iwctl` eingerichtet.
- **Bluetooth** (BlueZ, bluedevil).
- **Druck** (CUPS), deshalb auch kein Druck-Portal in xdg-desktop-portal-kde.
- **Spectacle** (braucht OpenCV).
- **Webbrowser:** Firefox braucht Rust und Node.js, Falkon braucht QtWebEngine.
- **Kreativprogramme** (Krita, Blender, Ardour …) und PySide6 für die symfonOS-Apps folgen als nächste Stufe.
