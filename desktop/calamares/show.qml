import QtQuick 2.15
import calamares.slideshow 1.0

Presentation {
    id: presentation
    function nextSlide() { presentation.goToNextSlide() }
    Timer { interval: 9000; running: presentation.activatedInCalamares; repeat: true; onTriggered: nextSlide() }

    component Card: Slide {
        property string heading
        property string body
        Rectangle { anchors.fill: parent; color: "#16161D" }
        Column {
            anchors.centerIn: parent; width: parent.width * 0.8; spacing: 18
            Text { text: heading; color: "#ECECF4"; font.pixelSize: 30; font.bold: true; wrapMode: Text.WordWrap; width: parent.width }
            Text { text: body; color: "#9A9AB0"; font.pixelSize: 18; wrapMode: Text.WordWrap; width: parent.width }
        }
    }
    Card { heading: "Willkommen bei symfonOS"; body: "Krita, GIMP, Inkscape, Blender, Kdenlive, Ardour, darktable und viele mehr sind sofort einsatzbereit." }
    Card { heading: "Sicher experimentieren"; body: "Vor jeder Softwareänderung entsteht ein Btrfs-Snapshot. Mit »syp rollback« oder über das Bootmenü geht es jederzeit zurück." }
    Card { heading: "Software aus jeder Quelle"; body: "Der Paket-Konverter übernimmt .deb-, .rpm- und Arch-Pakete und meldet fehlende Abhängigkeiten." }
    Card { heading: "Dein Desktop, dein Stil"; body: "Im Design-Center wählst du Hell/Dunkel, Akzentfarbe, Layout und Widgets – oder die farbneutrale Studio-Oberfläche." }
    Card { heading: "Hilfe, die lokal bleibt"; body: "Der Assistent läuft komplett auf deinem Rechner und führt Befehle nur nach deiner Bestätigung aus." }
}
