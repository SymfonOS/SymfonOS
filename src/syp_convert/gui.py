"""syp-convert GUI: Pakete per Drag & Drop umwandeln, Mapping-Bericht ansehen, installieren."""
from __future__ import annotations

import shlex
import sys
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, Qt, QThread, Signal
from PySide6.QtGui import QAction, QColor
from PySide6.QtWidgets import (QApplication, QFileDialog, QFrame, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
                               QProgressBar, QPushButton, QSplitter, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget)

import symfonos_ui
from syp.crypto import SecretKey

from . import convert
from .cli import make_mapper

SUFFIXES = (".deb", ".rpm", ".pkg.tar.zst", ".pkg.tar.xz", ".pkg.tar.gz")


class Worker(QObject):
    done = Signal(str, object, str)      # quelle, report|None, fehler
    finished = Signal()

    def __init__(self, files: list[Path], out: Path, key: str):
        super().__init__()
        self.files, self.out, self.key = files, out, key

    def run(self):
        mapper = make_mapper("/", False, None)
        signer = SecretKey.load(Path(self.key)) if self.key else None
        for f in self.files:
            try:
                _, rep = convert(f, self.out, mapper, signer)
                self.done.emit(str(f), rep, "")
            except Exception as e:   # GUI soll weiterlaufen und den Fehler anzeigen
                self.done.emit(str(f), None, str(e))
        self.finished.emit()


class DropLabel(QLabel):
    dropped = Signal(list)

    def __init__(self):
        super().__init__("Pakete hierher ziehen\n.deb · .rpm · .pkg.tar.zst")
        self.setObjectName("drop")
        self.setAlignment(Qt.AlignCenter)
        self.setAcceptDrops(True)
        self.setAccessibleName("Ablagefläche für Pakete")

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            self.setProperty("active", True)
            self.style().polish(self)
            e.acceptProposedAction()

    def dragLeaveEvent(self, e):
        self.setProperty("active", False)
        self.style().polish(self)

    def dropEvent(self, e):
        self.dragLeaveEvent(e)
        self.dropped.emit([Path(u.toLocalFile()) for u in e.mimeData().urls()])


