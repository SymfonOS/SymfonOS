# Qualität und Stabilität

Vorbild ist der Fedora-Prozess: Kein Paket erreicht Benutzer, ohne automatische Prüfungen bestanden
und eine Zeit in einem Testkanal verbracht zu haben, und jede Änderung am System lässt sich
zurücknehmen. Hier steht, was davon umgesetzt ist und was noch fehlt.

## Kanäle

```
Rezept-Änderung ──CI──▶ testing ──(7 Tage, Tests, keine Sperre)──▶ stable ──▶ Benutzer
                          ▲                                           ▲
                 Sicherheitsupdates: Schnellspur (sofort, nach den Tests)
```

| Kanal | Inhalt | Wer nutzt ihn |
|---|---|---|
| `testing` | jeder Bau, der alle ISO-Tests bestanden hat (GitHub-Release `repo-x86_64-testing`) | Entwickler, Tester (den auskommentierten Eintrag `testing` in `/etc/syp/syp.toml` aktivieren) |
| `stable` | aus testing übernommen mit `tools/promote.py` (GitHub-Release `repo-x86_64`) | Standard für alle Installationen |

Beides erledigt die Bau-CI nach jedem grünen Lauf (`tools/promote-release.sh`): erst testing veröffentlichen, dann übernehmen und stable neu signieren. Seit wann ein Paket in testing liegt, steht in `promotion.json` am testing-Release. stable verliert dabei nie Pakete; schrumpft es, bricht der Schritt ab.

`tools/promote.py` (entspricht Bodhi bei Fedora) übernimmt ein Paket erst, wenn
1. es mindestens **7 Tage** in testing liegt; Pakete mit `security`-Feld kommen über die Schnellspur sofort,
2. niemand es gesperrt hat (`distro/channels/hold` im Repository, z. B. bei einem offenen Fehlerbericht),
3. **alle Abhängigkeiten in stable erfüllbar** bleiben. Neue Bibliotheksversionen werden zusammen mit ihren Nutzern übernommen, nie einzeln.
4. Außerdem wird der Index von testing nur mit gültiger Signatur gelesen.

Wie Fedoras Bodhi sammelt symfonOS Karma, also Bewertungen von Testern: Für jedes wartende Paket legt die Bau-CI ein GitHub-Issue mit dem Label `testing` an (Titel `testing: NAME VERSION`, mit Stand und Anleitung). Tester stimmen mit 👍 oder 👎 ab, je Person eine Stimme, Bots zählen nicht (`tools/testing-karma.py`). Ab Karma +3 genügt ein Tag in testing, ab -2 bleibt das Paket in testing. Übernommene oder ersetzte Versionen schließt die CI; die Issue-Liste mit dem Label `testing` ist die Weboberfläche.

## Automatische Prüfungen (Gates)

Ein Commit auf `main` und jeder Paketbau müssen bestehen:

| Prüfung | Wo | Wirkung |
|---|---|---|
| C-Code mit `-Werror` | `ci.yml` | Warnungen brechen den Bau ab |
| Testsuite (Paketformat, Signaturen, Solver, Transaktionen, Rollback, Index-Schutz, Konverter, Härtung, Sicherheitsschicht, Prüfungsmodus, Kanäle, Bootstrap-Logik) | `ci.yml` | rund 75 Tests |
| dieselben Tests mit **AddressSanitizer und UBSan** | `ci.yml` | Speicherfehler und undefiniertes Verhalten fallen auf |
| `shellcheck` für alle Systemskripte, `ruff` für Python | `ci.yml` | |
| Härtungs- und Rechteprüfung jedes gebauten Pakets | `syp build` | PIE, RELRO, NX, TEXTREL, RPATH, world-writable → Abbruch |
| Prüfsumme jeder Quelle | `syp build` | keine Quelle ohne sha256 |
| Abhängigkeitsgraph ohne Zyklen | `recipes.yml` | `syp buildall -n` |
| Boot-Test in QEMU | `ci.yml`, `bootstrap.yml` | Live-System muss `multi-user.target` erreichen; Quellcode-ISO: kein Absturz, keine fehlgeschlagene Unit, sonst wird nichts veröffentlicht |
| Installationstest | `bootstrap.yml` | `symfonos-install` auf eine virtuelle Platte (GPT, UEFI und BIOS), das installierte System startet bis zur Anmeldung |
| VM-Test wie auf echter Hardware | `vm-tests.yml` (nach jedem Bootstrap) | veröffentlichtes ISO über die Firmware starten (SeaBIOS bzw. UEFI/OVMF, GRUB vom Medium – nicht `qemu -kernel`), im laufenden Live-System installieren, ohne ISO von der Platte über den installierten Bootloader neu starten: BIOS+GRUB, UEFI+GRUB, UEFI+systemd-boot mit LUKS (Passphrase wird eingetippt); Live- und installiertes System müssen `running` melden (`tools/vm-test.py`) |
| Desktop-Test (grafisch) | `bootstrap.yml` | Desktop-ISO mit virtio-GPU: `graphical.target`, KWin und Plasma-Sitzung, kein Absturz, Bildschirmfotos |
| Reproduzierbarkeit (Stichprobe, Gate) | `bootstrap.yml` | je Lauf 12 kleine Pakete zweimal gebaut und Inhalt (sha256, Rechte, Verweise) verglichen: zwei Bauten verschieden = nicht reproduzierbar, gleich aber anders als veröffentlicht = veröffentlichtes Paket veraltet; nicht reproduzierbar macht den Lauf rot, veraltet ist eine Warnung; Unterschiede in `ci-diag` (`tools/repro-check.sh`) |
| Rezept-Generator synchron | `ci.yml` | erzeugte Desktop-Rezepte entsprechen `tools/desktop-recipes.py` |

