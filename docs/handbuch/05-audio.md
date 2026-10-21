# Audio und Musikproduktion

symfonOS nutzt PipeWire mit JACK-Kompatibilität (`pipewire-jack`), den Kernel `linux-zen` und Echtzeit-Rechte über die Gruppe `realtime`. Ardour, Carla und Plugins (LV2/VST3/CLAP) laufen direkt.

## Knacksen und Aussetzer (XRUNs)
1. Prüfen, ob dein Benutzer in der Gruppe `realtime` ist: `groups`. Falls nicht: `sudo usermod -aG realtime $USER`, neu anmelden.
2. Puffer erhöhen: `pw-metadata -n settings 0 clock.force-quantum 512` (Standard 256; zurück mit 0).
3. Abtastrate festlegen: `pw-metadata -n settings 0 clock.force-rate 48000`.
4. Last beobachten: `pw-top`.
5. Energiesparmodus: im Leistungsprofil „Leistung“ wählen (powerprofilesctl set performance).

## Audio-Interface wird nicht erkannt
`wpctl status` listet Geräte. USB-Interfaces nach dem Anschließen in `journalctl -k -n 50` prüfen.