class Window(QMainWindow):
    def __init__(self, colors: dict):
        super().__init__()
        self.c = colors
        self.setWindowTitle("Paket-Konverter – symfonOS")
        self.resize(1080, 680)
        self.results: dict[str, object] = {}
        root = QWidget(objectName="root")
        self.setCentralWidget(root)
        lay = QVBoxLayout(root)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(14)
        title = QLabel("Paket-Konverter", objectName="title")
        sub = QLabel("Fremde Pakete in das symfonOS-Format .syp umwandeln. Abhängigkeiten werden "
                     "automatisch zugeordnet, fehlende werden gemeldet.", objectName="muted")
        sub.setWordWrap(True)
        lay.addWidget(title)
        lay.addWidget(sub)

        split = QSplitter()
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        self.drop = DropLabel()
        self.drop.dropped.connect(self.add_files)
        self.list = QListWidget()
        self.list.setAccessibleName("Paketliste")
        self.list.currentItemChanged.connect(self.show_report)
        add = QPushButton("Dateien wählen …")
        add.clicked.connect(self.pick)
        ll.addWidget(self.drop)
        ll.addWidget(self.list, 1)
        ll.addWidget(add)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Bericht", "Details"])
        self.tree.setColumnWidth(0, 300)
        self.tree.setAccessibleName("Mapping-Bericht")
        split.addWidget(left)
        split.addWidget(self.tree)
        split.setSizes([380, 700])
        lay.addWidget(split, 1)

        bar = QHBoxLayout()
        self.out = QLineEdit(str(Path.home() / "Pakete"))
        self.out.setAccessibleName("Ausgabeordner")
        self.key = QLineEdit()
        self.key.setPlaceholderText("Signaturschlüssel (optional, .key)")
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        self.go = QPushButton("Konvertieren", objectName="primary")
        self.go.clicked.connect(self.start)
        self.install = QPushButton("Installieren")
        self.install.setEnabled(False)
        self.install.clicked.connect(self.install_selected)
        for w in (QLabel("Ziel:"), self.out, self.key, self.progress, self.install, self.go):
            bar.addWidget(w)
        lay.addLayout(bar)
        quit_ = QAction("Beenden", self, shortcut="Ctrl+Q", triggered=self.close)
        self.addAction(quit_)

    def pick(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Pakete wählen", str(Path.home()),
                                                "Pakete (*.deb *.rpm *.pkg.tar.zst *.pkg.tar.xz)")
        self.add_files([Path(f) for f in files])

    def add_files(self, files: list[Path]):
        for f in files:
            if not f.name.endswith(SUFFIXES) or str(f) in self.results:
                continue
            self.results[str(f)] = None
            it = QListWidgetItem(f"⏳  {f.name}")
            it.setData(Qt.UserRole, str(f))
            self.list.addItem(it)

    def start(self):
        todo = [Path(p) for p, r in self.results.items() if r is None]
        if not todo:
            return
        out = Path(self.out.text()).expanduser()
        out.mkdir(parents=True, exist_ok=True)
        self.go.setEnabled(False)
        self.progress.setRange(0, len(todo))
        self.progress.setValue(0)
        self.progress.setVisible(True)
        self.thread = QThread()
        self.worker = Worker(todo, out, self.key.text().strip())
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.done.connect(self.on_done)
        self.worker.finished.connect(self.thread.quit)
        self.worker.finished.connect(lambda: (self.go.setEnabled(True), self.progress.setVisible(False)))
        self.thread.start()

    def on_done(self, src: str, rep, error: str):
        self.results[src] = rep if rep is not None else error
        self.progress.setValue(self.progress.value() + 1)
        for i in range(self.list.count()):
            it = self.list.item(i)
            if it.data(Qt.UserRole) == src:
                icon = "✗" if rep is None else ("✓" if rep.ok else "⚠")
                it.setText(f"{icon}  {Path(src).name}")
                it.setForeground(QColor(self.c["err" if rep is None else "ok" if rep.ok else "warn"]))
                if self.list.currentItem() is it or self.list.currentItem() is None:
                    self.list.setCurrentItem(it)
                    self.show_report(it)

    def show_report(self, item, _prev=None):
        self.tree.clear()
        self.install.setEnabled(False)
        if item is None:
            return
        r = self.results.get(item.data(Qt.UserRole))
        if r is None:
            QTreeWidgetItem(self.tree, ["Noch nicht konvertiert", ""])
            return
        if isinstance(r, str):
            it = QTreeWidgetItem(self.tree, ["Fehler", r])
            it.setForeground(0, QColor(self.c["err"]))
            return

        def section(title: str, rows: list[tuple[str, str]], color: str):
            if not rows:
                return
            s = QTreeWidgetItem(self.tree, [f"{title} ({len(rows)})", ""])
            s.setForeground(0, QColor(self.c[color]))
            for a, b in rows:
                QTreeWidgetItem(s, [a, b])
            s.setExpanded(True)

        QTreeWidgetItem(self.tree, ["Ausgabe", r.output])
        section("Fehlende Abhängigkeiten", [(u["dep"], u["kind"]) for u in r.unresolved], "err")
        section("Warnungen", [(w, "") for w in r.warnings], "warn")
        section("Zugeordnet", r.mapped, "ok")
        section("Ungeprüft", [(u, "") for u in r.unverified], "warn")
        section("Optional nicht verfügbar", [(o, "") for o in r.optional_missing], "muted")
        section("Ignoriert", [(i, "") for i in r.ignored], "muted")
        self.install.setEnabled(bool(r.output))

    def install_selected(self):
        r = self.results.get(self.list.currentItem().data(Qt.UserRole))
        if not r or isinstance(r, str):
            return
        if not r.ok and QMessageBox.warning(
                self, "Fehlende Abhängigkeiten",
                "Es fehlen Abhängigkeiten. Die Installation wird vermutlich scheitern. Trotzdem versuchen?",
                QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        args = ["syp", "-y", "install", r.output]
        if not self.key.text().strip():
            args.append("--allow-unsigned")
        proc = QProcess(self)
        proc.finished.connect(lambda code, _s: QMessageBox.information(
            self, "Installation", "Installiert." if code == 0 else
            f"Fehlgeschlagen ({code}):\n{bytes(proc.readAllStandardError()).decode()[-800:]}"))
        proc.start("pkexec", args)
        self.statusBar().showMessage("pkexec " + shlex.join(args))


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("syp-convert")
    app.setDesktopFileName("org.symfonos.Convert")
    w = Window(symfonos_ui.apply(app))
    w.add_files([Path(a) for a in sys.argv[1:]])
    w.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