## Robustheit der Werkzeuge

- **Transaktionen** prüfen Dateikonflikte vor jeder Änderung, legen einen Btrfs-Snapshot an und schreiben die Historie. `syp rollback` nimmt eine Transaktion zurück, `syp rollback --system` setzt das ganze System auf einen Snapshot.
- **Konfigurationsdateien** werden nie still überschrieben (`.sypnew`/`.sypsave`).
- **Downloads** wiederholen vorübergehende Fehler (Zeitüberschreitung, 5xx, 429) bis zu dreimal und weichen bei GNU, kernel.org, CPAN und IANA auf Mirrors aus. Weil jede Quelle per sha256 geprüft wird, kann ein Spiegel nichts unterschieben.
- **Lookaside-Cache** wie bei Fedora: Die CI legt jede Quelle unter ihrer sha256 im GitHub-Release `sources` ab (`tools/lookaside-sync.sh`). syp fragt dort zuerst (`SYP_LOOKASIDE`, im Bootstrap voreingestellt), dann den Projektserver. Ein Bau hängt so nicht davon ab, dass ein fremder Server erreichbar ist oder eine alte Version noch anbietet.
- **Bau ohne Netz**: Jeder Bauschritt läuft in einem eigenen Netz-Namensraum (nur `lo`). Was ein Paket braucht, muss als Quelle mit Prüfsumme im Rezept stehen; im Bootstrap ist das Pflicht (`SYP_BUILD_NET=strict`).
- **Bootstrap**: Ein Schritt gilt erst als erledigt, wenn er vollständig gelungen ist. Fehler brechen sofort ab, ein abgebrochener CI-Lauf setzt beim nächsten Mal an derselben Stelle fort (`tests/test_bootstrap.py`).
- **Reproduzierbarkeit**: `SOURCE_DATE_EPOCH` ist bei jedem Bau gesetzt, Bauten in der Chroot sind vom Host getrennt.

### Neubau bei geänderter Bauumgebung

Pakete tragen die Bau-Revision (`origin.build_rev`, `SYP_BUILD_REV`); `buildall` baut ältere neu, das alte Paket bleibt, bis der Neubau gelingt. Den ersten Anlass gab die Reproduzierbarkeits-Stichprobe: rund 450 der 605 Pakete stammten aus einer älteren Bauumgebung. Der vollständige Neubau (Revisionen 2 und 3, November 2026) deckte Abhängigkeiten von der Bauumgebung auf und beseitigte sie: python ohne pip und leere Python-Pakete (pip hielt die schon installierte Version für erledigt; jetzt `--ignore-installed`, ein Neubau ohne Dateien gilt als gescheitert) und Programme, die gegen nicht genannte Bibliotheken gelinkt waren (jetzt Abhängigkeiten aus `DT_NEEDED`, auch über `so:`-Provides). Das Reproduzierbarkeits-Gate fand danach in Qt-Paketen den zufälligen Bauordner in Objektdateien und Archiven, die Kernel-Version des Bau-Rechners (`CMAKE_SYSTEM_VERSION` in den Modul-JSONs) und SBOM-Dateien mit zufälligen Kennungen; Revision 4 setzt `-ffile-prefix-map`, eine feste `CMAKE_SYSTEM_VERSION`, deterministische `ar`-Archive (binutils) und baut Qt ohne SBOM.

## Update-Regeln für stable

Wie Fedoras *Updates Policy* soll stable vorhersehbar bleiben:

