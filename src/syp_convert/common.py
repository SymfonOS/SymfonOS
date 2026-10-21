"""Gemeinsame Logik: Zwischenformat, Versionen, Pfad-Umzug, Skripte, Paketbau."""
from __future__ import annotations

import os
import re
import shutil
import stat
from dataclasses import dataclass, field
from pathlib import Path

from syp.crypto import SecretKey
from syp.package import PackageInfo, build_package

from .elf import dynamic_info
from .mapping import Mapper, Report, SourceDep


class ConvertError(Exception):
    pass


@dataclass
class Converted:
    """Format-neutrales Zwischenergebnis eines Parsers."""
    fmt: str                     # deb | rpm | arch
    name: str
    version: str                 # bereits syp-normalisiert
    arch: str
    tree: Path                   # entpackte Nutzdaten
    summary: str = ""
    description: str = ""
    license: str = "unknown"
    url: str = ""
    packager: str = ""
    depends: list[SourceDep] = field(default_factory=list)
    optdepends: list[SourceDep] = field(default_factory=list)
    provides: list[str] = field(default_factory=list)
    conflicts: list[SourceDep] = field(default_factory=list)
    replaces: list[str] = field(default_factory=list)
    backup: list[str] = field(default_factory=list)
    scripts: dict[str, str] = field(default_factory=dict)
    source_version: str = ""
    warnings: list[str] = field(default_factory=list)


ARCH_MAP = {"amd64": "x86_64", "x86_64": "x86_64", "arm64": "aarch64", "aarch64": "aarch64",
            "all": "any", "noarch": "any", "any": "any", "i386": "i686", "i686": "i686"}

# Merged-/usr-Layout von symfonOS (wie Arch): alles nach /usr/bin und /usr/lib
RELOCATE = [("bin/", "usr/bin/"), ("sbin/", "usr/bin/"), ("usr/sbin/", "usr/bin/"),
            ("lib64/", "usr/lib/"), ("lib/x86_64-linux-gnu/", "usr/lib/"), ("lib/", "usr/lib/"),
            ("usr/lib64/", "usr/lib/"), ("usr/lib/x86_64-linux-gnu/", "usr/lib/"),
            ("usr/lib/aarch64-linux-gnu/", "usr/lib/")]
NAME_SANITIZE = re.compile(r"[^a-z0-9@._+-]")


def norm_version(v: str) -> str:
    """deb/rpm-Version -> syp-Version (epoch:ver-rel). `~` = Vorabversion, `^` = Nachversion."""
    v = v.strip()
    epoch = ""
    if re.match(r"^\d+:", v):
        epoch, v = v.split(":", 1)
        epoch += ":"
    rel = None
    if "-" in v:
        v, rel = v.rsplit("-", 1)
    v = v.replace("-", "_")
    v = re.sub(r"~(?=[A-Za-z])", "", v)
    v = v.replace("~", "pre").replace("^", ".0.")
    if rel is not None:
        rel = re.sub(r"[~^:-]", ".", rel)
    return f"{epoch}{v}" + (f"-{rel}" if rel else "")


def norm_name(n: str) -> str:
    return NAME_SANITIZE.sub("-", n.lower())


def relocate(tree: Path, warnings: list[str]) -> None:
    """Verschiebt Pfade ins Merged-/usr-Layout; vermeidet Symlink-Kollisionen."""
    for src_prefix, dst_prefix in RELOCATE:
        src = tree / src_prefix.rstrip("/")
        if not src.exists() or src.is_symlink():
            continue
        dst = tree / dst_prefix.rstrip("/")
        for p in sorted(src.rglob("*"), key=lambda p: len(p.parts), reverse=True):
            if p.is_dir() and not p.is_symlink():
                continue
            target = dst / p.relative_to(src)
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() or target.is_symlink():
                warnings.append(f"Pfadkollision bei Umzug: /{p.relative_to(tree)} -> /{target.relative_to(tree)}")
                continue
            os.rename(p, target)
        shutil.rmtree(src, ignore_errors=True)
        warnings.append(f"Pfade verschoben: /{src_prefix} -> /{dst_prefix}")


def audit_tree(tree: Path, warnings: list[str]) -> None:
    for p in tree.rglob("*"):
        rel = "/" + p.relative_to(tree).as_posix()
        if p.is_symlink() or not p.is_file():
            continue
        mode = p.stat().st_mode
        if mode & (stat.S_ISUID | stat.S_ISGID):
            warnings.append(f"setuid/setgid-Datei: {rel}")
    top = {p.name for p in tree.iterdir()} if tree.exists() else set()
    odd = top - {"usr", "etc", "opt", "var", "srv"}
    if odd:
        warnings.append(f"Ungewöhnliche Top-Level-Verzeichnisse: {', '.join(sorted(odd))}")
    if (tree / "opt").exists():
        warnings.append("Paket installiert nach /opt (typisch für Fremdsoftware)")


