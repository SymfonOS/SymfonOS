"""Versionsvergleich (kompatibel zu pacman/rpm: epoch:version-release)."""
from __future__ import annotations

from dataclasses import dataclass
from functools import total_ordering


def rpmvercmp(a: str, b: str) -> int:
    """Segmentweiser Vergleich wie rpm/pacman. Rückgabe -1, 0, 1."""
    if a == b:
        return 0
    i = j = 0
    la, lb = len(a), len(b)
    while i < la and j < lb:
        while i < la and not a[i].isalnum():
            i += 1
        while j < lb and not b[j].isalnum():
            j += 1
        if i >= la or j >= lb:
            break
        si, sj = i, j
        isnum = a[i].isdigit()
        if isnum:
            while i < la and a[i].isdigit():
                i += 1
            while j < lb and b[j].isdigit():
                j += 1
        else:
            while i < la and a[i].isalpha():
                i += 1
            while j < lb and b[j].isalpha():
                j += 1
        sa, sb = a[si:i], b[sj:j]
        if not sb:
            # Segmenttyp unterschiedlich: Zahl ist neuer als Buchstabe
            return 1 if isnum else -1
        if isnum:
            na, nb = int(sa), int(sb)
            if na != nb:
                return 1 if na > nb else -1
        elif sa != sb:
            return 1 if sa > sb else -1
    if i >= la and j >= lb:
        return 0
    # Rest: Buchstaben-Suffix gilt als Vorabversion (1.0a < 1.0)
    rest_a = a[i:] if i < la else ""
    rest_b = b[j:] if j < lb else ""
    if (not rest_a and not rest_b[:1].isalpha()) or rest_a[:1].isalpha():
        return -1
    return 1


@total_ordering
@dataclass(frozen=True)
class Version:
    epoch: int
    ver: str
    rel: str | None

    @classmethod
    def parse(cls, s: str) -> "Version":
        s = s.strip()
        epoch = 0
        if ":" in s:
            e, s = s.split(":", 1)
            epoch = int(e) if e.isdigit() else 0
        rel = None
        if "-" in s:
            s, rel = s.rsplit("-", 1)
        return cls(epoch, s, rel)

    def __str__(self) -> str:
        out = f"{self.epoch}:" if self.epoch else ""
        out += self.ver
        if self.rel is not None:
            out += f"-{self.rel}"
        return out

    def cmp(self, other: "Version") -> int:
        if self.epoch != other.epoch:
            return 1 if self.epoch > other.epoch else -1
        c = rpmvercmp(self.ver, other.ver)
        if c or self.rel is None or other.rel is None:
            return c
        return rpmvercmp(self.rel, other.rel)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Version) and self.cmp(other) == 0

    def __lt__(self, other: "Version") -> bool:
        return self.cmp(other) < 0

    def __hash__(self) -> int:
        return hash(str(self))


def vercmp(a: str, b: str) -> int:
    return Version.parse(a).cmp(Version.parse(b))
