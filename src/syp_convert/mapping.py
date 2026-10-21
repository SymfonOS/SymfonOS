"""Abhängigkeits-Mapping deb/rpm/arch -> symfonOS-Paketnamen.

Reihenfolge je Abhängigkeit:
  1. ignore-Liste (Glob)           -> entfällt (z. B. rpmlib(*), debconf)
  2. explizite Tabelle             -> mapping.toml bzw. /etc/syp/convert-map.toml
  3. Regex-Regeln                  -> z. B. python3-foo -> python-foo
  4. Originalname                  -> falls im Ziel-Repo vorhanden
Zusätzlich werden die DT_NEEDED-Sonamen aller ELF-Dateien aufgelöst (so:<soname>,
Arch-Stil `libfoo.so=1-64` oder Dateibesitzer in der lokalen Datenbank).
Kandidaten werden gegen den Repo-Index geprüft; ohne Index gilt das Ergebnis als ungeprüft.
"""
from __future__ import annotations

import fnmatch
import re
import tomllib
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from importlib import resources
from pathlib import Path

from syp.version import Version


@dataclass(frozen=True)
class SourceAtom:
    name: str
    op: str | None = None       # syp-Operatoren: = >= <= < >
    version: str | None = None


@dataclass(frozen=True)
class SourceDep:
    alternatives: tuple[SourceAtom, ...]

    def __str__(self) -> str:
        return " | ".join(f"{a.name}{a.op or ''}{a.version or ''}" for a in self.alternatives)


@dataclass
class Report:
    package: str
    source_format: str
    mapped: list[tuple[str, str]] = field(default_factory=list)
    ignored: list[str] = field(default_factory=list)
    unresolved: list[dict] = field(default_factory=list)
    optional_missing: list[str] = field(default_factory=list)
    unverified: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    depends: list[str] = field(default_factory=list)
    output: str = ""

    @property
    def ok(self) -> bool:
        return not self.unresolved

    def to_dict(self) -> dict:
        return asdict(self)

    def text(self) -> str:
        out = [f"Paket: {self.package} ({self.source_format}) -> {self.output or '–'}"]
        for src, dst in self.mapped:
            out.append(f"  ✓ {src}  ->  {dst}")
        for u in self.unresolved:
            out.append(f"  ✗ FEHLT: {u['dep']}  ({u['kind']})")
        for o in self.optional_missing:
            out.append(f"  ○ optional, nicht verfügbar: {o}")
        for u in self.unverified:
            out.append(f"  ? ungeprüft (kein Repo-Index): {u}")
        if self.ignored:
            out.append(f"  – ignoriert: {', '.join(self.ignored)}")
        for w in self.warnings:
            out.append(f"  ! {w}")
        return "\n".join(out)


GLIBC_SONAMES = re.compile(r"^(libc|libm|libdl|librt|libpthread|libresolv|libutil|libanl|"
                           r"libnsl|libmvec|ld-linux(-x86-64|-aarch64)?)\.so\.\d+$")


