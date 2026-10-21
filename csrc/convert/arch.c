/* arch.c – Arch-Linux-Pakete (.pkg.tar.zst/xz/gz) -> Zwischenformat */
#define _GNU_SOURCE
#include <archive.h>
#include <archive_entry.h>
#include <stdlib.h>
#include <string.h>

#include "convert.h"

static void parse_atom(const char *s, deplist *out) {
    char *t = xstrdup(s);
    char *c = strstr(t, ": ");
    if (c) *c = 0; /* optdepend-Beschreibung */
    char *x = str_trim(t);
    atom a;
    if (atom_parse(x, &a) == 0) {
        dep d = {xcalloc(1, sizeof(atom)), 1};
        d.alt[0] = a;
        dl_push_dep(out, d);
    }
    free(t);
}

int parse_arch(const char *path, const char *workdir, converted *c) {
    char *raw;
    size_t len;
    if (read_file(path, &raw, &len)) return -1;
    /* Metadaten lesen */
    struct archive *a = archive_read_new();
    archive_read_support_filter_all(a);
    archive_read_support_format_tar(a);
    char *pkginfo = NULL, *install = NULL;
    if (archive_read_open_memory(a, raw, len) == ARCHIVE_OK) {
        struct archive_entry *e;
        while (archive_read_next_header(a, &e) == ARCHIVE_OK) {
            const char *n = archive_entry_pathname(e);
            char **dst = !strcmp(n, ".PKGINFO") ? &pkginfo : !strcmp(n, ".INSTALL") ? &install : NULL;
            if (!dst) {
                archive_read_data_skip(a);
                continue;
            }
            sbuf b = {0};
            sb_init(&b);
            char buf[8192];
            la_ssize_t r;
            while ((r = archive_read_data(a, buf, sizeof buf)) > 0) sb_addn(&b, buf, (size_t)r);
            *dst = sb_take(&b);
        }
    }
    archive_read_free(a);
    int rc = -1;
    if (!pkginfo) {
        set_error("%s: .PKGINFO fehlt", path_basename(path));
        goto out;
    }
    static const char *meta[] = {".PKGINFO", ".MTREE", ".BUILDINFO", ".INSTALL", ".CHANGELOG", NULL};
    c->tree = path_join(workdir, "tree");
    if (extract_archive_mem(raw, len, c->tree, &c->warnings, meta)) goto out;
    hmap kv = {0};
    hm_init(&kv);
    char *save = NULL;
    for (char *l = strtok_r(pkginfo, "\n", &save); l; l = strtok_r(NULL, "\n", &save)) {
        if (l[0] == '#') continue;
        char *eq = strstr(l, " = ");
        if (!eq) continue;
        *eq = 0;
        char *k = str_trim(l), *v = str_trim(eq + 3);
        strvec *sv = hm_get(&kv, k);
        if (!sv) {
            sv = xcalloc(1, sizeof *sv);
            hm_set(&kv, k, sv);
        }
        sv_push(sv, v);
    }
#define G(k) (hm_get(&kv, k) && ((strvec *)hm_get(&kv, k))->n ? ((strvec *)hm_get(&kv, k))->v[0] : "")
#define L(k) ((strvec *)hm_get(&kv, k))
    c->name = xstrdup(G("pkgname"));
    c->version = xstrdup(G("pkgver"));
    c->source_version = xstrdup(G("pkgver"));
    c->arch = xstrdup(*G("arch") ? G("arch") : "x86_64");
    c->summary = xstrdup(G("pkgdesc"));
    c->url = xstrdup(G("url"));
    c->packager = xstrdup(G("packager"));
    strvec *lic = L("license");
    sbuf lb = {0};
    sb_init(&lb);
    for (size_t i = 0; lic && i < lic->n; i++) sb_addf(&lb, "%s%s", i ? " AND " : "", lic->v[i]);
    c->license = lb.len ? sb_take(&lb) : (free(lb.s), xstrdup("unknown"));
    const char *dk[] = {"depend", "optdepend", "conflict"};
    deplist *dl[] = {&c->depends, &c->optdepends, &c->conflicts};
    for (int i = 0; i < 3; i++) {
        strvec *v = L(dk[i]);
        for (size_t k = 0; v && k < v->n; k++) parse_atom(v->v[k], dl[i]);
    }
    const char *sk[] = {"provides", "replaces", "backup"};
    strvec *sl[] = {&c->provides, &c->replaces, &c->backup};
    for (int i = 0; i < 3; i++) {
        strvec *v = L(sk[i]);
        for (size_t k = 0; v && k < v->n; k++) sv_push(sl[i], v->v[k]);
    }
    if (install) {
        struct {
            const char *hook, *args;
        } calls[] = {{"pre_install", "\"$SYP_NEW_VERSION\""},
                     {"post_install", "\"$SYP_NEW_VERSION\""},
                     {"pre_upgrade", "\"$SYP_NEW_VERSION\" \"$SYP_OLD_VERSION\""},
                     {"post_upgrade", "\"$SYP_NEW_VERSION\" \"$SYP_OLD_VERSION\""},
                     {"pre_remove", "\"$SYP_OLD_VERSION\""},
                     {"post_remove", "\"$SYP_OLD_VERSION\""}};
        char *norm = str_replace(install, " ()", "()");
        for (size_t i = 0; i < sizeof calls / sizeof *calls; i++) {
            char *fn = xasprintf("%s()", calls[i].hook);
            if (strstr(norm, fn))
                jset(c->scripts, calls[i].hook, jstr_own(xasprintf("%s\n%s %s\n", install, calls[i].hook, calls[i].args)));
            free(fn);
        }
        free(norm);
    }
    HM_FOREACH(&kv, i) {
        sv_free(kv.vals[i]);
        free(kv.vals[i]);
    }
    hm_free(&kv);
    rc = 0;
out:
    free(pkginfo);
    free(install);
    free(raw);
    return rc;
}
