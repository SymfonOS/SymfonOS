/* solver.c – Backtracking-Solver
 *
 * Regeln (wie die frühere Referenzimplementierung):
 *  - Kandidaten: installierte Version bevorzugt (außer bei erzwungener Aktualisierung),
 *    Namensgleichheit vor bloßem Provide, neueste Version zuerst, dann Repo-Priorität.
 *  - Installierte Pakete bleiben, außer sie werden angefordert, aktualisiert oder müssen ersetzt werden.
 *  - Ist die Agenda leer, wird der Gesamtzustand geprüft; verletzte Abhängigkeiten (etwa durch
 *    Ersetzungen) kommen erneut auf die Agenda. Jede Runde legt ein neues Paket fest -> terminiert.
 */
#include "solver.h"

#include <stdlib.h>
#include <string.h>

#define MAX_STEPS 2000000

typedef struct agenda {
    dep *d;
    const char *why;
    int forced;
    struct agenda *next;
} agenda;

typedef struct {
    hmap sel;    /* name -> pkg* (gewählter Zustand) */
    hmap fixed;  /* name -> (void*)1 */
    /* Undo-Log */
    struct undo {
        char *name;
        pkg *prev;
        int was_fixed;
    } *log;
    size_t nlog, cap;
    /* Arena für Agenda-Knoten und generierte deps */
    ptrvec arena;
} state;

static void st_set(state *st, pkg *p) {
    if (st->nlog == st->cap) {
        st->cap = st->cap ? st->cap * 2 : 256;
        st->log = xrealloc(st->log, st->cap * sizeof *st->log);
    }
    st->log[st->nlog].name = xstrdup(p->name);
    st->log[st->nlog].prev = hm_get(&st->sel, p->name);
    st->log[st->nlog].was_fixed = hm_get(&st->fixed, p->name) != NULL;
    st->nlog++;
    hm_set(&st->sel, p->name, p);
    hm_set(&st->fixed, p->name, (void *)1);
}
static void st_undo(state *st, size_t mark) {
    while (st->nlog > mark) {
        struct undo *u = &st->log[--st->nlog];
        if (u->prev) hm_set(&st->sel, u->name, u->prev);
        else hm_del(&st->sel, u->name);
        if (!u->was_fixed) hm_del(&st->fixed, u->name);
        free(u->name);
    }
}
static agenda *ag(state *st, dep *d, const char *why, int forced, agenda *next) {
    agenda *a = xcalloc(1, sizeof *a);
    a->d = d;
    a->why = why;
    a->forced = forced;
    a->next = next;
    pv_push(&st->arena, a);
    return a;
}

void plan_init(plan *p) {
    memset(p, 0, sizeof *p);
    hm_init(&p->reasons);
}
void plan_free(plan *p) {
    free(p->install.v);
    sv_free(&p->remove);
    hm_free(&p->reasons);
}
int plan_empty(const plan *p) { return !p->install.n && !p->remove.n; }

static void index_pkg(hmap *by, pkg *p, int front) {
    for (size_t i = 0; i < p->nprov; i++) {
        ptrvec *v = hm_get(by, p->prov_name[i]);
        if (!v) {
            v = xcalloc(1, sizeof *v);
            hm_set(by, p->prov_name[i], v);
        }
        int dup = 0;
        for (size_t k = 0; k < v->n; k++)
            if (v->v[k] == p) dup = 1;
        if (dup) continue;
        pv_push(v, p);
        if (front && v->n > 1) {
            memmove(v->v + 1, v->v, (v->n - 1) * sizeof(void *));
            v->v[0] = p;
        }
    }
}

void solver_init(solver *s, ptrvec *available, hmap *installed) {
    memset(s, 0, sizeof *s);
    for (size_t i = 0; available && i < available->n; i++) pv_push(&s->available, available->v[i]);
    s->installed = installed;
    hm_init(&s->by_provide);
    for (size_t i = 0; i < s->available.n; i++) index_pkg(&s->by_provide, s->available.v[i], 0);
    hm_init(&s->inst_prov);
    if (installed) HM_FOREACH(installed, i) index_pkg(&s->inst_prov, installed->vals[i], 0);
}
static void free_index(hmap *m) {
    HM_FOREACH(m, i) {
        ptrvec *v = m->vals[i];
        free(v->v);
        free(v);
    }
    hm_free(m);
}
void solver_free(solver *s) {
    free_index(&s->by_provide);
    free_index(&s->inst_prov);
    free(s->available.v);
    sv_free(&s->problems);
}

static void problem(solver *s, char *msg) {
    if (!sv_contains(&s->problems, msg)) sv_push(&s->problems, msg);
    free(msg);
}

