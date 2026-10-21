/* mapping.c – Abhängigkeits-Mapping deb/rpm/arch -> symfonOS-Paketnamen und Bericht
 *
 * Reihenfolge je Abhängigkeit: Ignorierliste (Glob) -> Tabelle -> Regex-Regeln (POSIX ERE) ->
 * Originalname. Bibliotheken aus DT_NEEDED werden über so:<soname>, Arch-Stil `libfoo.so=1-64`
 * oder den Dateibesitzer in der lokalen Datenbank aufgelöst. Ohne Repo-Index gilt: ungeprüft.
 */
#define _GNU_SOURCE
#include <regex.h>
#include <stdlib.h>
#include <string.h>

#include "convert.h"
#include "toml.h"

typedef struct {
    regex_t re;
    char *repl;
} rule;

struct mapper {
    jval *data;
    rule *rules[3];
    size_t nrules[3];
    hmap by_provide; /* name -> strvec* (Versionen, "\x01" = unversioniert) */
    int have_index;
    database *db;
    regex_t glibc, sover;
};

static int fmt_idx(const char *fmt) { return !strcmp(fmt, "deb") ? 0 : !strcmp(fmt, "rpm") ? 1 : 2; }
static const char *FMTS[] = {"deb", "rpm", "arch"};

static void merge(jval *base, jval *extra) {
    for (size_t i = 0; i < extra->n; i++) {
        jval *b = jget(base, extra->keys[i]), *e = extra->items[i];
        if (b && b->t == J_OBJ && e->t == J_OBJ) {
            for (size_t k = 0; k < e->n; k++) jset(b, e->keys[k], jcopy(e->items[k]));
        } else {
            jset(base, extra->keys[i], jcopy(e));
        }
    }
}

mapper *mapper_new(const char *base_map, const char *extra_map, ptrvec *available, database *db) {
    mapper *m = xcalloc(1, sizeof *m);
    m->data = base_map ? toml_load(base_map) : NULL;
    if (!m->data) {
        if (base_map) fprintf(stderr, "Warnung: Mapping-Tabelle: %s\n", last_error());
        m->data = jobj();
    }
    if (extra_map && path_exists(extra_map)) {
        jval *x = toml_load(extra_map);
        if (x) {
            merge(m->data, x);
            json_free(x);
        }
    }
    jval *rules = jget(m->data, "rules");
    for (int f = 0; f < 3; f++) {
        jval *rl = jgeta(rules, FMTS[f]);
        for (size_t i = 0; rl && i < rl->n; i++) {
            jval *pair = rl->items[i];
            if (pair->t != J_ARR || pair->n != 2) continue;
            m->rules[f] = xrealloc(m->rules[f], (m->nrules[f] + 1) * sizeof(rule));
            rule *r = &m->rules[f][m->nrules[f]];
            if (regcomp(&r->re, pair->items[0]->s, REG_EXTENDED)) {
                fprintf(stderr, "Warnung: ungültige Regel %s\n", pair->items[0]->s);
                continue;
            }
            r->repl = xstrdup(pair->items[1]->s);
            m->nrules[f]++;
        }
    }
    hm_init(&m->by_provide);
    if (available && available->n) {
        m->have_index = 1;
        for (size_t i = 0; i < available->n; i++) {
            pkg *p = available->v[i];
            for (size_t k = 0; k < p->nprov; k++) {
                strvec *v = hm_get(&m->by_provide, p->prov_name[k]);
                if (!v) {
                    v = xcalloc(1, sizeof *v);
                    hm_set(&m->by_provide, p->prov_name[k], v);
                }
                sv_push(v, p->prov_ver[k] ? p->prov_ver[k] : "\x01");
            }
        }
    }
    m->db = db;
    regcomp(&m->glibc,
            "^(libc|libm|libdl|librt|libpthread|libresolv|libutil|libanl|libnsl|libmvec|ld-linux(-x86-64|-aarch64)?)"
            "\\.so\\.[0-9]+$",
            REG_EXTENDED | REG_NOSUB);
    regcomp(&m->sover, "^(.+\\.so)\\.([0-9]+)", REG_EXTENDED);
    return m;
}

