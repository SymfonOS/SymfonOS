"""Lesender Zugriff für das Software-Center über die syp-Kommandozeile (JSON).

Änderungen laufen über `pkexec syp`; das GUI enthält keine eigene Paketlogik.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

SYP = os.environ.get("SYP_BIN", "syp")

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
    update: bool = False

    def section(self) -> str:
        cats = set(self.categories)
        for title, keys in SECTIONS:
            if cats & keys:
                return title
        return "Weitere"


class SypError(Exception):
    pass


class Backend:
    def __init__(self, root: str = "/"):
        self.root = root
        self.reload()

    def _syp(self, *args: str):
        r = subprocess.run([SYP, "-r", self.root, *args], capture_output=True, text=True)
        if r.returncode:
            raise SypError(r.stderr.strip().removeprefix("Fehler: ") or f"syp {args[0]} fehlgeschlagen")
        return json.loads(r.stdout or "null")

    def reload(self) -> None:
        self._updates, self._update_error = [], None
        try:
            plan = self._syp("-n", "upgrade", "--no-sync", "--json")
            self._updates = [(p["name"], p["old"] or "neu", p["version"]) for p in plan["install"]]
        except (SypError, json.JSONDecodeError) as e:
            self._update_error = str(e)
        try:
            self._search = self._syp("search", "--json")
        except SypError:
            self._search = []

    def items(self, apps_only: bool = True) -> list[StoreItem]:
        upd = {n for n, _, _ in self._updates}
        out = []
        for p in self._search:
            if apps_only and not p["categories"]:
                continue          # Bibliotheken/Basispakete nicht im Store-Katalog
            out.append(StoreItem(p["name"], p["version"], p["summary"], p.get("description", ""),
                                 p["license"], p["url"], p["categories"], p["origin"], p["repo"], p["size"],
                                 p["installed"], p["name"] in upd))
        return sorted(out, key=lambda i: i.name)

    def updates(self) -> tuple[list[tuple[str, str, str]], str | None]:
        return self._updates, self._update_error

    def history(self) -> list[dict]:
        try:
            rows = self._syp("history", "--json", "--limit", "50")
        except SypError:
            return []
        return [{"id": t["id"], "when": time.strftime("%d.%m.%Y %H:%M", time.localtime(t["ts"])),
                 "cmd": t["cmdline"], "status": t["status"], "snapshot": t["snapshot_pre"]} for t in rows]

    def last_sync(self) -> float | None:
        d = Path(self.root) / "var/lib/syp/sync"
        files = list(d.glob("*.index.json.zst")) if d.exists() else []
        return max((f.stat().st_mtime for f in files), default=None)

    @staticmethod
    def command(action: str, *names: str) -> list[str]:
        base = ["pkexec", SYP, "-y"]
        return {"install": base + ["install", *names], "remove": base + ["remove", *names],
                "upgrade": base + ["upgrade"], "sync": ["pkexec", SYP, "sync"],
                "rollback": base + ["rollback", *names]}[action]
