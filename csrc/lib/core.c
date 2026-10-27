/* core.c – Konfiguration, Repositories (signierte Indizes, Downloads) und SQLite-Datenbank */
#define _GNU_SOURCE
#include "core.h"

#include <curl/curl.h>
#include <dirent.h>
#include <errno.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/utsname.h>
#include <time.h>
#include <unistd.h>
#include <zstd.h>

#include "toml.h"

/* --- Konfiguration ------------------------------------------------------------- */
static int cmp_repo(const void *a, const void *b) {
    return ((const repoconf *)a)->priority - ((const repoconf *)b)->priority;
}

int config_load(const char *root, config *c) {
    memset(c, 0, sizeof *c);
    c->root = xstrdup(root && *root ? root : "/");
    struct utsname u;
    uname(&u);
    c->arch = xstrdup(u.machine);
    c->snapshots = 1;
    c->snapshot_backend = xstrdup("snapper");
    c->keep_cache = 3;
    c->run_scripts = 1;
    c->require_signatures = 1;
    char *f = cfg_path(c, "etc/syp/syp.toml");
    if (path_exists(f)) {
        jval *t = toml_load(f);
        if (!t) {
            free(f);
            return -1;
        }
        const char *arch = jgets(t, "arch", NULL);
        if (arch) {
            free(c->arch);
            c->arch = xstrdup(arch);
        }
        jval *repos = jgeta(t, "repo");
        for (size_t i = 0; repos && i < repos->n; i++) {
            jval *r = repos->items[i];
            c->repos = xrealloc(c->repos, (c->nrepos + 1) * sizeof(repoconf));
            repoconf *rc = &c->repos[c->nrepos++];
            rc->name = xstrdup(jgets(r, "name", "repo"));
            rc->url = xstrdup(jgets(r, "url", ""));
            rc->priority = (int)jgeti(r, "priority", 50);
            rc->enabled = jgetb(r, "enabled", 1);
        }
        jval *s = jget(t, "snapshots");
        c->snapshots = jgetb(s, "enabled", 1);
        const char *be = jgets(s, "backend", NULL);
        if (be) {
            free(c->snapshot_backend);
            c->snapshot_backend = xstrdup(be);
        }
        c->keep_cache = (int)jgeti(jget(t, "cache"), "keep", 3);
        jval *sec = jget(t, "security");
        c->run_scripts = jgetb(sec, "run_scripts", 1);
        c->require_signatures = jgetb(sec, "require_signatures", 1);
        jval *ins = jget(t, "install");
        jval *ex = jgeta(ins, "exclude"), *in = jgeta(ins, "include");
        for (size_t i = 0; ex && i < ex->n; i++)
            if (ex->items[i]->t == J_STR) sv_push(&c->exclude, ex->items[i]->s);
        for (size_t i = 0; in && i < in->n; i++)
            if (in->items[i]->t == J_STR) sv_push(&c->include, in->items[i]->s);
        json_free(t);
    }
    free(f);
    if (c->nrepos > 1) qsort(c->repos, c->nrepos, sizeof(repoconf), cmp_repo);
    return 0;
}

char *cfg_path(const config *c, const char *rel) { return path_join(c->root, rel); }
int cfg_is_root(const config *c) { return strcmp(c->root, "/") == 0; }

int cfg_excluded(const config *c, const char *path) {
    if (!c->exclude.n) return 0;
    while (*path == '/') path++;
    char *abs = xasprintf("/%s", path);
    int ex = 0;
    for (size_t i = 0; i < c->exclude.n && !ex; i++) ex = glob_match(c->exclude.v[i], abs);
    for (size_t i = 0; ex && i < c->include.n; i++)
        if (glob_match(c->include.v[i], abs)) ex = 0;
    free(abs);
    return ex;
}