void mapper_free(mapper *m) {
    if (!m) return;
    for (int f = 0; f < 3; f++) {
        for (size_t i = 0; i < m->nrules[f]; i++) {
            regfree(&m->rules[f][i].re);
            free(m->rules[f][i].repl);
        }
        free(m->rules[f]);
    }
    HM_FOREACH(&m->by_provide, i) {
        sv_free(m->by_provide.vals[i]);
        free(m->by_provide.vals[i]);
    }
    hm_free(&m->by_provide);
    regfree(&m->glibc);
    regfree(&m->sover);
    json_free(m->data);
    free(m);
}

static char *apply_rule(const rule *r, const char *s) {
    regmatch_t g[10];
    if (regexec(&r->re, s, 10, g, 0)) return NULL;
    sbuf b = {0};
    sb_init(&b);
    sb_addn(&b, s, (size_t)g[0].rm_so);
    for (const char *p = r->repl; *p; p++) {
        if (*p == '\\' && p[1] >= '0' && p[1] <= '9') {
            int k = p[1] - '0';
            if (g[k].rm_so >= 0) sb_addn(&b, s + g[k].rm_so, (size_t)(g[k].rm_eo - g[k].rm_so));
            p++;
        } else {
            sb_addc(&b, *p);
        }
    }
    sb_add(&b, s + g[0].rm_eo);
    return sb_take(&b);
}

/* NULL = ignorieren, sonst Kandidatenliste */
static strvec *candidates(mapper *m, const char *fmt, const char *name) {
    jval *ign = jgeta(jget(m->data, "ignore"), fmt);
    for (size_t i = 0; ign && i < ign->n; i++)
        if (glob_match(ign->items[i]->s, name)) return NULL;
    strvec *out = xcalloc(1, sizeof *out);
    if (name[0] == '/') {
        const char *f = jgets(jget(m->data, "files"), name, NULL);
        if (f) sv_push(out, f);
        if (m->db) {
            char *rel = str_starts(name, "/bin/") ? xasprintf("usr/bin/%s", name + 5) : xstrdup(name + 1);
            db_owners(m->db, rel, out);
            free(rel);
        }
        return out;
    }
    jval *tab = jget(m->data, fmt);
    const char *t = jgets(tab, name, NULL);
    if (t) {
        if (!*t) {
            free(out);
            return NULL;
        }
        sv_push(out, t);
    }
    int f = fmt_idx(fmt);
    for (size_t i = 0; i < m->nrules[f]; i++) {
        char *r = apply_rule(&m->rules[f][i], name);
        if (!r) continue;
        if (!strcmp(r, "!ignore")) {
            free(r);
            if (!out->n) {
                free(out);
                return NULL;
            }
            return out;
        }
        if (!str_starts(r, "so:")) {
            str_lower(r);
            char *x = str_replace(r, "::", "-");
            free(r);
            r = x;
        }
        if (!sv_contains(out, r)) sv_push(out, r);
        free(r);
    }
    if (!sv_contains(out, name)) sv_push(out, name);
    return out;
}

static void cands_free(strvec *v) {
    if (v) {
        sv_free(v);
        free(v);
    }
}

static int exists(mapper *m, const char *n) { return m->have_index && hm_get(&m->by_provide, n); }

