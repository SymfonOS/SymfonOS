/* rpm.c – Fedora/openSUSE .rpm -> Zwischenformat (Header-Parser in C, Payload über libarchive) */
#define _GNU_SOURCE
#include <arpa/inet.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#include "convert.h"

enum {
    T_NAME = 1000, T_VERSION = 1001, T_RELEASE = 1002, T_EPOCH = 1003, T_SUMMARY = 1004, T_DESCRIPTION = 1005,
    T_LICENSE = 1014, T_PACKAGER = 1015, T_URL = 1020, T_ARCH = 1022, T_PREIN = 1023, T_POSTIN = 1024,
    T_PREUN = 1025, T_POSTUN = 1026, T_FILEFLAGS = 1037, T_PROVIDENAME = 1047, T_REQUIREFLAGS = 1048,
    T_REQUIRENAME = 1049, T_REQUIREVERSION = 1050, T_CONFLICTFLAGS = 1053, T_CONFLICTNAME = 1054,
    T_CONFLICTVERSION = 1055, T_PREINPROG = 1085, T_POSTINPROG = 1086, T_PREUNPROG = 1087,
    T_POSTUNPROG = 1088, T_OBSOLETENAME = 1090, T_PROVIDEFLAGS = 1112, T_PROVIDEVERSION = 1113,
    T_DIRINDEXES = 1116, T_BASENAMES = 1117, T_DIRNAMES = 1118, T_PAYLOADFORMAT = 1124,
    T_RECOMMENDNAME = 5046, T_SUGGESTNAME = 5049
};
#define SENSE_LESS 2
#define SENSE_GREATER 4
#define SENSE_EQUAL 8
#define SENSE_RPMLIB (1 << 24)

typedef struct {
    int tag, type;
    strvec strs;
    uint32_t *ints;
    size_t nints;
} tagval;

typedef struct {
    tagval *v;
    size_t n;
} header;

static uint32_t be32(const unsigned char *p) { return (uint32_t)p[0] << 24 | (uint32_t)p[1] << 16 | (uint32_t)p[2] << 8 | p[3]; }

static int read_header(const unsigned char *d, size_t len, size_t off, header *h, size_t *end) {
    if (off + 16 > len || memcmp(d + off, "\x8e\xad\xe8\x01", 4)) {
        set_error("RPM-Header-Magic fehlt");
        return -1;
    }
    uint32_t nidx = be32(d + off + 8), hsize = be32(d + off + 12);
    size_t idx = off + 16, store = idx + (size_t)nidx * 16;
    if (store + hsize > len) {
        set_error("RPM-Header abgeschnitten");
        return -1;
    }
    h->v = xcalloc(nidx + 1, sizeof(tagval));
    h->n = 0;
    for (uint32_t i = 0; i < nidx; i++) {
        const unsigned char *e = d + idx + i * 16;
        uint32_t tag = be32(e), type = be32(e + 4), o = be32(e + 8), cnt = be32(e + 12);
        size_t p = store + o;
        if (o >= hsize) continue;
        tagval *t = &h->v[h->n];
        memset(t, 0, sizeof *t);
        t->tag = (int)tag;
        t->type = (int)type;
        if (type == 6 || type == 8 || type == 9) {
            size_t c = type == 6 ? 1 : cnt;
            for (size_t k = 0; k < c && p < store + hsize; k++) {
                size_t l = strnlen((const char *)d + p, store + hsize - p);
                sv_push_own(&t->strs, xstrndup((const char *)d + p, l));
                p += l + 1;
            }
        } else if (type == 4) {
            t->ints = xcalloc(cnt + 1, sizeof(uint32_t));
            for (size_t k = 0; k < cnt && p + 4 <= store + hsize; k++, p += 4) t->ints[t->nints++] = be32(d + p);
        } else if (type == 3) {
            t->ints = xcalloc(cnt + 1, sizeof(uint32_t));
            for (size_t k = 0; k < cnt && p + 2 <= store + hsize; k++, p += 2)
                t->ints[t->nints++] = (uint32_t)(d[p] << 8 | d[p + 1]);
        } else {
            continue;
        }
        h->n++;
    }
    *end = store + hsize;
    return 0;
}

static void header_free(header *h) {
    for (size_t i = 0; i < h->n; i++) {
        sv_free(&h->v[i].strs);
        free(h->v[i].ints);
    }
    free(h->v);
}

static tagval *get(header *h, int tag) {
    for (size_t i = 0; i < h->n; i++)
        if (h->v[i].tag == tag) return &h->v[i];
    return NULL;
}
static const char *gs(header *h, int tag, const char *def) {
    tagval *t = get(h, tag);
    return t && t->strs.n ? t->strs.v[0] : def;
}

static void deps(header *h, int names, int flags, int versions, deplist *out) {
    tagval *n = get(h, names), *f = flags ? get(h, flags) : NULL, *v = versions ? get(h, versions) : NULL;
    for (size_t i = 0; n && i < n->strs.n; i++) {
        const char *name = n->strs.v[i];
        uint32_t fl = f && i < f->nints ? f->ints[i] : 0;
        const char *ver = v && i < v->strs.n ? v->strs.v[i] : "";
        if ((fl & SENSE_RPMLIB) || str_starts(name, "rpmlib(")) continue;
        depop op = OP_NONE;
        if (*ver) {
            if ((fl & SENSE_LESS) && (fl & SENSE_EQUAL)) op = OP_LE;
            else if ((fl & SENSE_GREATER) && (fl & SENSE_EQUAL)) op = OP_GE;
            else if (fl & SENSE_LESS) op = OP_LT;
            else if (fl & SENSE_GREATER) op = OP_GT;
            else if (fl & SENSE_EQUAL) op = OP_EQ;
        }
        char *nv = op ? norm_version(ver) : NULL;
        dl_push(out, name, op, nv);
        free(nv);
    }
}