/* --- zstd ----------------------------------------------------------------------- */
int zstd_compress_buf(const char *in, size_t n, int level, char **out, size_t *outn) {
    size_t cap = ZSTD_compressBound(n);
    char *o = xmalloc(cap);
    size_t r = ZSTD_compress(o, cap, in, n, level);
    if (ZSTD_isError(r)) {
        free(o);
        set_error("zstd: %s", ZSTD_getErrorName(r));
        return -1;
    }
    *out = o;
    *outn = r;
    return 0;
}
int zstd_decompress_buf(const char *in, size_t n, char **out, size_t *outn) {
    ZSTD_DStream *ds = ZSTD_createDStream();
    ZSTD_initDStream(ds);
    sbuf b = {0};
    sb_init(&b);
    char buf[1 << 16];
    ZSTD_inBuffer ib = {in, n, 0};
    while (ib.pos < ib.size) {
        ZSTD_outBuffer ob = {buf, sizeof buf, 0};
        size_t r = ZSTD_decompressStream(ds, &ob, &ib);
        if (ZSTD_isError(r)) {
            ZSTD_freeDStream(ds);
            free(b.s);
            set_error("zstd: %s", ZSTD_getErrorName(r));
            return -1;
        }
        sb_addn(&b, buf, ob.pos);
        if (r == 0 && ib.pos == ib.size) break;
    }
    ZSTD_freeDStream(ds);
    *outn = b.len;
    *out = sb_take(&b);
    return 0;
}

/* --- Downloads -------------------------------------------------------------------- */
static size_t curl_write(void *ptr, size_t sz, size_t n, void *ud) { return fwrite(ptr, sz, n, (FILE *)ud); }

int fetch_url(const char *url, const char *dest) {
    char *dir = path_dirname(dest);
    mkdir_p(dir, 0755);
    free(dir);
    char *part = xasprintf("%s.part", dest);
    int rc = 0;
    if (!strstr(url, "://")) {
        rc = copy_file(url, part);
    } else if (str_starts(url, "file://")) {
        rc = copy_file(url + 7, part);
    } else {
        /* Vorübergehende Fehler (Zeitüberschreitung, Verbindungsabbruch, HTTP 5xx/429) bis zu
         * SYP_FETCH_RETRIES-mal wiederholen; 404/403 sofort melden. */
        const char *re = getenv("SYP_FETCH_RETRIES");
        int tries = re && *re ? atoi(re) : 3;
        if (tries < 1) tries = 1;
        int http1 = 0;
        for (int attempt = 1;; attempt++) {
            FILE *f = fopen(part, "wb");
            if (!f) {
                set_error("%s: %s", part, strerror(errno));
                free(part);
                return -1;
            }
            CURL *h = curl_easy_init();
            char err[CURL_ERROR_SIZE] = "";
            long code = 0;
            curl_easy_setopt(h, CURLOPT_URL, url);
            curl_easy_setopt(h, CURLOPT_WRITEFUNCTION, curl_write);
            curl_easy_setopt(h, CURLOPT_WRITEDATA, f);
            curl_easy_setopt(h, CURLOPT_FOLLOWLOCATION, 1L);
            curl_easy_setopt(h, CURLOPT_MAXREDIRS, 10L);
            curl_easy_setopt(h, CURLOPT_FAILONERROR, 1L);
            curl_easy_setopt(h, CURLOPT_USERAGENT, "syp/" SYP_VERSION);
            curl_easy_setopt(h, CURLOPT_CONNECTTIMEOUT, 30L);
            curl_easy_setopt(h, CURLOPT_LOW_SPEED_LIMIT, 1L);
            curl_easy_setopt(h, CURLOPT_LOW_SPEED_TIME, 60L);
            curl_easy_setopt(h, CURLOPT_ERRORBUFFER, err);
            /* Manche Server (z. B. salsa.debian.org) brechen HTTP/2-Streams ab: Wiederholungen über HTTP/1.1 */
            if (http1) curl_easy_setopt(h, CURLOPT_HTTP_VERSION, (long)CURL_HTTP_VERSION_1_1);
            CURLcode r = curl_easy_perform(h);
            curl_easy_getinfo(h, CURLINFO_RESPONSE_CODE, &code);
            curl_easy_cleanup(h);
            fclose(f);
            if (r == CURLE_OK) {
                rc = 0;
                break;
            }
            set_error("Download %s: %s", url, *err ? err : curl_easy_strerror(r));
            rc = -1;
            int permanent = r == CURLE_HTTP_RETURNED_ERROR && code >= 400 && code < 500 && code != 408 && code != 429;
            if (permanent || attempt >= tries) break;
            if (r == CURLE_HTTP2 || r == CURLE_HTTP2_STREAM) http1 = 1;
            fprintf(stderr, "  Versuch %d/%d fehlgeschlagen (%s), neuer Versuch …\n", attempt, tries, last_error());
            sleep((unsigned)(2 * attempt));
        }
    }
    if (!rc && rename(part, dest)) {
        set_error("%s: %s", dest, strerror(errno));
        rc = -1;
    }
    if (rc) unlink(part);
    free(part);
    return rc;
}

