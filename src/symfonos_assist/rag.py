"""Lokale Dokumentensuche (BM25) über das symfonOS-Handbuch – ohne Embeddings-Modell."""
from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

DOC_DIRS = [Path("/usr/share/doc/symfonos"), Path(__file__).resolve().parents[2] / "docs"]
_TOKEN = re.compile(r"[a-zäöüß0-9][a-zäöüß0-9_.+-]*", re.I)
STOP = set("der die das und oder ein eine einer eines ist im in zu mit auf für von den dem des "
           "nicht wie was wird werden kann bei als auch an es sich sie ich du the a an of to is".split())


def tokens(text: str) -> list[str]:
    out = []
    for t in _TOKEN.findall(text.lower()):
        t = t.strip(".-")
        if t and t not in STOP:
            out.append(t)
            if len(t) > 6:
                out.append(t[:6])          # grobes Stemming für deutsche Komposita/Flexion
    return out


@dataclass
class Chunk:
    source: str
    heading: str
    text: str


def split_markdown(path: Path) -> list[Chunk]:
    chunks, heading, buf = [], path.stem, []
    for line in path.read_text(errors="replace").splitlines():
        if line.startswith("#"):
            if buf:
                chunks.append(Chunk(path.name, heading, "\n".join(buf).strip()))
            heading, buf = line.lstrip("#").strip(), []
        else:
            buf.append(line)
    if buf:
        chunks.append(Chunk(path.name, heading, "\n".join(buf).strip()))
    return [c for c in chunks if c.text]


class Index:
    def __init__(self, chunks: list[Chunk]):
        self.chunks = chunks
        self.tf = [Counter(tokens(c.heading + " " + c.heading + " " + c.text)) for c in chunks]
        self.len = [sum(t.values()) for t in self.tf]
        self.avg = sum(self.len) / max(1, len(self.len))
        df = Counter(t for tf in self.tf for t in tf)
        n = len(chunks)
        self.idf = {t: math.log(1 + (n - d + 0.5) / (d + 0.5)) for t, d in df.items()}

    @classmethod
    def build(cls, dirs: list[Path] | None = None) -> "Index":
        chunks = []
        for d in dirs or DOC_DIRS:
            if d.is_dir():
                for p in sorted(d.rglob("*.md")):
                    chunks += split_markdown(p)
        return cls(chunks)

    def search(self, query: str, k: int = 4) -> list[tuple[float, Chunk]]:
        q = tokens(query)
        k1, b = 1.5, 0.75
        scored = []
        for i, tf in enumerate(self.tf):
            s = 0.0
            for t in q:
                f = tf.get(t)
                if f:
                    s += self.idf[t] * f * (k1 + 1) / (f + k1 * (1 - b + b * self.len[i] / self.avg))
            if s > 0:
                scored.append((s, self.chunks[i]))
        scored.sort(key=lambda x: -x[0])
        return scored[:k]

    def context(self, query: str, k: int = 4, max_chars: int = 3500) -> str:
        out, used = [], 0
        for _, c in self.search(query, k):
            block = f"[{c.source} › {c.heading}]\n{c.text}"
            if used + len(block) > max_chars:
                block = block[: max(0, max_chars - used)]
            out.append(block)
            used += len(block)
            if used >= max_chars:
                break
        return "\n\n".join(out)

    def dump(self, path: Path) -> None:
        path.write_text(json.dumps([c.__dict__ for c in self.chunks], ensure_ascii=False))
