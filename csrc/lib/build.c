/* build.c – `syp build`: Quellen holen und prüfen, prepare/build/check/package ausführen,
 * ELF-Dateien strippen, Paket packen und signieren. */
#define _GNU_SOURCE
#include "build.h"

#include <archive.h>
#include <archive_entry.h>
#include <dirent.h>
#include <errno.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#include "core.h"
#include "pkg.h"
#include "toml.h"

static const char *DEF_CFLAGS = "-O2 -pipe -march=x86-64 -mtune=generic -fno-plt -fexceptions "
                                "-fstack-protector-strong -fstack-clash-protection -fcf-protection "
                                "-D_FORTIFY_SOURCE=3 -Wp,-D_GLIBCXX_ASSERTIONS";
static const char *DEF_LDFLAGS = "-Wl,-O1 -Wl,--sort-common -Wl,--as-needed -Wl,-z,relro -Wl,-z,now";

jval *recipe_load(const char *recipe) { return toml_load(recipe); }

char *recipe_pkgfile(const jval *r) {
    jval *p = jget(r, "package");
    jval *tmp = jobj();
    jset(tmp, "name", jstr(jgets(p, "name", "x")));
    jset(tmp, "version", jstr(jgets(p, "version", "0")));
    jset(tmp, "arch", jstr(jgets(p, "arch", "x86_64")));
    char *f = pkg_filename(tmp);
    json_free(tmp);
    return f;
}

static int extract_archive(const char *file, const char *dest) {
    struct archive *a = archive_read_new(), *w = archive_write_disk_new();
    archive_read_support_filter_all(a);
    archive_read_support_format_all(a);
    archive_write_disk_set_options(w, ARCHIVE_EXTRACT_TIME | ARCHIVE_EXTRACT_PERM |
                                          ARCHIVE_EXTRACT_SECURE_NODOTDOT | ARCHIVE_EXTRACT_SECURE_SYMLINKS);
    if (archive_read_open_filename(a, file, 1 << 16) != ARCHIVE_OK) {
        set_error("%s: %s", file, archive_error_string(a));
        archive_read_free(a);
        archive_write_free(w);
        return -1;
    }
    struct archive_entry *e;
    int rc = 0, r;
    while ((r = archive_read_next_header(a, &e)) == ARCHIVE_OK) {
        char *p = path_join(dest, archive_entry_pathname(e));
        archive_entry_set_pathname(e, p);
        const char *hl = archive_entry_hardlink(e);
        if (hl) {
            char *h = path_join(dest, hl);
            archive_entry_set_hardlink(e, h);
            free(h);
        }
        free(p);
        if (archive_write_header(w, e) == ARCHIVE_OK && archive_entry_size(e) > 0) {
            const void *buf;
            size_t sz;
            la_int64_t off;
            while (archive_read_data_block(a, &buf, &sz, &off) == ARCHIVE_OK) archive_write_data_block(w, buf, sz, off);
        }
        archive_write_finish_entry(w);
    }
    if (r != ARCHIVE_EOF) {
        set_error("%s: %s", file, archive_error_string(a));
        rc = -1;
    }
    archive_read_free(a);
    archive_write_free(w);
    return rc;
}

static const char *ARCHIVE_SUFFIXES[] = {".tar", ".tar.gz", ".tgz", ".tar.xz", ".txz", ".tar.zst", ".tar.bz2",
                                         ".tbz2", ".tar.lz", ".tar.lzma", ".zip", NULL};
static int is_archive(const char *name) {
    for (const char **s = ARCHIVE_SUFFIXES; *s; s++)
        if (str_ends(name, *s)) return 1;
    return 0;
}

/* Schreibt sha256 = "..." hinter die url-Zeile im Rezepttext */
static int write_sum(const char *recipe, const char *url, const char *sum) {
    char *text;
    if (read_file(recipe, &text, NULL)) return -1;
    sbuf out = {0};
    sb_init(&out);
    char *save = NULL, *copy = xstrdup(text);
    int pending = 0, done = 0;
    for (char *line = strtok_r(copy, "\n", &save); line; line = strtok_r(NULL, "\n", &save)) {
        char *t = str_trim(xstrdup(line)), *tt = t;
        if (pending) {
            if (str_starts(tt, "sha256")) {
                sb_addf(&out, "sha256 = \"%s\"\n", sum);
                pending = 0;
                done = 1;
                free(tt);
                continue;
            }
            if (!*tt || tt[0] == '[' || strchr(tt, '=')) {
                sb_addf(&out, "sha256 = \"%s\"\n", sum);
                pending = 0;
                done = 1;
            }
        }
        sb_add(&out, line);
        sb_addc(&out, '\n');
        if (!done && str_starts(tt, "url") && strstr(tt, url)) pending = 1;
        free(tt);
    }
    if (pending) sb_addf(&out, "sha256 = \"%s\"\n", sum);
    free(copy);
    free(text);
    int rc = write_file(recipe, out.s, out.len, 0644);
    free(out.s);
    return rc;
}

