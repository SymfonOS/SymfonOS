"""symfonOS Software-Center."""
from __future__ import annotations

import os
import time
import shlex
import sys

from PySide6.QtCore import QProcess, QSize, Qt
from PySide6.QtGui import QColor, QIcon
from PySide6.QtWidgets import (QApplication, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
                               QPlainTextEdit, QPushButton, QTabWidget, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout, QWidget)

import symfonos_ui

from .backend import SECTIONS, Backend, StoreItem

ORIGIN_LABEL = {"native": "symfonOS", "arch": "aus Arch konvertiert", "deb": "aus .deb konvertiert",
                "rpm": "aus .rpm konvertiert", "pacman-import": "Basissystem"}


class RunDialog(QDialog):
    """Führt einen syp-Befehl per pkexec aus und zeigt die Ausgabe live."""

    def __init__(self, parent, cmd: list[str]):
        super().__init__(parent)
        self.setWindowTitle("Wird ausgeführt …")
        self.resize(720, 420)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(f"<code>{shlex.join(cmd)}</code>"))
        self.log = QPlainTextEdit(readOnly=True)
        lay.addWidget(self.log)
        self.close_btn = QPushButton("Schließen", enabled=False)
        self.close_btn.clicked.connect(self.accept)
        lay.addWidget(self.close_btn, alignment=Qt.AlignRight)
        self.proc = QProcess(self)
        self.proc.setProcessChannelMode(QProcess.MergedChannels)
        self.proc.readyReadStandardOutput.connect(
            lambda: self.log.appendPlainText(bytes(self.proc.readAllStandardOutput()).decode(errors="replace")))
        self.proc.finished.connect(self.done_)
        self.proc.start(cmd[0], cmd[1:])

    def done_(self, code, _status):
        self.setWindowTitle("Fertig" if code == 0 else f"Fehlgeschlagen ({code})")
        self.close_btn.setEnabled(True)


class Details(QFrame):
    def __init__(self, store: "Store"):
        super().__init__(objectName="card")
        self.store = store
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 20, 20, 20)
        head = QHBoxLayout()
        self.icon = QLabel()
        self.title = QLabel(objectName="title")
        head.addWidget(self.icon)
        head.addWidget(self.title, 1)
        lay.addLayout(head)
        self.meta = QLabel(objectName="muted")
        self.meta.setWordWrap(True)
        self.desc = QLabel()
        self.desc.setWordWrap(True)
        self.desc.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.desc.setOpenExternalLinks(True)
        lay.addWidget(self.meta)
        lay.addWidget(self.desc, 1, Qt.AlignTop)
        btns = QHBoxLayout()
        self.remove = QPushButton("Entfernen")
        self.action = QPushButton(objectName="primary")
        btns.addStretch()
        btns.addWidget(self.remove)
        btns.addWidget(self.action)
        lay.addLayout(btns)
        self.remove.clicked.connect(lambda: store.run("remove", self.item.name))
        self.action.clicked.connect(lambda: store.run("install", self.item.name))
        self.show_item(None)

    def show_item(self, item: StoreItem | None):
        self.item = item
        for w in (self.remove, self.action):
            w.setVisible(item is not None)
        if item is None:
            self.title.setText("Software")
            self.meta.setText("")
            self.desc.setText("Wähle links eine Anwendung aus.")
            self.icon.clear()
            return
        self.icon.setPixmap(app_icon(item.name).pixmap(64, 64))
        self.title.setText(item.name)
        state = (f"installiert {item.installed}" + (f" · Update auf {item.version}" if item.update else "")
                 if item.installed else f"Version {item.version}")
        self.meta.setText(f"{state} · Lizenz {item.license} · {ORIGIN_LABEL.get(item.origin, item.origin)}"
                          f" · Repo {item.repo} · {item.size / 1e6:.1f} MB")
        url = f'<p><a href="{item.url}">{item.url}</a></p>' if item.url else ""
        self.desc.setText(f"<p><b>{item.summary}</b></p><p>{item.description}</p>{url}")
        self.remove.setVisible(bool(item.installed))
        self.action.setVisible(not item.installed or item.update)
        self.action.setText("Aktualisieren" if item.update else "Installieren")
        if item.origin in ("deb", "rpm"):
            self.meta.setText(self.meta.text() + " · ⚠ Fremdpaket, Skripte werden nicht automatisch ausgeführt")


def app_icon(name: str) -> QIcon:
    for n in (name, f"org.kde.{name}", f"org.gnome.{name}", "application-x-executable"):
        ic = QIcon.fromTheme(n)
        if not ic.isNull():
            return ic
    return QIcon()


