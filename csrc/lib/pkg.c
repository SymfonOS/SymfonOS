/* pkg.c – Paketmetadaten, Lesen/Prüfen/Entpacken und Bauen von .syp-Dateien */
#define _GNU_SOURCE
#include "pkg.h"

#include <archive.h>
#include <archive_entry.h>
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#define F_INFO ".SYPINFO.json"
#define F_FILES ".FILES.json"
#define F_DATA "data.tar.zst"
#define F_SIG ".SIGNATURE.json"

static const char *LIST_KEYS[] = {"depends", "optdepends", "provides", "conflicts", "replaces",
                                  "backup",  "groups",     "categories", NULL};
static const char *SCRIPT_KEYS[] = {"pre_install", "post_install", "pre_upgrade",
                                    "post_upgrade", "pre_remove",  "post_remove", NULL};

/* --- Metadaten ------------------------------------------------------------ */
void info_defaults(jval *info) {
    const char *strs[][2] = {{"arch", "x86_64"}, {"summary", ""},  {"description", ""},
                             {"license", "unknown"}, {"url", ""}, {"packager", ""}};
    for (size_t i = 0; i < sizeof strs / sizeof *strs; i++)
        if (!jget(info, strs[i][0])) jset(info, strs[i][0], jstr(strs[i][1]));
    for (const char **k = LIST_KEYS; *k; k++)
        if (!jget(info, *k)) jset(info, *k, jarr());
    if (!jget(info, "scripts")) jset(info, "scripts", jobj());
    if (!jget(info, "origin")) {
        jval *o = jobj();
        jset(o, "format", jstr("native"));
        jset(info, "origin", o);
    }
    if (!jget(info, "installed_size")) jset(info, "installed_size", jint(0));
    if (!jget(info, "build_date")) jset(info, "build_date", jint(0));
    if (!jget(info, "format")) jset(info, "format", jint(SYP_FORMAT));
}

jval *info_new(const char *name, const char *version) {
    jval *i = jobj();
    jset(i, "name", jstr(name));
    jset(i, "version", jstr(version));
    info_defaults(i);
    return i;
}

static int name_ok(const char *n) {
    if (!*n) return 0;
    for (const char *p = n; *p; p++) {
        int ok = (*p >= 'a' && *p <= 'z') || (*p >= '0' && *p <= '9') || strchr("@._+", *p) ||
                 (*p == '-' && p != n);
        if (!ok) return 0;
    }
    return 1;
}

int info_validate(const jval *info) {
    const char *n = jgets(info, "name", ""), *v = jgets(info, "version", "");
    if (!name_ok(n)) {
        set_error("Ungültiger Paketname: '%s'", n);
        return -1;
    }
    if (!*v || strpbrk(v, " /")) {
        set_error("Ungültige Version: '%s'", v);
        return -1;
    }
    const char *dk[] = {"depends", "conflicts", NULL};
    for (const char **k = dk; *k; k++) {
        jval *a = jgeta(info, *k);
        for (size_t i = 0; a && i < a->n; i++) {
            dep d;
            if (a->items[i]->t != J_STR || dep_parse(a->items[i]->s, &d)) return -1;
            dep_free(&d);
        }
    }
    jval *sc = jget(info, "scripts");
    for (size_t i = 0; sc && sc->t == J_OBJ && i < sc->n; i++) {
        int ok = 0;
        for (const char **k = SCRIPT_KEYS; *k; k++)
            if (!strcmp(*k, sc->keys[i])) ok = 1;
        if (!ok) {
            set_error("Unbekannter Skript-Hook: %s", sc->keys[i]);
            return -1;
        }
    }
    return 0;
}

strvec info_list(const jval *info, const char *key) {
    strvec sv = {0};
    jarr_strings(jgeta(info, key), &sv);
    return sv;
}

char *pkg_filename(const jval *info) {
    char *v = str_replace(jgets(info, "version", "0"), ":", "~");
    char *f = xasprintf("%s-%s-%s.syp", jgets(info, "name", "x"), v, jgets(info, "arch", "x86_64"));
    free(v);
    return f;
}

