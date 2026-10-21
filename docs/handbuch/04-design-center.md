# Design-Center

Die App **Design-Center** (`symfonos-design`) bündelt alle Einstellungen zum Aussehen:
- **Modus**: Hell, Dunkel oder Automatisch (Wechsel zu festen Uhrzeiten, per systemd-Timer)
- **Akzentfarbe**: sieben Vorgaben oder eine eigene Farbe; GTK-Apps erhalten den nächstliegenden Ton
- **Farbneutrale Oberfläche** („Studio Neutral“): graue Oberfläche ohne Tönung und ohne Unschärfe – für Farbkorrektur und Fotoentwicklung
- **Layout**: Atelier (Leiste oben, Dock unten), Klassisch (Leiste unten), Studio (Dock links, maximale Fläche)
- **Schrift & Größe**: Inter und JetBrains Mono, Skalierung 100–200 %
- **Bewegung**: Animationen von „Aus“ bis „Langsam“
- **Desktop & Widgets**: Hintergrundbild, Uhr, Notizen, Systemmonitor, Wetter …
- **Profile**: Vorlagen, Export/Import als JSON

Ohne Oberfläche: `symfonos-design --preset "Studio Neutral"` bzw. `symfonos-design --apply`. `symfonos-design --print` zeigt die ausgeführten Plasma-Befehle.