def scan_elf(tree: Path) -> tuple[set[str], set[str]]:
    """(benötigte Sonamen, bereitgestellte Sonamen) aller ELF-Dateien im Baum."""
    needed, provided = set(), set()
    for p in tree.rglob("*"):
        if p.is_file() and not p.is_symlink():
            n, so = dynamic_info(p)
            needed.update(n)
            if so:
                provided.add(so)
    # Bibliotheken, die nur per Symlink-Name vorliegen
    for p in tree.rglob("*.so*"):
        if p.is_symlink() and ".so." in p.name:
            provided.add(p.name)
    return needed, provided


def desktop_categories(tree: Path) -> list[str]:
    cats: list[str] = []
    for d in (tree / "usr/share/applications").glob("*.desktop"):
        for line in d.read_text(errors="replace").splitlines():
            if line.startswith("Categories="):
                cats += [c for c in line.split("=", 1)[1].split(";") if c and c not in cats]
    return cats


def wrap_script(body: str, args: str) -> str:
    """Bettet ein Fremdskript ein; Skripte mit Shebang laufen mit ihrem eigenen Interpreter."""
    if not body.startswith("#!"):
        return f"set -- {args}\n{body.rstrip()}\n"
    marker = "__SYP_SCRIPT_EOF__"
    if marker in body:
        raise ConvertError("Skript enthält Heredoc-Markierung")
    return (f'f=$(mktemp)\ntrap \'rm -f "$f"\' EXIT\ncat >"$f" <<\'{marker}\'\n{body.rstrip()}\n'
            f'{marker}\nchmod 700 "$f"\n"$f" {args}\n')


def finalize(c: Converted, mapper: Mapper, out_dir: Path, signer: SecretKey | None,
             mark_reviewed: bool = False, keep_versions: bool = False) -> tuple[Path, Report]:
    c.arch = ARCH_MAP.get(c.arch, c.arch)
    relocate(c.tree, c.warnings)
    audit_tree(c.tree, c.warnings)
    needed, provided = scan_elf(c.tree)
    report = Report(package=c.name, source_format=c.fmt, warnings=list(c.warnings))
    provides = list(dict.fromkeys(c.provides + [f"so:{s}" for s in sorted(provided)]))
    depends = mapper.map_deps(c.fmt, c.depends, report, keep_versions=keep_versions)
    depends += mapper.map_sonames(sorted(needed - provided), report)
    depends = [d for d in dict.fromkeys(depends) if d.split("|")[0].split(">")[0].split("=")[0]
               .split("<")[0].strip() != c.name]
    optdeps = mapper.map_deps(c.fmt, c.optdepends, report, optional=True, keep_versions=False)
    conflicts = mapper.map_deps(c.fmt, c.conflicts, report, optional=True, keep_versions=True)
    if c.scripts:
        report.warnings.append(
            f"Paket enthält Skripte ({', '.join(sorted(c.scripts))}) – vor Freigabe prüfen")
        for s in c.scripts.values():
            for helper in ("debconf", "dpkg-maintscript-helper", "update-alternatives",
                           "deb-systemd-helper", "rpm ", "dpkg "):
                if helper in s:
                    report.warnings.append(f"Skript nutzt distro-spezifisches Werkzeug: {helper.strip()}")
    info = PackageInfo(
        name=c.name, version=c.version, arch=c.arch, summary=c.summary,
        description=c.description, license=c.license or "unknown", url=c.url,
        packager=c.packager, depends=depends, optdepends=optdeps, provides=provides,
        conflicts=[x for x in conflicts if not x.startswith(c.name)],
        replaces=c.replaces, backup=[b.lstrip("/") for b in c.backup
                                     if (c.tree / b.lstrip("/")).is_file()],
        categories=desktop_categories(c.tree), scripts=c.scripts,
        origin={"format": c.fmt, "source_version": c.source_version,
                "scripts_reviewed": mark_reviewed or not c.scripts,
                "unresolved": [u["dep"] for u in report.unresolved]},
    )
    if info.license == "unknown":
        report.warnings.append("Lizenz unbekannt – bitte manuell setzen (--license)")
    path = build_package(c.tree, info, out_dir, signer)
    report.output = str(path)
    report.depends = depends
    return path, report
