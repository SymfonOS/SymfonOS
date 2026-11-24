"""Bildvergleich des Desktop-Tests (tools/needle.py): Ausschnitt finden, auch leicht verschoben und in PPM
oder PNG, und ein Bild ohne den Ausschnitt ablehnen; die Needles im Repo passen zu ihren eigenen Ausschnitten."""
import json
import random
import subprocess
import sys

from helpers import ROOT

sys.path.insert(0, str(ROOT / "tools"))
import needle  # noqa: E402

TOOL = ROOT / "tools/needle.py"


def screen(w=320, h=200, seed=1, box=None):
    rnd = random.Random(seed)
    px = bytearray()
    for y in range(h):
        for x in range(w):
            v = 40 + (x + y) // 8                      # ruhiger Verlauf wie ein Hintergrundbild
            px += bytes((v, v // 2, v + 30))
    if box:
        bx, by = box
        for y in range(by, by + 20, 2):
            for x in range(bx, bx + 60, 2):
                v = 255 if rnd.random() < 0.5 else 0  # »Schrift«: kräftige Struktur in 2×2-Punkten
                for yy in (y, y + 1):
                    i = (yy * w + x) * 3
                    px[i:i + 6] = bytes((v, v, v)) * 2
    return needle.Image(w, h, bytes(px))


def ppm(img, path):
    path.write_bytes(b"P6\n%d %d\n255\n" % (img.w, img.h) + img.rgb)


def test_needle_match(tmp_path, monkeypatch):
    nd = tmp_path / "needles"
    env = {"NEEDLE_DIR": str(nd)}
    ref = screen(box=(100, 50))
    needle.write_png(ref, tmp_path / "ref.png")
    assert needle.read(tmp_path / "ref.png").rgb == ref.rgb          # PNG schreiben und lesen
    run = lambda *a: subprocess.run([TOOL, *map(str, a)], capture_output=True, text=True,
                                    env={**__import__("os").environ, **env})
    assert run("make", tmp_path / "ref.png", "fenster", "100,50,60,20=Titel", "--shift", "20").returncode == 0
    assert json.loads((nd / "fenster.json").read_text())["areas"][0]["label"] == "Titel"

    shots = tmp_path / "shots"
    shots.mkdir()
    ppm(screen(seed=1, box=(107, 46)), shots / "fenster.ppm")     # um 7/−4 Pixel verschoben
    r = run("check", shots)
    assert r.returncode == 0 and "1 von 1" in r.stdout and "bei 107,46" in r.stdout, r.stdout

    ppm(screen(seed=1), shots / "fenster.ppm")                    # Fenster fehlt
    r = run("check", shots)
    assert r.returncode == 1 and "NICHT gefunden" in r.stdout, r.stdout

    ppm(screen(seed=1, box=(160, 120)), shots / "fenster.ppm")    # zu weit weg (shift 20)
    assert run("check", shots).returncode == 1

    # neue Needle (--soft, »strict«: false): Abweichung nur als Warnung, der Test scheitert nicht
    assert run("make", tmp_path / "ref.png", "neu", "100,50,60,20", "--soft").returncode == 0
    assert json.loads((nd / "neu.json").read_text())["strict"] is False
    (nd / "fenster.json").unlink()
    ppm(screen(seed=1), shots / "neu.ppm")
    r = run("check", shots)
    assert r.returncode == 0 and "nur Warnung: neu" in r.stdout, r.stdout


def test_repo_needles_match_their_crops():
    """Jede Needle im Repo: Ausschnitte lesbar, Bereichsgrößen stimmen mit den Bildern überein."""
    files = sorted((ROOT / "distro/needles").glob("*.json"))
    assert files
    for j in files:
        n = json.loads(j.read_text())
        for i, a in enumerate(n["areas"]):
            img = needle.read(j.with_name(f"{j.stem}-{i}.png"))
            assert (img.w, img.h) == (a["w"], a["h"]), j
