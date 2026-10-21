# Datensicherung

Snapshots ersetzen keine Sicherung – sie liegen auf demselben Datenträger.
- **Déjà Dup** (App „Sicherungen“): verschlüsselte, inkrementelle Sicherung von `~` auf USB-Platte, NAS oder Cloud; tägliche Erinnerung
- **Kreativprojekte**: große Mediendateien bei Bedarf ausschließen (Einstellungen › Zu ignorierende Ordner) und separat archivieren
- **Systemkonfiguration**: `syp list --explicit > ~/paketliste.txt` sichert die Liste selbst installierter Pakete; Wiederherstellung mit `xargs -a ~/paketliste.txt sudo syp install`
- Prüfe Sicherungen regelmäßig durch eine Test-Wiederherstellung.