static char *soname_target(mapper *m, const char *so) {
    char *k = xasprintf("so:%s", so);
    if (exists(m, k)) return k;
    free(k);
    regmatch_t g[3];
    if (m->have_index && !regexec(&m->sover, so, 3, g, 0)) {
        char *base = xstrndup(so + g[1].rm_so, (size_t)(g[1].rm_eo - g[1].rm_so));
        char *major = xstrndup(so + g[2].rm_so, (size_t)(g[2].rm_eo - g[2].rm_so));
        strvec *vs = hm_get(&m->by_provide, base);
        char *res = NULL;
        for (size_t i = 0; vs && i < vs->n && !res; i++) {
            const char *v = vs->v[i];
            size_t ml = strlen(major);
            if (v[0] != '\x01' && !strncmp(v, major, ml) && (v[ml] == '-' || v[ml] == 0))
                res = xasprintf("%s=%s", base, v);
        }
        free(base);
        free(major);
        if (res) return res;
    }
    if (m->db) {
        const char *dirs[] = {"usr/lib/", "usr/lib64/"};
        for (int i = 0; i < 2; i++) {
            char *p = xasprintf("%s%s", dirs[i], so);
            strvec own = {0};
            db_owners(m->db, p, &own);
            free(p);
            if (own.n) {
                char *r = xstrdup(own.v[0]);
                sv_free(&own);
                return r;
            }
            sv_free(&own);
        }
    }
    return NULL;
}

static char *resolve(mapper *m, const char *cand) {
    if (str_starts(cand, "so:")) {
        if (!regexec(&m->glibc, cand + 3, 0, NULL, 0)) return xstrdup("glibc");
        return soname_target(m, cand + 3);
    }
    return exists(m, cand) ? xstrdup(cand) : NULL;
}

static char *fmt_atom(const char *name, const atom *a, const char *fmt, int keep) {
    if (!a->op || !a->version) return xstrdup(name);
    if (!strcmp(fmt, "arch") || keep) return xasprintf("%s%s%s", name, op_str(a->op), a->version);
    if (a->op == OP_GE || a->op == OP_GT) {
        /* Epoch/Revision anderer Distros sind nicht vergleichbar */
        const char *v = a->version, *c = strchr(v, ':');
        if (c) v = c + 1;
        const char *d = strrchr(v, '-');
        char *ver = d ? xstrndup(v, (size_t)(d - v)) : xstrdup(v);
        char *r = xasprintf("%s>=%s", name, ver);
        free(ver);
        return r;
    }
    return xstrdup(name);
}

/* --- Bericht ----------------------------------------------------------------------- */
report *report_new(const char *pkg, const char *fmt) {
    report *r = xcalloc(1, sizeof *r);
    r->r = jobj();
    jset(r->r, "package", jstr(pkg ? pkg : ""));
    jset(r->r, "source_format", jstr(fmt));
    const char *lists[] = {"mapped", "ignored", "unresolved", "optional_missing", "unverified", "warnings", "depends"};
    for (size_t i = 0; i < sizeof lists / sizeof *lists; i++) jset(r->r, lists[i], jarr());
    jset(r->r, "output", jstr(""));
    return r;
}
void report_free(report *r) {
    if (!r) return;
    json_free(r->r);
    free(r);
}
static void push_str(report *r, const char *key, const char *s) { jpush(jgeta(r->r, key), jstr(s)); }
void report_warn(report *r, const char *msg) { push_str(r, "warnings", msg); }
int report_ok(const report *r) { return jgeta(r->r, "unresolved")->n == 0; }

char *report_text(const report *r) {
    sbuf b = {0};
    sb_init(&b);
    const char *out = jgets(r->r, "output", "");
    sb_addf(&b, "Paket: %s (%s) -> %s", jgets(r->r, "package", ""), jgets(r->r, "source_format", ""),
            *out ? out : "–");
    jval *a = jgeta(r->r, "mapped");
    for (size_t i = 0; i < a->n; i++) sb_addf(&b, "\n  ✓ %s  ->  %s", a->items[i]->items[0]->s, a->items[i]->items[1]->s);
    a = jgeta(r->r, "unresolved");
    for (size_t i = 0; i < a->n; i++)
        sb_addf(&b, "\n  ✗ FEHLT: %s  (%s)", jgets(a->items[i], "dep", ""), jgets(a->items[i], "kind", ""));
    a = jgeta(r->r, "optional_missing");
    for (size_t i = 0; i < a->n; i++) sb_addf(&b, "\n  ○ optional, nicht verfügbar: %s", a->items[i]->s);
    a = jgeta(r->r, "unverified");
    for (size_t i = 0; i < a->n; i++) sb_addf(&b, "\n  ? ungeprüft (kein Repo-Index): %s", a->items[i]->s);
    a = jgeta(r->r, "ignored");
    if (a->n) {
        sb_add(&b, "\n  – ignoriert: ");
        for (size_t i = 0; i < a->n; i++) sb_addf(&b, "%s%s", i ? ", " : "", a->items[i]->s);
    }
    a = jgeta(r->r, "warnings");
    for (size_t i = 0; i < a->n; i++) sb_addf(&b, "\n  ! %s", a->items[i]->s);
    return sb_take(&b);
}

