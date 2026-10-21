"""Abhängigkeits-Ausdrücke: `name`, `name>=1.2`, `a | b>=2`, `so:libfoo.so.1`."""
from __future__ import annotations

import re
from dataclasses import dataclass

from .version import Version

_ATOM = re.compile(r"^\s*([^\s<>=|]+)\s*(?:(>=|<=|=|<|>)\s*([^\s|]+))?\s*$")
OPS = (">=", "<=", "=", "<", ">")


@dataclass(frozen=True)
class Atom:
    name: str
    op: str | None = None
    version: str | None = None

    @classmethod
    def parse(cls, s: str) -> "Atom":
        m = _ATOM.match(s)
        if not m:
            raise ValueError(f"Ungültiger Abhängigkeitsausdruck: {s!r}")
        return cls(m.group(1), m.group(2), m.group(3))

    def __str__(self) -> str:
        return f"{self.name}{self.op}{self.version}" if self.op else self.name

    def matches_version(self, version: str | None) -> bool:
        if self.op is None:
            return True
        if version is None:
            return False  # unversioniertes Provide erfüllt keine versionierte Abhängigkeit
        c = Version.parse(version).cmp(Version.parse(self.version or ""))
        return {
            "=": c == 0, ">=": c >= 0, "<=": c <= 0, ">": c > 0, "<": c < 0,
        }[self.op]


@dataclass(frozen=True)
class Dep:
    """Disjunktion von Atomen (Alternativen)."""
    alternatives: tuple[Atom, ...]

    @classmethod
    def parse(cls, s: str) -> "Dep":
        return cls(tuple(Atom.parse(p) for p in s.split("|")))

    def __str__(self) -> str:
        return " | ".join(str(a) for a in self.alternatives)


def provide_parts(s: str) -> tuple[str, str | None]:
    """`name=1.0` -> ("name", "1.0"); `name` -> ("name", None)."""
    if "=" in s:
        n, v = s.split("=", 1)
        return n.strip(), v.strip()
    return s.strip(), None
