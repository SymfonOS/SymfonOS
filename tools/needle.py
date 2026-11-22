#!/usr/bin/env python3
"""Bildvergleich für den Desktop-Test (wie die »Needles« von openQA, vereinfacht).

Eine Needle ist ein Ausschnitt eines Referenz-Bildschirmfotos: distro/needles/<name>.json nennt die
Bereiche (x, y, w, h) und die Ausschnitte liegen daneben als <name>-<i>.png. Beim Vergleich wird jeder
Ausschnitt im neuen Bildschirmfoto in der Nähe seiner Position gesucht (Fenster und Leisten dürfen sich um
»shift« Pixel verschieben); erreicht die normierte Kreuzkorrelation (Graustufen) mindestens »match«,
gilt der Bereich als gefunden.

    tools/needle.py make REFERENZ.png NAME x,y,w,h [x,y,w,h ...] [--shift 40] [--match 0.9]
    tools/needle.py match BILD.(png|ppm) NAME     # 0 = alle Bereiche gefunden, 1 = nicht
    tools/needle.py check VERZEICHNIS             # alle Needles gegen die gleichnamigen Bilder

Ohne Pillow/numpy: PNG (8 Bit RGB/RGBA) und PPM (P6) werden hier selbst gelesen, wie sie QEMU und
ImageMagick schreiben.
"""
from __future__ import annotations

import argparse
import json
import os
import struct
import sys
import zlib
from pathlib import Path

NEEDLES = Path(os.environ.get("NEEDLE_DIR") or Path(__file__).resolve().parent.parent / "distro" / "needles")


class Image:
    def __init__(self, w: int, h: int, rgb: bytes):
        self.w, self.h, self.rgb = w, h, rgb

    def crop(self, x: int, y: int, w: int, h: int) -> "Image":
        rows = [self.rgb[((y + r) * self.w + x) * 3:((y + r) * self.w + x + w) * 3] for r in range(h)]
        return Image(w, h, b"".join(rows))


def read(path: str | Path) -> Image:
    data = Path(path).read_bytes()
    if data[:2] == b"P6":
        return _ppm(data)
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return _png(data)
    raise ValueError(f"{path}: weder PNG noch PPM")


def _ppm(data: bytes) -> Image:
    fields, pos = [], 2
    while len(fields) < 3:
        while data[pos:pos + 1].isspace():
            pos += 1
        if data[pos:pos + 1] == b"#":
            pos = data.index(b"\n", pos) + 1
            continue
        end = pos
        while not data[end:end + 1].isspace():
            end += 1
        fields.append(int(data[pos:end]))
        pos = end
    w, h, maxval = fields
    if maxval != 255:
        raise ValueError("PPM mit mehr als 8 Bit")
    return Image(w, h, data[pos + 1:pos + 1 + w * h * 3])