static char *fetch_source(const char *url, const char *fname, const char *srccache) {
    char *dest = path_join(srccache, fname);
    if (!path_exists(dest)) {
        printf("  Lade %s\n", url);
        fflush(stdout);
        if (fetch_url(url, dest)) {
            free(dest);
            return NULL;
        }
    }
    return dest;
}

int recipe_fill_sums(const char *recipe, const char *srccache) {
    jval *r = recipe_load(recipe);
    if (!r) return -1;
    mkdir_p(srccache, 0755);
    jval *src = jgeta(r, "source");
    int rc = 0;
    for (size_t i = 0; src && i < src->n; i++) {
        const char *url = jgets(src->items[i], "url", NULL), *sum = jgets(src->items[i], "sha256", "");
        if (!url || (*sum && strcmp(sum, "TODO"))) continue;
        const char *fname = jgets(src->items[i], "filename", path_basename(url));
        char *f = fetch_source(url, fname, srccache), h[65];
        if (!f || sha256_file(f, h)) {
            rc = -1;
            free(f);
            break;
        }
        printf("  %s  %s\n", h, fname);
        if (write_sum(recipe, url, h)) rc = -1;
        free(f);
    }
    json_free(r);
    return rc;
}

static int is_elf(const char *p) {
    FILE *f = fopen(p, "rb");
    if (!f) return 0;
    char m[4] = {0};
    size_t n = fread(m, 1, 4, f);
    fclose(f);
    return n == 4 && !memcmp(m, "\x7f" "ELF", 4);
}

static void post_process(const char *dir, int strip) {
    struct dirent **list;
    int n = scandir(dir, &list, NULL, alphasort);
    for (int i = 0; i < n; i++) {
        const char *d = list[i]->d_name;
        if (strcmp(d, ".") && strcmp(d, "..")) {
            char *p = path_join(dir, d);
            struct stat st;
            if (lstat(p, &st) == 0) {
                if (S_ISDIR(st.st_mode)) post_process(p, strip);
                else if (S_ISREG(st.st_mode) && str_ends(d, ".la")) unlink(p); /* libtool-Archive */
                else if (S_ISREG(st.st_mode) && !strcmp(d, "dir") && strstr(p, "/share/info/")) unlink(p);
                else if (strip && S_ISREG(st.st_mode) && is_elf(p)) {
                    const char *mode = str_ends(d, ".a") || str_ends(d, ".o") ? "--strip-debug" : "--strip-unneeded";
                    if (strstr(p, "/lib/modules/") || strstr(p, "/usr/lib/debug/")) mode = NULL;
                    if (mode) {
                        char *argv[] = {"strip", (char *)mode, p, NULL};
                        run_cmd(argv, NULL, NULL);
                    }
                }
            }
            free(p);
        }
        free(list[i]);
    }
    if (n >= 0) free(list);
}

static char **build_env(const char *src, const char *dest, const char *rdir, const jval *pk, const jval *opts) {
    strvec env = {0};
    extern char **environ;
    for (char **e = environ; *e; e++)
        if (!str_starts(*e, "SRCDIR=") && !str_starts(*e, "DESTDIR=") && !str_starts(*e, "CFLAGS=") &&
            !str_starts(*e, "CXXFLAGS=") && !str_starts(*e, "LDFLAGS=") && !str_starts(*e, "MAKEFLAGS="))
            sv_push(&env, *e);
    const char *ver = jgets(pk, "version", "0");
    char *v = xstrdup(ver), *dash = strrchr(v, '-'), *colon = strchr(v, ':');
    char *rel = dash ? xstrdup(dash + 1) : xstrdup("1");
    if (dash) *dash = 0;
    long nproc = sysconf(_SC_NPROCESSORS_ONLN);
    const char *cf = jgets(opts, "cflags", DEF_CFLAGS), *lf = jgets(opts, "ldflags", DEF_LDFLAGS);
    sv_push_own(&env, xasprintf("SRCDIR=%s", src));
    sv_push_own(&env, xasprintf("DESTDIR=%s", dest));
    sv_push_own(&env, xasprintf("pkgdir=%s", dest));
    sv_push_own(&env, xasprintf("RECIPEDIR=%s", rdir));
    sv_push_own(&env, xasprintf("PKGNAME=%s", jgets(pk, "name", "")));
    sv_push_own(&env, xasprintf("PKGVER=%s", colon ? colon + 1 : v));
    sv_push_own(&env, xasprintf("PKGREL=%s", rel));
    sv_push_own(&env, xasprintf("CFLAGS=%s", cf));
    sv_push_own(&env, xasprintf("CXXFLAGS=%s", cf));
    sv_push_own(&env, xasprintf("LDFLAGS=%s", lf));
    sv_push_own(&env, xasprintf("MAKEFLAGS=-j%ld", nproc > 0 ? nproc : 1));
    sv_push_own(&env, xasprintf("NPROC=%ld", nproc > 0 ? nproc : 1));
    if (!getenv("SOURCE_DATE_EPOCH")) sv_push(&env, "SOURCE_DATE_EPOCH=1700000000");
    free(v);
    free(rel);
    sv_push_own(&env, NULL);
    return env.v;
}

