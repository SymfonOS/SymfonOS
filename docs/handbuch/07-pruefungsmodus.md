# Prüfungsmodus und Safe Exam Browser

## Prüfungsmodus
`sudo symfonos-exam start --url https://moodle.schule.de --allow moodle.schule.de` startet eine abgeschottete Sitzung:
- eigener Benutzer `exam` ohne Zugriff auf persönliche Dateien
- Kiosk-Compositor (cage) mit genau einem Vollbildfenster, kein Taskwechsel, keine Zwischenablage-Manager
- Firewall erlaubt nur die freigegebenen Domains (nftables), DNS nur über den lokalen Resolver
- Bildschirmaufnahme, Fernzugriff, Assistent, Bluetooth und USB-Datenträger werden gestoppt bzw. gesperrt
- Beenden mit `sudo symfonos-exam stop` (Rechner wird in den Normalzustand zurückgesetzt)
Status: `symfonos-exam status`.

## Safe Exam Browser (SEB)
Offiziell gibt es SEB nur für Windows, macOS und iOS. Möglichkeiten unter symfonOS:
1. **Prüfungsmodus mit Kiosk-Browser** – für Prüfungen ohne SEB-Pflicht (z. B. Moodle-Quiz ohne SEB-Schlüssel).
2. **Community-Client seb-linux** (AppImage, inoffiziell) – `symfonos-exam start --seb datei.seb`. Ob er akzeptiert wird, entscheidet die Prüfungsorganisation.
3. **Windows-VM** – `symfonos-exam vm`; funktioniert nur, wenn die SEB-Konfiguration virtuelle Maschinen erlaubt.
Wine wird nicht unterstützt (SEB verweigert den Start bzw. ist nicht lauffähig).
