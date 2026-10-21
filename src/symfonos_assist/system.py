"""Lesende Systemdiagnose + regelbasierte Log-Analyse (funktioniert auch ohne KI-Modell)."""
from __future__ import annotations

import platform
import re
import shutil
import subprocess
from dataclasses import dataclass


def _run(cmd: list[str], timeout: int = 10) -> str:
    if shutil.which(cmd[0]) is None:
        return ""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return (r.stdout or r.stderr).strip()
    except (subprocess.TimeoutExpired, OSError):
        return ""


def os_release() -> str:
    try:
        with open("/etc/os-release") as f:
            d = dict(l.rstrip().split("=", 1) for l in f if "=" in l)
        return d.get("PRETTY_NAME", "").strip('"')
    except OSError:
        return ""


def summary() -> str:
    """Kurzer Systemsteckbrief für den Prompt (keine persönlichen Daten)."""
    lines = [f"System: {os_release() or 'unbekannt'}", f"Kernel: {platform.release()}",
             "Desktop: KDE Plasma (Wayland)"]
    pk = _run(["syp", "list"])
    if pk:
        lines.append(f"Installierte Pakete: {len(pk.splitlines())}")
    gpu = [l for l in _run(["lspci"]).splitlines() if re.search(r"VGA|3D|Display", l)]
    if gpu:
        lines.append("GPU: " + "; ".join(g.split(": ", 1)[-1] for g in gpu))
    df = _run(["df", "-h", "--output=avail,pcent", "/"]).splitlines()
    if len(df) > 1:
        lines.append(f"Freier Speicher /: {df[1].strip()}")
    failed = _run(["systemctl", "--failed", "--no-legend", "--plain"])
    if failed:
        lines.append("Fehlgeschlagene Dienste: " + ", ".join(l.split()[0] for l in failed.splitlines()))
    return "\n".join(lines)


def journal_errors(lines: int = 120) -> str:
    return _run(["journalctl", "-b", "-p", "3", "--no-pager", "-o", "short-monotonic", "-n", str(lines)])


def collect_logs() -> str:
    parts = {"journal (Fehler, aktueller Boot)": journal_errors(),
             "systemctl --failed": _run(["systemctl", "--failed", "--no-pager"]),
             "syp doctor": _run(["syp", "doctor"]),
             "Benutzerdienste mit Fehlern": _run(["systemctl", "--user", "--failed", "--no-pager"])}
    return "\n\n".join(f"### {k}\n{v}" for k, v in parts.items() if v)


@dataclass
class Finding:
    pattern: str
    title: str
    advice: str
    commands: tuple[str, ...] = ()


RULES = [
    Finding(r"No space left on device|ENOSPC", "Datenträger voll",
            "Paket-Cache aufräumen und alte Snapshots löschen.",
            ("syp clean --keep 1", "snapper list", "df -h /")),
    Finding(r"firmware: failed to load|Direct firmware load .* failed", "Fehlende Firmware",
            "Firmware-Paket installieren bzw. aktualisieren.", ("syp install linux-firmware sof-firmware",)),
    Finding(r"NVRM|nvidia.*(failed|error)", "NVIDIA-Treiberproblem",
            "Hardware-Einrichtung erneut ausführen; sie wählt den passenden Treiber.",
            ("pkexec symfonos-hwsetup",)),
    Finding(r"xrun|XRUN|underrun", "Audio-Aussetzer (XRUNs)",
            "Puffer erhöhen oder Echtzeit-Rechte prüfen (Gruppe realtime).",
            ("groups", "pw-metadata -n settings 0 clock.force-quantum 512")),
    Finding(r"I/O error|blk_update_request|BTRFS (error|critical)", "Datenträgerfehler",
            "Sofort Backup prüfen und Dateisystem kontrollieren.",
            ("sudo btrfs device stats /", "sudo smartctl -H /dev/nvme0n1")),
    Finding(r"Out of memory|oom-kill|OOM", "Arbeitsspeicher erschöpft",
            "zram ist aktiv; große Projekte ggf. mit weniger Vorschauqualität öffnen.", ("free -h", "zramctl")),
    Finding(r"Failed to start (\S+)", "Dienst startet nicht",
            "Status und Log des Dienstes ansehen.", ("systemctl status {1}", "journalctl -u {1} -b")),
    Finding(r"signature.*(invalid|ungültig)|Unbekannter Signaturschlüssel", "Paketsignatur ungültig",
            "Schlüsselbund aktualisieren und neu synchronisieren.", ("syp install symfonos-keyring", "syp sync")),
]


def analyze(text: str) -> list[tuple[Finding, list[str]]]:
    hits, seen = [], set()
    for rule in RULES:
        for m in re.finditer(rule.pattern, text):
            groups = [m.group(0)] + list(m.groups())
            cmds = []
            for c in rule.commands:
                try:
                    cmds.append(c.format(*groups))
                except IndexError:
                    cmds.append(c)
            key = (rule.title, tuple(cmds))
            if key not in seen:
                seen.add(key)
                hits.append((rule, cmds))
            break
    return hits
