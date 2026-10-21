"""Befehlsvorschläge erkennen, Risiko einstufen, nur nach Bestätigung ausführen."""
from __future__ import annotations

import re
import shlex
import subprocess
from dataclasses import dataclass

READ_ONLY = {"journalctl", "systemctl", "syp", "df", "du", "free", "lsblk", "lspci", "lsusb",
             "uname", "ip", "ping", "cat", "ls", "grep", "snapper", "zramctl", "pw-top", "pactl",
             "wpctl", "groups", "id", "findmnt", "btrfs", "sensors", "inxi", "nmcli", "hostnamectl",
             "localectl", "timedatectl", "loginctl", "uptime", "which", "file", "head", "tail"}
READ_ONLY_SUB = {"systemctl": {"status", "--failed", "list-units", "is-active", "is-enabled", "show",
                               "cat", "list-timers"},
                 "syp": {"info", "search", "list", "files", "owns", "history", "doctor", "verify"},
                 "snapper": {"list"}, "btrfs": {"device", "filesystem"}, "nmcli": {"device", "connection", "general"}}
DANGER = [r"\brm\s+-[a-z]*r[a-z]*f?\s+(/|~|\$HOME|\*)", r"\bdd\b.*\bof=/dev/", r"\bmkfs", r"\bwipefs",
          r"\bchmod\s+-R\s+\d+\s+/", r"\bchown\s+-R\b.*\s/(\s|$)", r"curl[^|]*\|\s*(sudo\s+)?(ba)?sh",
          r"wget[^|]*\|\s*(sudo\s+)?(ba)?sh", r">\s*/dev/sd", r":\(\)\s*\{", r"\bsyp\b.*--allow-unsigned",
          r"\bsyp\b.*--trust-scripts", r"\bsnapper\b.*\bdelete\b", r"\bsyp\s+rollback\s+--system",
          r"\bparted\b", r"\bfdisk\b", r"\bshred\b", r"\bsystemctl\s+(mask|disable)\s+(sddm|NetworkManager|systemd)"]
BLOCK_RE = re.compile(r"```(?:bash|sh|shell|console)?\s*\n(.*?)```", re.S)


@dataclass
class Proposal:
    command: str
    risk: str            # lesend | ändernd | gefährlich

    @property
    def needs_strong_confirm(self) -> bool:
        return self.risk == "gefährlich"


def classify(cmd: str) -> str:
    if any(re.search(p, cmd) for p in DANGER):
        return "gefährlich"
    if re.search(r"[;&|]|\$\(|`|>", cmd.replace("2>&1", "")):
        return "ändernd"            # Verkettung/Umleitung nie als harmlos werten
    try:
        argv = shlex.split(cmd)
    except ValueError:
        return "gefährlich"
    if not argv:
        return "ändernd"
    if argv[0] in ("sudo", "pkexec", "doas"):
        return "ändernd"
    prog = argv[0]
    if prog in READ_ONLY:
        subs = READ_ONLY_SUB.get(prog)
        if subs is None or (len(argv) > 1 and argv[1] in subs):
            return "lesend"
    return "ändernd"


def extract(text: str) -> list[Proposal]:
    out = []
    for block in BLOCK_RE.findall(text):
        for line in block.splitlines():
            line = line.strip().removeprefix("$ ").strip()
            if line and not line.startswith("#"):
                out.append(Proposal(line, classify(line)))
    return out


def confirm_cli(p: Proposal, ask=input) -> bool:
    """Ausdrückliche Bestätigung; gefährliche Befehle verlangen das Wort AUSFÜHREN."""
    print(f"\n  Vorschlag ({p.risk}): {p.command}")
    if p.needs_strong_confirm:
        print("  ⚠ Dieser Befehl kann Daten unwiderruflich verändern.")
        return ask("  Zum Ausführen exakt AUSFÜHREN eingeben: ").strip() == "AUSFÜHREN"
    return ask("  Ausführen? [j/N] ").strip().lower() in ("j", "ja", "y", "yes")


def run(cmd: str, timeout: int = 120, max_chars: int = 6000) -> tuple[int, str]:
    """Führt einen bestätigten Befehl als aktueller Benutzer aus (Root nur über pkexec/sudo)."""
    try:
        r = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, timeout=timeout)
        out = (r.stdout + ("\n" + r.stderr if r.stderr else "")).strip()
        return r.returncode, out[-max_chars:]
    except subprocess.TimeoutExpired:
        return 124, f"Zeitüberschreitung nach {timeout}s"