pkg *pkg_new(jval *info) {
    if (info_validate(info)) {
        json_free(info);
        return NULL;
    }
    info_defaults(info);
    pkg *p = xcalloc(1, sizeof *p);
    p->info = info;
    p->name = jgets(info, "name", "");
    p->version = jgets(info, "version", "");
    p->priority = 50;
    jval *a = jgeta(info, "depends");
    p->deps = xcalloc(a ? a->n : 1, sizeof(dep));
    for (size_t i = 0; a && i < a->n; i++) dep_parse(a->items[i]->s, &p->deps[p->ndeps++]);
    a = jgeta(info, "conflicts");
    for (size_t i = 0; a && i < a->n; i++) {
        dep d;
        if (dep_parse(a->items[i]->s, &d)) continue;
        p->conflicts = xrealloc(p->conflicts, (p->nconf + d.n) * sizeof(atom));
        for (size_t j = 0; j < d.n; j++) p->conflicts[p->nconf++] = d.alt[j];
        free(d.alt);
    }
    a = jgeta(info, "provides");
    size_t np = 1 + (a ? a->n : 0);
    p->prov_name = xcalloc(np, sizeof(char *));
    p->prov_ver = xcalloc(np, sizeof(char *));
    p->prov_name[0] = xstrdup(p->name);
    p->prov_ver[0] = xstrdup(p->version);
    p->nprov = 1;
    for (size_t i = 0; a && i < a->n; i++) {
        if (a->items[i]->t != J_STR) continue;
        provide_parts(a->items[i]->s, &p->prov_name[p->nprov], &p->prov_ver[p->nprov]);
        p->nprov++;
    }
    return p;
}

void pkg_free(pkg *p) {
    if (!p) return;
    for (size_t i = 0; i < p->ndeps; i++) dep_free(&p->deps[i]);
    free(p->deps);
    for (size_t i = 0; i < p->nconf; i++) {
        free(p->conflicts[i].name);
        free(p->conflicts[i].version);
    }
    free(p->conflicts);
    for (size_t i = 0; i < p->nprov; i++) {
        free(p->prov_name[i]);
        free(p->prov_ver[i]);
    }
    free(p->prov_name);
    free(p->prov_ver);
    free(p->repo);
    free(p->file);
    free(p->sha256);
    free(p->local_path);
    free(p->reason);
    json_free(p->info);
    free(p);
}

int pkg_satisfies(const pkg *p, const atom *a) {
    for (size_t i = 0; i < p->nprov; i++)
        if (!strcmp(p->prov_name[i], a->name) && atom_matches_version(a, p->prov_ver[i])) return 1;
    return 0;
}

int pkg_conflicts(const pkg *a, const pkg *b) {
    if (!strcmp(a->name, b->name)) return 0;
    for (size_t i = 0; i < a->nconf; i++)
        if (pkg_satisfies(b, &a->conflicts[i])) return 1;
    for (size_t i = 0; i < b->nconf; i++)
        if (pkg_satisfies(a, &b->conflicts[i])) return 1;
    return 0;
}

static int write_outer(const char *path, long long mtime, const char *names[4], const char *data[4],
                       const size_t lens[4]);

/* --- Digest ---------------------------------------------------------------- */
static char *digest(const sypfile *f) {
    char a[65], b[65], c[65];
    sha256_hex(f->info_b, f->info_n, a);
    sha256_hex(f->files_b, f->files_n, b);
    sha256_hex(f->data_b, f->data_n, c);
    return xasprintf("syp-v1\n%s\n%s\n%s\n", a, b, c);
}

/* --- Lesen ----------------------------------------------------------------- */
static int read_entry(struct archive *a, char **out, size_t *n) {
    sbuf b = {0};
    sb_init(&b);
    char buf[65536];
    la_ssize_t r;
    while ((r = archive_read_data(a, buf, sizeof buf)) > 0) sb_addn(&b, buf, (size_t)r);
    if (r < 0) {
        free(b.s);
        set_error("tar: %s", archive_error_string(a));
        return -1;
    }
    *n = b.len;
    *out = sb_take(&b);
    return 0;
}

static int syp_open_impl(const char *path, sypfile *f, int with_data);
int syp_open(const char *path, sypfile *f) { return syp_open_impl(path, f, 1); }
int syp_open_meta(const char *path, sypfile *f) { return syp_open_impl(path, f, 0); }

