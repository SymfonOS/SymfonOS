# Der lokale Assistent

Der Assistent (`symfonos-assist`, App „Assistent“) läuft vollständig lokal über Ollama mit einem kleinen Open-Source-Modell. Es werden keine Daten ins Internet gesendet.

- Fragen stellen: einfach tippen, z. B. „Wie mache ich ein Update rückgängig?“
- **System prüfen** bzw. `/diagnose`: liest Fehler aus dem Journal, fehlgeschlagene Dienste und `syp doctor` und schlägt Lösungen vor
- `/docs <frage>`: nur im Handbuch suchen (funktioniert auch ohne Modell)
- Befehle schlägt der Assistent nur vor. Jeder Befehl muss einzeln bestätigt werden; gefährliche Befehle (z. B. Löschen, Formatieren) nur durch Eingabe von AUSFÜHREN.

## Modell wechseln
`/etc/symfonos/assist.toml` bzw. `~/.config/symfonos/assist.toml`:
```
model = "symfonos-assist"   # Standard: Qwen3 4B Instruct (Q4_K_M)
```
Für schwache Hardware (4 GB RAM): `sudo symfonos-assist-model small` (Qwen3 1.7B). Der Dienst `ollama` muss laufen: `systemctl status ollama`.
