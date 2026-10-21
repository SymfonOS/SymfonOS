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
| `testing` | jeder erfolgreiche Bau | Entwickler, Tester (`[[repo]] name = "testing"` in `/etc/syp/syp.toml`) |
| `stable` | aus testing übernommen mit `tools/promote.py` | Standard für alle Installationen |

`tools/promote.py` (entspricht Bodhi bei Fedora) übernimmt ein Paket erst, wenn
1. es mindestens **7 Tage** in testing liegt; Pakete mit `security`-Feld kommen über die Schnellspur sofort,
2. niemand es gesperrt hat (`testing/hold`, z. B. bei einem offenen Fehlerbericht),
3. **alle Abhängigkeiten in stable erfüllbar** bleiben. Neue Bibliotheksversionen werden zusammen mit ihren Nutzern übernommen, nie einzeln.
4. Außerdem wird der Index von testing nur mit gültiger Signatur gelesen.

Fedora sammelt zusätzlich Karma, also Bewertungen von Testern. Bei symfonOS gibt es bisher nur die Sperrliste.

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
| Boot-Test in QEMU | `ci.yml` (ISO) | Live-System muss bis zum Login starten |

## Robustheit der Werkzeuge

- **Transaktionen** prüfen Dateikonflikte vor jeder Änderung, legen einen Btrfs-Snapshot an und schreiben die Historie. `syp rollback` nimmt eine Transaktion zurück, `syp rollback --system` setzt das ganze System auf einen Snapshot.
- **Konfigurationsdateien** werden nie still überschrieben (`.sypnew`/`.sypsave`).
- **Downloads** wiederholen vorübergehende Fehler (Zeitüberschreitung, 5xx, 429) bis zu dreimal und weichen bei GNU, kernel.org, CPAN und IANA auf Mirrors aus. Weil jede Quelle per sha256 geprüft wird, kann ein Spiegel nichts unterschieben.
- **Bootstrap**: Ein Schritt gilt erst als erledigt, wenn er vollständig gelungen ist. Fehler brechen sofort ab, ein abgebrochener CI-Lauf setzt beim nächsten Mal an derselben Stelle fort (`tests/test_bootstrap.py`).
- **Reproduzierbarkeit**: `SOURCE_DATE_EPOCH` ist bei jedem Bau gesetzt, Bauten in der Chroot sind vom Host getrennt.

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

- Karma-Abstimmung und eine Weboberfläche für testing-Updates fehlen; bisher gibt es nur die Sperrliste.
- Openqa-artige Tests, die Installation und Desktop automatisch in QEMU durchspielen. Bisher gibt es nur den Boot-Test des Live-Systems.
- Bau ohne Netzzugriff (`unshare -n`) und Vergleich zweier unabhängiger Bauten (reproduzierbare Pakete).
- Upstream-Signaturen der Quell-Tarballs prüfen (GPG), statt Prüfsummen nur beim ersten Laden festzuhalten.
- Ein Lookaside-Cache für Quellen, wie bei Fedora, damit Bauten nicht von der Erreichbarkeit fremder Server abhängen.
