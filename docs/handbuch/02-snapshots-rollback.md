# Snapshots und Rollback

Das Systemlaufwerk nutzt Btrfs mit den Subvolumes `@` (System), `@home`, `@snapshots`, `@var_log`, `@cache`. Vor und nach jeder syp-Transaktion legt snapper automatisch einen Snapshot an.

## Eine Paketänderung zurücknehmen
1. `syp history` zeigt alle Transaktionen mit Nummer.
2. `sudo syp rollback 42` stellt den Paketzustand **vor** Transaktion 42 wieder her (aus dem Paket-Cache).
3. Ohne Nummer wird die letzte erfolgreiche Transaktion zurückgenommen.

Der Paket-Cache behält standardmäßig drei Versionen je Paket (`sudo syp clean --keep 3`).

## Ganzes System zurücksetzen
- `sudo syp rollback --system 42` setzt das System auf den Snapshot vor Transaktion 42 zurück (snapper rollback). Danach neu starten. `/home` bleibt unberührt.
- Startet das System nicht mehr: Im GRUB-Menü „symfonOS Snapshots“ wählen und einen älteren Snapshot booten. Läuft er, dauerhaft übernehmen mit `sudo snapper rollback`.

## Snapshots verwalten
`snapper list` zeigt alle Snapshots. Ältere werden automatisch aufgeräumt (Zahl-Algorithmus, max. 20). Der Datenträger läuft voll? `sudo snapper delete <nr>` für alte Snapshots und `sudo syp clean --keep 1`.
