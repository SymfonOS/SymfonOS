# Der lokale Assistent

Der Assistent (`symfonos-assist`, App „Assistent“) läuft vollständig lokal mit einem kleinen Open-Source-Modell (im symfonOS-Desktop über llama.cpp, in der Arch-Variante über Ollama). Es werden keine Daten ins Internet gesendet.

## Einrichten
Das Modell ist nicht im ISO enthalten (rund 2,5 GB). Einmalig laden und den Dienst starten:
```
sudo symfonos-assist-model
```
Das Skript wählt nach Arbeitsspeicher das Standardmodell oder das kleine Modell, lädt es nach `/var/lib/symfonos-assist/models/` und startet den Dienst `symfonos-assist-llm` (bzw. `ollama`). Ohne Modell funktionieren Handbuchsuche und Systemprüfung trotzdem.

- Fragen stellen: einfach tippen, z. B. „Wie mache ich ein Update rückgängig?“
- **System prüfen** bzw. `/diagnose`: liest Fehler aus dem Journal, fehlgeschlagene Dienste und `syp doctor` und schlägt Lösungen vor
- `/docs <frage>`: nur im Handbuch suchen (funktioniert auch ohne Modell)
- Befehle schlägt der Assistent nur vor. Jeder Befehl muss einzeln bestätigt werden; gefährliche Befehle (z. B. Löschen, Formatieren) nur durch Eingabe von AUSFÜHREN.

## Modell wechseln
`/etc/symfonos/assist.toml` bzw. `~/.config/symfonos/assist.toml`:
```
model = "symfonos-assist"   # Standard: Qwen3 4B Instruct (Q4_K_M)
```
Für schwache Hardware (4 GB RAM): `sudo symfonos-assist-model small` (Qwen3 1.7B); eine eigene GGUF-Datei: `sudo symfonos-assist-model /pfad/modell.gguf`. Der Dienst muss laufen: `systemctl status symfonos-assist-llm` (Arch-Variante: `systemctl status ollama`).