/* --- Repositories ------------------------------------------------------------------ */
/* Signierte Indizes laufen ab (Standard 14 Tage): schützt vor Freeze-Angriffen, bei denen ein
 * Spiegel dauerhaft einen alten, korrekt signierten Stand ausliefert. */
long long index_expiry(void) {
    const char *d = getenv("SYP_INDEX_VALID_DAYS");
    long long days = d && *d ? atoll(d) : 14;
    return (long long)time(NULL) + days * 86400;
}

static jval *load_index_file(const char *path) {
    char *raw, *js;
    size_t rn, jn;
    jval *idx = NULL;
    if (read_file(path, &raw, &rn)) return NULL;
    if (zstd_decompress_buf(raw, rn, &js, &jn) == 0) {
        idx = json_parse(js, jn);
        free(js);
    }
    free(raw);
    return idx;
}

jval *repo_show_index(const char *repo_dir, const char *pubkey_path) {
    char *idx = path_join(repo_dir, "index.json.zst");
    jval *r = NULL;
    if (pubkey_path) {
        pubkey k;
        if (pubkey_load(pubkey_path, &k)) goto out;
        keyring kr = {&k, 1};
        const pubkey *ok = verify_file(&kr, idx);
        free(k.comment);
        if (!ok) goto out;
    }
    r = load_index_file(idx);
    if (!r && !last_error()[0]) set_error("%s: Index nicht lesbar", idx);
out:
    free(idx);
    return r;
}

/* Prüft einen frisch geladenen (signaturgeprüften) Index gegen Name, Architektur, Ablauf und
 * den zuletzt akzeptierten Stand (kein Zurückspielen älterer Indizes). */
static int check_index(const config *c, const repoconf *r, const char *fresh, const char *current) {
    jval *n = load_index_file(fresh);
    if (!n) {
        set_error("Repo %s: Index unlesbar", r->name);
        return -1;
    }
    int rc = 0;
    const char *name = jgets(n, "name", NULL), *arch = jgets(n, "arch", NULL);
    long long gen = jgeti(n, "generated", 0), exp = jgeti(n, "expires", 0), now = (long long)time(NULL);
    if (name && strcmp(name, r->name)) {
        set_error("Repo %s: Index gehört zu Repo »%s« (Verwechslungsangriff?)", r->name, name);
        rc = -1;
    } else if (arch && strcmp(arch, c->arch) && strcmp(arch, "any")) {
        set_error("Repo %s: Index für Architektur %s, System ist %s", r->name, arch, c->arch);
        rc = -1;
    } else if (exp && exp < now && !getenv("SYP_ALLOW_EXPIRED")) {
        set_error("Repo %s: signierter Index ist seit %lld Tagen abgelaufen (Freeze-Angriff oder veralteter "
                  "Spiegel?)",
                  r->name, (now - exp) / 86400 + 1);
        rc = -1;
    } else {
        jval *old = load_index_file(current);
        long long oldgen = old ? jgeti(old, "generated", 0) : 0;
        if (old && gen < oldgen) {
            set_error("Repo %s: Index ist älter als der bereits bekannte Stand (Rollback-Angriff?)", r->name);
            rc = -1;
        }
        json_free(old);
    }
    json_free(n);
    return rc;
}