static int syp_open_impl(const char *path, sypfile *f, int with_data) {
    memset(f, 0, sizeof *f);
    f->path = xstrdup(path);
    struct archive *a = archive_read_new();
    archive_read_support_format_tar(a);
    if (archive_read_open_filename(a, path, 1 << 16) != ARCHIVE_OK) {
        set_error("%s: kein gültiges .syp (%s)", path, archive_error_string(a));
        archive_read_free(a);
        return -1;
    }
    const char *want[] = {F_INFO, F_FILES, F_DATA, F_SIG};
    char *sigb = NULL;
    size_t sign = 0;
    struct archive_entry *e;
    int idx = 0, rc = 0;
    while (archive_read_next_header(a, &e) == ARCHIVE_OK) {
        const char *name = archive_entry_pathname(e);
        if (idx >= 4 || strcmp(name, want[idx])) {
            set_error("%s: unerwarteter Aufbau (Eintrag '%s')", path, name);
            rc = -1;
            break;
        }
        char **dst[] = {&f->info_b, &f->files_b, &f->data_b, &sigb};
        size_t *len[] = {&f->info_n, &f->files_n, &f->data_n, &sign};
        if (idx == 2 && !with_data) {
            archive_read_data_skip(a);
            idx++;
            continue;
        }
        if (read_entry(a, dst[idx], len[idx])) {
            rc = -1;
            break;
        }
        idx++;
    }
    archive_read_free(a);
    if (!rc && idx != 4) {
        set_error("%s: unvollständiges Paket", path);
        rc = -1;
    }
    if (!rc) {
        f->info = json_parse(f->info_b, f->info_n);
        f->files = json_parse(f->files_b, f->files_n);
        f->sig = json_parse(sigb, sign);
        if (!f->info || !f->files || !f->sig || f->files->t != J_ARR) {
            set_error("%s: beschädigte Metadaten", path);
            rc = -1;
        } else if (jgeti(f->info, "format", 1) > SYP_FORMAT) {
            set_error("%s: Paketformat %lld wird nicht unterstützt", path, jgeti(f->info, "format", 1));
            rc = -1;
        } else {
            info_defaults(f->info);
        }
    }
    free(sigb);
    if (rc) syp_close(f);
    return rc;
}

void syp_close(sypfile *f) {
    free(f->path);
    json_free(f->info);
    json_free(f->files);
    json_free(f->sig);
    free(f->info_b);
    free(f->files_b);
    free(f->data_b);
    memset(f, 0, sizeof *f);
}

int syp_resign(const char *path, const seckey *k) {
    sypfile f;
    if (syp_open(path, &f)) return -1;
    char *d = digest(&f);
    jval *sig = sign_data(k, d, strlen(d));
    free(d);
    char *sigb = json_dump(sig, 1, 0), *part = xasprintf("%s.part", path);
    const char *names[4] = {F_INFO, F_FILES, F_DATA, F_SIG};
    const char *bufs[4] = {f.info_b, f.files_b, f.data_b, sigb};
    size_t lens[4] = {f.info_n, f.files_n, f.data_n, strlen(sigb)};
    int rc = write_outer(part, jgeti(f.info, "build_date", 0), names, bufs, lens);
    if (!rc && rename(part, path)) rc = -1;
    free(part);
    free(sigb);
    json_free(sig);
    syp_close(&f);
    return rc;
}

const pubkey *syp_verify(const sypfile *f, const keyring *kr) {
    if (!jgets(f->sig, "key_id", NULL)) {
        set_error("%s: Paket ist nicht signiert", path_basename(f->path));
        return NULL;
    }
    char *d = digest(f);
    const pubkey *k = verify_data(kr, d, strlen(d), f->sig);
    free(d);
    if (!k) {
        char *m = xasprintf("%s: %s", path_basename(f->path), last_error());
        set_error("%s", m);
        free(m);
    }
    return k;
}

static int unsafe_path(const char *p) {
    if (!*p || *p == '/') return 1;
    const char *s = p;
    while (*s) {
        const char *e = strchr(s, '/');
        size_t n = e ? (size_t)(e - s) : strlen(s);
        if (n == 2 && !strncmp(s, "..", 2)) return 1;
        if (!e) break;
        s = e + 1;
    }
    return 0;
}

