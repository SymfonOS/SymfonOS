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
    icon: str = ""          # Icon= des Programmstarters (installierte Pakete)

    def section(self) -> str:
        cats = set(self.categories)
        for title, keys in SECTIONS:
            if cats & keys:
                return title
        return "Weitere"


class SypError(Exception):
    pass


GENERIC = {"Qt", "KDE", "GTK", "GNOME"}


def desktop_entry(text: str) -> dict[str, str]:
    """Schlüssel des [Desktop Entry] eines sichtbaren Programmstarters (leer bei NoDisplay/Hidden/Type≠Application)."""
    entry, kv = False, {}
    for line in text.splitlines():
        if line.startswith("["):
            entry = line.strip() == "[Desktop Entry]"
        elif entry and "=" in line:
            k, v = line.split("=", 1)
            kv.setdefault(k.strip(), v.strip())
    if kv.get("Type") != "Application" or kv.get("NoDisplay") == "true" or kv.get("Hidden") == "true":
        return {}
    return kv


def desktop_categories(text: str) -> list[str]:
    """Kategorien eines sichtbaren Programmstarters (leer bei NoDisplay/Hidden oder Type≠Application)."""
    kv = desktop_entry(text)
    return [c for c in kv.get("Categories", "").split(";") if c and c not in GENERIC and not c.startswith("X-")]


class Backend:
    def __init__(self, root: str = "/"):
        self.root = root
        self.reload()

    def _syp(self, *args: str):
        try:
            r = subprocess.run([SYP, "-r", self.root, *args], capture_output=True, text=True, timeout=300)
        except (OSError, subprocess.TimeoutExpired) as e:   # syp fehlt oder hängt: Fehler zeigen statt Absturz
            raise SypError(f"syp {args[0]} nicht ausführbar: {e}") from e
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
        except (SypError, json.JSONDecodeError) as e:
            # sonst sähe der Store einfach leer aus; die Meldung erscheint wie Update-Fehler
            self._search = []
            self._update_error = self._update_error or f"Katalog nicht lesbar: {e}"
        self._local, self._icons = self._local_apps()

    def _local_apps(self) -> tuple[dict[str, list[str]], dict[str, str]]:
        """Kategorien und Symbole installierter Pakete aus ihren Programmstartern. Die Kategorien braucht es für
        Pakete, die ohne gebaut wurden (syp build übernimmt sie erst seit 1.0.0-6 selbst)."""
        apps = Path(self.root) / "usr/share/applications"
        entries: dict[Path, dict[str, str]] = {}
        for f in sorted(apps.glob("*.desktop")) if apps.is_dir() else []:
            try:
                kv = desktop_entry(f.read_text(errors="replace"))
            except OSError:
                continue
            if kv:
                entries[f] = kv
        if not entries:
            return {}, {}
        r = subprocess.run([SYP, "-r", self.root, "owns", *map(str, entries)], capture_output=True, text=True)
        cats: dict[str, list[str]] = {}
        icons: dict[str, str] = {}
        for line in r.stdout.splitlines():
            path, sep, owners = line.partition(" gehört zu ")
            if not sep or owners == "keinem Paket":
                continue
            kv = entries.get(Path(self.root) / path.lstrip("/"), {})
            c = [x for x in kv.get("Categories", "").split(";") if x and x not in GENERIC and not x.startswith("X-")]
            for pkg in owners.split(", "):
                cats.setdefault(pkg, [])
                cats[pkg] += [x for x in c if x not in cats[pkg]]
                if kv.get("Icon"):
                    icons.setdefault(pkg, kv["Icon"])
        return cats, icons

    def items(self, apps_only: bool = True) -> list[StoreItem]:
        upd = {n for n, _, _ in self._updates}
        out = []
        for p in self._search:
            cats = p["categories"] or (self._local.get(p["name"], []) if p["installed"] else [])
            if apps_only and not cats:
                continue          # Bibliotheken/Basispakete nicht im Store-Katalog
            out.append(StoreItem(p["name"], p["version"], p["summary"], p.get("description", ""),
                                 p["license"], p["url"], cats, p["origin"], p["repo"], p["size"],
                                 p["installed"], p["name"] in upd, self._icons.get(p["name"], "")))
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