int repo_sync(const config *c, const keyring *kr, int quiet) {
    char *syncdir = cfg_path(c, "var/lib/syp/sync");
    mkdir_p(syncdir, 0755);
    int rc = 0;
    for (size_t i = 0; i < c->nrepos; i++) {
        const repoconf *r = &c->repos[i];
        if (!r->enabled) continue;
        char *tmp = xasprintf("%s/.%s.new", syncdir, r->name), *tmpsig = xasprintf("%s.sig", tmp);
        char *u1 = xasprintf("%s/index.json.zst", r->url), *u2 = xasprintf("%s/index.json.zst.sig", r->url);
        if (fetch_url(u1, tmp) || fetch_url(u2, tmpsig)) {
            rc = -1;
        } else {
            const pubkey *k = c->require_signatures ? verify_file(kr, tmp) : NULL;
            if (c->require_signatures && !k) {
                char *m = xasprintf("Repo %s: %s", r->name, last_error());
                set_error("%s", m);
                free(m);
                rc = -1;
            } else {
                char *dest = xasprintf("%s/%s.index.json.zst", syncdir, r->name), *ds = xasprintf("%s.sig", dest);
                if (check_index(c, r, tmp, dest)) {
                    rc = -1;
                } else {
                    if (k && !quiet) printf(":: %s: Index signiert von %s (%s)\n", r->name, k->id, k->comment);
                    rename(tmp, dest);
                    rename(tmpsig, ds);
                }
                free(dest);
                free(ds);
            }
        }
        unlink(tmp);
        unlink(tmpsig);
        free(tmp);
        free(tmpsig);
        free(u1);
        free(u2);
        if (rc) break;
    }
    free(syncdir);
    return rc;
}

void repo_load_synced(const config *c, ptrvec *out) {
    for (size_t i = 0; i < c->nrepos; i++) {
        const repoconf *r = &c->repos[i];
        if (!r->enabled) continue;
        char *rel = xasprintf("var/lib/syp/sync/%s.index.json.zst", r->name), *f = cfg_path(c, rel);
        free(rel);
        char *raw, *js;
        size_t rn, jn;
        if (read_file(f, &raw, &rn) == 0) {
            if (zstd_decompress_buf(raw, rn, &js, &jn) == 0) {
                jval *idx = json_parse(js, jn);
                jval *pk = jgeta(idx, "packages");
                for (size_t k = 0; pk && k < pk->n; k++) {
                    jval *e = pk->items[k];
                    jval *info = jget(e, "info");
                    if (!info) continue;
                    pkg *p = pkg_new(jcopy(info));
                    if (!p) continue;
                    p->repo = xstrdup(r->name);
                    p->file = xstrdup(jgets(e, "file", ""));
                    p->sha256 = xstrdup(jgets(e, "sha256", ""));
                    p->size = jgeti(e, "size", 0);
                    p->priority = r->priority;
                    pv_push(out, p);
                }
                json_free(idx);
                free(js);
            }
            free(raw);
        }
        free(f);
    }
}

char *repo_fetch_package(const config *c, const pkg *p) {
    if (p->local_path) return xstrdup(p->local_path);
    const repoconf *r = NULL;
    for (size_t i = 0; i < c->nrepos; i++)
        if (!strcmp(c->repos[i].name, p->repo)) r = &c->repos[i];
    if (!r) {
        set_error("Repo %s nicht konfiguriert", p->repo);
        return NULL;
    }
    char *cache = cfg_path(c, "var/cache/syp/pkg"), *dest = path_join(cache, path_basename(p->file)), h[65];
    free(cache);
    if (sha256_file(dest, h) || strcmp(h, p->sha256)) {
        char *url = xasprintf("%s/%s", r->url, p->file);
        int rc = fetch_url(url, dest);
        free(url);
        if (rc) {
            free(dest);
            return NULL;
        }
    }
    if (sha256_file(dest, h) || strcmp(h, p->sha256)) {
        unlink(dest);
        set_error("%s: Prüfsumme stimmt nicht mit dem signierten Index überein", p->name);
        free(dest);
        return NULL;
    }
    return dest;
}

pkg *local_package(const char *path) {
    sypfile f;
    if (syp_open_meta(path, &f)) return NULL;
    pkg *p = pkg_new(jcopy(f.info));
    syp_close(&f);
    if (!p) return NULL;
    char h[65];
    sha256_file(path, h);
    struct stat st;
    stat(path, &st);
    p->repo = xstrdup("lokal");
    p->file = xstrdup(path_basename(path));
    p->sha256 = xstrdup(h);
    p->size = st.st_size;
    p->priority = 0;
    p->local_path = realpath(path, NULL);
    return p;
}