- **Kernkomponenten** (glibc, gcc, binutils, systemd, Kernel-Hauptversion, Python-Hauptversion, OpenSSL-Hauptversion, Mesa, KDE Plasma) wechseln die Hauptversion nur mit einem Release. Dazwischen kommen nur Fehlerbehebungen und Sicherheitsupdates.
- **Programme** (Krita, Blender, Ardour …) dürfen in stable neue Versionen bekommen, wenn ihr Datenformat abwärtskompatibel bleibt.
- **Jedes Update** erhöht `pkgrel` oder `version`. Ein Downgrade ist ausgeschlossen: Der Solver nimmt nie eine ältere Version, und der Index-Rollback-Schutz verhindert alte Stände.
- **Sicherheitsupdates** tragen die CVE-Kennungen im Feld `security` und erscheinen in `syp audit`.

## Releases

Ein Release (z. B. 2026.10) ist ein eingefrorener Stand von stable mit Installationsmedium.

**Ablauf**: Beta-Freeze (nur noch Fehlerbehebungen) → Final-Freeze (nur noch Blocker-Fixes) → Freigabe.

**Freigabekriterien**, bei Fedora *Release Criteria* genannt. Jedes Kriterium ist ein Blocker:

1. Installation auf UEFI und BIOS, jeweils mit und ohne Verschlüsselung; Start in das installierte System
2. Secure Boot mit eigenen Schlüsseln: `init` → `enroll` → `sign` → Neustart mit Secure Boot an
3. TPM-Entsperrung und Wiederherstellungsschlüssel funktionieren
4. Update vom vorherigen Release ohne Eingriff; Rollback per `syp rollback --system` startet wieder
5. `symfonos-security status` ohne ✗ auf einer frischen Installation
6. Prüfungsmodus: Browser-Kiosk mit Allowlist, Ende stellt den Normalzustand wieder her; die SEB-Optionen B (seb-linux) und C (Windows-VM) starten
7. Audio mit niedriger Latenz (PipeWire/JACK) und Grafiktablett funktionieren (ab der Desktop-Stufe)
8. Keine bekannten offenen Sicherheitslücken mit hohem Schweregrad in stable (`syp audit` auf dem Release-Stand)

## Offene Punkte

