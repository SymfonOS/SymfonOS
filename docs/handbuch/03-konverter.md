# Fremdpakete umwandeln (syp-convert)

Mit der App **Paket-Konverter** oder `syp-convert` lassen sich Pakete aus Debian/Ubuntu (`.deb`), Fedora/openSUSE (`.rpm`) und Arch (`.pkg.tar.zst`) in `.syp` umwandeln.

## Benutzung
- `syp-convert programm.deb -o ~/Pakete` – umwandeln, Bericht anzeigen
- `syp-convert --strict programm.rpm` – Abbruch (Exit 2), wenn Abhängigkeiten fehlen
- `syp-convert --json paket.deb` – maschinenlesbarer Bericht
- danach `sudo syp install --allow-unsigned ~/Pakete/programm-….syp` oder mit `-k schlüssel.key` signieren

## Wie Abhängigkeiten zugeordnet werden
1. Ignorierliste (z. B. `debconf`, `rpmlib(...)`)
2. Mapping-Tabelle (`libc6` → `glibc`, `libgtk-3-0` → `gtk3` …), eigene Ergänzungen in `/etc/syp/convert-map.toml`
3. Regeln (`python3-foo` → `python-foo`, `perl(Foo::Bar)` → `perl-foo-bar`)
4. Bibliotheken aus den ELF-Dateien (DT_NEEDED) werden über `so:`-Provides oder Dateibesitzer aufgelöst
Fehlende Abhängigkeiten erscheinen rot im Bericht („FEHLT“).

## Sicherheit
Installationsskripte konvertierter Pakete (postinst, %post, .install) werden **nicht** automatisch ausgeführt. Prüfe sie mit `syp info --json paket.syp` und führe sie bei Bedarf mit `--trust-scripts` aus. setuid-Bits werden beim Umwandeln entfernt und gemeldet.

## Grenzen
Binärpakete anderer Distributionen sind gegen deren Bibliotheksversionen gebaut. Am zuverlässigsten funktionieren in sich geschlossene Pakete (z. B. Programme unter /opt). Für proprietäre Programme sind oft Flatpak oder AppImage besser.