char *build_recipe(const char *recipe, const char *outdir, const seckey *signer, const build_opts *o) {
    char *rpath = realpath(recipe, NULL);
    if (!rpath) {
        set_error("%s: %s", recipe, strerror(errno));
        return NULL;
    }
    if (o->update_sums && recipe_fill_sums(rpath, o->srccache)) {
        free(rpath);
        return NULL;
    }
    jval *r = recipe_load(rpath);
    if (!r) {
        free(rpath);
        return NULL;
    }
    char *rdir = path_dirname(rpath);
    jval *pk = jget(r, "package");
    if (!pk) {
        set_error("%s: [package] fehlt", recipe);
        json_free(r);
        free(rdir);
        free(rpath);
        return NULL;
    }
    jval *info = jcopy(pk);
    jval *mk = jget(info, "makedepends");
    if (mk) {
        /* makedepends gehören ins Rezept, nicht ins Paket */
        for (size_t i = 0; i < info->n; i++)
            if (!strcmp(info->keys[i], "makedepends")) {
                json_free(info->items[i]);
                free(info->keys[i]);
                memmove(&info->items[i], &info->items[i + 1], (info->n - i - 1) * sizeof(jval *));
                memmove(&info->keys[i], &info->keys[i + 1], (info->n - i - 1) * sizeof(char *));
                info->n--;
                break;
            }
    }
    jval *scripts = jget(r, "scripts");
    jset(info, "scripts", scripts ? jcopy(scripts) : jobj());
    jval *origin = jobj();
    jset(origin, "format", jstr("native"));
    jset(origin, "recipe", jstr(path_basename(rdir)));
    jset(info, "origin", origin);
    char *out = NULL;
    if (info_validate(info)) goto done;
    char *work = make_tempdir("syp-build-");
    char *src = path_join(work, "src"), *dest = path_join(work, "pkg");
    mkdir_p(src, 0755);
    mkdir_p(dest, 0755);
    const char *cache = o->srccache ? o->srccache : "/var/cache/syp/sources";
    mkdir_p(cache, 0755);
    jval *sources = jgeta(r, "source");
    int fail = 0;
    for (size_t i = 0; sources && i < sources->n && !fail; i++) {
        jval *s = sources->items[i];
        const char *url = jgets(s, "url", NULL), *path = jgets(s, "path", NULL);
        if (url) {
            const char *sum = jgets(s, "sha256", NULL);
            if (!sum || !*sum || !strcmp(sum, "TODO")) {
                set_error("Quelle ohne sha256: %s (syp build --update-sums)", url);
                fail = 1;
                break;
            }
            const char *fname = jgets(s, "filename", path_basename(url));
            char *f = fetch_source(url, fname, cache), h[65];
            if (!f) {
                fail = 1;
                break;
            }
            if (sha256_file(f, h) || strcmp(h, sum)) {
                set_error("sha256 falsch: %s (erwartet %s, erhalten %s)", url, sum, h);
                unlink(f);
                free(f);
                fail = 1;
                break;
            }
            if (jgetb(s, "extract", 1) && is_archive(fname)) {
                if (extract_archive(f, src)) fail = 1;
            } else {
                char *d = path_join(src, fname);
                if (copy_file(f, d)) fail = 1;
                free(d);
            }
            free(f);
        } else if (path) {
            char *p = path_join(rdir, path), *rp = realpath(p, NULL);
            free(p);
            if (!rp || (strcmp(rp, rdir) && !(str_starts(rp, rdir) && rp[strlen(rdir)] == '/'))) {
                set_error("Quellpfad außerhalb des Rezepts: %s", path);
                free(rp);
                fail = 1;
                break;
            }
            const char *dn = jgets(s, "dest", path_basename(rp));
            char *d = path_join(src, dn);
            static const char *ign[] = {".git", "out", "work", "__pycache__", "*.syp", ".pytest_cache", NULL};
            if (copy_tree(rp, d, ign)) fail = 1;
            free(d);
            free(rp);
        }
    }
    if (!fail) {
        char **env = build_env(src, dest, rdir, pk, jget(r, "options"));
        const char *steps[] = {"prepare", "build", "check", "package", NULL};
        jval *b = jget(r, "build");
        for (const char **st = steps; *st && !fail; st++) {
            if (!strcmp(*st, "check") && o->nocheck) continue;
            const char *script = jgets(b, *st, NULL);
            if (!script) continue;
            printf("==> %s()\n", *st);
            fflush(stdout);
            char *argv[] = {"bash", "-euo", "pipefail", "-c", (char *)script, NULL};
            int rc = run_cmd(argv, src, env);
            if (rc) {
                set_error("%s() fehlgeschlagen (%d)", *st, rc);
                fail = 1;
            }
        }
        for (char **e = env; *e; e++) free(*e);
        free(env);
    }
    if (!fail) {
        post_process(dest, jgetb(jget(r, "options"), "strip", 1));
        out = syp_build(dest, info, outdir, signer);
    }
    if (!o->keep_work) rm_rf(work);
    else printf("   Arbeitsverzeichnis: %s\n", work);
    free(src);
    free(dest);
    free(work);
done:
    json_free(info);
    json_free(r);
    free(rdir);
    free(rpath);
    return out;
}
