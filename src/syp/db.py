"""Lokale Paketdatenbank (SQLite) inkl. Transaktionshistorie für Rollbacks."""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from .package import PackageInfo

SCHEMA = """
CREATE TABLE IF NOT EXISTS packages (
    name TEXT PRIMARY KEY,
    version TEXT NOT NULL,
    info TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT 'explicit',
    installed_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS files (
    path TEXT NOT NULL,
    pkg TEXT NOT NULL REFERENCES packages(name) ON DELETE CASCADE,
    type TEXT NOT NULL,
    sha256 TEXT,
    PRIMARY KEY (path, pkg)
);
CREATE INDEX IF NOT EXISTS files_path ON files(path);
CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    cmdline TEXT NOT NULL,
    status TEXT NOT NULL,           -- running | done | failed
    before TEXT NOT NULL,           -- {name: version}
    after TEXT,
    snapshot_pre TEXT,
    snapshot_post TEXT
);
"""


class InstalledPackage:
    def __init__(self, info: PackageInfo, reason: str, installed_at: int):
        self.info, self.reason, self.installed_at = info, reason, installed_at

    @property
    def name(self) -> str:
        return self.info.name

    @property
    def version(self) -> str:
        return self.info.version


class Database:
    def __init__(self, path: Path):
        self.readonly = False
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            self.conn = sqlite3.connect(path, isolation_level=None)
            self.conn.execute("PRAGMA foreign_keys=ON")
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.executescript(SCHEMA)
        except (OSError, sqlite3.OperationalError):
            # Ohne Schreibrechte (normaler Benutzer): lesend öffnen bzw. leere In-Memory-DB
            self.readonly = True
            uri = f"file:{path}?mode=ro&immutable=1" if path.exists() else "file::memory:"
            self.conn = sqlite3.connect(uri, uri=True, isolation_level=None)
            if not path.exists():
                self.conn.executescript(SCHEMA)

    # --- Pakete -------------------------------------------------------------
    def installed(self) -> dict[str, InstalledPackage]:
        rows = self.conn.execute("SELECT info, reason, installed_at FROM packages")
        return {p.name: p for p in (InstalledPackage(PackageInfo.from_dict(json.loads(i)), r, t)
                                    for i, r, t in rows)}

    def get(self, name: str) -> InstalledPackage | None:
        return self.installed().get(name)

    def state(self) -> dict[str, str]:
        return dict(self.conn.execute("SELECT name, version FROM packages"))

    def files_of(self, name: str) -> list[dict]:
        rows = self.conn.execute("SELECT path, type, sha256 FROM files WHERE pkg=? ORDER BY path", (name,))
        return [{"path": p, "type": t, "sha256": s} for p, t, s in rows]

    def owners(self, path: str) -> list[str]:
        return [r[0] for r in self.conn.execute("SELECT pkg FROM files WHERE path=?", (path,))]

    def put(self, info: PackageInfo, files: list[dict], reason: str) -> None:
        with self.conn:
            self.conn.execute("BEGIN")
            self.conn.execute("DELETE FROM files WHERE pkg=?", (info.name,))
            self.conn.execute(
                "INSERT INTO packages(name, version, info, reason, installed_at) VALUES(?,?,?,?,?) "
                "ON CONFLICT(name) DO UPDATE SET version=excluded.version, info=excluded.info, "
                "installed_at=excluded.installed_at",
                (info.name, info.version, json.dumps(info.to_dict()), reason, int(time.time())))
            self.conn.executemany(
                "INSERT INTO files(path, pkg, type, sha256) VALUES(?,?,?,?)",
                [(f["path"], info.name, f["type"], f.get("sha256")) for f in files])

    def delete(self, name: str) -> None:
        self.conn.execute("DELETE FROM packages WHERE name=?", (name,))

    def set_reason(self, name: str, reason: str) -> None:
        self.conn.execute("UPDATE packages SET reason=? WHERE name=?", (reason, name))

    # --- Transaktionen ------------------------------------------------------
    def tx_begin(self, cmdline: str, snapshot_pre: str | None) -> int:
        cur = self.conn.execute(
            "INSERT INTO transactions(ts, cmdline, status, before, snapshot_pre) VALUES(?,?,?,?,?)",
            (int(time.time()), cmdline, "running", json.dumps(self.state()), snapshot_pre))
        return cur.lastrowid

    def tx_end(self, tx: int, status: str, snapshot_post: str | None = None) -> None:
        self.conn.execute("UPDATE transactions SET status=?, after=?, snapshot_post=? WHERE id=?",
                          (status, json.dumps(self.state()), snapshot_post, tx))

    def history(self, limit: int = 50) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id, ts, cmdline, status, before, after, snapshot_pre, snapshot_post "
            "FROM transactions ORDER BY id DESC LIMIT ?", (limit,))
        keys = ("id", "ts", "cmdline", "status", "before", "after", "snapshot_pre", "snapshot_post")
        out = []
        for r in rows:
            d = dict(zip(keys, r))
            d["before"] = json.loads(d["before"])
            d["after"] = json.loads(d["after"]) if d["after"] else None
            out.append(d)
        return out

    def tx(self, tx_id: int) -> dict | None:
        return next((t for t in self.history(10**9) if t["id"] == tx_id), None)
