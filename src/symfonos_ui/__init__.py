"""Gemeinsamer Qt-Stil aller symfonOS-Apps (folgt Hell/Dunkel und Akzentfarbe des Systems)."""
from __future__ import annotations

import configparser
import os
from pathlib import Path

DEFAULT_ACCENT = "#7C5CFF"   # symfonOS-Violett

DARK = dict(bg="#16161D", surface="#1F1F29", raised="#282834", border="#34344A",
            text="#ECECF4", muted="#9A9AB0", ok="#3DD68C", warn="#F5B83D", err="#FF6B6B")
LIGHT = dict(bg="#F6F6FA", surface="#FFFFFF", raised="#EEEEF5", border="#D9D9E6",
             text="#1B1B24", muted="#5E5E73", ok="#118A50", warn="#A86A00", err="#C62828")


def system_accent() -> str:
    """Akzentfarbe aus KDE (kdeglobals AccentColor) oder Standard."""
    kg = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "kdeglobals"
    if kg.exists():
        cp = configparser.ConfigParser(interpolation=None, strict=False)
        try:
            cp.read(kg)
            v = cp.get("General", "AccentColor", fallback="")
            if v and "," in v:
                r, g, b = (int(x) for x in v.split(",")[:3])
                return f"#{r:02X}{g:02X}{b:02X}"
        except (configparser.Error, ValueError):
            pass
    return DEFAULT_ACCENT


def is_dark(app) -> bool:
    try:
        from PySide6.QtCore import Qt
        return app.styleHints().colorScheme() == Qt.ColorScheme.Dark
    except Exception:
        return True


def stylesheet(dark: bool = True, accent: str | None = None) -> str:
    c = DARK if dark else LIGHT
    a = accent or system_accent()
    sel = "rgba({}, {}, {}, 0.22)".format(*(int(a[i:i + 2], 16) for i in (1, 3, 5)))
    return f"""
    * {{ font-family: "Inter", "Noto Sans", sans-serif; font-size: 10.5pt; color: {c['text']}; }}
    QMainWindow, QWidget#root {{ background: {c['bg']}; }}
    QFrame#card, QListWidget, QTreeWidget, QTextEdit, QPlainTextEdit, QTextBrowser {{
        background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 12px; padding: 6px; }}
    QListWidget::item, QTreeWidget::item {{ padding: 6px; border-radius: 8px; }}
    QListWidget::item:selected, QTreeWidget::item:selected {{ background: {sel}; color: {c['text']}; }}
    QLineEdit, QComboBox, QSpinBox {{ background: {c['surface']}; border: 1px solid {c['border']};
        border-radius: 10px; padding: 7px 10px; }}
    QLineEdit:focus, QComboBox:focus {{ border: 2px solid {a}; }}
    QPushButton {{ background: {c['raised']}; border: 1px solid {c['border']}; border-radius: 10px;
        padding: 8px 16px; }}
    QPushButton:hover {{ border-color: {a}; }}
    QPushButton:focus {{ outline: none; border: 2px solid {a}; }}
    QPushButton#primary {{ background: {a}; color: white; border: none; font-weight: 600; }}
    QPushButton#primary:disabled {{ background: {c['border']}; color: {c['muted']}; }}
    QLabel#title {{ font-size: 18pt; font-weight: 700; }}
    QLabel#muted {{ color: {c['muted']}; }}
    QLabel#drop {{ border: 2px dashed {c['border']}; border-radius: 16px; padding: 28px;
        color: {c['muted']}; font-size: 12pt; }}
    QLabel#drop[active="true"] {{ border-color: {a}; color: {a}; }}
    QProgressBar {{ background: {c['raised']}; border: none; border-radius: 6px; height: 10px;
        text-align: center; }}
    QProgressBar::chunk {{ background: {a}; border-radius: 6px; }}
    QTabBar::tab {{ padding: 8px 16px; border-radius: 8px; margin: 2px; }}
    QTabBar::tab:selected {{ background: {a}; color: white; }}
    QTabWidget::pane {{ border: none; }}
    QScrollBar:vertical {{ width: 10px; background: transparent; }}
    QScrollBar::handle:vertical {{ background: {c['border']}; border-radius: 5px; min-height: 30px; }}
    """


def colors(dark: bool) -> dict:
    return DARK if dark else LIGHT


def apply(app) -> dict:
    dark = is_dark(app)
    app.setStyleSheet(stylesheet(dark))
    return colors(dark)
