# Paketverwaltung mit syp

symfonOS verwaltet Software ausschließlich mit `syp`. Pakete haben die Endung `.syp`, sind mit Ed25519 signiert und werden aus signierten Repositories geladen. Grafisch geht alles über die App **Software**.

## Pakete suchen und installieren
- `syp search krita` – Pakete suchen
- `syp info krita` – Details, Lizenz, Abhängigkeiten, Herkunft
- `sudo syp install krita` – installieren (Abhängigkeiten werden automatisch aufgelöst)
- `sudo syp install ./datei.syp` – lokale Paketdatei installieren
- `sudo syp install "python>=3.13"` – mit Versionsbedingung

## System aktualisieren
`sudo syp upgrade` synchronisiert die Repos und aktualisiert alle Pakete. Vorher wird automatisch ein Btrfs-Snapshot angelegt. Ohne Netzabgleich: `sudo syp upgrade --no-sync`. Nur den Plan anzeigen: `syp -n upgrade`.

## Pakete entfernen
- `sudo syp remove gimp` – entfernen; schlägt fehl, wenn andere Pakete es brauchen
- `sudo syp remove --cascade gimp` – inklusive abhängiger Pakete
- `sudo syp autoremove` – nicht mehr benötigte Abhängigkeiten entfernen

## Konfigurationsdateien
Hast du eine Konfigurationsdatei unter /etc geändert, überschreibt ein Update sie nicht. Die neue Version landet als `datei.sypnew` daneben. Beim Entfernen wird eine geänderte Datei als `datei.sypsave` aufbewahrt. `syp doctor` listet offene `.sypnew`-Dateien.

## Dateien und Besitzer
- `syp files krita` – alle Dateien eines Pakets
- `syp owns /usr/bin/krita` – zu welchem Paket gehört eine Datei
- `syp verify` – installierte Dateien gegen Prüfsummen prüfen (erkennt Beschädigung/Manipulation)

## Repositories
Die Repos sind in `/etc/syp/syp.toml` eingetragen, vertrauenswürdige Schlüssel liegen in `/etc/syp/keys/*.pub`. `sudo syp sync` lädt die signierten Indizes neu. Ein Index mit ungültiger Signatur wird abgelehnt.

## Fehlermeldungen
- „Nichts stellt »X« bereit“: Abhängigkeit fehlt im Repo. Erst `sudo syp sync`, dann prüfen, ob das Repo aktiviert ist.
- „Dateikonflikte“: eine Datei gehört schon einem anderen Paket oder liegt unverwaltet im System. Unverwaltete Dateien gezielt mit `--overwrite '/pfad*'` überschreiben.
- „Eine andere syp-Instanz läuft bereits“: warten, bis Software-Center oder Update fertig ist.
- „Signatur ungültig“ / „Unbekannter Signaturschlüssel“: `sudo syp install symfonos-keyring`, dann `sudo syp sync`.