/* Erfüllt der gewählte Zustand das Atom? */
static int sel_satisfies(solver *s, state *st, const atom *a) {
    /* verfügbare Anbieter */
    ptrvec *v = hm_get(&s->by_provide, a->name);
    for (size_t i = 0; v && i < v->n; i++) {
        pkg *p = v->v[i];
        if (hm_get(&st->sel, p->name) == p && pkg_satisfies(p, a)) return 1;
    }
    /* installierte Pakete, sofern noch gewählt */
    v = hm_get(&s->inst_prov, a->name);
    for (size_t i = 0; v && i < v->n; i++) {
        pkg *p = v->v[i];
        if (hm_get(&st->sel, p->name) == p && pkg_satisfies(p, a)) return 1;
    }
    return 0;
}
static int sel_satisfies_dep(solver *s, state *st, const dep *d) {
    for (size_t i = 0; i < d->n; i++)
        if (sel_satisfies(s, st, &d->alt[i])) return 1;
    return 0;
}

typedef struct {
    pkg *p;
    const atom *a;
    int prefer_newest;
    const pkg *inst;
} cand;

static int cmp_cand(const void *x, const void *y) {
    const cand *a = x, *b = y;
    int ia = a->inst && !a->prefer_newest && !strcmp(a->inst->version, a->p->version);
    int ib = b->inst && !b->prefer_newest && !strcmp(b->inst->version, b->p->version);
    if (ia != ib) return ib - ia;
    int na = strcmp(a->p->name, a->a->name) != 0, nb = strcmp(b->p->name, b->a->name) != 0;
    if (na != nb) return na - nb;
    int v = vercmp(b->p->version, a->p->version);
    if (v) return v;
    return a->p->priority - b->p->priority;
}

static void candidates(solver *s, const dep *d, int prefer_newest, ptrvec *out) {
    for (size_t k = 0; k < d->n; k++) {
        const atom *a = &d->alt[k];
        ptrvec *v = hm_get(&s->by_provide, a->name);
        if (!v) continue;
        cand *c = xcalloc(v->n + 1, sizeof *c);
        size_t n = 0;
        const pkg *inst = hm_get(s->installed, a->name);
        for (size_t i = 0; i < v->n; i++)
            if (pkg_satisfies(v->v[i], a)) c[n++] = (cand){v->v[i], a, prefer_newest, inst};
        qsort(c, n, sizeof *c, cmp_cand);
        for (size_t i = 0; i < n; i++) {
            int dup = 0;
            for (size_t j = 0; j < out->n; j++)
                if (out->v[j] == c[i].p) dup = 1;
            if (!dup) pv_push(out, c[i].p);
        }
        free(c);
    }
}

static agenda *broken(solver *s, state *st) {
    agenda *head = NULL;
    HM_FOREACH(&st->sel, i) {
        pkg *p = st->sel.vals[i];
        for (size_t k = 0; k < p->ndeps; k++)
            if (!sel_satisfies_dep(s, st, &p->deps[k])) head = ag(st, &p->deps[k], p->name, 0, head);
    }
    return head;
}

static int search(solver *s, state *st, agenda *a) {
    if (++s->steps > MAX_STEPS) {
        problem(s, xstrdup("Abhängigkeitsauflösung abgebrochen (zu komplex)"));
        return 0;
    }
    if (!a) {
        a = broken(s, st);
        if (!a) return 1;
    }
    const dep *d = a->d;
    if (!a->forced && sel_satisfies_dep(s, st, d)) return search(s, st, a->next);
    ptrvec cands = {0};
    candidates(s, d, a->forced, &cands);
    if (!cands.n) {
        char *ds = dep_str(d);
        problem(s, xasprintf("Nichts stellt »%s« bereit (benötigt von %s)", ds, a->why));
        free(ds);
        return 0;
    }
    int ok = 0;
    for (size_t i = 0; i < cands.n && !ok; i++) {
        pkg *c = cands.v[i];
        pkg *cur = hm_get(&st->sel, c->name);
        int isfixed = hm_get(&st->fixed, c->name) != NULL;
        if (cur && isfixed && !strcmp(cur->version, c->version)) {
            ok = search(s, st, a->next);
            break;
        }
        if (cur && isfixed) continue;
        pkg *clash = NULL;
        HM_FOREACH(&st->sel, k) {
            pkg *o = st->sel.vals[k];
            if (strcmp(o->name, c->name) && pkg_conflicts(c, o)) {
                clash = o;
                break;
            }
        }
        if (clash) {
            problem(s, xasprintf("%s %s steht in Konflikt mit %s", c->name, c->version, clash->name));
            continue;
        }
        size_t mark = st->nlog;
        st_set(st, c);
        agenda *next = a->next;
        for (size_t k = c->ndeps; k-- > 0;) next = ag(st, &c->deps[k], c->name, 0, next);
        ok = search(s, st, next);
        if (!ok) st_undo(st, mark);
    }
    free(cands.v);
    return ok;
}

