# Vorinstallierte Anwendungen

Alle Apps werden als `.syp` ausgeliefert. **Binärquelle** ist bei Fremdsoftware Arch Linux `[extra]`; die Pakete werden mit `syp-convert` umgewandelt und mit dem symfonOS-Schlüssel neu signiert. Fedora- und Ubuntu-Binärpakete eignen sich nicht als Basis, weil sie gegen andere glibc-, Qt- und Bibliotheksversionen gebaut sind (ABI). Deshalb nennt die Spalte **Vorbild** die Distribution, deren Standardauswahl die App abdeckt. Lizenzen sind die Upstream-SPDX-Angaben; verbindlich sind die Paketmetadaten (`syp info <paket>`). `tools/license-report.py` prüft sie in der CI.

Legende Quelle: **Arch** = aus Arch konvertiert · **eigen** = symfonOS-Eigenentwicklung

## Kreativ-Apps („Artcraft“)

| Bereich | App | Paket | Quelle | Lizenz |
|---|---|---|---|---|
| Malen/Illustration | Krita (+ G'MIC-Plugin) | `krita`, `krita-plugin-gmic` | Arch | GPL-3.0-or-later |
| Malen | MyPaint | `mypaint` | Arch | GPL-2.0-or-later |
| Bildbearbeitung | GIMP 3 (+ G'MIC) | `gimp`, `gimp-plugin-gmic` | Arch | GPL-3.0-or-later |
| Vektorgrafik | Inkscape | `inkscape` | Arch | GPL-2.0-or-later |
| Layout/DTP | Scribus | `scribus` | Arch | GPL-2.0-or-later |
| Schriftgestaltung | FontForge | `fontforge` | Arch | GPL-3.0-or-later |
| 2D-Animation | Synfig Studio | `synfig` | Arch | GPL-3.0-or-later |
| RAW-Foto | darktable | `darktable` | Arch | GPL-3.0-or-later |
| RAW-Foto | RawTherapee | `rawtherapee` | Arch | GPL-3.0-or-later |
| Fotoverwaltung | digiKam | `digikam` | Arch | GPL-2.0-or-later |
| Panorama | Hugin | `hugin` | Arch | GPL-2.0-or-later |
| Farbkalibrierung | ArgyllCMS, colord | `argyllcms`, `colord` | Arch | AGPL-3.0-only, GPL-2.0-or-later |
| 3D/Animation | Blender | `blender` | Arch | GPL-2.0-or-later |
| CAD/3D-Druck | FreeCAD (nachinstallierbar: `syp install freecad`) | `freecad` | Arch | LGPL-2.0-or-later |
| Videoschnitt | Kdenlive | `kdenlive` | Arch | GPL-3.0-or-later |
| Streaming/Aufnahme | OBS Studio | `obs-studio` | Arch | GPL-2.0-or-later |
| Transkodierung | HandBrake | `handbrake` | Arch | GPL-2.0-only |
| DAW | Ardour | `ardour` | Arch | GPL-2.0-or-later |
| Audioeditor | Audacity | `audacity` | Arch | GPL-3.0-or-later |
| Musikproduktion | LMMS | `lmms` | Arch | GPL-2.0-or-later |
| Notensatz | MuseScore | `musescore` | Arch | GPL-3.0-only |
| Drumcomputer | Hydrogen | `hydrogen` | Arch | GPL-2.0-or-later |
| Plugin-Host | Carla | `carla` | Arch | GPL-2.0-or-later |
| Plugins/Synths | Calf, ZynAddSubFX, LSP Plugins, Surge XT | `calf`, `zynaddsubfx`, `lsp-plugins-lv2/-clap`, `surge-xt` | Arch | LGPL-2.1-or-later, GPL-2.0-or-later, LGPL-3.0-or-later, GPL-3.0-or-later |
| Werkzeuge | G'MIC, ImageMagick, FFmpeg, GStreamer-Plugins | `gmic`, `imagemagick`, `ffmpeg`, `gst-plugins-*`, `gst-libav` | Arch | CECILL-2.1, ImageMagick, GPL-3.0-or-later (Build mit x264/x265), LGPL-2.1-or-later |
| Bildformate | PSD/EXR/JXL/AVIF/HEIF/WebP in Dolphin & Gwenview | `kimageformats`, `qt6-imageformats`, `libjxl`, `libavif`, `libheif` | Arch | LGPL-2.1-or-later, BSD-3-Clause, BSD-2-Clause, LGPL-3.0-or-later |

## Standard-Apps (Äquivalente zu Fedora Workstation/KDE und Ubuntu/Kubuntu)

| Funktion | App | Paket | Vorbild | Quelle | Lizenz |
|---|---|---|---|---|---|
| Webbrowser | Firefox (de) | `firefox`, `firefox-i18n-de` | Fedora + Ubuntu | Arch | MPL-2.0 |
| E-Mail/Kalender | Thunderbird (de) | `thunderbird`, `thunderbird-i18n-de` | Ubuntu, Fedora KDE | Arch | MPL-2.0 |
| Büro | LibreOffice Fresh (de, Rechtschreibung) | `libreoffice-fresh(-de)`, `hunspell-de`, `hyphen-de` | Fedora + Ubuntu | Arch | MPL-2.0 |
| Dateimanager | Dolphin | `dolphin` | Fedora KDE/Kubuntu (Nautilus) | Arch | GPL-2.0-or-later |
| Texteditor | Kate | `kate` | GNOME Text Editor | Arch | LGPL-2.0-or-later |
| Terminal | Konsole | `konsole` | GNOME Terminal/Ptyxis | Arch | GPL-2.0-or-later |
| Medienplayer (Video) | Haruna | `haruna` | Totem/Showtime | Arch | GPL-3.0-or-later |
| Musik | Elisa | `elisa` | Rhythmbox | Arch | LGPL-3.0-or-later |
| Bildbetrachter | Gwenview | `gwenview` | Loupe/Eye of GNOME | Arch | GPL-2.0-or-later |
| PDF/Dokumente | Okular | `okular` | Papers/Evince | Arch | GPL-2.0-or-later |
| Taschenrechner | KCalc | `kcalc` | GNOME Rechner | Arch | GPL-2.0-or-later |
| Systemmonitor | Plasma-Systemmonitor | `plasma-systemmonitor` | GNOME Systemüberwachung | Arch | GPL-2.0-or-later |
| Software-Center | **symfonOS Software** | `symfonos-tools` | GNOME Software/App Center | eigen | GPL-3.0-or-later |
| Backup | Déjà Dup | `deja-dup` | Ubuntu (Déjà Dup) | Arch | GPL-3.0-or-later |
| Bildschirmfoto | Spectacle | `spectacle` | GNOME Screenshot | Arch | GPL-2.0-or-later |
| Archive | Ark | `ark` | File Roller | Arch | GPL-2.0-or-later |
| Datenträger | KDE Partition Manager | `partitionmanager` | GNOME Disks | Arch | GPL-3.0-or-later |
| Speicheranalyse | Filelight | `filelight` | Baobab | Arch | GPL-2.0-or-later |
| Scannen | Skanpage | `skanpage` | Document Scanner | Arch | GPL-2.0-or-later |
| Kamera | Snapshot | `snapshot` | Snapshot/Cheese | Arch | GPL-3.0-or-later |
| Kalender/Kontakte | Merkuro | `merkuro` | GNOME Kalender/Kontakte | Arch | GPL-3.0-or-later |
| Wetter/Uhren | KWeather, KClock | `kweather`, `kclock` | GNOME Wetter/Uhren | Arch | GPL-2.0-or-later |
| Zeichentabelle | KCharSelect | `kcharselect` | GNOME Zeichen | Arch | GPL-2.0-or-later |
| Dateisuche | KFind | `kfind` | – | Arch | GPL-2.0-or-later |
| Fernzugriff | KRDC | `krdc` | Remmina/Connections | Arch | GPL-2.0-or-later |
| Torrent | KTorrent | `ktorrent` | Transmission (Ubuntu) | Arch | GPL-2.0-or-later |
| Smartphone | KDE Connect | `kdeconnect` | GSConnect | Arch | GPL-2.0-or-later |
| Drucken | Druckerverwaltung, CUPS | `print-manager`, `cups` | Fedora + Ubuntu | Arch | GPL-2.0-or-later, Apache-2.0 |
| Bildschirmleser | Orca | `orca` | Fedora + Ubuntu | Arch | LGPL-2.1-or-later |
| Flatpak | Flatpak + Einstellungsmodul | `flatpak`, `flatpak-kcm` | Fedora | Arch | LGPL-2.1-or-later |

## symfonOS-eigene Komponenten

| Komponente | Befehl/App | Lizenz |
|---|---|---|
| Paketmanager | `syp` | GPL-3.0-or-later |
| Paket-Konverter | `syp-convert`, „Paket-Konverter“ | GPL-3.0-or-later |
| Software-Center | „Software“ (`symfonos-store`) | GPL-3.0-or-later |
| Design-Center | `symfonos-design` | GPL-3.0-or-later |
| Assistent | `symfonos-assist`, „Assistent“ | GPL-3.0-or-later |
| Prüfungsmodus | `symfonos-exam` | GPL-3.0-or-later |
| Hardware-Einrichtung | `symfonos-hwsetup` | GPL-3.0-or-later |
| Designs, Farbschemata, Hintergründe, Logo | `symfonos-branding` | CC-BY-SA-4.0 |
| KI-Laufzeit / Modell | Ollama / Qwen3-4B-Instruct-2507 (Download beim ersten Start) | MIT / Apache-2.0 |

## Nicht vorinstalliert (proprietär oder lizenzrechtlich heikel)
DaVinci Resolve, NVIDIA-Userspace-Treiber, Steam, Spotify, Microsoft-Schriften und Codecs mit Patentlizenzpflicht außerhalb der EU. Sie lassen sich nachinstallieren: NVIDIA automatisch über `symfonos-hwsetup`, die übrigen über Flatpak (Flathub) oder `syp-convert`. Siehe [RISIKEN.md](RISIKEN.md#lizenzfragen).
