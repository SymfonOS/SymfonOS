#!/usr/bin/env python3
"""Karma für Updates in testing (wie Bodhi bei Fedora), mit GitHub-Issues als Weboberfläche.

Für jedes Paket, das in testing auf die Übernahme nach stable wartet, gibt es ein Issue mit dem Label
»testing« und dem Titel »testing: NAME VERSION«. Tester stimmen mit 👍 (funktioniert) oder 👎 (kaputt) am
Issue ab; je Person zählt eine Stimme, Stimmen von Bots zählen nicht. Die Summe ist das Karma, das
tools/promote.py auswertet: ab +3 genügt ein Tag in testing, ab -2 wartet das Paket, bis jemand es
behebt oder die Stimmen sich ändern.

    tools/testing-karma.py collect KARMA.json      # Karma aller offenen testing-Issues einsammeln
    tools/testing-karma.py sync REPORT.json        # Issues anlegen, Stand eintragen, erledigte schließen

REPORT.json schreibt tools/promote.py --report. Braucht gh (GH_TOKEN) und GITHUB_REPOSITORY.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

LABEL = "testing"
PREFIX = "testing: "
MARK = "<!-- symfonos-testing -->"
MAX_NEW = int(os.environ.get("KARMA_MAX_NEW", "40"))


def gh(*args: str, data: dict | None = None) -> object:
    cmd = ["gh", "api", *args]
    if data is not None:
        cmd += ["--input", "-"]
    r = subprocess.run(cmd, input=json.dumps(data) if data is not None else None, capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(f"gh api {' '.join(args)}: {(r.stderr or r.stdout).strip()[:300]}")
    return json.loads(r.stdout) if r.stdout.strip() else None


def repo() -> str:
    return os.environ["GITHUB_REPOSITORY"]


def open_issues() -> dict[str, dict]:
    """Offene testing-Issues je »name version« (nur die vom Werkzeug angelegten)."""
    out, page = {}, 1
    while True:
        batch = gh(f"repos/{repo()}/issues?labels={LABEL}&state=open&per_page=100&page={page}")
        for i in batch:
            if "pull_request" in i or MARK not in (i.get("body") or "") or not i["title"].startswith(PREFIX):
                continue
            out[i["title"][len(PREFIX):].strip()] = i
        if len(batch) < 100:
            return out
        page += 1


def karma_of(number: int) -> int:
    votes: dict[str, int] = {}
    page = 1
    while True:
        batch = gh(f"repos/{repo()}/issues/{number}/reactions?per_page=100&page={page}")
        for r in batch:
            user = r.get("user") or {}
            if user.get("type") == "Bot":
                continue
            v = {"+1": 1, "-1": -1}.get(r.get("content"))
            if v is None:
                continue
            # wer beides gibt, zählt nicht (hebt sich auf)
            login = user.get("login", "")
            votes[login] = 0 if login in votes and votes[login] != v else v
        if len(batch) < 100:
            return sum(votes.values())
        page += 1


def body(tag: str, r: dict) -> str:
    name = r["name"]
    state = {"stable": "nach stable übernommen", "ersetzt": "durch eine neuere Version ersetzt"}.get(
        r.get("status", ""), r.get("reason") or "wartet")
    return f"""{MARK}
**{tag}** liegt im Kanal *testing* und wartet auf die Übernahme nach *stable*.

**Testen:** in `/etc/syp/syp.toml` den Eintrag `testing` aktivieren, `sudo syp upgrade` ausführen, `{name}`
benutzen und hier abstimmen: 👍 funktioniert · 👎 kaputt (bitte mit Kommentar, was nicht geht).

**Regeln:** Karma ab +3 → nach einem Tag in *stable*; ab -2 → bleibt in *testing*, bis es behoben ist.
Sonst gilt die Wartezeit von {r.get('need', 7):g} Tagen{' (Sicherheitsupdate)' if r.get('security') else ''}.

**Stand:** {r.get('days', 0):g} Tage in testing, Karma {r.get('karma', 0):+d} – {state}
"""


def collect(path: str) -> int:
    k = {tag: karma_of(i["number"]) for tag, i in open_issues().items()}
    with open(path, "w") as f:
        json.dump(k, f, indent=1, sort_keys=True, ensure_ascii=False)
    print(f":: Karma aus {len(k)} testing-Issues: " + (", ".join(f"{t} {v:+d}" for t, v in sorted(k.items()) if v)
                                                     or "keine Stimmen"))
    return 0


def sync(path: str) -> int:
    with open(path) as f:
        report = json.load(f)
    try:
        gh(f"repos/{repo()}/labels", "-X", "POST", data={"name": LABEL, "color": "7c5cff",
                                                        "description": "Update in testing: mit 👍/👎 abstimmen"})
    except RuntimeError:
        pass                                    # Label gibt es schon
    issues = open_issues()
    made = closed = updated = 0
    for tag, r in sorted(report.items()):
        i = issues.get(tag)
        if r.get("status") in ("stable", "ersetzt"):
            if i:
                gh(f"repos/{repo()}/issues/{i['number']}", "-X", "PATCH",
                   data={"body": body(tag, r), "state": "closed",
                         "state_reason": "completed" if r["status"] == "stable" else "not_planned"})
                closed += 1
            continue
        if not i:
            if made >= MAX_NEW:
                continue                        # Rest im nächsten Lauf: nicht Hunderte Issues auf einmal
            gh(f"repos/{repo()}/issues", "-X", "POST",
               data={"title": PREFIX + tag, "body": body(tag, r), "labels": [LABEL]})
            made += 1
        elif i.get("body") != body(tag, r):
            gh(f"repos/{repo()}/issues/{i['number']}", "-X", "PATCH", data={"body": body(tag, r)})
            updated += 1
    # Issues zu Versionen, die gar nicht mehr in testing liegen (neu gebaut, ersetzt)
    for tag, i in issues.items():
        if tag not in report:
            gh(f"repos/{repo()}/issues/{i['number']}", "-X", "PATCH",
               data={"state": "closed", "state_reason": "not_planned"})
            closed += 1
    print(f":: testing-Issues: {made} neu, {updated} aktualisiert, {closed} geschlossen")
    return 0


def main() -> int:
    if len(sys.argv) != 3 or sys.argv[1] not in ("collect", "sync"):
        print(__doc__, file=sys.stderr)
        return 2
    return collect(sys.argv[2]) if sys.argv[1] == "collect" else sync(sys.argv[2])


if __name__ == "__main__":
    sys.exit(main())
