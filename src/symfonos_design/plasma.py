"""Übersetzt ein Design-Profil in KDE-Plasma-6-Befehle (alles im Benutzerkontext, kein Root)."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from importlib import resources
from pathlib import Path

CONFIG = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "symfonos/design.json"

ACCENTS = {"Violett": "#7C5CFF", "Ozean": "#2D8CFF", "Mint": "#1FBF8F", "Sonne": "#F5A623",
           "Koralle": "#FF6B5C", "Magenta": "#E0459B", "Graphit": "#7A7F8C"}
LAYOUTS = {"dock": "Atelier (Leiste oben, Dock unten)", "classic": "Klassisch (Leiste unten)",
           "studio": "Studio (Dock links, maximale Arbeitsfläche)"}
WIDGETS = {"org.kde.plasma.analogclock": "Uhr", "org.kde.plasma.notes": "Notizen",
           "org.kde.plasma.systemmonitor": "Systemmonitor", "org.kde.plasma.weather": "Wetter",
           "org.kde.plasma.mediacontroller": "Medienwiedergabe", "org.kde.plasma.calendar": "Kalender"}


@dataclass
class Profile:
    name: str = "symfonOS Standard"
    mode: str = "dark"                   # dark | light | auto
    accent: str = ACCENTS["Violett"]
    layout: str = "dock"
    font: str = "Inter"
    mono_font: str = "JetBrains Mono"
    font_size: int = 10
    scale: float = 1.0                   # Fensterskalierung (Wayland)
    animation: float = 1.0               # 0 = aus, 0.5 = schnell, 1 = normal, 2 = langsam
    icons: str = "Papirus"
    cursor: str = "Bibata-Modern-Classic"
    wallpaper: str = "/usr/share/wallpapers/symfonOS/contents/images/3840x2160.png"
    widgets: list[str] = field(default_factory=list)
    neutral_ui: bool = False             # farbneutrale Oberfläche für farbkritische Arbeit
    auto_dark_at: str = "19:00"
    auto_light_at: str = "07:00"

    @classmethod
    def load(cls, path: Path = CONFIG) -> "Profile":
        if path.exists():
            data = json.loads(path.read_text())
            return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
        return cls()

    def save(self, path: Path = CONFIG) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False))


PRESETS = {
    "symfonOS Dunkel": Profile(),
    "Papier Hell": Profile(name="Papier Hell", mode="light", accent=ACCENTS["Ozean"], layout="dock"),
    "Studio Neutral": Profile(name="Studio Neutral", mode="dark", accent=ACCENTS["Graphit"],
                              layout="studio", neutral_ui=True, animation=0.5),
    "Klassisch": Profile(name="Klassisch", mode="light", accent=ACCENTS["Ozean"], layout="classic"),
}


def effective_mode(p: Profile, now: str | None = None) -> str:
    if p.mode != "auto":
        return p.mode
    import time
    now = now or time.strftime("%H:%M")
    return "light" if p.auto_light_at <= now < p.auto_dark_at else "dark"


def color_scheme(p: Profile, mode: str) -> str:
    if p.neutral_ui:
        return "SymfonosNeutral"
    return "SymfonosDark" if mode == "dark" else "SymfonosLight"


def layout_script(name: str) -> str:
    return (resources.files("symfonos_design") / "layouts" / f"{name}.js").read_text()


def commands(p: Profile, include_layout: bool = True) -> list[list[str]]:
    """Alle Befehle, die das Profil anwenden (reihenfolgerichtig, idempotent)."""
    mode = effective_mode(p)
    kw = ["kwriteconfig6", "--file"]
    font = f"{p.font},{p.font_size},-1,5,400,0,0,0,0,0,0,0,0,0,0,1"
    mono = f"{p.mono_font},{p.font_size},-1,5,400,0,0,0,0,0,0,0,0,0,0,1"
    cmds = [
        ["plasma-apply-lookandfeel", "--apply",
         "org.symfonos.dark" if mode == "dark" else "org.symfonos.light"],
        ["plasma-apply-colorscheme", color_scheme(p, mode)],
        ["plasma-apply-colorscheme", "--accent-color", p.accent],
        ["plasma-apply-desktoptheme", "default"],        # Breeze, folgt dem Farbschema
        ["plasma-apply-cursortheme", p.cursor],
        ["/usr/lib/plasma-changeicons", f"{p.icons}-Dark" if mode == "dark" else p.icons],
        kw + ["kdeglobals", "--group", "General", "--key", "font", font],
        kw + ["kdeglobals", "--group", "General", "--key", "menuFont", font],
        kw + ["kdeglobals", "--group", "General", "--key", "toolBarFont", font],
        kw + ["kdeglobals", "--group", "General", "--key", "fixed", mono],
        kw + ["kdeglobals", "--group", "KDE", "--key", "AnimationDurationFactor",
              str(p.animation if p.animation > 0 else 0)],
        kw + ["kwinrc", "--group", "Xwayland", "--key", "Scale", str(p.scale)],
        kw + ["kwinrc", "--group", "Plugins", "--key", "blurEnabled",
              "false" if p.neutral_ui else "true"],
        # GTK-Apps (GIMP, Inkscape, Ardour …) folgen Dark/Light
        ["gsettings", "set", "org.gnome.desktop.interface", "color-scheme",
         "prefer-dark" if mode == "dark" else "prefer-light"],
        ["gsettings", "set", "org.gnome.desktop.interface", "accent-color", _gnome_accent(p.accent)],
        ["plasma-apply-wallpaperimage", p.wallpaper],
        ["qdbus6", "org.kde.KWin", "/KWin", "reconfigure"],
    ]
    if include_layout:
        cmds.append(["qdbus6", "org.kde.plasmashell", "/PlasmaShell",
                     "org.kde.PlasmaShell.evaluateScript", layout_script(p.layout)])
    cmds.append(["qdbus6", "org.kde.plasmashell", "/PlasmaShell", "org.kde.PlasmaShell.evaluateScript",
                 layout_script("widgets").replace("%WIDGETS%", ",".join(p.widgets))])
    return cmds


def _gnome_accent(hex_: str) -> str:
    """Nächstliegende der festen GNOME-Akzentfarben."""
    table = {"blue": "#3584E4", "teal": "#2190A4", "green": "#3A944A", "yellow": "#C88800",
             "orange": "#ED5B00", "red": "#E62D42", "pink": "#D56199", "purple": "#9141AC",
             "slate": "#6F8396"}
    r, g, b = (int(hex_[i:i + 2], 16) for i in (1, 3, 5))
    return min(table, key=lambda k: sum((int(table[k][i:i + 2], 16) - v) ** 2
                                        for i, v in zip((1, 3, 5), (r, g, b))))


def apply(p: Profile, include_layout: bool = True, log=print) -> list[str]:
    """Wendet das Profil an; fehlende Werkzeuge werden übersprungen. Rückgabe: Fehler."""
    errors = []
    for cmd in commands(p, include_layout):
        if shutil.which(cmd[0]) is None and not Path(cmd[0]).exists():
            continue
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode:
            errors.append(f"{cmd[0]}: {r.stderr.strip()[:200]}")
    p.save()
    return errors