static void collect_syp(const char *dir, strvec *out) {
    struct dirent **list;
    int n = scandir(dir, &list, NULL, alphasort);
    for (int i = 0; i < n; i++) {
        const char *d = list[i]->d_name;
        if (strcmp(d, ".") && strcmp(d, "..")) {
            char *p = path_join(dir, d);
            if (is_dir(p)) collect_syp(p, out);
            else if (str_ends(d, ".syp")) sv_push(out, p);
            free(p);
        }
        free(list[i]);
    }
    if (n >= 0) free(list);
}

int repo_build_index(const char *repo_dir, const char *name, const char *arch, const seckey *signer) {
    char *pool = path_join(repo_dir, "pool");
    strvec files = {0};
    collect_syp(pool, &files);
    sv_sort(&files);
    free(pool);
    jval *pk = jarr();
    for (size_t i = 0; i < files.n; i++) {
        sypfile f;
        if (syp_open_meta(files.v[i], &f)) {
            fprintf(stderr, "Warnung: %s\n", last_error());
            continue;
        }
        const char *a = jgets(f.info, "arch", "");
        if (!strcmp(a, arch) || !strcmp(a, "any")) {
            jval *e = jobj();
            char h[65];
            sha256_file(files.v[i], h);
            struct stat st;
            stat(files.v[i], &st);
            jset(e, "info", jcopy(f.info));
            jset(e, "file", jstr(files.v[i] + strlen(repo_dir) + (repo_dir[strlen(repo_dir) - 1] == '/' ? 0 : 1)));
            jset(e, "sha256", jstr(h));
            jset(e, "size", jint(st.st_size));
            jpush(pk, e);
        }
        syp_close(&f);
    }
    sv_free(&files);
    jval *idx = jobj();
    jset(idx, "name", jstr(name));
    jset(idx, "arch", jstr(arch));
    jset(idx, "generated", jint((long long)time(NULL)));
    jset(idx, "expires", jint(index_expiry()));
    jset(idx, "packages", pk);
    char *js = json_dump(idx, 0, 0), *z;
    size_t zn;
    json_free(idx);
    int rc = zstd_compress_buf(js, strlen(js), 19, &z, &zn);
    free(js);
    if (rc) return -1;
    char *out = path_join(repo_dir, "index.json.zst");
    rc = write_file(out, z, zn, 0644);
    free(z);
    if (!rc && signer) rc = sign_file(signer, out);
    free(out);
    return rc;
}

/* Fügt ein Paket dem vorhandenen Index hinzu, ohne den Pool neu zu lesen */
int repo_index_add(const char *repo_dir, const char *name, const char *arch, const seckey *signer,
                   const char *pkgfile) {
    char *ip = path_join(repo_dir, "index.json.zst"), *raw = NULL, *js = NULL;
    size_t rn, jn;
    jval *idx = NULL;
    if (read_file(ip, &raw, &rn) == 0 && zstd_decompress_buf(raw, rn, &js, &jn) == 0) idx = json_parse(js, jn);
    free(raw);
    free(js);
    if (!idx) {
        idx = jobj();
        jset(idx, "packages", jarr());
    }
    jset(idx, "name", jstr(name));
    jset(idx, "arch", jstr(arch));
    jset(idx, "generated", jint((long long)time(NULL)));
    jset(idx, "expires", jint(index_expiry()));
    jval *pk = jgeta(idx, "packages");
    sypfile f;
    if (syp_open_meta(pkgfile, &f)) {
        json_free(idx);
        free(ip);
        return -1;
    }
    const char *rel = pkgfile + strlen(repo_dir) + (repo_dir[strlen(repo_dir) - 1] == '/' ? 0 : 1);
    for (size_t i = 0; i < pk->n; i++)
        if (!strcmp(jgets(pk->items[i], "file", ""), rel)) {
            json_free(pk->items[i]);
            memmove(&pk->items[i], &pk->items[i + 1], (pk->n - i - 1) * sizeof(jval *));
            pk->n--;
            break;
        }
    jval *e = jobj();
    char h[65];
    sha256_file(pkgfile, h);
    struct stat st;
    stat(pkgfile, &st);
    jset(e, "info", jcopy(f.info));
    jset(e, "file", jstr(rel));
    jset(e, "sha256", jstr(h));
    jset(e, "size", jint(st.st_size));
    jpush(pk, e);
    syp_close(&f);
    char *out = json_dump(idx, 0, 0), *z;
    size_t zn;
    json_free(idx);
    int rc = zstd_compress_buf(out, strlen(out), 19, &z, &zn);
    free(out);
    if (!rc) rc = write_file(ip, z, zn, 0644);
    if (!rc) free(z);
    if (!rc && signer) rc = sign_file(signer, ip);
    free(ip);
    return rc;
}

