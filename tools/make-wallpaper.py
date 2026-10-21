#!/usr/bin/env python3
"""Erzeugt das symfonOS-Hintergrundbild (Plasma-Wallpaper-Paket) reproduzierbar aus Code."""
import json
import math
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QLinearGradient, QPainter, QPainterPath, QPen, QRadialGradient


def render(w: int, h: int, dark: bool) -> QImage:
    img = QImage(w, h, QImage.Format_RGB32)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    g = QLinearGradient(0, 0, w, h)
    g.setColorAt(0, QColor("#120F24" if dark else "#EDEBFF"))
    g.setColorAt(1, QColor("#2A1B5C" if dark else "#C9D8FF"))
    p.fillRect(img.rect(), g)
    glow = QRadialGradient(QPointF(w * 0.72, h * 0.35), w * 0.45)
    glow.setColorAt(0, QColor(124, 92, 255, 120 if dark else 90))
    glow.setColorAt(1, QColor(124, 92, 255, 0))
    p.fillRect(img.rect(), glow)
    # Klangwellen / Pinselstriche
    for i in range(14):
        path = QPainterPath()
        amp, phase, y0 = h * (0.04 + i * 0.006), i * 0.45, h * (0.45 + i * 0.022)
        path.moveTo(0, y0)
        for x in range(0, w + 20, 20):
            path.lineTo(x, y0 + amp * math.sin(x / w * 2 * math.pi * 1.3 + phase))
        alpha = 200 - i * 12
        col = QColor(255, 255, 255, max(alpha // 3, 12)) if dark else QColor(91, 60, 240, max(alpha // 3, 12))
        p.setPen(QPen(col, max(1.0, w / 1600), Qt.SolidLine, Qt.RoundCap))
        p.drawPath(path)
    p.end()
    return img


def main(out: Path) -> None:
    QGuiApplication([])
    for pkg, name, dark in (("symfonOS", "symfonOS", True), ("symfonOS-Light", "symfonOS Hell", False)):
        d = out / pkg / "contents" / "images"
        d.mkdir(parents=True, exist_ok=True)
        for w, h in ((3840, 2160), (2560, 1600), (1920, 1080)):
            render(w, h, dark).save(str(d / f"{w}x{h}.png"))
        meta = {"KPlugin": {"Id": pkg, "Name": name, "License": "CC-BY-SA-4.0",
                            "Authors": [{"Name": "symfonOS"}]}}
        (out / pkg / "metadata.json").write_text(json.dumps(meta, indent=2))


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "build/wallpapers"))
