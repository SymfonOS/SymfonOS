"""syp-convert: wandelt .deb, .rpm und .pkg.tar.* in symfonOS-.syp-Pakete um."""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from syp.crypto import SecretKey

from . import arch, deb, rpm
from .common import ConvertError, finalize
from .mapping import Mapper, Report

__all__ = ["convert", "detect", "ConvertError", "Mapper", "Report"]


def detect(path: Path) -> str:
    n = path.name
    head = path.read_bytes()[:8]
    if n.endswith(".deb") or head.startswith(b"!<arch>\n"):
        return "deb"
    if n.endswith(".rpm") or head[:4] == b"\xed\xab\xee\xdb":
        return "rpm"
    if ".pkg.tar" in n:
        return "arch"
    raise ConvertError(f"Unbekanntes Paketformat: {n}")


PARSERS = {"deb": deb.parse, "rpm": rpm.parse, "arch": arch.parse}


def convert(path: Path, out_dir: Path, mapper: Mapper, signer: SecretKey | None = None,
            mark_reviewed: bool = False, keep_versions: bool = False,
            overrides: dict | None = None) -> tuple[Path, Report]:
    fmt = detect(path)
    work = Path(tempfile.mkdtemp(prefix="syp-convert-"))
    try:
        c = PARSERS[fmt](path, work)
        for k, v in (overrides or {}).items():
            if v:
                setattr(c, k, v)
        return finalize(c, mapper, out_dir, signer, mark_reviewed, keep_versions)
    finally:
        shutil.rmtree(work, ignore_errors=True)