/* --- Mapping ---------------------------------------------------------------------- */
strvec map_deps(mapper *m, const char *fmt, const deplist *deps, report *rep, int optional, int keep) {
    strvec result = {0};
    for (size_t i = 0; i < deps->n; i++) {
        const dep *d = &deps->v[i];
        strvec alts = {0}, unver = {0};
        int ignored = 0;
        for (size_t k = 0; k < d->n; k++) {
            const atom *a = &d->alt[k];
            strvec *c = candidates(m, fmt, a->name);
            if (!c) {
                ignored = 1;
                continue;
            }
            char *hit = NULL;
            for (size_t j = 0; j < c->n && !hit; j++) hit = resolve(m, c->v[j]);
            if (hit) {
                char *s = fmt_atom(hit, a, fmt, keep);
                if (!sv_contains(&alts, s)) sv_push(&alts, s);
                free(s);
                free(hit);
            } else if (!m->have_index && c->n) {
                char *s = fmt_atom(c->v[0], a, fmt, keep);
                if (!sv_contains(&unver, s)) sv_push(&unver, s);
                free(s);
            }
            cands_free(c);
        }
        char *src = dep_str(d);
        strvec *use = alts.n ? &alts : unver.n ? &unver : NULL;
        if (use) {
            sbuf b = {0};
            sb_init(&b);
            for (size_t j = 0; j < use->n; j++) sb_addf(&b, "%s%s", j ? " | " : "", use->v[j]);
            if (use == &alts) {
                jval *pair = jarr();
                jpush(pair, jstr(src));
                jpush(pair, jstr(b.s));
                jpush(jgeta(rep->r, "mapped"), pair);
            } else {
                char *x = xasprintf("%s -> %s", src, b.s);
                push_str(rep, "unverified", x);
                free(x);
            }
            sv_push_own(&result, sb_take(&b));
        } else if (ignored) {
            push_str(rep, "ignored", src);
        } else if (optional) {
            push_str(rep, "optional_missing", src);
        } else {
            jval *u = jobj();
            jset(u, "dep", jstr(src));
            jset(u, "kind", jstr("Abhängigkeit"));
            jpush(jgeta(rep->r, "unresolved"), u);
        }
        free(src);
        sv_free(&alts);
        sv_free(&unver);
    }
    return result;
}

strvec map_sonames(mapper *m, const strvec *sonames, report *rep) {
    strvec out = {0};
    for (size_t i = 0; i < sonames->n; i++) {
        const char *so = sonames->v[i];
        char *t = !regexec(&m->glibc, so, 0, NULL, 0) ? xstrdup("glibc") : soname_target(m, so);
        char *label = xasprintf("ELF %s", so);
        if (t) {
            jval *pair = jarr();
            jpush(pair, jstr(label));
            jpush(pair, jstr(t));
            jpush(jgeta(rep->r, "mapped"), pair);
            sv_push_own(&out, t);
        } else if (!m->have_index && !m->db) {
            char *x = xasprintf("%s -> so:%s", label, so);
            push_str(rep, "unverified", x);
            free(x);
            sv_push_own(&out, xasprintf("so:%s", so));
        } else {
            jval *u = jobj();
            jset(u, "dep", jstr(so));
            jset(u, "kind", jstr("Bibliothek (DT_NEEDED)"));
            jpush(jgeta(rep->r, "unresolved"), u);
        }
        free(label);
    }
    return out;
}
