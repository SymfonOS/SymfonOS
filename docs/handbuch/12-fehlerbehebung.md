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
Im GRUB-Menü „symfonOS Snapshots“ einen Snapshot vor dem Update wählen, dann `sudo snapper rollback`. Alternativ den Fallback-Kernel „linux-lts“ wählen.

## Datenträger voll
`df -h /`, dann `sudo syp clean --keep 1`, alte Snapshots mit `snapper list` / `sudo snapper delete <nr>` löschen, `du -sh ~/.cache`.

## Kein Netzwerk
`nmcli device` zeigt die Schnittstellen. WLAN-Firmware fehlt? `journalctl -k | grep -i firmware`. Prüfungsmodus aktiv? `symfonos-exam status`.