char *repo_add_to_pool(const char *repo_dir, const char *pkg_path) {
    sypfile f;
    if (syp_open_meta(pkg_path, &f)) return NULL;
    char letter[2] = {jgets(f.info, "name", "x")[0], 0};
    syp_close(&f);
    char *pool = path_join(repo_dir, "pool"), *d = path_join(pool, letter);
    mkdir_p(d, 0755);
    char *dest = path_join(d, path_basename(pkg_path));
    free(pool);
    free(d);
    if (copy_file(pkg_path, dest)) {
        free(dest);
        return NULL;
    }
    return dest;
}

/* --- Datenbank ---------------------------------------------------------------------- */
static const char *SCHEMA =
    "CREATE TABLE IF NOT EXISTS packages (name TEXT PRIMARY KEY, version TEXT NOT NULL, info TEXT NOT NULL,"
    " reason TEXT NOT NULL DEFAULT 'explicit', installed_at INTEGER NOT NULL);"
    "CREATE TABLE IF NOT EXISTS files (path TEXT NOT NULL, pkg TEXT NOT NULL REFERENCES packages(name)"
    " ON DELETE CASCADE, type TEXT NOT NULL, sha256 TEXT, PRIMARY KEY (path, pkg));"
    "CREATE INDEX IF NOT EXISTS files_path ON files(path);"
    "CREATE TABLE IF NOT EXISTS transactions (id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL,"
    " cmdline TEXT NOT NULL, status TEXT NOT NULL, before TEXT NOT NULL, after TEXT, snapshot_pre TEXT,"
    " snapshot_post TEXT);";

int db_open(const char *path, database *d) {
    memset(d, 0, sizeof *d);
    char *dir = path_dirname(path);
    int can = mkdir_p(dir, 0755) == 0 && (access(dir, W_OK) == 0) &&
              (!path_exists(path) || access(path, W_OK) == 0);
    free(dir);
    if (can && sqlite3_open(path, &d->db) == SQLITE_OK &&
        sqlite3_exec(d->db, "PRAGMA foreign_keys=ON; PRAGMA journal_mode=WAL; PRAGMA busy_timeout=5000;",
                     NULL, NULL, NULL) == SQLITE_OK &&
        sqlite3_exec(d->db, SCHEMA, NULL, NULL, NULL) == SQLITE_OK)
        return 0;
    if (d->db) sqlite3_close(d->db);
    d->db = NULL;
    /* Ohne Schreibrechte: unveränderlich lesen bzw. leere In-Memory-DB */
    d->readonly = 1;
    char *uri = path_exists(path) ? xasprintf("file:%s?mode=ro&immutable=1", path) : xstrdup(":memory:");
    int rc = sqlite3_open_v2(uri, &d->db, SQLITE_OPEN_READONLY | SQLITE_OPEN_URI, NULL);
    if (!path_exists(path)) {
        sqlite3_close(d->db);
        rc = sqlite3_open(":memory:", &d->db);
        sqlite3_exec(d->db, SCHEMA, NULL, NULL, NULL);
    }
    free(uri);
    if (rc != SQLITE_OK) {
        set_error("Datenbank %s: %s", path, sqlite3_errmsg(d->db));
        return -1;
    }
    return 0;
}
void db_close(database *d) {
    if (d->db) sqlite3_close(d->db);
    d->db = NULL;
}

static sqlite3_stmt *prep(database *d, const char *sql) {
    sqlite3_stmt *st = NULL;
    if (sqlite3_prepare_v2(d->db, sql, -1, &st, NULL) != SQLITE_OK) die("SQL: %s", sqlite3_errmsg(d->db));
    return st;
}

