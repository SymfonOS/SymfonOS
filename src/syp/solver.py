"""Abhängigkeitsauflösung per Backtracking.

Regeln:
* Kandidaten: neueste Version zuerst, bei Gleichstand bessere Repo-Priorität,
  bereits installierte Version bevorzugt, Namensgleichheit vor bloßem Provide.
* Installierte Pakete bleiben unverändert, außer sie werden angefordert, aktualisiert
  (`upgrade`) oder müssen für eine Abhängigkeit ersetzt werden.
* Am Ende wird der Gesamtzustand geprüft (alle Abhängigkeiten erfüllt, keine Konflikte);
  durch Ersetzungen verletzte Abhängigkeiten werden nachträglich aufgelöst.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from .deps import Atom, Dep
from .version import Version


class SolverError(Exception):
    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("\n".join(problems))


@dataclass
class Plan:
    install: list = field(default_factory=list)   # RepoPackage (neu oder Upgrade/Downgrade)
    remove: list[str] = field(default_factory=list)
    reasons: dict[str, str] = field(default_factory=dict)  # name -> explicit|dep

    @property
    def empty(self) -> bool:
        return not self.install and not self.remove


def _satisfies(pkg, atom: Atom) -> bool:
    for name, ver in pkg.info.provides_items():
        if name == atom.name and atom.matches_version(ver):
            return True
    return False


def _conflicts(a, b) -> bool:
    if a.name == b.name:
        return False
    return any(_satisfies(b, c) for c in a.info.conflict_objs) or \
        any(_satisfies(a, c) for c in b.info.conflict_objs)


class Solver:
    MAX_STEPS = 200_000

    def __init__(self, available: list, installed: dict):
        """available: RepoPackage-Liste; installed: name -> InstalledPackage."""
        self.installed = installed
        self.by_provide: dict[str, list] = defaultdict(list)
        for p in available:
            for name, _ in p.info.provides_items():
                self.by_provide[name].append(p)
        self.problems: list[str] = []
        self.steps = 0

    # -- Kandidaten -------------------------------------------------------------
    def candidates(self, dep: Dep, prefer_newest: bool = False) -> list:
        out = []
        for atom in dep.alternatives:
            cands = [p for p in self.by_provide.get(atom.name, []) if _satisfies(p, atom)]
            inst = self.installed.get(atom.name)

            def key(p, atom=atom, inst=inst):
                is_inst = bool(inst and inst.version == p.version and not prefer_newest)
                return (not is_inst, p.name != atom.name, _NegVer(p.version), p.priority)

            out.extend(sorted(cands, key=key))
        seen, uniq = set(), []
        for p in out:
            if id(p) not in seen:
                seen.add(id(p))
                uniq.append(p)
        return uniq

    # -- Suche ------------------------------------------------------------------
    def _search(self, sel: dict, fixed: frozenset, agenda: list, forced: set) -> dict | None:
        self.steps += 1
        if self.steps > self.MAX_STEPS:
            raise SolverError(["Abhängigkeitsauflösung abgebrochen (zu komplex)"])
        if not agenda:
            # Jede Runde legt mindestens ein neues Paket fest -> terminiert
            agenda = self._broken(sel)
            if not agenda:
                return sel
        (dep, why), rest = agenda[0], agenda[1:]
        key = str(dep)
        if key not in forced:
            for p in sel.values():
                if any(_satisfies(p, a) for a in dep.alternatives):
                    return self._search(sel, fixed, rest, forced)
        cands = self.candidates(dep, prefer_newest=key in forced)
        if not cands:
            self.problems.append(f"Nichts stellt »{dep}« bereit (benötigt von {why})")
            return None
        for cand in cands:
            cur = sel.get(cand.name)
            if cur is not None and cur.version == cand.version and cand.name in fixed:
                return self._search(sel, fixed, rest, forced - {key})
            if cand.name in fixed and cur is not None:
                continue  # bereits in diesem Lauf festgelegt
            clash = next((o for o in sel.values() if o.name != cand.name and _conflicts(cand, o)), None)
            if clash is not None:
                self.problems.append(f"{cand.name} {cand.version} steht in Konflikt mit {clash.name}")
                continue
            new = dict(sel)
            new[cand.name] = cand
            more = [(d, f"{cand.name}") for d in cand.info.dep_objs]
            r = self._search(new, fixed | {cand.name}, rest + more, forced - {key})
            if r is not None:
                return r
        return None

    def _broken(self, sel: dict) -> list:
        out = []
        for p in sel.values():
            for d in p.info.dep_objs:
                if not any(_satisfies(q, a) for q in sel.values() for a in d.alternatives):
                    out.append((d, p.name))
        return out

    def _finish(self, sel: dict, removed: set, reinstall: frozenset = frozenset()) -> Plan:
        plan = Plan()
        for name, p in sel.items():
            inst = self.installed.get(name)
            if p is inst:
                continue
            if inst is not None and inst.version == p.version and name not in reinstall \
                    and not getattr(p, "local_path", None):
                continue
            plan.install.append(p)
        plan.remove = sorted(removed)
        return plan

    def _run(self, sel: dict, agenda: list, forced: set) -> dict:
        r = self._search(sel, frozenset(), agenda, forced)
        if r is None:
            raise SolverError(list(dict.fromkeys(self.problems))[-8:] or ["Keine Lösung gefunden"])
        return r

    # -- API --------------------------------------------------------------------
    def install(self, requests: list[str], local: list | None = None) -> Plan:
        """requests: Abhängigkeitsausdrücke; local: RepoPackage aus lokalen Dateien."""
        sel = dict(self.installed)
        for lp in local or []:
            for name, _ in lp.info.provides_items():
                self.by_provide[name].insert(0, lp)
        agenda = [(Dep.parse(r), "Anfrage") for r in requests]
        agenda += [(Dep.parse(f"{lp.name}={lp.version}"), "lokale Datei") for lp in local or []]
        forced = {str(d) for d, _ in agenda}
        result = self._run(sel, agenda, forced)
        explicit = {d.alternatives[0].name for d, _ in agenda}
        plan = self._finish(result, set(), frozenset(explicit))
        for p in plan.install:
            plan.reasons[p.name] = "explicit" if p.name in explicit or any(
                _satisfies(p, d.alternatives[0]) for d, _ in agenda) else "dep"
        return plan

    def upgrade(self) -> Plan:
        sel = dict(self.installed)
        removed = set()
        # replaces: neues Paket ersetzt installiertes
        for provs in self.by_provide.values():
            for p in provs:
                for r in p.info.replaces:
                    if r in sel and p.name not in sel:
                        del sel[r]
                        removed.add(r)
                        sel[p.name] = p
        agenda = [(Dep.parse(f"{n}>={p.version}"), "Systemaktualisierung")
                  for n, p in sorted(sel.items()) if self.by_provide.get(n)]
        forced = {str(d) for d, _ in agenda}
        result = self._run(sel, agenda, forced)
        plan = self._finish(result, removed)
        plan.reasons = {p.name: self.installed[p.name].reason if p.name in self.installed else "dep"
                        for p in plan.install}
        return plan

    def remove(self, names: list[str], cascade: bool = False) -> Plan:
        targets = set(names)
        missing = targets - set(self.installed)
        if missing:
            raise SolverError([f"Nicht installiert: {', '.join(sorted(missing))}"])
        while True:
            rest = {n: p for n, p in self.installed.items() if n not in targets}
            broken = [(d, p.name) for p in rest.values() for d in p.info.dep_objs
                      if not any(_satisfies(q, a) for q in rest.values() for a in d.alternatives)]
            if not broken:
                break
            if not cascade:
                raise SolverError([f"{who} benötigt »{d}« (nutze --cascade)" for d, who in broken])
            targets |= {who for _, who in broken}
        return Plan(remove=sorted(targets))

    def orphans(self) -> list[str]:
        """Als Abhängigkeit installierte Pakete, die niemand mehr braucht (iterativ)."""
        keep = dict(self.installed)
        changed = True
        while changed:
            changed = False
            for n, p in list(keep.items()):
                if p.reason != "dep":
                    continue
                needed = any(_satisfies(p, a) for q in keep.values() if q is not p
                             for d in q.info.dep_objs for a in d.alternatives)
                if not needed:
                    del keep[n]
                    changed = True
        return sorted(set(self.installed) - set(keep))


class _NegVer:
    """Sortierschlüssel: höhere Version zuerst."""

    def __init__(self, v: str):
        self.v = Version.parse(v)

    def __lt__(self, other: "_NegVer") -> bool:
        return self.v.cmp(other.v) > 0

    def __eq__(self, other: object) -> bool:
        return isinstance(other, _NegVer) and self.v.cmp(other.v) == 0
