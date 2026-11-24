"""tools/pkg-compare.py: Pakete inhaltlich vergleichen, Binärdateien und ar-Archive mit Fundstelle."""
from __future__ import annotations

import subprocess

from helpers import ROOT, make_pkg


def ar(*members: tuple[str, str, int]) -> str:
    out = "!<arch>\n"
    for name, body, mtime in members:
        out += f"{name + '/':<16}{mtime:<12}{0:<6}{0:<6}{'100644':<8}{len(body):<10}`\n" + body
        out += "\n" if len(body) % 2 else ""
    return out


def compare(a, b):
    return subprocess.run(["python3", ROOT / "tools/pkg-compare.py", a, b, "alt", "neu"], capture_output=True, text=True)


def test_pkg_compare(tmp_path):
    same = {"usr/lib/liby.a": ar(("main.o", "\0ELF eins", 1700000000), ("yyerror.o", "\0ELF zwei", 1700000000))}
    a = make_pkg(tmp_path, "p", "1-1", same, out=tmp_path / "a")
    b = make_pkg(tmp_path, "p", "1-1", same, out=tmp_path / "b")
    assert compare(a, b).returncode == 0

    c = make_pkg(tmp_path, "p", "1-1", {"usr/lib/liby.a": ar(("main.o", "\0ELF eins", 1700000000),
                                                            ("yyerror.o", "\0ELF zwEi", 1700000000))},
                 out=tmp_path / "c")
    r = compare(a, c)
    assert r.returncode == 1
    assert "ar-Glied 'yyerror.o/'" in r.stdout and "'main.o/'" not in r.stdout, r.stdout

    d = make_pkg(tmp_path, "p", "1-1", {"usr/lib/liby.a": "\0\1\2\3abcd"}, out=tmp_path / "d")
    e = make_pkg(tmp_path, "p", "1-1", {"usr/lib/liby.a": "\0\1\2\3abXd"}, out=tmp_path / "e")
    r = compare(d, e)
    assert r.returncode == 1 and "erste Abweichung bei Byte 6" in r.stdout, r.stdout

    f = make_pkg(tmp_path, "p", "1-1", {"usr/lib/liby.a": "zeile 1\nzeile 2\n"}, out=tmp_path / "f")
    g = make_pkg(tmp_path, "p", "1-1", {"usr/lib/liby.a": "zeile 1\nzeile 3\n"}, out=tmp_path / "g")
    r = compare(f, g)
    assert r.returncode == 1 and "-zeile 2" in r.stdout and "+zeile 3" in r.stdout, r.stdout