class Mapper:
    def __init__(self, available: list | None = None, db=None, extra_map: Path | None = None):
        data = tomllib.loads((resources.files("syp_convert") / "mapping.toml").read_text())
        if extra_map and extra_map.exists():
            for k, v in tomllib.loads(extra_map.read_text()).items():
                data.setdefault(k, {}).update(v) if isinstance(v, dict) else data.__setitem__(k, v)
        self.table: dict[str, dict[str, str]] = {f: data.get(f, {}) for f in ("deb", "rpm", "arch")}
        self.files: dict[str, str] = data.get("files", {})
        self.ignore: dict[str, list[str]] = data.get("ignore", {})
        self.rules = {f: [(re.compile(p), r) for p, r in data.get("rules", {}).get(f, [])]
                      for f in ("deb", "rpm", "arch")}
        self.db = db
        self.by_provide: dict[str, list] | None = None
        if available is not None:
            self.by_provide = defaultdict(list)
            for p in available:
                for n, v in p.info.provides_items():
                    self.by_provide[n].append(v)

    # -- Kandidaten -----------------------------------------------------------------
    def candidates(self, fmt: str, name: str) -> list[str] | None:
        """None = ignorieren, sonst Liste möglicher syp-Namen (beste zuerst)."""
        if any(fnmatch.fnmatchcase(name, g) for g in self.ignore.get(fmt, [])):
            return None
        out: list[str] = []
        if name.startswith("/"):
            if name in self.files:
                out.append(self.files[name])
            if self.db is not None:
                rel = name.lstrip("/").replace("bin/", "usr/bin/", 1) if name.startswith("/bin/") \
                    else name.lstrip("/")
                out += self.db.owners(rel)
            return out
        if name in self.table.get(fmt, {}):
            v = self.table[fmt][name]
            if v == "":
                return None
            out.append(v)
        for rx, repl in self.rules.get(fmt, []):
            if rx.match(name):
                r = rx.sub(repl, name)
                if r == "!ignore":
                    return None if not out else out
                out.append(r if r.startswith("so:") else r.lower().replace("::", "-"))
        out.append(name)
        return list(dict.fromkeys(out))

    def _resolve(self, cand: str) -> str | None:
        if cand.startswith("so:"):
            so = cand[3:]
            return "glibc" if GLIBC_SONAMES.match(so) else self._soname_target(so)
        return cand if self.exists(cand) else None

    def exists(self, name: str) -> bool:
        return self.by_provide is not None and name in self.by_provide

    def _fmt_atom(self, name: str, a: SourceAtom, fmt: str, keep_versions: bool) -> str:
        if not a.op or not a.version:
            return name
        if fmt == "arch" or keep_versions:
            return f"{name}{a.op}{a.version}"
        if a.op in (">=", ">"):
            v = Version.parse(a.version)
            return f"{name}>={v.ver}"    # Epoch/Revision anderer Distros sind nicht vergleichbar
        return name

    def map_deps(self, fmt: str, deps: list[SourceDep], report: Report, optional: bool = False,
                 keep_versions: bool = False) -> list[str]:
        result = []
        for dep in deps:
            alts, ignored, unverified = [], False, []
            for a in dep.alternatives:
                cands = self.candidates(fmt, a.name)
                if cands is None:
                    ignored = True
                    continue
                hit = next(filter(None, (self._resolve(c) for c in cands)), None)
                if hit:
                    alts.append(self._fmt_atom(hit, a, fmt, keep_versions))
                elif self.by_provide is None and cands:
                    unverified.append(self._fmt_atom(cands[0], a, fmt, keep_versions))
            if alts:
                s = " | ".join(dict.fromkeys(alts))
                report.mapped.append((str(dep), s))
                result.append(s)
            elif unverified:
                s = " | ".join(dict.fromkeys(unverified))
                report.unverified.append(f"{dep} -> {s}")
                result.append(s)
            elif ignored:
                report.ignored.append(str(dep))
            elif optional:
                report.optional_missing.append(str(dep))
            else:
                report.unresolved.append({"dep": str(dep), "kind": "Abhängigkeit"})
        return result

    def map_sonames(self, sonames: list[str], report: Report) -> list[str]:
        out = []
        for so in sonames:
            if GLIBC_SONAMES.match(so):
                target = "glibc"
            else:
                target = self._soname_target(so)
            if target:
                report.mapped.append((f"ELF {so}", target))
                out.append(target)
            elif self.by_provide is None and self.db is None:
                report.unverified.append(f"ELF {so} -> so:{so}")
                out.append(f"so:{so}")
            else:
                report.unresolved.append({"dep": so, "kind": "Bibliothek (DT_NEEDED)"})
        return out

    def _soname_target(self, so: str) -> str | None:
        if self.exists(f"so:{so}"):
            return f"so:{so}"
        m = re.match(r"^(.+\.so)\.(\d+)", so)
        if m and self.by_provide is not None:
            base, major = m.groups()
            for v in self.by_provide.get(base, []):
                if v and v.split("-")[0] == major:
                    return f"{base}={v}"
        if self.db is not None:
            for d in ("usr/lib/", "usr/lib64/"):
                owners = self.db.owners(d + so)
                if owners:
                    return owners[0]
        return None
