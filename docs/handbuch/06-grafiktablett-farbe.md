# Grafiktablett und Farbmanagement

## Grafiktablett
Wacom, Huion, XP-Pen und Gaomon werden über libwacom/den Kernel erkannt. Einstellungen unter Systemeinstellungen › Grafiktablett: Bildschirmzuordnung, Druckkurve, Tastenbelegung. Für ältere Huion-/XP-Pen-Geräte ist `digimend-kernel-drivers` verfügbar (`sudo syp install digimend-kernel-drivers-dkms`; `symfonos-hwsetup` erledigt das automatisch).

## Farbmanagement
KDE Plasma 6 unterstützt ICC-Profile pro Bildschirm (Systemeinstellungen › Anzeige › Farbprofil) und HDR. Kalibrieren mit ArgyllCMS (vorinstalliert, `dispcal`/`colprof`) oder DisplayCAL als Flatpak. Krita, GIMP, darktable und Blender verwenden das Monitorprofil. Für farbkritische Arbeit im Design-Center „Studio Neutral“ aktivieren.
