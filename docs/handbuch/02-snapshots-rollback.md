# Snapshots und Rollback

Das Systemlaufwerk nutzt Btrfs mit den Subvolumes `@` (System, eingehängt als `/`), `@home`, `@snapshots` (`/.snapshots`), `@var_log` und `@cache`. Vor jeder syp-Transaktion legt syp selbst einen schreibgeschützten Snapshot des Systems an: `/.snapshots/syp-<Zeit>-pre`. Die letzten zehn bleiben erhalten, ältere löscht syp automatisch.

## Eine Paketänderung zurücknehmen
1. `syp history` zeigt alle Transaktionen mit Nummer.
2. `sudo syp rollback 42` stellt den Paketzustand **vor** Transaktion 42 wieder her (aus dem Paket-Cache).
3. Ohne Nummer wird die letzte erfolgreiche Transaktion zurückgenommen.

Der Paket-Cache behält standardmäßig drei Versionen je Paket (`sudo syp clean --keep 3`).

## Ganzes System zurücksetzen
`sudo syp rollback --system` setzt das System auf den Snapshot vor der letzten Transaktion zurück, `sudo syp rollback --system 42` auf den vor Transaktion 42. Danach neu starten.

Was dabei passiert:
- Das bisherige `@` wird in `@.vor-rollback-<Zeit>` umbenannt und bleibt vollständig erhalten.
- Eine beschreibbare Kopie des Snapshots wird zum neuen `@`. Das laufende System ändert sich bis zum Neustart nicht.
- `/home`, `/var/log` und `/var/cache` sind eigene Subvolumes und bleiben unberührt.
- Mit GRUB liegen Kernel und initramfs in `/boot` innerhalb von `@` und werden mit zurückgesetzt. Mit systemd-boot liegen sie auf der EFI-Partition und bleiben auf dem neuen Stand.

Läuft der zurückgesetzte Stand, kann der alte gelöscht werden: Oberste Ebene einhängen (`sudo mount -o subvolid=5 /dev/<wurzel> /mnt`), dann `sudo btrfs subvolume delete /mnt/@.vor-rollback-<Zeit>` und `sudo umount /mnt`.

Startet das System nach einem Update nicht mehr, vom Installationsmedium starten, die oberste Ebene wie oben einhängen und `@` von Hand tauschen: `mv /mnt/@ /mnt/@.kaputt` und `btrfs subvolume snapshot /mnt/@snapshots/syp-<Zeit>-pre /mnt/@`.

## Speicherplatz
Ein Snapshot hält ersetzte Dateien fest. syp prüft deshalb vor jeder Transaktion den freien Platz und bricht ab, bevor etwas verändert wird. Die Meldung „Zu wenig Speicherplatz“ nennt Bedarf und freien Platz. Dann hilft:
- `sudo syp clean --keep 1` leert den Paket-Cache bis auf eine Version je Paket.
- Alte Snapshots löschen: `sudo btrfs subvolume delete /.snapshots/syp-<Zeit>-pre`.
- Alte `@.vor-rollback-*` löschen (siehe oben).

## Automatische Aktualisierung
Der Timer `syp-sync.timer` gleicht die Paketquellen regelmäßig ab; `syp upgrade` zeigt dann verfügbare Updates. Die Paketquelle ist das GitHub-Release `repo-x86_64` (Kanal stable) des symfonOS-Projekts: Pakete kommen dorthin erst, wenn sie 7 Tage im Testkanal lagen, Sicherheitsupdates sofort. Index und Pakete sind signiert, der Schlüssel kommt mit dem Paket `symfonos-keyring`.
