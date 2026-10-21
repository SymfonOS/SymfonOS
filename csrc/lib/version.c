/* version.c – Versionsvergleich und Abhängigkeitsausdrücke */
#define _GNU_SOURCE
#include "version.h"

#include <ctype.h>
#include <stdlib.h>
#include <string.h>

int rpmvercmp(const char *a, const char *b) {
    if (strcmp(a, b) == 0) return 0;
    size_t i = 0, j = 0, la = strlen(a), lb = strlen(b);
    while (i < la && j < lb) {
        while (i < la && !isalnum((unsigned char)a[i])) i++;
        while (j < lb && !isalnum((unsigned char)b[j])) j++;
        if (i >= la || j >= lb) break;
        size_t si = i, sj = j;
        int isnum = isdigit((unsigned char)a[i]);
        if (isnum) {
            while (i < la && isdigit((unsigned char)a[i])) i++;
            while (j < lb && isdigit((unsigned char)b[j])) j++;
        } else {
            while (i < la && isalpha((unsigned char)a[i])) i++;
            while (j < lb && isalpha((unsigned char)b[j])) j++;
        }
        if (j == sj) return isnum ? 1 : -1;
        if (isnum) {
            while (si < i - 1 && a[si] == '0') si++;   /* führende Nullen */
            while (sj < j - 1 && b[sj] == '0') sj++;
            size_t na = i - si, nb = j - sj;
            if (na != nb) return na > nb ? 1 : -1;
            int c = strncmp(a + si, b + sj, na);
            if (c) return c > 0 ? 1 : -1;
        } else {
            size_t na = i - si, nb = j - sj, n = na < nb ? na : nb;
            int c = strncmp(a + si, b + sj, n);
            if (c) return c > 0 ? 1 : -1;
            if (na != nb) return na > nb ? 1 : -1;
        }
    }
    if (i >= la && j >= lb) return 0;
    const char *ra = i < la ? a + i : "", *rb = j < lb ? b + j : "";
    if ((!*ra && !isalpha((unsigned char)*rb)) || isalpha((unsigned char)*ra)) return -1;
    return 1;
}

typedef struct {
    long epoch;
    char *ver, *rel;
} pver;

static void vparse(const char *s, pver *v) {
    v->epoch = 0;
    const char *colon = strchr(s, ':');
    if (colon) {
        int alldig = colon > s;
        for (const char *p = s; p < colon; p++)
            if (!isdigit((unsigned char)*p)) alldig = 0;
        if (alldig) v->epoch = strtol(s, NULL, 10);
        s = colon + 1;
    }
    const char *dash = strrchr(s, '-');
    if (dash) {
        v->ver = xstrndup(s, (size_t)(dash - s));
        v->rel = xstrdup(dash + 1);
    } else {
        v->ver = xstrdup(s);
        v->rel = NULL;
    }
}

int vercmp(const char *a, const char *b) {
    pver x, y;
    vparse(a, &x);
    vparse(b, &y);
    int c;
    if (x.epoch != y.epoch) c = x.epoch > y.epoch ? 1 : -1;
    else {
        c = rpmvercmp(x.ver, y.ver);
        if (!c && x.rel && y.rel) c = rpmvercmp(x.rel, y.rel);
    }
    free(x.ver);
    free(x.rel);
    free(y.ver);
    free(y.rel);
    return c;
}

const char *op_str(depop op) {
    switch (op) {
    case OP_EQ: return "=";
    case OP_GE: return ">=";
    case OP_LE: return "<=";
    case OP_LT: return "<";
    case OP_GT: return ">";
    default: return "";
    }
}

int atom_parse(const char *s, atom *out) {
    while (*s == ' ' || *s == '\t') s++;
    const char *p = s;
    while (*p && !strchr(" \t<>=|", *p)) p++;
    if (p == s) return -1;
    out->name = xstrndup(s, (size_t)(p - s));
    out->op = OP_NONE;
    out->version = NULL;
    while (*p == ' ' || *p == '\t') p++;
    if (*p == 0) return 0;
    if (!strncmp(p, ">=", 2)) out->op = OP_GE, p += 2;
    else if (!strncmp(p, "<=", 2)) out->op = OP_LE, p += 2;
    else if (*p == '=') out->op = OP_EQ, p++;
    else if (*p == '<') out->op = OP_LT, p++;
    else if (*p == '>') out->op = OP_GT, p++;
    else goto bad;
    while (*p == ' ' || *p == '\t') p++;
    const char *v = p;
    while (*p && !strchr(" \t|", *p)) p++;
    if (p == v) goto bad;
    out->version = xstrndup(v, (size_t)(p - v));
    while (*p == ' ' || *p == '\t') p++;
    if (*p) goto bad;
    return 0;
bad:
    free(out->name);
    free(out->version);
    out->name = out->version = NULL;
    return -1;
}

int dep_parse(const char *s, dep *out) {
    out->alt = NULL;
    out->n = 0;
    char *copy = xstrdup(s), *save = NULL;
    for (char *tok = strtok_r(copy, "|", &save); tok; tok = strtok_r(NULL, "|", &save)) {
        atom a;
        if (atom_parse(tok, &a)) {
            free(copy);
            dep_free(out);
            set_error("Ungültiger Abhängigkeitsausdruck: '%s'", s);
            return -1;
        }
        out->alt = xrealloc(out->alt, (out->n + 1) * sizeof(atom));
        out->alt[out->n++] = a;
    }
    free(copy);
    if (!out->n) {
        set_error("Ungültiger Abhängigkeitsausdruck: '%s'", s);
        return -1;
    }
    return 0;
}

void dep_free(dep *d) {
    for (size_t i = 0; i < d->n; i++) {
        free(d->alt[i].name);
        free(d->alt[i].version);
    }
    free(d->alt);
    d->alt = NULL;
    d->n = 0;
}

char *atom_str(const atom *a) {
    return a->op ? xasprintf("%s%s%s", a->name, op_str(a->op), a->version) : xstrdup(a->name);
}

char *dep_str(const dep *d) {
    sbuf b = {0};
    sb_init(&b);
    for (size_t i = 0; i < d->n; i++) {
        if (i) sb_add(&b, " | ");
        char *s = atom_str(&d->alt[i]);
        sb_add(&b, s);
        free(s);
    }
    return sb_take(&b);
}

int atom_matches_version(const atom *a, const char *version) {
    if (a->op == OP_NONE) return 1;
    if (!version) return 0;
    int c = vercmp(version, a->version);
    switch (a->op) {
    case OP_EQ: return c == 0;
    case OP_GE: return c >= 0;
    case OP_LE: return c <= 0;
    case OP_GT: return c > 0;
    case OP_LT: return c < 0;
    default: return 1;
    }
}

void provide_parts(const char *s, char **name, char **version) {
    const char *eq = strchr(s, '=');
    if (eq) {
        char *n = xstrndup(s, (size_t)(eq - s)), *v = xstrdup(eq + 1);
        *name = xstrdup(str_trim(n));
        *version = xstrdup(str_trim(v));
        free(n);
        free(v);
    } else {
        char *n = xstrdup(s);
        *name = xstrdup(str_trim(n));
        free(n);
        *version = NULL;
    }
}
