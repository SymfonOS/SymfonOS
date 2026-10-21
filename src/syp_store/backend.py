"""Lesender Zugriff auf Repos/DB für das Software-Center; Änderungen laufen über `pkexec syp`."""
from __future__ import annotations

import time
from dataclasses import dataclass

from syp import repo
from syp.config import Config
from syp.db import Database
from syp.solver import Solver, SolverError
from syp.version import Version

# freedesktop-Kategorie -> Store-Rubrik
SECTIONS = [
    ("Grafik & Illustration", {"Graphics", "2DGraphics", "VectorGraphics", "RasterGraphics"}),
    ("Foto", {"Photography"}),
    ("Video", {"Video", "AudioVideoEditing"}),
    ("Audio & Musik", {"Audio", "Music", "Midi", "Mixer", "Sequencer"}),
    ("3D & Animation", {"3DGraphics", "Animation"}),
    ("Büro", {"Office", "WordProcessor", "Spreadsheet", "Presentation"}),
    ("Internet", {"Network", "WebBrowser", "Email", "Chat"}),
    ("Entwicklung", {"Development", "IDE"}),
    ("System", {"System", "Settings", "Utility"}),
]


@dataclass
class StoreItem:
    name: str
    version: str
    summary: str
    description: str
    license: str
    url: str
    categories: list[str]
    origin: str
    repo: str
    size: int
    installed: str | None

    @property
    def update(self) -> bool:
        return bool(self.installed) and Version.parse(self.version) > Version.parse(self.installed)

    def section(self) -> str:
        cats = set(self.categories)
        for title, keys in SECTIONS:
            if cats & keys:
                return title
        return "Weitere"


class Backend:
    def __init__(self, root: str = "/"):
        self.cfg = Config.load(root)
        self.reload()

    def reload(self) -> None:
        self.db = Database(self.cfg.db_path)
        self.available = repo.load_synced(self.cfg)
        self.installed = self.db.installed()

    def items(self, apps_only: bool = True) -> list[StoreItem]:
        newest: dict[str, object] = {}
        for p in self.available:
            cur = newest.get(p.name)
            if cur is None or Version.parse(p.version) > Version.parse(cur.version):
                newest[p.name] = p
        out = []
        for p in newest.values():
            if apps_only and not p.info.categories:
                continue          # Bibliotheken/Basispakete nicht im Store-Katalog
            inst = self.installed.get(p.name)
            out.append(StoreItem(p.name, p.version, p.info.summary, p.info.description,
                                 p.info.license, p.info.url, p.info.categories,
                                 p.info.origin.get("format", "native"), p.repo, p.size,
                                 inst.version if inst else None))
        return sorted(out, key=lambda i: i.name)

    def updates(self) -> tuple[list[tuple[str, str, str]], str | None]:
        try:
            plan = Solver(self.available, self.installed).upgrade()
        except SolverError as e:
            return [], str(e)
        return [(p.name, self.installed[p.name].version if p.name in self.installed else "neu",
                 p.version) for p in plan.install], None

    def history(self) -> list[dict]:
        rows = []
        for t in self.db.history(50):
            rows.append({"id": t["id"], "when": time.strftime("%d.%m.%Y %H:%M", time.localtime(t["ts"])),
                         "cmd": t["cmdline"], "status": t["status"], "snapshot": t["snapshot_pre"]})
        return rows

    def last_sync(self) -> float | None:
        files = list(self.cfg.sync_dir.glob("*.index.json.zst")) if self.cfg.sync_dir.exists() else []
        return max((f.stat().st_mtime for f in files), default=None)

    @staticmethod
    def command(action: str, *names: str) -> list[str]:
        base = ["pkexec", "syp", "-y"]
        return {"install": base + ["install", *names], "remove": base + ["remove", *names],
                "upgrade": base + ["upgrade"], "sync": ["pkexec", "syp", "sync"],
                "rollback": base + ["rollback", *names]}[action]

