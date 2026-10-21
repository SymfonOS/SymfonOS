/* deb.c – Debian/Ubuntu .deb -> Zwischenformat */
#define _GNU_SOURCE
#include <ctype.h>
#include <stdlib.h>
#include <string.h>

#include "convert.h"

typedef struct {
    char *name;
    char *data;
    size_t len;
} armember;

static int read_ar(const char *path, armember **out, size_t *n) {
    char *d;
    size_t len;
    if (read_file(path, &d, &len)) return -1;
    if (len < 8 || memcmp(d, "!<arch>\n", 8)) {
        free(d);
        set_error("%s: kein ar-Archiv", path_basename(path));
        return -1;
    }
    *out = NULL;
    *n = 0;
    size_t off = 8;
    while (off + 60 <= len) {
        char name[17], size[11];
        memcpy(name, d + off, 16);
        name[16] = 0;
        memcpy(size, d + off + 48, 10);
        size[10] = 0;
        size_t sz = strtoul(size, NULL, 10);
        if (off + 60 + sz > len) break;
        char *nm = str_trim(name);
        size_t nl = strlen(nm);
        if (nl && nm[nl - 1] == '/') nm[nl - 1] = 0;
        *out = xrealloc(*out, (*n + 1) * sizeof(armember));
        (*out)[*n].name = xstrdup(nm);
        (*out)[*n].data = xstrndup(d + off + 60, sz);
        (*out)[*n].len = sz;
        (*n)++;
        off += 60 + sz + (sz & 1);
    }
    free(d);
    return 0;
}

/* RFC-822-Kontrollfelder; Fortsetzungszeilen beginnen mit Leerzeichen */
static jval *parse_control(const char *text) {
    jval *f = jobj();
    char *copy = xstrdup(text), *save = NULL, *cur = NULL;
    for (char *line = strtok_r(copy, "\n", &save); line; line = strtok_r(NULL, "\n", &save)) {
        if ((line[0] == ' ' || line[0] == '\t') && cur) {
            jval *v = jget(f, cur);
            char *n = xasprintf("%s\n%s", v->s, line + 1);
            jset(f, cur, jstr_own(n));
        } else {
            char *c = strchr(line, ':');
            if (!c) continue;
            *c = 0;
            free(cur);
            cur = xstrdup(str_trim(line));
            jset(f, cur, jstr(str_trim(c + 1)));
        }
    }
    free(cur);
    free(copy);
    return f;
}

static depop deb_op(const char *o) {
    if (!strcmp(o, "<<")) return OP_LT;
    if (!strcmp(o, "<=")) return OP_LE;
    if (!strcmp(o, "=")) return OP_EQ;
    if (!strcmp(o, ">=")) return OP_GE;
    if (!strcmp(o, ">>")) return OP_GT;
    if (!strcmp(o, "<")) return OP_LE; /* veraltete Schreibweise */
    if (!strcmp(o, ">")) return OP_GE;
    return OP_NONE;
}

/* Entfernt [arch]-Einschränkungen und <profile> */
static char *strip_restrictions(const char *s) {
    sbuf b = {0};
    sb_init(&b);
    int depth_sq = 0, depth_ang = 0;
    for (const char *p = s; *p; p++) {
        if (*p == '[') depth_sq++;
        else if (*p == ']' && depth_sq) depth_sq--;
        else if (*p == '<' && !depth_sq && p[1] != '<' && p[1] != '=' && (p == s || p[-1] != '<') &&
                 (p == s || isspace((unsigned char)p[-1])) && !strchr(p, '(')) depth_ang++;
        else if (*p == '>' && depth_ang) depth_ang--;
        else if (!depth_sq && !depth_ang) sb_addc(&b, *p);
    }
    return sb_take(&b);
}