def _png(data: bytes) -> Image:
    pos, idat, w = 8, [], 0
    while pos < len(data):
        n, typ = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + n]
        if typ == b"IHDR":
            w, h, depth, ctype, _, _, interlace = struct.unpack(">IIBBBBB", body)
            if depth != 8 or ctype not in (2, 6) or interlace:
                raise ValueError("nur 8-Bit-RGB/RGBA-PNG ohne Interlacing")
            bpp = 3 if ctype == 2 else 4
        elif typ == b"IDAT":
            idat.append(body)
        elif typ == b"IEND":
            break
        pos += 12 + n
    raw = zlib.decompress(b"".join(idat))
    stride = w * bpp
    out = bytearray(h * stride)
    prev = bytearray(stride)
    for r in range(h):
        f = raw[r * (stride + 1)]
        line = bytearray(raw[r * (stride + 1) + 1:(r + 1) * (stride + 1)])
        if f == 1:
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 255
        elif f == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 255
        elif f == 3:
            for i in range(stride):
                line[i] = (line[i] + ((line[i - bpp] if i >= bpp else 0) + prev[i]) // 2) & 255
        elif f == 4:
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                b, c = prev[i], prev[i - bpp] if i >= bpp else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[i] = (line[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        out[r * stride:(r + 1) * stride] = line
        prev = line
    if bpp == 4:
        rgb = bytearray(w * h * 3)
        rgb[0::3], rgb[1::3], rgb[2::3] = out[0::4], out[1::4], out[2::4]
        return Image(w, h, bytes(rgb))
    return Image(w, h, bytes(out))


def write_png(img: Image, path: str | Path) -> None:
    raw = b"".join(b"\0" + img.rgb[r * img.w * 3:(r + 1) * img.w * 3] for r in range(img.h))

    def chunk(t: bytes, b: bytes) -> bytes:
        return struct.pack(">I", len(b)) + t + b + struct.pack(">I", zlib.crc32(t + b) & 0xFFFFFFFF)
    Path(path).write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", img.w, img.h, 8, 2, 0, 0, 0))
                           + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def _gray(img: Image, step: int) -> list[list[int]]:
    """Graustufen, je step Pixel ein Wert (schneller Vergleich ohne numpy)."""
    g = []
    for y in range(0, img.h, step):
        row, base = [], y * img.w * 3
        for x in range(0, img.w, step):
            i = base + x * 3
            row.append((img.rgb[i] * 3 + img.rgb[i + 1] * 6 + img.rgb[i + 2]) // 10)
        g.append(row)
    return g


def similarity(screen: Image, ref: Image, x: int, y: int, shift: int, step: int = 2) -> tuple[float, int, int]:
    """Beste Ähnlichkeit (0…1) des Ausschnitts ref um (x, y) herum, mit Position."""
    sg = _gray(screen, 1)
    rg = _gray(ref, step)
    rh, rw = len(rg), len(rg[0])
    best = (0.0, x, y)

    flat = [v for row in rg for v in row]
    n = len(flat)
    rmean = sum(flat) / n
    rdev = [[v - rmean for v in row] for row in rg]
    rnorm = sum(v * v for row in rdev for v in row) ** 0.5

    def score(dx: int, dy: int) -> float:
        # normierte Kreuzkorrelation: vergleicht die Struktur (Text, Kanten), nicht nur die Helligkeit –
        # dunkle Schrift auf dunkler Leiste unterscheidet sich sonst kaum von der leeren Leiste.
        # Bei einfarbigen Ausschnitten (keine Struktur) die mittlere Abweichung.
        ox, oy = x + dx, y + dy
        win = [[sg[oy + r * step][ox + c * step] for c in range(rw)] for r in range(rh)]
        if rnorm < 8 * n ** 0.5:
            return 1 - sum(abs(win[r][c] - rg[r][c]) for r in range(rh) for c in range(rw)) / (255 * n)
        wmean = sum(map(sum, win)) / n
        num = sum((win[r][c] - wmean) * rdev[r][c] for r in range(rh) for c in range(rw))
        wnorm = sum((v - wmean) ** 2 for row in win for v in row) ** 0.5
        return num / (rnorm * wnorm) if wnorm else 0.0

    cand = {(dx, dy) for dy in range(-shift, shift + 1) for dx in range(-shift, shift + 1)
            if 0 <= y + dy and y + dy + ref.h <= screen.h and 0 <= x + dx and x + dx + ref.w <= screen.w}
    # grob (jede zweite Position), dann pixelgenau um die besten Treffer
    coarse = sorted(((score(dx, dy), dx, dy) for dx, dy in cand if dx % 2 == 0 and dy % 2 == 0), reverse=True)[:3]
    seen = set()
    for _, cx, cy in coarse:
        for dy in (cy - 1, cy, cy + 1):
            for dx in (cx - 1, cx, cx + 1):
                if (dx, dy) in seen or (dx, dy) not in cand:
                    continue
                seen.add((dx, dy))
                v = score(dx, dy)
                if v > best[0]:
                    best = (v, x + dx, y + dy)
    return best


def load(name: str) -> dict:
    return json.loads((NEEDLES / f"{name}.json").read_text())


def match(screen_path: str | Path, name: str) -> tuple[bool, list[str]]:
    n = load(name)
    screen = read(screen_path)
    ok, msgs = True, []
    for i, a in enumerate(n["areas"]):
        ref = read(NEEDLES / f"{name}-{i}.png")
        v, fx, fy = similarity(screen, ref, a["x"], a["y"], n.get("shift", 40))
        hit = v >= n.get("match", 0.8)
        ok &= hit
        msgs.append(f"{name}[{i}] {a.get('label', '')}: {v:.3f} bei {fx},{fy} "
                    f"({'gefunden' if hit else 'NICHT gefunden'}, nötig {n.get('match', 0.9)})")
    return ok, msgs


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    mk = sub.add_parser("make")
    mk.add_argument("ref")
    mk.add_argument("name")
    mk.add_argument("areas", nargs="+", help="x,y,w,h oder x,y,w,h=Bezeichnung")
    mk.add_argument("--shift", type=int, default=40)
    mk.add_argument("--match", type=float, default=0.8)
    m = sub.add_parser("match")
    m.add_argument("screen")
    m.add_argument("name")
    c = sub.add_parser("check")
    c.add_argument("dir")
    a = ap.parse_args()

    if a.cmd == "make":
        ref = read(a.ref)
        NEEDLES.mkdir(parents=True, exist_ok=True)
        areas = []
        for i, spec in enumerate(a.areas):
            geo, _, label = spec.partition("=")
            x, y, w, h = map(int, geo.split(","))
            write_png(ref.crop(x, y, w, h), NEEDLES / f"{a.name}-{i}.png")
            areas.append({"x": x, "y": y, "w": w, "h": h, **({"label": label} if label else {})})
        (NEEDLES / f"{a.name}.json").write_text(json.dumps(
            {"areas": areas, "shift": a.shift, "match": a.match}, indent=2, ensure_ascii=False) + "\n")
        return 0
    if a.cmd == "match":
        ok, msgs = match(a.screen, a.name)
        print("\n".join(msgs))
        return 0 if ok else 1
    # check: jede Needle gegen <dir>/<name>.png (oder .ppm); fehlendes Bild = nicht geprüft
    failed, checked = [], 0
    for j in sorted(NEEDLES.glob("*.json")):
        name = j.stem
        img = next((p for p in (Path(a.dir) / f"{name}.png", Path(a.dir) / f"{name}.ppm") if p.exists()), None)
        if not img:
            print(f"{name}: kein Bildschirmfoto")
            continue
        checked += 1
        ok, msgs = match(img, name)
        print("\n".join(msgs))
        if not ok:
            failed.append(name)
    print(f"Bildvergleich: {checked - len(failed)} von {checked} Bildschirmfotos passen"
          + (f" (abweichend: {', '.join(failed)})" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