class Store(QMainWindow):
    def __init__(self, colors: dict, root: str = "/"):
        super().__init__()
        self.c = colors
        self.backend = Backend(root)
        self.setWindowTitle("Software – symfonOS")
        self.resize(1180, 760)
        self.tabs = QTabWidget(documentMode=True)
        self.setCentralWidget(self.tabs)
        self.tabs.addTab(self._discover(), "Entdecken")
        self.tabs.addTab(self._updates(), "Aktualisierungen")
        self.tabs.addTab(self._history(), "Verlauf")
        self.refresh()

    # --- Entdecken / Installiert ------------------------------------------------------
    def _discover(self) -> QWidget:
        w = QWidget(objectName="root")
        lay = QHBoxLayout(w)
        lay.setContentsMargins(18, 18, 18, 18)
        lay.setSpacing(14)
        self.sections = QListWidget()
        self.sections.setFixedWidth(220)
        self.sections.setAccessibleName("Rubriken")
        for t in ["Alle", "Installiert"] + [s for s, _ in SECTIONS] + ["Weitere"]:
            self.sections.addItem(t)
        self.sections.setCurrentRow(0)
        self.sections.currentRowChanged.connect(lambda _: self.fill())
        mid = QVBoxLayout()
        self.search = QLineEdit(placeholderText="Anwendungen suchen …  (Strg+F)", clearButtonEnabled=True)
        self.search.setAccessibleName("Suche")
        self.search.textChanged.connect(lambda _: self.fill())
        self.list = QListWidget()
        self.list.setIconSize(QSize(40, 40))
        self.list.setAccessibleName("Anwendungen")
        self.list.currentItemChanged.connect(
            lambda it, _p: self.details.show_item(it.data(Qt.UserRole) if it else None))
        mid.addWidget(self.search)
        mid.addWidget(self.list, 1)
        self.details = Details(self)
        self.details.setMinimumWidth(400)
        lay.addWidget(self.sections)
        lay.addLayout(mid, 1)
        lay.addWidget(self.details, 1)
        return w

    def fill(self):
        sec = self.sections.currentItem().text() if self.sections.currentItem() else "Alle"
        q = self.search.text().lower().split()
        self.list.clear()
        for it in self.items:
            if sec == "Installiert" and not it.installed:
                continue
            if sec not in ("Alle", "Installiert") and it.section() != sec:
                continue
            hay = f"{it.name} {it.summary} {' '.join(it.categories)}".lower()
            if not all(t in hay for t in q):
                continue
            badge = "  ⬆" if it.update else ("  ✓" if it.installed else "")
            li = QListWidgetItem(app_icon(it.name), f"{it.name}{badge}\n{it.summary}")
            li.setData(Qt.UserRole, it)
            self.list.addItem(li)

    # --- Updates -------------------------------------------------------------------
    def _updates(self) -> QWidget:
        w = QWidget(objectName="root")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(18, 18, 18, 18)
        top = QHBoxLayout()
        self.upd_label = QLabel(objectName="title")
        sync = QPushButton("Nach Updates suchen")
        sync.clicked.connect(lambda: self.run("sync"))
        self.upd_btn = QPushButton("Alles aktualisieren", objectName="primary")
        self.upd_btn.clicked.connect(lambda: self.run("upgrade"))
        top.addWidget(self.upd_label, 1)
        top.addWidget(sync)
        top.addWidget(self.upd_btn)
        lay.addLayout(top)
        self.upd_info = QLabel(objectName="muted")
        self.upd_info.setWordWrap(True)
        lay.addWidget(self.upd_info)
        self.upd_tree = QTreeWidget()
        self.upd_tree.setHeaderLabels(["Paket", "Installiert", "Neu"])
        self.upd_tree.setColumnWidth(0, 360)
        lay.addWidget(self.upd_tree, 1)
        return w

    def _history(self) -> QWidget:
        w = QWidget(objectName="root")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(18, 18, 18, 18)
        lay.addWidget(QLabel("Verlauf & Wiederherstellung", objectName="title"))
        hint = QLabel("Vor jeder Änderung legt symfonOS automatisch einen Btrfs-Snapshot an. "
                      "Du kannst einzelne Transaktionen zurücknehmen oder beim Start im Bootmenü "
                      "einen früheren Systemzustand wählen.", objectName="muted")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        self.hist = QTreeWidget()
        self.hist.setHeaderLabels(["#", "Zeitpunkt", "Aktion", "Status", "Snapshot"])
        self.hist.setColumnWidth(2, 520)
        lay.addWidget(self.hist, 1)
        rb = QPushButton("Ausgewählte Transaktion zurücknehmen")
        rb.clicked.connect(self.rollback)
        lay.addWidget(rb, alignment=Qt.AlignRight)
        return w

    def rollback(self):
        it = self.hist.currentItem()
        if it is None:
            return
        if QMessageBox.question(self, "Zurücknehmen",
                                f"Paketzustand vor Transaktion {it.text(0)} wiederherstellen?") \
                == QMessageBox.Yes:
            self.run("rollback", it.text(0))

    # --- Allgemein -------------------------------------------------------------------
    def refresh(self):
        self.backend.reload()
        self.items = self.backend.items()
        self.fill()
        ups, err = self.backend.updates()
        self.upd_tree.clear()
        for n, old, new in ups:
            QTreeWidgetItem(self.upd_tree, [n, old, new])
        self.upd_label.setText((f"{len(ups)} Aktualisierung" + ("en" if len(ups) > 1 else "")) if ups
                                else "System ist aktuell")
        self.upd_btn.setEnabled(bool(ups))
        ts = self.backend.last_sync()
        info = f"Zuletzt geprüft: {time.strftime('%d.%m.%Y %H:%M', time.localtime(ts))}" if ts \
            else "Noch nie nach Updates gesucht."
        self.upd_info.setText(info + (f"\n⚠ {err}" if err else "") +
                              "\nVor dem Update wird automatisch ein Snapshot erstellt.")
        self.hist.clear()
        for h in self.backend.history():
            row = QTreeWidgetItem(self.hist, [str(h["id"]), h["when"], h["cmd"], h["status"],
                                              h["snapshot"] or "–"])
            if h["status"] != "done":
                row.setForeground(3, QColor(self.c["err"]))

    def run(self, action: str, *names: str):
        if os.environ.get("SYMFONOS_STORE_DEMO"):
            QMessageBox.information(self, "Demo", shlex.join(Backend.command(action, *names)))
            return
        RunDialog(self, Backend.command(action, *names)).exec()
        self.refresh()


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("symfonos-store")
    app.setDesktopFileName("org.symfonos.Store")
    s = Store(symfonos_ui.apply(app), os.environ.get("SYP_ROOT", "/"))
    s.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