static void state_init(state *st, solver *s) {
    memset(st, 0, sizeof *st);
    hm_init(&st->sel);
    hm_init(&st->fixed);
    HM_FOREACH(s->installed, i) hm_set(&st->sel, s->installed->keys[i], s->installed->vals[i]);
}
static void state_free(state *st) {
    for (size_t i = 0; i < st->nlog; i++) free(st->log[i].name);
    free(st->log);
    for (size_t i = 0; i < st->arena.n; i++) free(st->arena.v[i]);
    free(st->arena.v);
    hm_free(&st->sel);
    hm_free(&st->fixed);
}

static void finish(solver *s, state *st, plan *out, strvec *reinstall) {
    HM_FOREACH(&st->sel, i) {
        pkg *p = st->sel.vals[i];
        pkg *inst = hm_get(s->installed, p->name);
        if (p == inst) continue;
        if (inst && !strcmp(inst->version, p->version) && !(reinstall && sv_contains(reinstall, p->name)) &&
            !p->local_path)
            continue;
        pv_push(&out->install, p);
    }
}

static int cmp_pkgname(const void *a, const void *b) {
    return strcmp((*(pkg *const *)a)->name, (*(pkg *const *)b)->name);
}

int solve_install(solver *s, char **requests, size_t nreq, ptrvec *local, plan *out) {
    for (size_t i = 0; local && i < local->n; i++) index_pkg(&s->by_provide, local->v[i], 1);
    state st;
    state_init(&st, s);
    dep *reqd = xcalloc(nreq + (local ? local->n : 0) + 1, sizeof(dep));
    size_t nd = 0;
    agenda *head = NULL, **tail = &head;
    for (size_t i = 0; i < nreq; i++) {
        if (dep_parse(requests[i], &reqd[nd])) {
            problem(s, xstrdup(last_error()));
            continue;
        }
        *tail = ag(&st, &reqd[nd++], "Anfrage", 1, NULL);
        tail = &(*tail)->next;
    }
    for (size_t i = 0; local && i < local->n; i++) {
        pkg *lp = local->v[i];
        char *r = xasprintf("%s=%s", lp->name, lp->version);
        dep_parse(r, &reqd[nd]);
        free(r);
        *tail = ag(&st, &reqd[nd++], "lokale Datei", 1, NULL);
        tail = &(*tail)->next;
    }
    int ok = s->problems.n == 0 && search(s, &st, head);
    if (ok) {
        strvec explicit_names = {0};
        for (size_t i = 0; i < nd; i++) sv_push(&explicit_names, reqd[i].alt[0].name);
        finish(s, &st, out, &explicit_names);
        for (size_t i = 0; i < out->install.n; i++) {
            pkg *p = out->install.v[i];
            int ex = sv_contains(&explicit_names, p->name);
            for (size_t k = 0; !ex && k < nd; k++)
                if (pkg_satisfies(p, &reqd[k].alt[0])) ex = 1;
            hm_set(&out->reasons, p->name, ex ? "explicit" : "dep");
        }
        sv_free(&explicit_names);
        if (out->install.n > 1) qsort(out->install.v, out->install.n, sizeof(void *), cmp_pkgname);
    }
    for (size_t i = 0; i < nd; i++) dep_free(&reqd[i]);
    free(reqd);
    state_free(&st);
    if (!ok && !s->problems.n) sv_push(&s->problems, "Keine Lösung gefunden");
    return ok ? 0 : -1;
}

int solve_upgrade(solver *s, plan *out) {
    state st;
    state_init(&st, s);
    /* replaces: neues Paket ersetzt installiertes */
    for (size_t i = 0; i < s->available.n; i++) {
        pkg *p = s->available.v[i];
        jval *rep = jgeta(p->info, "replaces");
        for (size_t k = 0; rep && k < rep->n; k++) {
            const char *r = rep->items[k]->s;
            if (hm_get(&st.sel, r) && !hm_get(&st.sel, p->name)) {
                hm_del(&st.sel, r);
                if (!sv_contains(&out->remove, r)) sv_push(&out->remove, r);
                hm_set(&st.sel, p->name, p);
            }
        }
    }
    strvec names = {0};
    HM_FOREACH(&st.sel, i) if (hm_get(&s->by_provide, st.sel.keys[i])) sv_push(&names, st.sel.keys[i]);
    sv_sort(&names);
    dep *reqd = xcalloc(names.n + 1, sizeof(dep));
    agenda *head = NULL, **tail = &head;
    for (size_t i = 0; i < names.n; i++) {
        pkg *p = hm_get(&st.sel, names.v[i]);
        char *r = xasprintf("%s>=%s", p->name, p->version);
        dep_parse(r, &reqd[i]);
        free(r);
        *tail = ag(&st, &reqd[i], "Systemaktualisierung", 1, NULL);
        tail = &(*tail)->next;
    }
    int ok = search(s, &st, head);
    if (ok) {
        finish(s, &st, out, NULL);
        for (size_t i = 0; i < out->install.n; i++) {
            pkg *p = out->install.v[i];
            pkg *inst = hm_get(s->installed, p->name);
            hm_set(&out->reasons, p->name, inst && inst->reason && !strcmp(inst->reason, "dep") ? "dep" : (inst ? "explicit" : "dep"));
        }
        if (out->install.n > 1) qsort(out->install.v, out->install.n, sizeof(void *), cmp_pkgname);
    }
    for (size_t i = 0; i < names.n; i++) dep_free(&reqd[i]);
    free(reqd);
    sv_free(&names);
    state_free(&st);
    if (!ok && !s->problems.n) sv_push(&s->problems, "Keine Lösung gefunden");
    return ok ? 0 : -1;
}