int syp_extract(const sypfile *f, const char *dest) {
    hmap expected = {0};
    hm_init(&expected);
    for (size_t i = 0; i < f->files->n; i++)
        hm_set(&expected, jgets(f->files->items[i], "path", ""), f->files->items[i]);
    struct archive *a = archive_read_new();
    archive_read_support_filter_all(a);
    archive_read_support_format_tar(a);
    if (archive_read_open_memory(a, f->data_b, f->data_n) != ARCHIVE_OK) {
        set_error("Nutzdaten unlesbar: %s", archive_error_string(a));
        archive_read_free(a);
        hm_free(&expected);
        return -1;
    }
    mkdir_p(dest, 0755);
    strvec links = {0};
    struct archive_entry *e;
    int rc = 0, r;
    while ((r = archive_read_next_header(a, &e)) == ARCHIVE_OK) {
        char *name = xstrdup(archive_entry_pathname(e));
        size_t nl = strlen(name);
        while (nl > 1 && name[nl - 1] == '/') name[--nl] = 0;
        mode_t type = archive_entry_filetype(e);
        if (unsafe_path(name)) {
            set_error("Unsicherer Pfad im Paket: %s", name);
            rc = -1;
        }
        for (size_t i = 0; !rc && i < links.n; i++) {
            size_t ll = strlen(links.v[i]);
            if (!strncmp(name, links.v[i], ll) && name[ll] == '/') {
                set_error("Pfad hinter Symlink: %s", name);
                rc = -1;
            }
        }
        if (!rc && type != AE_IFREG && type != AE_IFDIR && type != AE_IFLNK) {
            set_error("Nicht erlaubter Eintragstyp: %s", name);
            rc = -1;
        }
        if (!rc && archive_entry_hardlink(e)) {
            set_error("Hardlinks sind nicht erlaubt: %s", name);
            rc = -1;
        }
        if (!rc && !hm_get(&expected, name)) {
            set_error("Nicht deklarierte Datei: %s", name);
            rc = -1;
        }
        if (rc) {
            free(name);
            break;
        }
        char *out = path_join(dest, name);
        mode_t mode = archive_entry_perm(e);
        if (type == AE_IFDIR) {
            mkdir_p(out, 0755);
            chmod(out, mode);
        } else if (type == AE_IFLNK) {
            unlink(out);
            if (symlink(archive_entry_symlink(e), out)) {
                set_error("symlink %s: %s", name, strerror(errno));
                rc = -1;
            }
            sv_push(&links, name);
        } else {
            char *dir = path_dirname(out);
            mkdir_p(dir, 0755);
            free(dir);
            unlink(out);
            int fd = open(out, O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC | O_NOFOLLOW, 0600);
            if (fd < 0) {
                set_error("%s: %s", out, strerror(errno));
                rc = -1;
            } else {
                if (archive_read_data_into_fd(a, fd) != ARCHIVE_OK) {
                    set_error("Entpacken %s: %s", name, archive_error_string(a));
                    rc = -1;
                }
                fchmod(fd, mode);
                if (geteuid() == 0 && fchown(fd, archive_entry_uid(e), archive_entry_gid(e))) {
                    set_error("chown %s: %s", name, strerror(errno));
                    rc = -1;
                }
                close(fd);
            }
        }
        free(out);
        free(name);
        if (rc) break;
    }
    if (!rc && r != ARCHIVE_EOF && r != ARCHIVE_OK) {
        set_error("Nutzdaten beschädigt: %s", archive_error_string(a));
        rc = -1;
    }
    archive_read_free(a);
    sv_free(&links);
    for (size_t i = 0; !rc && i < f->files->n; i++) {
        jval *fe = f->files->items[i];
        if (strcmp(jgets(fe, "type", ""), "file")) continue;
        char *p = path_join(dest, jgets(fe, "path", "")), h[65];
        if (sha256_file(p, h) || strcmp(h, jgets(fe, "sha256", ""))) {
            set_error("Prüfsumme falsch: %s", jgets(fe, "path", ""));
            rc = -1;
        }
        free(p);
    }
    hm_free(&expected);
    return rc;
}