static void parse_rel(const char *s, deplist *out) {
    if (!s || !*s) return;
    char *copy = xstrdup(s), *save = NULL;
    for (char *group = strtok_r(copy, ",", &save); group; group = strtok_r(NULL, ",", &save)) {
        char *g = str_trim(group);
        if (!*g) continue;
        dep d = {0};
        char *s2 = NULL;
        for (char *alt = strtok_r(g, "|", &s2); alt; alt = strtok_r(NULL, "|", &s2)) {
            char *clean = strip_restrictions(alt), *p = str_trim(clean);
            size_t nl = strcspn(p, " \t([<:");
            if (!nl) {
                free(clean);
                continue;
            }
            atom a = {xstrndup(p, nl), OP_NONE, NULL};
            char *rest = strchr(p + nl, '(');
            if (rest) {
                rest++;
                while (isspace((unsigned char)*rest)) rest++;
                size_t ol = strspn(rest, "<>=");
                char *op = xstrndup(rest, ol);
                a.op = deb_op(op);
                free(op);
                rest += ol;
                while (isspace((unsigned char)*rest)) rest++;
                size_t vl = strcspn(rest, ") \t");
                if (a.op && vl) {
                    char *v = xstrndup(rest, vl);
                    a.version = norm_version(v);
                    free(v);
                } else {
                    a.op = OP_NONE;
                }
            }
            d.alt = xrealloc(d.alt, (d.n + 1) * sizeof(atom));
            d.alt[d.n++] = a;
            free(clean);
        }
        if (d.n) dl_push_dep(out, d);
    }
    free(copy);
}

static char *deb_license(const char *tree, const char *pkg) {
    char *rel = xasprintf("usr/share/doc/%s/copyright", pkg), *p = path_join(tree, rel), *txt;
    free(rel);
    strvec lic = {0};
    if (read_file(p, &txt, NULL) == 0) {
        char *save = NULL;
        for (char *l = strtok_r(txt, "\n", &save); l; l = strtok_r(NULL, "\n", &save)) {
            if (!str_starts(l, "License:")) continue;
            char *v = str_trim(l + 8);
            if (*v && !sv_contains(&lic, v)) sv_push(&lic, v);
        }
        free(txt);
    }
    free(p);
    if (!lic.n) return xstrdup("unknown");
    sbuf b = {0};
    sb_init(&b);
    for (size_t i = 0; i < lic.n && i < 4; i++) sb_addf(&b, "%s%s", i ? " AND " : "", lic.v[i]);
    sv_free(&lic);
    return sb_take(&b);
}