- Karma: Stimmen kommen nur über GitHub-Konten; eine eigene Weboberfläche mit Anmeldung (wie Bodhi mit FAS) gibt es nicht.
- Openqa-artige Tests mit Bildvergleich. Seit November 2026 tippt der Desktop-Test über QEMU (KRunner, Konsole, ein Befehl, der im Journal ankommen muss) und hält dabei Bildschirmfotos fest; nach drei bestandenen Läufen in Folge lässt ein Fehlschlag den Desktop-Test scheitern. Seit November 2026 vergleicht `tools/needle.py` die Bildschirmfotos mit Referenzausschnitten aus `distro/needles` (Leiste und Dock, KRunner-Suchfeld, Konsolenfenster mit Menü; normierte Kreuzkorrelation, Verschiebung bis 30–60 Pixel erlaubt), nach drei bestandenen Läufen in Folge seit dem 22.11.2026 als Gate (`NEEDLE_STRICT=1`): passt ein Ausschnitt nicht, scheitert der Desktop-Test. Seit dem 24.11.2026 prüft er auch die Fenster der Programme aus dem Programmstart-Test (Dolphin, Okular, Software, Design-Center, Paket-Konverter, Assistent: Fenstertitel und ein typisches Element); seit drei bestandenen Läufen (24.11.2026) ebenfalls als Gate; neue Needles tragen `strict: false` und melden eine Abweichung nur als Warnung, bis sie sich in drei Läufen bewährt haben. Neue Needles entstehen mit `tools/needle.py make` aus einem Bildschirmfoto eines guten Laufs (`--soft` für eine neue).
- Reproduzierbarkeit: ein Gate über eine rotierende Stichprobe (12 Pakete je Lauf, nach der Veröffentlichung); große Pakete (Quellen über 30 MB) prüft nur der manuelle Start mit `repro_only`. Der Vergleich mit dem veröffentlichten Paket ist zugleich einer zwischen zwei Rechnern (anderer CI-Runner, andere Kernel-Version); so wurden die Befunde behoben, die zu Revision 4 führten (Qt-Module zuletzt 4 von 4 gleich). Für einen unabhängigen Nachbau außerhalb der eigenen CI gibt es seit November 2026 `tools/nachbau.sh`: Es prüft die Signatur des stable-Index, setzt aus genau diesem Repo eine Bauumgebung auf (alle Pakete, wie in der CI), baut die gewählten Pakete (oder eine tägliche Stichprobe) mit `syp build --chroot` und vergleicht sie inhaltlich mit den veröffentlichten (`tools/pkg-compare.py`); das Ergebnis ist ein Abschnitt zum Einfügen in ein Issue. Schon der erste Versuch fand, dass das stable-Release noch Dateien älterer Bauten mit gleicher Version auslieferte (600 von 605 passten nicht zum signierten Index; behoben in `publish-repo.sh`). Der Workflow »Nachbau« führt es wöchentlich auf einem frischen GitHub-Runner aus (nicht in der Bauumgebung des Bootstraps): Bauumgebung allein aus dem signierten testing-Repo (`NACHBAU_ENV_REPO`; gegen diesen Stand baut die CI), Stichprobe von 16 Paketen, Ergebnis in der Zusammenfassung des Laufs; eine Abweichung macht ihn rot. Der Vergleich zeigt bei Binärdateien die erste Abweichung, bei `.a`-Archiven das abweichende Glied; so fiel auf, dass bison, glibc und libsepol am 18.11.2026 noch mit dem nicht-deterministischen `ar` aus binutils 2.45-1 gebaut wurden (Bauzeit in der Symboltabelle) – neu gebaut als Revision 2; seither macht `syp build` die Kopfzeilen jedes `.a`-Archivs selbst deterministisch. Ebenso baut `syp build` im chroot in einem festen Ordner je Paket (`/var/tmp/syp-build/NAME` statt eines zufälligen Namens): bash hielt den Bauordner in `bashbug` und `Makefile.inc` fest und war deshalb nicht reproduzierbar. Eine Durchsicht aller 605 stable-Pakete fand den zufälligen Bauordner in 45 weiteren (etwa `curl-config`, Python-Metadaten, Debug-Pfade in glibc, grub, boost); sie wurden am 24.11.2026 mit einer Revision mehr neu gebaut. Weil der Ordnername auch verdeckt wirkt (ELF-Abschnitte von editorconfig, efivar, elisa um 8 Byte verschoben – so lang ist der Zufallsanhang), baut Bau-Revision 5 danach alle Pakete einmal neu. Seit dieser Revision (25.11.2026) kommt der Nachbau auf einem fremden Runner bitgleich heraus (8 von 8, darunter die zuvor abweichenden editorconfig, efivar und elisa). Offen: Nachbauten von Dritten, die regelmäßig laufen und ihre Ergebnisse veröffentlichen.
- Upstream-Signaturen (GPG): `tools/source-sigs.py` prüft sie für alle Pakete in `distro/source-keys.toml`, bei jeder Rezeptänderung (Pull Request und Push auf main) und wöchentlich vollständig (Workflow »Quellsignaturen«). Die Schlüssel werden einmal per `discover` gesucht und nur eingetragen, wenn sie das Archiv mit der schon festgeschriebenen sha256 bestätigen; danach muss jede neue Version von einem dieser Schlüssel signiert sein. Stand: 374 von 605 Paketen mit Upstream-Signatur; abgelaufene Schlüssel werden vollständig mit Unterschlüsseln gespeichert, alte Archive bleiben damit prüfbar. Projekte, die nur eine Prüfsummenliste signieren (SHA256SUMS mit .asc/.sig/.gpg), werden ebenfalls erkannt: geprüft wird die Signatur der Liste und dass sie das Archiv mit seiner sha256 nennt. Viele Projekte auf GitHub und GitLab signieren keine Archive, wohl aber ihre Release-Tags (etwa systemd, mpv, libvpx, efivar): Ist die Quelle ein Tag-Archiv der Forge, prüft `source-sigs.py` die Signatur des Tags und vergleicht das Archiv Datei für Datei (Git-Hash, ausführbar, Verweis) mit dem signierten Baum – nichts darf hinzukommen oder abweichen (seit November 2026, 15 Pakete). Offen: die übrigen Projekte ohne Signaturdatei und ohne signierten Tag bleiben bei der festgehaltenen Prüfsumme; ein neuer Schlüssel nach einem Wechsel beim Projekt muss von Hand geprüft werden.
- `/usr/bin` und `/usr/sbin` zusammenlegen (wie Fedora 42, Arch 2025): bewusst zurückgestellt. Auf installierten Systemen müsste ein gefülltes Verzeichnis durch einen Verweis ersetzt werden, und zwar erst, wenn alle Pakete ihre Programme verschoben haben; dazu kommen SELinux-Dateikontexte und feste Pfade in Diensten. Der Gewinn ist vor allem Angleichung, das Risiko liegt im Update-Pfad. Bis dahin legen einzelne Pakete Verweise in `/usr/sbin` an, wo Programme fest dort erwartet werden (z. B. `/sbin/modprobe` durch den Kernel).