void db_installed(database *d, hmap *out) {
    hm_init(out);
    sqlite3_stmt *st = prep(d, "SELECT info, reason, installed_at FROM packages");
    while (sqlite3_step(st) == SQLITE_ROW) {
        const char *js = (const char *)sqlite3_column_text(st, 0);
        jval *info = json_parse(js, strlen(js));
        pkg *p = info ? pkg_new(info) : NULL;
        if (!p) continue;
        p->installed = 1;
        p->repo = xstrdup("installiert");
        p->reason = xstrdup((const char *)sqlite3_column_text(st, 1));
        p->installed_at = sqlite3_column_int64(st, 2);
        hm_set(out, p->name, p);
    }
    sqlite3_finalize(st);
}

jval *db_state(database *d) {
    jval *o = jobj();
    sqlite3_stmt *st = prep(d, "SELECT name, version FROM packages ORDER BY name");
    while (sqlite3_step(st) == SQLITE_ROW)
        jset(o, (const char *)sqlite3_column_text(st, 0), jstr((const char *)sqlite3_column_text(st, 1)));
    sqlite3_finalize(st);
    return o;
}

jval *db_files_of(database *d, const char *name) {
    jval *a = jarr();
    sqlite3_stmt *st = prep(d, "SELECT path, type, sha256 FROM files WHERE pkg=? ORDER BY path");
    sqlite3_bind_text(st, 1, name, -1, SQLITE_TRANSIENT);
    while (sqlite3_step(st) == SQLITE_ROW) {
        jval *e = jobj();
        jset(e, "path", jstr((const char *)sqlite3_column_text(st, 0)));
        jset(e, "type", jstr((const char *)sqlite3_column_text(st, 1)));
        const unsigned char *h = sqlite3_column_text(st, 2);
        jset(e, "sha256", h ? jstr((const char *)h) : jnull());
        jpush(a, e);
    }
    sqlite3_finalize(st);
    return a;
}

void db_owners(database *d, const char *path, strvec *out) {
    sqlite3_stmt *st = prep(d, "SELECT pkg FROM files WHERE path=?");
    sqlite3_bind_text(st, 1, path, -1, SQLITE_TRANSIENT);
    while (sqlite3_step(st) == SQLITE_ROW) sv_push(out, (const char *)sqlite3_column_text(st, 0));
    sqlite3_finalize(st);
}

