# Fehlerbehebung

## Systemcheck
`syp doctor` prüft abgebrochene Transaktionen, unerfüllte Abhängigkeiten, Schlüssel und veraltete Repos. Der Assistent („System prüfen“) wertet zusätzlich das Journal aus.

## Fehlerprotokolle
- `journalctl -b -p 3` – Fehler seit dem letzten Start
- `journalctl -b -1 -p 3` – Fehler des vorherigen Starts (nach Absturz)
- `systemctl --failed` – fehlgeschlagene Dienste; Details mit `systemctl status <dienst>`

## Update abgebrochen
`syp doctor` zeigt die betroffene Transaktion. Mit `sudo syp rollback <nr>` zurücknehmen oder `sudo syp upgrade` erneut ausführen.

## System startet nicht
Vom Installationsmedium starten, die oberste Btrfs-Ebene einhängen und `@` durch den Snapshot vor dem Update ersetzen (Schritte in Kapitel „Snapshots und Rollback“). Startet das System noch bis zur Anmeldung: `sudo syp rollback --system`, dann neu starten.

## Datenträger voll
`df -h /`, dann `sudo syp clean --keep 1`, alte Snapshots mit `ls /.snapshots` / `sudo btrfs subvolume delete /.snapshots/syp-<Zeit>-pre` löschen, `du -sh ~/.cache`.

## Kein Netzwerk
`nmcli device` zeigt die Schnittstellen. WLAN-Firmware fehlt? `journalctl -k | grep -i firmware`. Prüfungsmodus aktiv? `symfonos-exam status`.