/* --- Schreiben ------------------------------------------------------------- */
static void walk(const char *root, const char *rel, jval *out) {
    char *dir = rel ? path_join(root, rel) : xstrdup(root);
    struct dirent **list;
    int n = scandir(dir, &list, NULL, alphasort);
    if (n < 0) {
        free(dir);
        return;
    }
    for (int i = 0; i < n; i++) {
        const char *dn = list[i]->d_name;
        if (!strcmp(dn, ".") || !strcmp(dn, "..")) {
            free(list[i]);
            continue;
        }
        char *r = rel ? xasprintf("%s/%s", rel, dn) : xstrdup(dn);
        char *full = path_join(root, r);
        struct stat st;
        if (lstat(full, &st) == 0) {
            jval *e = jobj();
            jset(e, "path", jstr(r));
            if (S_ISLNK(st.st_mode)) {
                char t[4096];
                ssize_t tl = readlink(full, t, sizeof t - 1);
                t[tl > 0 ? tl : 0] = 0;
                jset(e, "type", jstr("link"));
                jset(e, "target", jstr(t));
                jset(e, "mode", jint(0777));
                jpush(out, e);
            } else if (S_ISDIR(st.st_mode)) {
                jset(e, "type", jstr("dir"));
                jset(e, "mode", jint(st.st_mode & 07777));
                jpush(out, e);
                walk(root, r, out);
            } else if (S_ISREG(st.st_mode)) {
                char h[65];
                sha256_file(full, h);
                jset(e, "type", jstr("file"));
                jset(e, "mode", jint(st.st_mode & 07777));
                jset(e, "size", jint(st.st_size));
                jset(e, "sha256", jstr(h));
                jpush(out, e);
            } else {
                json_free(e);
                fprintf(stderr, "Warnung: nicht unterstützter Dateityp übersprungen: %s\n", r);
            }
        }
        free(full);
        free(r);
        free(list[i]);
    }
    free(list);
    free(dir);
}

static int cmp_path(const void *a, const void *b) {
    return strcmp(jgets(*(jval *const *)a, "path", ""), jgets(*(jval *const *)b, "path", ""));
}

jval *scan_tree(const char *tree) {
    jval *out = jarr();
    walk(tree, NULL, out);
    if (out->n > 1) qsort(out->items, out->n, sizeof(jval *), cmp_path);
    return out;
}

static la_ssize_t sb_writer(struct archive *a, void *ud, const void *buf, size_t n) {
    (void)a;
    sb_addn((sbuf *)ud, buf, n);
    return (la_ssize_t)n;
}
static int sb_open(struct archive *a, void *ud) {
    (void)a;
    (void)ud;
    return ARCHIVE_OK;
}

static int make_data(const char *tree, const jval *files, long long mtime, sbuf *out) {
    struct archive *a = archive_write_new();
    archive_write_set_format_pax_restricted(a);
    archive_write_add_filter_zstd(a);
    const char *lvl = getenv("SYP_ZSTD_LEVEL");
    char *opt = xasprintf("zstd:compression-level=%s", lvl && *lvl ? lvl : "19");
    archive_write_set_options(a, opt);
    archive_write_set_options(a, "zstd:threads=0");
    free(opt);
    archive_write_set_bytes_in_last_block(a, 1);
    sb_init(out);
    if (archive_write_open(a, out, sb_open, sb_writer, NULL) != ARCHIVE_OK) {
        set_error("tar: %s", archive_error_string(a));
        archive_write_free(a);
        return -1;
    }
    int rc = 0;
    for (size_t i = 0; i < files->n && !rc; i++) {
        jval *f = files->items[i];
        const char *rel = jgets(f, "path", ""), *type = jgets(f, "type", "");
        struct archive_entry *e = archive_entry_new();
        archive_entry_set_pathname(e, rel);
        archive_entry_set_uid(e, 0);
        archive_entry_set_gid(e, 0);
        archive_entry_set_uname(e, "root");
        archive_entry_set_gname(e, "root");
        archive_entry_set_mtime(e, (time_t)mtime, 0);
        archive_entry_set_perm(e, (mode_t)jgeti(f, "mode", 0644));
        if (!strcmp(type, "dir")) {
            archive_entry_set_filetype(e, AE_IFDIR);
            archive_write_header(a, e);
        } else if (!strcmp(type, "link")) {
            archive_entry_set_filetype(e, AE_IFLNK);
            archive_entry_set_symlink(e, jgets(f, "target", ""));
            archive_write_header(a, e);
        } else {
            archive_entry_set_filetype(e, AE_IFREG);
            archive_entry_set_size(e, jgeti(f, "size", 0));
            if (archive_write_header(a, e) != ARCHIVE_OK) {
                set_error("tar: %s", archive_error_string(a));
                rc = -1;
            } else {
                char *full = path_join(tree, rel);
                int fd = open(full, O_RDONLY | O_CLOEXEC);
                if (fd < 0) {
                    set_error("%s: %s", full, strerror(errno));
                    rc = -1;
                } else {
                    char buf[1 << 16];
                    ssize_t r;
                    while ((r = read(fd, buf, sizeof buf)) > 0) archive_write_data(a, buf, (size_t)r);
                    close(fd);
                }
                free(full);
            }
        }
        archive_entry_free(e);
    }
    if (archive_write_close(a) != ARCHIVE_OK && !rc) {
        set_error("tar: %s", archive_error_string(a));
        rc = -1;
    }
    archive_write_free(a);
    return rc;
}