int db_put(database *d, const jval *info, const jval *files, const char *reason) {
    const char *name = jgets(info, "name", "");
    char *js = json_dump(info, 0, 0);
    sqlite3_exec(d->db, "BEGIN", NULL, NULL, NULL);
    sqlite3_stmt *st = prep(d, "DELETE FROM files WHERE pkg=?");
    sqlite3_bind_text(st, 1, name, -1, SQLITE_TRANSIENT);
    sqlite3_step(st);
    sqlite3_finalize(st);
    st = prep(d, "INSERT INTO packages(name, version, info, reason, installed_at) VALUES(?,?,?,?,?) "
                 "ON CONFLICT(name) DO UPDATE SET version=excluded.version, info=excluded.info, "
                 "installed_at=excluded.installed_at");
    sqlite3_bind_text(st, 1, name, -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(st, 2, jgets(info, "version", ""), -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(st, 3, js, -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(st, 4, reason, -1, SQLITE_TRANSIENT);
    sqlite3_bind_int64(st, 5, (sqlite3_int64)time(NULL));
    int rc = sqlite3_step(st) == SQLITE_DONE ? 0 : -1;
    sqlite3_finalize(st);
    st = prep(d, "INSERT OR REPLACE INTO files(path, pkg, type, sha256) VALUES(?,?,?,?)");
    for (size_t i = 0; !rc && i < files->n; i++) {
        jval *f = files->items[i];
        sqlite3_reset(st);
        sqlite3_bind_text(st, 1, jgets(f, "path", ""), -1, SQLITE_TRANSIENT);
        sqlite3_bind_text(st, 2, name, -1, SQLITE_TRANSIENT);
        sqlite3_bind_text(st, 3, jgets(f, "type", "file"), -1, SQLITE_TRANSIENT);
        const char *h = jgets(f, "sha256", NULL);
        if (h) sqlite3_bind_text(st, 4, h, -1, SQLITE_TRANSIENT);
        else sqlite3_bind_null(st, 4);
        if (sqlite3_step(st) != SQLITE_DONE) rc = -1;
    }
    sqlite3_finalize(st);
    if (rc) set_error("Datenbank: %s", sqlite3_errmsg(d->db));
    sqlite3_exec(d->db, rc ? "ROLLBACK" : "COMMIT", NULL, NULL, NULL);
    free(js);
    return rc;
}

int db_delete(database *d, const char *name) {
    sqlite3_stmt *st = prep(d, "DELETE FROM packages WHERE name=?");
    sqlite3_bind_text(st, 1, name, -1, SQLITE_TRANSIENT);
    int rc = sqlite3_step(st) == SQLITE_DONE ? 0 : -1;
    sqlite3_finalize(st);
    return rc;
}

long long db_tx_begin(database *d, const char *cmdline, const char *snap_pre) {
    jval *s = db_state(d);
    char *js = json_dump(s, 0, 0);
    json_free(s);
    sqlite3_stmt *st = prep(d, "INSERT INTO transactions(ts, cmdline, status, before, snapshot_pre) VALUES(?,?,?,?,?)");
    sqlite3_bind_int64(st, 1, (sqlite3_int64)time(NULL));
    sqlite3_bind_text(st, 2, cmdline, -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(st, 3, "running", -1, SQLITE_STATIC);
    sqlite3_bind_text(st, 4, js, -1, SQLITE_TRANSIENT);
    if (snap_pre) sqlite3_bind_text(st, 5, snap_pre, -1, SQLITE_TRANSIENT);
    else sqlite3_bind_null(st, 5);
    sqlite3_step(st);
    sqlite3_finalize(st);
    free(js);
    return sqlite3_last_insert_rowid(d->db);
}

void db_tx_end(database *d, long long tx, const char *status, const char *snap_post) {
    jval *s = db_state(d);
    char *js = json_dump(s, 0, 0);
    json_free(s);
    sqlite3_stmt *st = prep(d, "UPDATE transactions SET status=?, after=?, snapshot_post=? WHERE id=?");
    sqlite3_bind_text(st, 1, status, -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(st, 2, js, -1, SQLITE_TRANSIENT);
    if (snap_post) sqlite3_bind_text(st, 3, snap_post, -1, SQLITE_TRANSIENT);
    else sqlite3_bind_null(st, 3);
    sqlite3_bind_int64(st, 4, tx);
    sqlite3_step(st);
    sqlite3_finalize(st);
    free(js);
}

static jval *tx_rows(database *d, const char *sql, long long arg) {
    jval *a = jarr();
    sqlite3_stmt *st = prep(d, sql);
    sqlite3_bind_int64(st, 1, arg);
    while (sqlite3_step(st) == SQLITE_ROW) {
        jval *t = jobj();
        jset(t, "id", jint(sqlite3_column_int64(st, 0)));
        jset(t, "ts", jint(sqlite3_column_int64(st, 1)));
        jset(t, "cmdline", jstr((const char *)sqlite3_column_text(st, 2)));
        jset(t, "status", jstr((const char *)sqlite3_column_text(st, 3)));
        const char *b = (const char *)sqlite3_column_text(st, 4), *af = (const char *)sqlite3_column_text(st, 5);
        jval *bj = b ? json_parse(b, strlen(b)) : NULL;
        jval *aj = af ? json_parse(af, strlen(af)) : NULL;
        jset(t, "before", bj ? bj : jobj());
        jset(t, "after", aj ? aj : jnull());
        const unsigned char *sp = sqlite3_column_text(st, 6), *so = sqlite3_column_text(st, 7);
        jset(t, "snapshot_pre", sp ? jstr((const char *)sp) : jnull());
        jset(t, "snapshot_post", so ? jstr((const char *)so) : jnull());
        jpush(a, t);
    }
    sqlite3_finalize(st);
    return a;
}

jval *db_history(database *d, int limit) {
    return tx_rows(d,
                   "SELECT id, ts, cmdline, status, before, after, snapshot_pre, snapshot_post FROM transactions "
                   "ORDER BY id DESC LIMIT ?",
                   limit);
}

jval *db_tx(database *d, long long id) {
    jval *a = tx_rows(d,
                      "SELECT id, ts, cmdline, status, before, after, snapshot_pre, snapshot_post FROM transactions "
                      "WHERE id=?",
                      id);
    jval *r = a->n ? jcopy(a->items[0]) : NULL;
    json_free(a);
    return r;
}
