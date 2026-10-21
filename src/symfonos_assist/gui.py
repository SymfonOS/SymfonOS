"""symfonOS-Assistent – Chatfenster."""
from __future__ import annotations

import html
import sys

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
                               QMainWindow, QMessageBox, QPushButton, QTextBrowser, QVBoxLayout,
                               QWidget)

import symfonos_ui

from . import llm, safety
from .agent import Assistant


class Streamer(QObject):
    token = Signal(str)
    done = Signal(str)

    def __init__(self, fn):
        super().__init__()
        self.fn = fn

    def run(self):
        try:
            for t in self.fn():
                self.token.emit(t)
            self.done.emit("")
        except llm.LLMError as e:
            self.done.emit(str(e))


class Window(QMainWindow):
    def __init__(self, colors: dict):
        super().__init__()
        self.c = colors
        self.a = Assistant()
        self.setWindowTitle("Assistent – symfonOS")
        self.resize(820, 720)
        root = QWidget(objectName="root")
        self.setCentralWidget(root)
        lay = QVBoxLayout(root)
        lay.setContentsMargins(18, 16, 18, 16)
        head = QHBoxLayout()
        head.addWidget(QLabel("Assistent", objectName="title"))
        ok = llm.available(self.a.s)
        self.state = QLabel(("● lokal bereit · " if ok else "○ Modell nicht erreichbar · ") + self.a.s.model,
                            objectName="muted")
        head.addStretch()
        head.addWidget(self.state)
        lay.addLayout(head)
        note = QLabel("Läuft vollständig auf diesem Rechner. Befehle werden nur nach deiner "
                      "Bestätigung ausgeführt.", objectName="muted")
        lay.addWidget(note)
        self.view = QTextBrowser(openExternalLinks=True)
        self.view.setAccessibleName("Gesprächsverlauf")
        lay.addWidget(self.view, 1)
        self.cards = QVBoxLayout()
        lay.addLayout(self.cards)
        row = QHBoxLayout()
        self.input = QLineEdit(placeholderText="Frage stellen, z. B. „Mein Ton knackst in Ardour“")
        self.input.returnPressed.connect(self.send)
        diag = QPushButton("System prüfen")
        diag.clicked.connect(self.diagnose)
        send = QPushButton("Senden", objectName="primary")
        send.clicked.connect(self.send)
        row.addWidget(self.input, 1)
        row.addWidget(diag)
        row.addWidget(send)
        lay.addLayout(row)
        self.buf = ""

    def bubble(self, who: str, text: str):
        color = self.c["muted"]
        self.view.append(f'<p style="color:{color};margin-bottom:0">{who}</p>'
                         f'<p style="margin-top:2px;white-space:pre-wrap">{html.escape(text)}</p>')

    def _stream(self, fn, label="symfonOS"):
        self.clear_cards()
        self.view.append(f'<p style="color:{self.c["muted"]};margin-bottom:0">{label}</p>')
        self.buf = ""
        self.thread = QThread()
        self.worker = Streamer(fn)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.token.connect(self.on_token)
        self.worker.done.connect(self.on_done)
        self.worker.done.connect(self.thread.quit)
        self.thread.start()

    def on_token(self, t: str):
        self.buf += t
        cur = self.view.textCursor()
        cur.movePosition(cur.MoveOperation.End)
        cur.insertText(t)
        self.view.ensureCursorVisible()

    def on_done(self, err: str):
        if err:
            self.bubble("Fehler", err)
            return
        for p in self.a.proposals():
            self.add_card(p)

    def send(self):
        q = self.input.text().strip()
        if not q:
            return
        self.input.clear()
        self.bubble("Du", q)
        self._stream(lambda: self.a.ask(q))

    def diagnose(self):
        offline, findings, stream = self.a.diagnose()
        self.bubble("Regelbasierte Analyse", offline)
        for _, cmds in findings:
            for c in cmds:
                self.add_card(safety.Proposal(c, safety.classify(c)))
        if stream:
            self._stream(lambda: stream, "KI-Analyse")

    def clear_cards(self):
        while self.cards.count():
            w = self.cards.takeAt(0).widget()
            if w:
                w.deleteLater()

    def add_card(self, p: safety.Proposal):
        f = QFrame(objectName="card")
        h = QHBoxLayout(f)
        color = {"lesend": "ok", "ändernd": "warn", "gefährlich": "err"}[p.risk]
        lab = QLabel(f'<code>{html.escape(p.command)}</code> '
                     f'<span style="color:{self.c[color]}">· {p.risk}</span>')
        lab.setTextInteractionFlags(Qt.TextSelectableByMouse)
        run = QPushButton("Ausführen …")
        run.clicked.connect(lambda: self.run(p, f))
        skip = QPushButton("Ablehnen")
        skip.clicked.connect(f.deleteLater)
        h.addWidget(lab, 1)
        h.addWidget(skip)
        h.addWidget(run)
        self.cards.addWidget(f)

    def run(self, p: safety.Proposal, card: QFrame):
        if p.needs_strong_confirm:
            text, ok = QInputDialog.getText(self, "Gefährlicher Befehl",
                                            f"{p.command}\n\nKann Daten unwiderruflich verändern.\n"
                                            "Zum Ausführen AUSFÜHREN eingeben:")
            if not ok or text.strip() != "AUSFÜHREN":
                return
        elif QMessageBox.question(self, "Befehl ausführen?", f"{p.command}\n\nRisiko: {p.risk}") \
                != QMessageBox.Yes:
            return
        card.deleteLater()
        code, out = safety.run(p.command)
        self.bubble(f"$ {p.command} (Exit {code})", out[-3000:] or "(keine Ausgabe)")
        if llm.available(self.a.s):
            self._stream(lambda: self.a.feed_output(p.command, code, out))


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("symfonos-assist")
    app.setDesktopFileName("org.symfonos.Assist")
    try:
        w = Window(symfonos_ui.apply(app))
    except ValueError as e:
        QMessageBox.critical(None, "Assistent", str(e))
        return 2
    w.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
