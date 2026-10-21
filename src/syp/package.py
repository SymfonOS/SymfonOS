"""Das .syp-Paketformat.

Ein .syp ist ein unkomprimiertes POSIX-tar mit genau diesen Mitgliedern (Reihenfolge fix):

    .SYPINFO.json   Metadaten (Name, Version, Abhängigkeiten, Skripte, Herkunft ...)
    .FILES.json     Dateiliste mit sha256, Modus, Größe, Symlink-Zielen
    data.tar.zst    Nutzdaten relativ zu / (zstd, reproduzierbar)
    .SIGNATURE.json Ed25519-Signatur über den Digest der drei vorigen Mitglieder
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import stat
import tarfile
import time
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

import zstandard

from .crypto import Keyring, PublicKey, SecretKey, SignatureError
from .deps import Atom, Dep, provide_parts

FORMAT_VERSION = 1
INFO, FILES, DATA, SIG = ".SYPINFO.json", ".FILES.json", "data.tar.zst", ".SIGNATURE.json"
NAME_RE = re.compile(r"^[a-z0-9@._+][a-z0-9@._+-]*$")
SCRIPT_KEYS = ("pre_install", "post_install", "pre_upgrade", "post_upgrade",
               "pre_remove", "post_remove")


class PackageError(Exception):
    pass


@dataclass
class PackageInfo:
    name: str
    version: str
    arch: str = "x86_64"
    summary: str = ""
    description: str = ""
    license: str = "unknown"          # SPDX-Ausdruck
    url: str = ""
    packager: str = ""
    depends: list[str] = field(default_factory=list)
    optdepends: list[str] = field(default_factory=list)
    provides: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    replaces: list[str] = field(default_factory=list)
    backup: list[str] = field(default_factory=list)   # Konfigdateien (relativ zu /)
    groups: list[str] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)  # freedesktop-Kategorien fürs Store
    scripts: dict[str, str] = field(default_factory=dict)
    origin: dict = field(default_factory=lambda: {"format": "native"})
    installed_size: int = 0
    build_date: int = 0
    format: int = FORMAT_VERSION

    def validate(self) -> None:
        if not NAME_RE.match(self.name):
            raise PackageError(f"Ungültiger Paketname: {self.name!r}")
        if not self.version or any(c in self.version for c in " /"):
            raise PackageError(f"Ungültige Version: {self.version!r}")
        for d in self.depends + self.conflicts:
            Dep.parse(d)
        for p in self.provides:
            provide_parts(p)
        unknown = set(self.scripts) - set(SCRIPT_KEYS)
        if unknown:
            raise PackageError(f"Unbekannte Skript-Hooks: {sorted(unknown)}")

    @classmethod
    def from_dict(cls, d: dict) -> "PackageInfo":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}

    @property
    def filename(self) -> str:
        return f"{self.name}-{self.version.replace(':', '~')}-{self.arch}.syp"

    @property
    def dep_objs(self) -> list[Dep]:
        return [Dep.parse(d) for d in self.depends]

    @property
    def conflict_objs(self) -> list[Atom]:
        return [a for d in self.conflicts for a in Dep.parse(d).alternatives]

    def provides_items(self) -> list[tuple[str, str | None]]:
        return [(self.name, self.version)] + [provide_parts(p) for p in self.provides]


def _canon(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def _digest(info_b: bytes, files_b: bytes, data_b: bytes) -> bytes:
    h = [hashlib.sha256(x).hexdigest() for x in (info_b, files_b, data_b)]
    return ("syp-v1\n" + "\n".join(h) + "\n").encode()


def _sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def scan_tree(tree: Path) -> list[dict]:
    """Dateiliste eines Staging-Baums (sortiert, Pfade relativ ohne führenden /)."""
    entries = []
    for dirpath, dirnames, filenames in os.walk(tree):
        dirnames.sort()
        for name in sorted(dirnames) + sorted(filenames):
            p = Path(dirpath) / name
            rel = p.relative_to(tree).as_posix()
            st = p.lstat()
            mode = stat.S_IMODE(st.st_mode)
            if stat.S_ISLNK(st.st_mode):
                entries.append({"path": rel, "type": "link", "target": os.readlink(p), "mode": 0o777})
            elif stat.S_ISDIR(st.st_mode):
                entries.append({"path": rel, "type": "dir", "mode": mode})
            elif stat.S_ISREG(st.st_mode):
                entries.append({"path": rel, "type": "file", "mode": mode, "size": st.st_size,
                                "sha256": _sha256_file(p)})
            else:
                raise PackageError(f"Nicht unterstützter Dateityp: {rel}")
    entries.sort(key=lambda e: e["path"])
    return entries


def _make_data(tree: Path, entries: list[dict], mtime: int) -> bytes:
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.PAX_FORMAT) as tf:
        for e in entries:
            p = tree / e["path"]
            ti = tarfile.TarInfo(e["path"])
            ti.uid = ti.gid = 0
            ti.uname = ti.gname = "root"
            ti.mtime = mtime
            ti.mode = e["mode"]
            if e["type"] == "dir":
                ti.type = tarfile.DIRTYPE
                tf.addfile(ti)
            elif e["type"] == "link":
                ti.type = tarfile.SYMTYPE
                ti.linkname = e["target"]
                tf.addfile(ti)
            else:
                ti.size = e["size"]
                with open(p, "rb") as f:
                    tf.addfile(ti, f)
    level = int(os.environ.get("SYP_ZSTD_LEVEL", "19"))
    return zstandard.ZstdCompressor(level=level, threads=-1).compress(raw.getvalue())


def build_package(tree: Path, info: PackageInfo, out_dir: Path,
                  signer: SecretKey | None = None) -> Path:
    """Packt einen Staging-Baum (DESTDIR) zu einem signierten .syp."""
    info.validate()
    mtime = int(os.environ.get("SOURCE_DATE_EPOCH", info.build_date or time.time()))
    info.build_date = info.build_date or mtime
    entries = scan_tree(tree)
    info.installed_size = sum(e.get("size", 0) for e in entries)
    for b in info.backup:
        if not any(e["path"] == b.lstrip("/") and e["type"] == "file" for e in entries):
            raise PackageError(f"backup-Datei nicht im Paket: {b}")
    info_b, files_b = _canon(info.to_dict()), _canon(entries)
    data_b = _make_data(tree, entries, mtime)
    sig = signer.sign(_digest(info_b, files_b, data_b)) if signer else {"key_id": None}
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / info.filename
    tmp = out.with_suffix(".syp.part")
    with tarfile.open(tmp, "w", format=tarfile.PAX_FORMAT) as tf:
        for name, payload in ((INFO, info_b), (FILES, files_b), (DATA, data_b),
                              (SIG, _canon(sig))):
            ti = tarfile.TarInfo(name)
            ti.size, ti.mtime, ti.mode = len(payload), mtime, 0o644
            tf.addfile(ti, io.BytesIO(payload))
    os.replace(tmp, out)
    return out


class SypPackage:
    def __init__(self, path: Path):
        self.path = Path(path)
        try:
            with tarfile.open(self.path, "r:") as tf:
                names = tf.getnames()
                if names != [INFO, FILES, DATA, SIG]:
                    raise PackageError(f"{path}: unerwarteter Aufbau {names}")
                self._info_b = tf.extractfile(INFO).read()
                self._files_b = tf.extractfile(FILES).read()
                self._data_b = tf.extractfile(DATA).read()
                self.signature = json.loads(tf.extractfile(SIG).read())
        except tarfile.TarError as e:
            raise PackageError(f"{path}: kein gültiges .syp ({e})") from e
        self.info = PackageInfo.from_dict(json.loads(self._info_b))
        self.files: list[dict] = json.loads(self._files_b)
        if self.info.format > FORMAT_VERSION:
            raise PackageError(f"{path}: Paketformat {self.info.format} wird nicht unterstützt")

    def verify(self, keyring: Keyring) -> PublicKey:
        if not self.signature.get("key_id"):
            raise SignatureError(f"{self.path.name}: Paket ist nicht signiert")
        return keyring.verify(_digest(self._info_b, self._files_b, self._data_b), self.signature)

    def extract(self, dest: Path) -> None:
        """Entpackt die Nutzdaten sicher und prüft jede Datei gegen .FILES.json."""
        expected = {e["path"]: e for e in self.files}
        raw = zstandard.ZstdDecompressor().decompressobj().decompress(self._data_b)
        links: set[str] = set()
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as tf:
            members = tf.getmembers()
            for m in members:
                pp = PurePosixPath(m.name)
                if pp.is_absolute() or ".." in pp.parts:
                    raise PackageError(f"Unsicherer Pfad im Paket: {m.name}")
                if any(m.name.startswith(l + "/") for l in links):
                    raise PackageError(f"Pfad hinter Symlink: {m.name}")
                if not (m.isfile() or m.isdir() or m.issym()):
                    raise PackageError(f"Nicht erlaubter Eintragstyp: {m.name}")
                if m.name not in expected:
                    raise PackageError(f"Nicht deklarierte Datei: {m.name}")
                if m.issym():
                    links.add(m.name)
            dest.mkdir(parents=True, exist_ok=True)
            tf.extractall(dest, members=members, filter="fully_trusted")
        for path, e in expected.items():
            if e["type"] == "file" and _sha256_file(dest / path) != e["sha256"]:
                raise PackageError(f"Prüfsumme falsch: {path}")
