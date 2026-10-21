"""symfonos-assist – Chat im Terminal."""
from __future__ import annotations

import argparse
import sys

from . import llm, safety
from .agent import Assistant

HELP = """Befehle:  /diagnose  Logs analysieren   /docs <frage>  Handbuch durchsuchen
          /status    Modellstatus        /neu  Verlauf löschen        /exit  Beenden"""


def stream_print(it) -> None:
    try:
        for tok in it:
            print(tok, end="", flush=True)
    except llm.LLMError as e:
        print(f"\n[Fehler] {e}")
    print()


def offer_commands(a: Assistant, ask=input) -> None:
    for p in a.proposals():
        if safety.confirm_cli(p, ask):
            code, out = safety.run(p.command)
            print(f"  → Exit-Code {code}\n" + "\n".join("    " + l for l in out.splitlines()[-30:]))
            stream_print(a.feed_output(p.command, code, out))
            offer_commands(a, ask)
            return
        print("  Übersprungen.")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="symfonos-assist", description="Lokaler symfonOS-Assistent")
    ap.add_argument("frage", nargs="*", help="Einmalige Frage (sonst interaktiv)")
    ap.add_argument("--diagnose", action="store_true")
    a_ = ap.parse_args(argv)
    try:
        a = Assistant()
    except ValueError as e:
        print(e, file=sys.stderr)
        return 2
    if a_.diagnose:
        offline, findings, stream = a.diagnose()
        print("Regelbasierte Analyse:\n" + offline)
        for f, cmds in findings:
            for c in cmds:
                print(f"   ```bash\n   {c}\n   ```")
        if stream:
            print("\nKI-Analyse:")
            stream_print(stream)
            offer_commands(a)
        return 0
    if a_.frage:
        stream_print(a.ask(" ".join(a_.frage)))
        offer_commands(a)
        return 0
    status = "bereit" if llm.available(a.s) else "NICHT erreichbar – nur Handbuchsuche und /diagnose"
    print(f"symfonOS-Assistent · Modell {a.s.model} ({a.s.backend}) · {status}\n{HELP}")
    while True:
        try:
            q = input("\nDu › ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not q:
            continue
        if q in ("/exit", "/quit"):
            return 0
        if q == "/neu":
            a.history.clear()
            continue
        if q == "/status":
            print(f"{a.s.backend} {a.s.url} {a.s.model}: "
                  f"{'erreichbar' if llm.available(a.s) else 'nicht erreichbar'}; "
                  f"{len(a.index.chunks)} Handbuch-Abschnitte indiziert")
            continue
        if q.startswith("/docs"):
            for score, c in a.index.search(q[5:], 3):
                print(f"\n[{c.source} › {c.heading}] ({score:.1f})\n{c.text[:700]}")
            continue
        if q == "/diagnose":
            offline, _, stream = a.diagnose()
            print(offline)
            if stream:
                stream_print(stream)
                offer_commands(a)
            continue
        print("symfonOS › ", end="")
        stream_print(a.ask(q))
        offer_commands(a)


if __name__ == "__main__":
    sys.exit(main())