int parse_rpm(const char *path, const char *workdir, converted *c) {
    char *raw;
    size_t len;
    if (read_file(path, &raw, &len)) return -1;
    const unsigned char *d = (const unsigned char *)raw;
    header sig = {0}, h = {0};
    size_t end;
    int rc = -1;
    if (len < 96 || memcmp(d, "\xed\xab\xee\xdb", 4)) {
        set_error("Keine RPM-Datei (Lead-Magic fehlt)");
        goto out;
    }
    if (read_header(d, len, 96, &sig, &end)) goto out;
    end += (8 - end % 8) % 8;
    if (read_header(d, len, end, &h, &end)) goto out;
    const char *pf = gs(&h, T_PAYLOADFORMAT, "cpio");
    if (strcmp(pf, "cpio")) {
        set_error("Payload-Format %s nicht unterstützt", pf);
        goto out;
    }
    c->tree = path_join(workdir, "tree");
    if (extract_archive_mem(raw + end, len - end, c->tree, &c->warnings, NULL)) goto out;
    const char *name = gs(&h, T_NAME, "unknown");
    tagval *ep = get(&h, T_EPOCH);
    char *ver = xasprintf("%s-%s", gs(&h, T_VERSION, "0"), gs(&h, T_RELEASE, "1"));
    char *full = ep && ep->nints ? xasprintf("%u:%s", ep->ints[0], ver) : xstrdup(ver);
    free(ver);
    c->name = norm_name(name);
    c->version = norm_version(full);
    c->source_version = full;
    c->arch = xstrdup(gs(&h, T_ARCH, "noarch"));
    c->summary = xstrdup(gs(&h, T_SUMMARY, ""));
    c->description = xstrdup(gs(&h, T_DESCRIPTION, ""));
    c->license = xstrdup(gs(&h, T_LICENSE, "unknown"));
    c->url = xstrdup(gs(&h, T_URL, ""));
    c->packager = xstrdup(gs(&h, T_PACKAGER, ""));
    deps(&h, T_REQUIRENAME, T_REQUIREFLAGS, T_REQUIREVERSION, &c->depends);
    deps(&h, T_RECOMMENDNAME, 0, 0, &c->optdepends);
    deps(&h, T_SUGGESTNAME, 0, 0, &c->optdepends);
    deps(&h, T_CONFLICTNAME, T_CONFLICTFLAGS, T_CONFLICTVERSION, &c->conflicts);
    tagval *ob = get(&h, T_OBSOLETENAME);
    for (size_t i = 0; ob && i < ob->strs.n; i++) sv_push(&c->replaces, ob->strs.v[i]);
    tagval *pn = get(&h, T_PROVIDENAME), *pv = get(&h, T_PROVIDEVERSION);
    for (size_t i = 0; pn && i < pn->strs.n; i++) {
        const char *p = pn->strs.v[i];
        if (!strcmp(p, name) || strchr(p, '(')) continue;
        const char *v = pv && i < pv->strs.n ? pv->strs.v[i] : "";
        char *nn = norm_name(p);
        if (*v) {
            char *nv = norm_version(v);
            sv_push_own(&c->provides, xasprintf("%s=%s", nn, nv));
            free(nv);
        } else {
            sv_push(&c->provides, nn);
        }
        free(nn);
    }
    tagval *dn = get(&h, T_DIRNAMES), *bn = get(&h, T_BASENAMES), *di = get(&h, T_DIRINDEXES),
           *ff = get(&h, T_FILEFLAGS);
    for (size_t i = 0; bn && di && ff && dn && i < bn->strs.n && i < di->nints && i < ff->nints; i++)
        if ((ff->ints[i] & 1) && di->ints[i] < dn->strs.n) {
            char *p = xasprintf("%s%s", dn->strs.v[di->ints[i]], bn->strs.v[i]);
            const char *q = p;
            while (*q == '/') q++;
            sv_push(&c->backup, q);
            free(p);
        }
    /* $1 = Anzahl verbleibender Installationen: 1 Installation, 2 Upgrade, 0 Entfernen */
    struct {
        int tag, prog;
        const char *hook, *args;
    } hk[] = {{T_PREIN, T_PREINPROG, "pre_install", "1"},   {T_PREIN, T_PREINPROG, "pre_upgrade", "2"},
              {T_POSTIN, T_POSTINPROG, "post_install", "1"}, {T_POSTIN, T_POSTINPROG, "post_upgrade", "2"},
              {T_PREUN, T_PREUNPROG, "pre_remove", "0"},     {T_POSTUN, T_POSTUNPROG, "post_remove", "0"}};
    for (size_t i = 0; i < sizeof hk / sizeof *hk; i++) {
        const char *body = gs(&h, hk[i].tag, NULL);
        if (!body || !*body) continue;
        const char *prog = gs(&h, hk[i].prog, "/bin/sh");
        char *b = str_starts(body, "#!") ? xstrdup(body) : xasprintf("#!%s\n%s", *prog ? prog : "/bin/sh", body);
        jset(c->scripts, hk[i].hook, jstr_own(wrap_script(b, hk[i].args)));
        free(b);
    }
    rc = 0;
out:
    header_free(&sig);
    header_free(&h);
    free(raw);
    return rc;
}