int parse_deb(const char *path, const char *workdir, converted *c) {
    armember *m;
    size_t n;
    if (read_ar(path, &m, &n)) return -1;
    armember *ctl = NULL, *data = NULL;
    for (size_t i = 0; i < n; i++) {
        if (str_starts(m[i].name, "control.tar")) ctl = &m[i];
        if (str_starts(m[i].name, "data.tar")) data = &m[i];
    }
    int rc = -1;
    char *ctldir = path_join(workdir, "control");
    c->tree = path_join(workdir, "tree");
    if (!ctl || !data) {
        set_error("%s: control/data fehlen", path_basename(path));
        goto out;
    }
    if (extract_archive_mem(ctl->data, ctl->len, ctldir, NULL, NULL) ||
        extract_archive_mem(data->data, data->len, c->tree, &c->warnings, NULL))
        goto out;
    char *cp = path_join(ctldir, "control"), *txt;
    if (read_file(cp, &txt, NULL)) {
        free(cp);
        goto out;
    }
    free(cp);
    jval *f = parse_control(txt);
    free(txt);
    const char *pname = jgets(f, "Package", NULL), *ver = jgets(f, "Version", NULL);
    if (!pname || !ver) {
        json_free(f);
        set_error("%s: Package/Version fehlen", path_basename(path));
        goto out;
    }
    c->name = norm_name(pname);
    c->version = norm_version(ver);
    c->source_version = xstrdup(ver);
    c->arch = xstrdup(jgets(f, "Architecture", "all"));
    c->url = xstrdup(jgets(f, "Homepage", ""));
    c->packager = xstrdup(jgets(f, "Maintainer", ""));
    const char *desc = jgets(f, "Description", "");
    const char *nl = strchr(desc, '\n');
    c->summary = nl ? xstrndup(desc, (size_t)(nl - desc)) : xstrdup(desc);
    sbuf ld = {0};
    sb_init(&ld);
    if (nl) {
        char *rest = xstrdup(nl + 1), *s2 = NULL;
        for (char *l = strtok_r(rest, "\n", &s2); l; l = strtok_r(NULL, "\n", &s2)) {
            char *t = str_trim(l);
            sb_addf(&ld, "%s%s", ld.len ? "\n" : "", strcmp(t, ".") ? l : "");
        }
        free(rest);
    }
    char *ldt = sb_take(&ld);
    c->description = xstrdup(str_trim(ldt));
    free(ldt);
    parse_rel(jgets(f, "Pre-Depends", ""), &c->depends);
    parse_rel(jgets(f, "Depends", ""), &c->depends);
    parse_rel(jgets(f, "Recommends", ""), &c->optdepends);
    parse_rel(jgets(f, "Suggests", ""), &c->optdepends);
    parse_rel(jgets(f, "Conflicts", ""), &c->conflicts);
    parse_rel(jgets(f, "Breaks", ""), &c->conflicts);
    c->license = deb_license(c->tree, pname);
    deplist prov = {0}, repl = {0};
    parse_rel(jgets(f, "Provides", ""), &prov);
    for (size_t i = 0; i < prov.n; i++) {
        atom *a = &prov.v[i].alt[0];
        char *nn = norm_name(a->name);
        sv_push_own(&c->provides, a->version ? xasprintf("%s=%s", nn, a->version) : xstrdup(nn));
        free(nn);
    }
    parse_rel(jgets(f, "Replaces", ""), &repl);
    for (size_t i = 0; i < repl.n; i++)
        for (size_t k = 0; k < c->conflicts.n; k++)
            for (size_t j = 0; j < c->conflicts.v[k].n; j++)
                if (!strcmp(c->conflicts.v[k].alt[j].name, repl.v[i].alt[0].name) &&
                    !sv_contains(&c->replaces, repl.v[i].alt[0].name))
                    sv_push(&c->replaces, repl.v[i].alt[0].name);
    dl_free(&prov);
    dl_free(&repl);
    json_free(f);
    char *cf = path_join(ctldir, "conffiles");
    if (read_file(cf, &txt, NULL) == 0) {
        char *s2 = NULL;
        for (char *l = strtok_r(txt, "\n", &s2); l; l = strtok_r(NULL, "\n", &s2)) {
            char *t = str_trim(l);
            if (str_starts(t, "remove-on-upgrade ")) t += 18;
            if (*t) sv_push(&c->backup, t);
        }
        free(txt);
    }
    free(cf);
    struct {
        const char *file, *hook, *args;
    } hooks[] = {{"preinst", "pre_install", "install"},
                 {"preinst", "pre_upgrade", "upgrade \"$SYP_OLD_VERSION\""},
                 {"postinst", "post_install", "configure"},
                 {"postinst", "post_upgrade", "configure \"$SYP_OLD_VERSION\""},
                 {"prerm", "pre_remove", "remove"},
                 {"postrm", "post_remove", "remove"}};
    for (size_t i = 0; i < sizeof hooks / sizeof *hooks; i++) {
        char *p = path_join(ctldir, hooks[i].file), *body;
        if (read_file(p, &body, NULL) == 0) {
            jset(c->scripts, hooks[i].hook, jstr_own(wrap_script(body, hooks[i].args)));
            free(body);
        }
        free(p);
    }
    const char *extra[] = {"triggers", "templates", "config"};
    for (int i = 0; i < 3; i++) {
        char *p = path_join(ctldir, extra[i]);
        if (path_exists(p)) sv_push_own(&c->warnings, xasprintf("dpkg-%s vorhanden – wird von syp nicht ausgewertet", extra[i]));
        free(p);
    }
    rc = 0;
out:
    for (size_t i = 0; i < n; i++) {
        free(m[i].name);
        free(m[i].data);
    }
    free(m);
    free(ctldir);
    return rc;
}
