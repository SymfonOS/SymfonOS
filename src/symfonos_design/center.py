"""symfonOS Design-Center: Modus, Akzentfarbe, Layout, Schrift, Animationen, Widgets, Profile."""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from . import plasma
from .plasma import ACCENTS, LAYOUTS, PRESETS, WIDGETS, Profile


def gui(run: bool = True):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QFontDatabase
    from PySide6.QtWidgets import (QApplication, QButtonGroup, QCheckBox, QColorDialog, QComboBox,
                                   QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel,
                                   QListWidget, QMainWindow, QMessageBox, QPushButton, QRadioButton,
                                   QSlider, QSpinBox, QStackedWidget, QVBoxLayout, QWidget)

    import symfonos_ui

    class Center(QMainWindow):
        def __init__(self):
            super().__init__()
            self.p = Profile.load()
            self.setWindowTitle("Design-Center – symfonOS")
            self.resize(1040, 680)
            root = QWidget(objectName="root")
            self.setCentralWidget(root)
            outer = QHBoxLayout(root)
            outer.setContentsMargins(18, 18, 18, 18)
            outer.setSpacing(16)
            self.nav = QListWidget()
            self.nav.setFixedWidth(210)
            self.nav.setAccessibleName("Bereiche")
            self.pages = QStackedWidget()
            for title, page in (("Erscheinungsbild", self.page_look()), ("Layout", self.page_layout()),
                                ("Schrift & Größe", self.page_fonts()), ("Bewegung", self.page_motion()),
                                ("Desktop & Widgets", self.page_widgets()), ("Profile", self.page_profiles())):
                self.nav.addItem(title)
                self.pages.addWidget(page)
            self.nav.currentRowChanged.connect(self.pages.setCurrentIndex)
            self.nav.setCurrentRow(0)
            right = QVBoxLayout()
            right.addWidget(self.pages, 1)
            bar = QHBoxLayout()
            self.status = QLabel(objectName="muted")
            reset = QPushButton("Zurücksetzen")
            reset.clicked.connect(lambda: self.load_profile(Profile()))
            apply_btn = QPushButton("Anwenden", objectName="primary")
            apply_btn.clicked.connect(self.apply)
            bar.addWidget(self.status, 1)
            bar.addWidget(reset)
            bar.addWidget(apply_btn)
            right.addLayout(bar)
            outer.addWidget(self.nav)
            outer.addLayout(right, 1)
            self.load_profile(self.p)

        # -- Seiten ------------------------------------------------------------------
        def card(self, title: str, hint: str = "") -> tuple[QFrame, QVBoxLayout]:
            f = QFrame(objectName="card")
            lay = QVBoxLayout(f)
            lay.setContentsMargins(20, 18, 20, 18)
            t = QLabel(f"<b>{title}</b>")
            lay.addWidget(t)
            if hint:
                h = QLabel(hint, objectName="muted")
                h.setWordWrap(True)
                lay.addWidget(h)
            return f, lay

        def page(self, *cards) -> QWidget:
            w = QWidget()
            lay = QVBoxLayout(w)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(14)
            for c in cards:
                lay.addWidget(c)
            lay.addStretch()
            return w

        def page_look(self) -> QWidget:
            mode, ml = self.card("Modus", "Automatisch wechselt zu festen Uhrzeiten zwischen Hell und Dunkel.")
            row = QHBoxLayout()
            self.mode_group = QButtonGroup(self)
            for key, label in (("light", "☀  Hell"), ("dark", "☾  Dunkel"), ("auto", "◐  Automatisch")):
                b = QRadioButton(label)
                b.setProperty("key", key)
                self.mode_group.addButton(b)
                row.addWidget(b)
            row.addStretch()
            ml.addLayout(row)
            acc, al = self.card("Akzentfarbe", "Wird für Schaltflächen, Auswahl und Fokus verwendet – "
                                               "auch in GTK-Apps (nächstliegender Farbton).")
            grid = QHBoxLayout()
            self.swatches = []
            for name, hex_ in ACCENTS.items():
                b = QPushButton()
                b.setFixedSize(40, 40)
                b.setToolTip(name)
                b.setAccessibleName(f"Akzent {name}")
                b.setStyleSheet(f"background:{hex_}; border-radius:20px; border:3px solid transparent;")
                b.clicked.connect(lambda _=False, h=hex_: self.set_accent(h))
                self.swatches.append((b, hex_))
                grid.addWidget(b)
            custom = QPushButton("Eigene …")
            custom.clicked.connect(self.pick_color)
            grid.addWidget(custom)
            grid.addStretch()
            al.addLayout(grid)
            neutral, nl = self.card("Farbneutrale Oberfläche",
                                    "Für Farbkorrektur und Fotoentwicklung: neutrales Grau ohne Tönung, "
                                    "keine Transparenz/Unschärfe, damit die Wahrnehmung nicht verfälscht wird.")
            self.neutral = QCheckBox("Studio-Neutral aktivieren")
            nl.addWidget(self.neutral)
            return self.page(mode, acc, neutral)

        def page_layout(self) -> QWidget:
            c, lay = self.card("Leisten-Layout", "Wird beim Anwenden neu aufgebaut. Eigene Anpassungen "
                                                "kannst du danach wie gewohnt per Rechtsklick vornehmen.")
            self.layout_group = QButtonGroup(self)
            for key, label in LAYOUTS.items():
                b = QRadioButton(label)
                b.setProperty("key", key)
                self.layout_group.addButton(b)
                lay.addWidget(b)
            self.keep_layout = QCheckBox("Aktuelles Layout beim Anwenden nicht verändern")
            lay.addWidget(self.keep_layout)
            return self.page(c)

        def page_fonts(self) -> QWidget:
            c, lay = self.card("Schriften", "Standard: Inter für die Oberfläche, JetBrains Mono für Code "
                                            "und Terminal. Noto deckt alle Schriftsysteme und Emoji ab.")
            g = QGridLayout()
            fams = sorted(set(QFontDatabase.families()) | {"Inter", "JetBrains Mono", "Noto Sans"})
            self.font = QComboBox()
            self.font.addItems(fams)
            self.mono = QComboBox()
            self.mono.addItems(fams)
            self.size = QSpinBox(minimum=8, maximum=20)
            self.scale = QComboBox()
            for s in ("100 %", "125 %", "150 %", "175 %", "200 %"):
                self.scale.addItem(s, float(s.split()[0]) / 100)
            for i, (lab, w) in enumerate((("Oberfläche", self.font), ("Festbreite", self.mono),
                                          ("Größe (pt)", self.size), ("Skalierung", self.scale))):
                g.addWidget(QLabel(lab), i, 0)
                g.addWidget(w, i, 1)
            lay.addLayout(g)
            return self.page(c)

        def page_motion(self) -> QWidget:
            c, lay = self.card("Animationen", "„Aus“ reduziert Bewegung vollständig (Barrierefreiheit, "
                                              "ältere Hardware).")
            self.anim = QSlider(Qt.Horizontal, minimum=0, maximum=4)
            self.anim.setAccessibleName("Animationsgeschwindigkeit")
            self.anim_label = QLabel()
            self.anim.valueChanged.connect(lambda v: self.anim_label.setText(
                ["Aus", "Sehr schnell", "Schnell", "Normal", "Langsam"][v]))
            lay.addWidget(self.anim)
            lay.addWidget(self.anim_label)
            return self.page(c)

        def page_widgets(self) -> QWidget:
            wp, wl = self.card("Hintergrundbild")
            row = QHBoxLayout()
            self.wall = QLabel()
            self.wall.setWordWrap(True)
            pick = QPushButton("Bild wählen …")
            pick.clicked.connect(self.pick_wall)
            row.addWidget(self.wall, 1)
            row.addWidget(pick)
            wl.addLayout(row)
            wd, dl = self.card("Desktop-Widgets", "Werden auf dem ersten Bildschirm platziert.")
            self.widget_boxes = {}
            for wid, label in WIDGETS.items():
                cb = QCheckBox(label)
                self.widget_boxes[wid] = cb
                dl.addWidget(cb)
            return self.page(wp, wd)

        def page_profiles(self) -> QWidget:
            c, lay = self.card("Vorlagen & Profile", "Profile lassen sich exportieren und auf anderen "
                                                     "symfonOS-Rechnern importieren.")
            for name in PRESETS:
                b = QPushButton(name)
                b.clicked.connect(lambda _=False, n=name: self.load_profile(Profile(**asdict(PRESETS[n]))))
                lay.addWidget(b)
            row = QHBoxLayout()
            exp, imp = QPushButton("Exportieren …"), QPushButton("Importieren …")
            exp.clicked.connect(self.export)
            imp.clicked.connect(self.import_)
            row.addWidget(exp)
            row.addWidget(imp)
            lay.addLayout(row)
            return self.page(c)

        # -- Logik -------------------------------------------------------------------
        def set_accent(self, hex_: str):
            self.p.accent = hex_
            for b, h in self.swatches:
                border = "white" if h.lower() == hex_.lower() else "transparent"
                b.setStyleSheet(f"background:{h}; border-radius:20px; border:3px solid {border};")
            QApplication.instance().setStyleSheet(
                symfonos_ui.stylesheet(self.p.mode != "light", hex_))
            self.status.setText(f"Vorschau: {hex_}")

        def pick_color(self):
            c = QColorDialog.getColor(QColor(self.p.accent), self, "Akzentfarbe")
            if c.isValid():
                self.set_accent(c.name().upper())

        def pick_wall(self):
            f, _ = QFileDialog.getOpenFileName(self, "Hintergrundbild", str(Path.home() / "Bilder"),
                                               "Bilder (*.png *.jpg *.jpeg *.webp *.avif)")
            if f:
                self.p.wallpaper = f
                self.wall.setText(f)

        def load_profile(self, p: Profile):
            self.p = p
            for b in self.mode_group.buttons():
                b.setChecked(b.property("key") == p.mode)
            for b in self.layout_group.buttons():
                b.setChecked(b.property("key") == p.layout)
            self.neutral.setChecked(p.neutral_ui)
            self.font.setCurrentText(p.font)
            self.mono.setCurrentText(p.mono_font)
            self.size.setValue(p.font_size)
            self.scale.setCurrentIndex(max(0, self.scale.findData(p.scale)))
            self.anim.setValue({0: 0, 0.25: 1, 0.5: 2, 1.0: 3, 2.0: 4}.get(p.animation, 3))
            self.anim_label.setText(["Aus", "Sehr schnell", "Schnell", "Normal", "Langsam"][self.anim.value()])
            self.wall.setText(p.wallpaper)
            for wid, cb in self.widget_boxes.items():
                cb.setChecked(wid in p.widgets)
            self.set_accent(p.accent)
            self.status.setText(f"Profil: {p.name}")

        def collect(self) -> Profile:
            p = self.p
            p.mode = self.mode_group.checkedButton().property("key")
            p.layout = self.layout_group.checkedButton().property("key")
            p.neutral_ui = self.neutral.isChecked()
            p.font, p.mono_font = self.font.currentText(), self.mono.currentText()
            p.font_size, p.scale = self.size.value(), self.scale.currentData()
            p.animation = [0, 0.25, 0.5, 1.0, 2.0][self.anim.value()]
            p.widgets = [w for w, cb in self.widget_boxes.items() if cb.isChecked()]
            return p

        def apply(self):
            errors = plasma.apply(self.collect(), include_layout=not self.keep_layout.isChecked())
            if errors:
                QMessageBox.warning(self, "Teilweise angewendet", "\n".join(errors))
            self.status.setText("Angewendet ✓")

        def export(self):
            f, _ = QFileDialog.getSaveFileName(self, "Profil exportieren", "symfonos-design.json")
            if f:
                Path(f).write_text(json.dumps(asdict(self.collect()), indent=2, ensure_ascii=False))

        def import_(self):
            f, _ = QFileDialog.getOpenFileName(self, "Profil importieren", "", "Profil (*.json)")
            if f:
                self.load_profile(Profile.load(Path(f)))

    app = QApplication(sys.argv)
    app.setApplicationName("symfonos-design")
    app.setDesktopFileName("org.symfonos.Design")
    symfonos_ui.apply(app)
    w = Center()
    w.show()
    return app.exec() if run else (app, w)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="symfonos-design")
    ap.add_argument("--apply", action="store_true", help="Gespeichertes Profil anwenden (ohne GUI)")
    ap.add_argument("--apply-auto", action="store_true",
                    help="Nur Hell/Dunkel nach Uhrzeit nachziehen (für systemd-Timer)")
    ap.add_argument("--preset", choices=list(PRESETS))
    ap.add_argument("--print", action="store_true", help="Befehle nur anzeigen")
    a = ap.parse_args(argv)
    if not (a.apply or a.apply_auto or a.preset or a.print):
        return gui()
    p = Profile(**asdict(PRESETS[a.preset])) if a.preset else Profile.load()
    if a.print:
        for c in plasma.commands(p):
            print(" ".join(c if len(" ".join(c)) < 200 else c[:-1] + ["<script>"]))
        return 0
    if a.apply_auto and p.mode != "auto":
        return 0
    errs = plasma.apply(p, include_layout=not a.apply_auto)
    for e in errs:
        print(e, file=sys.stderr)
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