static int write_outer(const char *path, long long mtime, const char *names[4], const char *data[4],
                       const size_t lens[4]) {
    struct archive *a = archive_write_new();
    archive_write_set_format_pax_restricted(a);
    archive_write_set_bytes_in_last_block(a, 1);
    if (archive_write_open_filename(a, path) != ARCHIVE_OK) {
        set_error("%s: %s", path, archive_error_string(a));
        archive_write_free(a);
        return -1;
    }
    for (int i = 0; i < 4; i++) {
        struct archive_entry *e = archive_entry_new();
        archive_entry_set_pathname(e, names[i]);
        archive_entry_set_filetype(e, AE_IFREG);
        archive_entry_set_perm(e, 0644);
        archive_entry_set_size(e, (la_int64_t)lens[i]);
        archive_entry_set_mtime(e, (time_t)mtime, 0);
        archive_write_header(a, e);
        archive_write_data(a, data[i], lens[i]);
        archive_entry_free(e);
    }
    int rc = archive_write_close(a) == ARCHIVE_OK ? 0 : -1;
    if (rc) set_error("%s: %s", path, archive_error_string(a));
    archive_write_free(a);
    return rc;
}

char *syp_build(const char *tree, jval *info, const char *outdir, const seckey *signer) {
    info_defaults(info);
    if (info_validate(info)) return NULL;
    const char *sde = getenv("SOURCE_DATE_EPOCH");
    long long mtime = jgeti(info, "build_date", 0);
    if (sde && *sde) mtime = atoll(sde);
    if (!mtime) mtime = (long long)time(NULL);
    if (!jgeti(info, "build_date", 0)) jset(info, "build_date", jint(mtime));
    jval *files = scan_tree(tree);
    long long size = 0;
    for (size_t i = 0; i < files->n; i++) size += jgeti(files->items[i], "size", 0);
    jset(info, "installed_size", jint(size));
    jval *backup = jgeta(info, "backup");
    for (size_t i = 0; backup && i < backup->n; i++) {
        const char *b = backup->items[i]->s;
        while (*b == '/') b++;
        int found = 0;
        for (size_t j = 0; j < files->n; j++)
            if (!strcmp(jgets(files->items[j], "path", ""), b) && !strcmp(jgets(files->items[j], "type", ""), "file"))
                found = 1;
        if (!found) {
            set_error("backup-Datei nicht im Paket: %s", b);
            json_free(files);
            return NULL;
        }
    }
    sypfile f = {0};
    f.info_b = json_dump(info, 1, 0);
    f.info_n = strlen(f.info_b);
    f.files_b = json_dump(files, 1, 0);
    f.files_n = strlen(f.files_b);
    sbuf data;
    if (make_data(tree, files, mtime, &data)) {
        free(f.info_b);
        free(f.files_b);
        json_free(files);
        return NULL;
    }
    f.data_n = data.len;
    f.data_b = sb_take(&data);
    jval *sig;
    if (signer) {
        char *d = digest(&f);
        sig = sign_data(signer, d, strlen(d));
        free(d);
    } else {
        sig = jobj();
        jset(sig, "key_id", jnull());
    }
    char *sigb = json_dump(sig, 1, 0);
    mkdir_p(outdir, 0755);
    char *fn = pkg_filename(info), *out = path_join(outdir, fn), *part = xasprintf("%s.part", out);
    const char *names[4] = {F_INFO, F_FILES, F_DATA, F_SIG};
    const char *bufs[4] = {f.info_b, f.files_b, f.data_b, sigb};
    size_t lens[4] = {f.info_n, f.files_n, f.data_n, strlen(sigb)};
    int rc = write_outer(part, mtime, names, bufs, lens);
    if (!rc && rename(part, out)) {
        set_error("%s: %s", out, strerror(errno));
        rc = -1;
    }
    free(part);
    free(fn);
    free(sigb);
    json_free(sig);
    json_free(files);
    free(f.info_b);
    free(f.files_b);
    free(f.data_b);
    if (rc) {
        free(out);
        return NULL;
    }
    return out;
}