static int rest_satisfies(hmap *inst, strvec *targets, const atom *a) {
    HM_FOREACH(inst, i) {
        pkg *q = inst->vals[i];
        if (!sv_contains(targets, q->name) && pkg_satisfies(q, a)) return 1;
    }
    return 0;
}

int solve_remove(solver *s, char **names, size_t n, int cascade, plan *out) {
    strvec targets = {0};
    for (size_t i = 0; i < n; i++) {
        if (!hm_get(s->installed, names[i])) {
            problem(s, xasprintf("Nicht installiert: %s", names[i]));
            continue;
        }
        if (!sv_contains(&targets, names[i])) sv_push(&targets, names[i]);
    }
    if (s->problems.n) {
        sv_free(&targets);
        return -1;
    }
    for (;;) {
        int changed = 0, broke = 0;
        HM_FOREACH(s->installed, i) {
            pkg *p = s->installed->vals[i];
            if (sv_contains(&targets, p->name)) continue;
            for (size_t k = 0; k < p->ndeps; k++) {
                int sat = 0;
                for (size_t j = 0; j < p->deps[k].n && !sat; j++)
                    sat = rest_satisfies(s->installed, &targets, &p->deps[k].alt[j]);
                if (!sat) {
                    broke = 1;
                    if (!cascade) {
                        char *ds = dep_str(&p->deps[k]);
                        problem(s, xasprintf("%s benötigt »%s« (nutze --cascade)", p->name, ds));
                        free(ds);
                    } else if (!sv_contains(&targets, p->name)) {
                        sv_push(&targets, p->name);
                        changed = 1;
                    }
                }
            }
        }
        if (!broke || !cascade || !changed) break;
    }
    if (s->problems.n) {
        sv_free(&targets);
        return -1;
    }
    sv_sort(&targets);
    for (size_t i = 0; i < targets.n; i++) sv_push(&out->remove, targets.v[i]);
    sv_free(&targets);
    return 0;
}

void solve_orphans(solver *s, strvec *out) {
    hmap keep = {0};
    hm_init(&keep);
    HM_FOREACH(s->installed, i) hm_set(&keep, s->installed->keys[i], s->installed->vals[i]);
    int changed = 1;
    while (changed) {
        changed = 0;
        HM_FOREACH(&keep, i) {
            pkg *p = keep.vals[i];
            if (!p->reason || strcmp(p->reason, "dep")) continue;
            int needed = 0;
            HM_FOREACH(&keep, j) {
                pkg *q = keep.vals[j];
                if (q == p) continue;
                for (size_t k = 0; k < q->ndeps && !needed; k++)
                    for (size_t a = 0; a < q->deps[k].n && !needed; a++)
                        if (pkg_satisfies(p, &q->deps[k].alt[a])) needed = 1;
                if (needed) break;
            }
            if (!needed) {
                hm_del(&keep, p->name);
                changed = 1;
            }
        }
    }
    HM_FOREACH(s->installed, i) if (!hm_get(&keep, s->installed->keys[i])) sv_push(out, s->installed->keys[i]);
    sv_sort(out);
    hm_free(&keep);
}

void solver_broken_installed(solver *s, strvec *out) {
    strvec none = {0};
    HM_FOREACH(s->installed, i) {
        pkg *p = s->installed->vals[i];
        for (size_t k = 0; k < p->ndeps; k++) {
            int sat = 0;
            for (size_t j = 0; j < p->deps[k].n && !sat; j++) sat = rest_satisfies(s->installed, &none, &p->deps[k].alt[j]);
            if (!sat) {
                char *ds = dep_str(&p->deps[k]);
                sv_push_own(out, xasprintf("%s: Abhängigkeit »%s« nicht erfüllt", p->name, ds));
                free(ds);
            }
        }
    }
    sv_sort(out);
}
