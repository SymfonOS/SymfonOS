/* util.c – Hilfsfunktionen für syp */
#define _GNU_SOURCE
#include "util.h"

#include <locale.h>
#include <langinfo.h>
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <fnmatch.h>
#include <sodium.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <unistd.h>

int g_tty = 0;
char HM_TOMB[] = "";
static char g_err[4096];

void *xmalloc(size_t n) {
    void *p = malloc(n ? n : 1);
    if (!p) die("Kein Speicher");
    return p;
}
void *xcalloc(size_t n, size_t m) {
    void *p = calloc(n ? n : 1, m ? m : 1);
    if (!p) die("Kein Speicher");
    return p;
}
void *xrealloc(void *p, size_t n) {
    p = realloc(p, n ? n : 1);
    if (!p) die("Kein Speicher");
    return p;
}
char *xstrdup(const char *s) { return xstrndup(s, strlen(s)); }
char *xstrndup(const char *s, size_t n) {
    char *r = xmalloc(n + 1);
    memcpy(r, s, n);
    r[n] = 0;
    return r;
}
char *xasprintf(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    char *s = NULL;
    if (vasprintf(&s, fmt, ap) < 0) die("Kein Speicher");
    va_end(ap);
    return s;
}

void die(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    fprintf(stderr, "%sFehler:%s ", col("31;1"), col_end());
    vfprintf(stderr, fmt, ap);
    fputc('\n', stderr);
    va_end(ap);
    exit(1);
}

void set_error(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(g_err, sizeof g_err, fmt, ap);
    va_end(ap);
}
const char *last_error(void) { return g_err; }

/* --- sbuf ---------------------------------------------------------------- */
void sb_init(sbuf *b) {
    b->cap = 64;
    b->len = 0;
    b->s = xmalloc(b->cap);
    b->s[0] = 0;
}
static void sb_grow(sbuf *b, size_t extra) {
    if (!b->s) sb_init(b);
    if (b->len + extra + 1 > b->cap) {
        while (b->len + extra + 1 > b->cap) b->cap *= 2;
        b->s = xrealloc(b->s, b->cap);
    }
}
void sb_addn(sbuf *b, const char *s, size_t n) {
    sb_grow(b, n);
    memcpy(b->s + b->len, s, n);
    b->len += n;
    b->s[b->len] = 0;
}
void sb_add(sbuf *b, const char *s) { sb_addn(b, s, strlen(s)); }
void sb_addc(sbuf *b, char c) { sb_addn(b, &c, 1); }
void sb_addf(sbuf *b, const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    char *s = NULL;
    if (vasprintf(&s, fmt, ap) < 0) die("Kein Speicher");
    va_end(ap);
    sb_add(b, s);
    free(s);
}
char *sb_take(sbuf *b) {
    if (!b->s) sb_init(b);
    char *s = b->s;
    b->s = NULL;
    b->len = b->cap = 0;
    return s;
}

/* --- Vektoren ------------------------------------------------------------- */
void sv_push_own(strvec *sv, char *s) {
    if (sv->n == sv->cap) {
        sv->cap = sv->cap ? sv->cap * 2 : 8;
        sv->v = xrealloc(sv->v, sv->cap * sizeof(char *));
    }
    sv->v[sv->n++] = s;
}
void sv_push(strvec *sv, const char *s) { sv_push_own(sv, xstrdup(s)); }
int sv_contains(const strvec *sv, const char *s) {
    for (size_t i = 0; i < sv->n; i++)
        if (strcmp(sv->v[i], s) == 0) return 1;
    return 0;
}
void sv_free(strvec *sv) {
    for (size_t i = 0; i < sv->n; i++) free(sv->v[i]);
    free(sv->v);
    sv->v = NULL;
    sv->n = sv->cap = 0;
}
static int cmpstr(const void *a, const void *b) { return strcmp(*(char *const *)a, *(char *const *)b); }
void sv_sort(strvec *sv) {
    if (sv->n) qsort(sv->v, sv->n, sizeof(char *), cmpstr);
}
void pv_push(ptrvec *pv, void *p) {
    if (pv->n == pv->cap) {
        pv->cap = pv->cap ? pv->cap * 2 : 8;
        pv->v = xrealloc(pv->v, pv->cap * sizeof(void *));
    }
    pv->v[pv->n++] = p;
}

/* --- Hashmap -------------------------------------------------------------- */
static uint64_t hash(const char *s) {
    uint64_t h = 1469598103934665603ULL;
    for (; *s; s++) h = (h ^ (unsigned char)*s) * 1099511628211ULL;
    return h;
}
void hm_init(hmap *m) {
    m->cap = 64;
    m->n = 0;
    m->keys = xcalloc(m->cap, sizeof(char *));
    m->vals = xcalloc(m->cap, sizeof(void *));
}
static size_t hm_slot(const hmap *m, const char *k, int *found) {
    size_t i = hash(k) & (m->cap - 1), tomb = (size_t)-1;
    for (;;) {
        char *key = m->keys[i];
        if (!key) {
            *found = 0;
            return tomb != (size_t)-1 ? tomb : i;
        }
        if (key == HM_TOMB) {
            if (tomb == (size_t)-1) tomb = i;
        } else if (strcmp(key, k) == 0) {
            *found = 1;
            return i;
        }
        i = (i + 1) & (m->cap - 1);
    }
}
void *hm_get(const hmap *m, const char *k) {
    if (!m->cap) return NULL;
    int f;
    size_t i = hm_slot(m, k, &f);
    return f ? m->vals[i] : NULL;
}
static void hm_rehash(hmap *m) {
    hmap n = {0};
    n.cap = m->cap * 2;
    n.keys = xcalloc(n.cap, sizeof(char *));
    n.vals = xcalloc(n.cap, sizeof(void *));
    for (size_t i = 0; i < m->cap; i++)
        if (m->keys[i] && m->keys[i] != HM_TOMB) {
            int f;
            size_t j = hm_slot(&n, m->keys[i], &f);
            n.keys[j] = m->keys[i];
            n.vals[j] = m->vals[i];
            n.n++;
        }
    free(m->keys);
    free(m->vals);
    *m = n;
}
void hm_set(hmap *m, const char *k, void *v) {
    if (!m->cap) hm_init(m);
    if ((m->n + 1) * 2 > m->cap) hm_rehash(m);
    int f;
    size_t i = hm_slot(m, k, &f);
    if (!f) {
        m->keys[i] = xstrdup(k);
        m->n++;
    }
    m->vals[i] = v;
}
void hm_del(hmap *m, const char *k) {
    if (!m->cap) return;
    int f;
    size_t i = hm_slot(m, k, &f);
    if (f) {
        free(m->keys[i]);
        m->keys[i] = HM_TOMB;
        m->vals[i] = NULL;
        m->n--;
    }
}
void hm_free(hmap *m) {
    for (size_t i = 0; i < m->cap; i++)
        if (m->keys[i] && m->keys[i] != HM_TOMB) free(m->keys[i]);
    free(m->keys);
    free(m->vals);
    memset(m, 0, sizeof *m);
}

/* --- Strings -------------------------------------------------------------- */
int str_starts(const char *s, const char *p) { return strncmp(s, p, strlen(p)) == 0; }
int str_ends(const char *s, const char *suffix) {
    size_t a = strlen(s), b = strlen(suffix);
    return a >= b && strcmp(s + a - b, suffix) == 0;
}
char *str_trim(char *s) {
    while (*s == ' ' || *s == '\t' || *s == '\n' || *s == '\r') s++;
    char *e = s + strlen(s);
    while (e > s && (e[-1] == ' ' || e[-1] == '\t' || e[-1] == '\n' || e[-1] == '\r')) *--e = 0;
    return s;
}
void str_lower(char *s) {
    for (; *s; s++)
        if (*s >= 'A' && *s <= 'Z') *s += 32;
}
char *str_replace(const char *s, const char *from, const char *to) {
    sbuf b = {0};
    sb_init(&b);
    size_t fl = strlen(from);
    const char *p;
    while (fl && (p = strstr(s, from))) {
        sb_addn(&b, s, (size_t)(p - s));
        sb_add(&b, to);
        s = p + fl;
    }
    sb_add(&b, s);
    return sb_take(&b);
}
int glob_match(const char *pattern, const char *s) { return fnmatch(pattern, s, 0) == 0; }

/* --- Dateien -------------------------------------------------------------- */
int read_file(const char *path, char **out, size_t *len) {
    int fd = open(path, O_RDONLY | O_CLOEXEC);
    if (fd < 0) {
        set_error("%s: %s", path, strerror(errno));
        return -1;
    }
    sbuf b = {0};
    sb_init(&b);
    char buf[65536];
    ssize_t r;
    while ((r = read(fd, buf, sizeof buf)) > 0) sb_addn(&b, buf, (size_t)r);
    close(fd);
    if (r < 0) {
        free(b.s);
        set_error("%s: %s", path, strerror(errno));
        return -1;
    }
    if (len) *len = b.len;
    *out = sb_take(&b);
    return 0;
}
int write_file(const char *path, const void *data, size_t len, int mode) {
    char *tmp = xasprintf("%s.tmp.%d", path, getpid());
    int fd = open(tmp, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, mode);
    if (fd < 0) {
        set_error("%s: %s", path, strerror(errno));
        free(tmp);
        return -1;
    }
    const char *p = data;
    while (len) {
        ssize_t w = write(fd, p, len);
        if (w < 0) {
            if (errno == EINTR) continue;
            close(fd);
            unlink(tmp);
            free(tmp);
            set_error("%s: %s", path, strerror(errno));
            return -1;
        }
        p += w;
        len -= (size_t)w;
    }
    fchmod(fd, (mode_t)mode);
    close(fd);
    int rc = rename(tmp, path);
    if (rc) set_error("%s: %s", path, strerror(errno));
    free(tmp);
    return rc ? -1 : 0;
}
int mkdir_p(const char *path, int mode) {
    char *p = xstrdup(path);
    for (char *s = p + 1; *s; s++) {
        if (*s == '/') {
            *s = 0;
            if (mkdir(p, (mode_t)mode) && errno != EEXIST) {
                set_error("mkdir %s: %s", p, strerror(errno));
                free(p);
                return -1;
            }
            *s = '/';
        }
    }
    int rc = mkdir(p, (mode_t)mode);
    if (rc && errno != EEXIST) {
        set_error("mkdir %s: %s", p, strerror(errno));
        free(p);
        return -1;
    }
    free(p);
    return 0;
}
int rm_rf(const char *path) {
    struct stat st;
    if (lstat(path, &st)) return errno == ENOENT ? 0 : -1;
    if (S_ISDIR(st.st_mode)) {
        DIR *d = opendir(path);
        if (d) {
            struct dirent *e;
            while ((e = readdir(d))) {
                if (!strcmp(e->d_name, ".") || !strcmp(e->d_name, "..")) continue;
                char *c = path_join(path, e->d_name);
                rm_rf(c);
                free(c);
            }
            closedir(d);
        }
        return rmdir(path);
    }
    return unlink(path);
}
int copy_file(const char *src, const char *dst) {
    int in = open(src, O_RDONLY | O_CLOEXEC);
    if (in < 0) {
        set_error("%s: %s", src, strerror(errno));
        return -1;
    }
    struct stat st;
    fstat(in, &st);
    int out = open(dst, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, st.st_mode & 07777);
    if (out < 0) {
        set_error("%s: %s", dst, strerror(errno));
        close(in);
        return -1;
    }
    char buf[1 << 16];
    ssize_t r;
    while ((r = read(in, buf, sizeof buf)) > 0)
        if (write(out, buf, (size_t)r) != r) {
            r = -1;
            break;
        }
    fchmod(out, st.st_mode & 07777);
    close(in);
    close(out);
    if (r < 0) set_error("Kopieren %s: %s", src, strerror(errno));
    return r < 0 ? -1 : 0;
}
int path_exists(const char *p) {
    struct stat st;
    return lstat(p, &st) == 0;
}
int is_dir(const char *p) {
    struct stat st;
    return stat(p, &st) == 0 && S_ISDIR(st.st_mode);
}
char *path_join(const char *a, const char *b) {
    while (*b == '/') b++;
    size_t la = strlen(a);
    if (la && a[la - 1] == '/') return xasprintf("%s%s", a, b);
    return xasprintf("%s/%s", a, b);
}
char *path_dirname(const char *p) {
    const char *s = strrchr(p, '/');
    if (!s) return xstrdup(".");
    if (s == p) return xstrdup("/");
    return xstrndup(p, (size_t)(s - p));
}
const char *path_basename(const char *p) {
    const char *s = strrchr(p, '/');
    return s ? s + 1 : p;
}
char *make_tempdir(const char *prefix) {
    const char *t = getenv("TMPDIR");
    char *tmpl = xasprintf("%s/%sXXXXXX", t && *t ? t : "/tmp", prefix);
    if (!mkdtemp(tmpl)) die("mkdtemp: %s", strerror(errno));
    return tmpl;
}
int copy_tree(const char *src, const char *dst, const char *const *ignore) {
    struct stat st;
    if (lstat(src, &st)) {
        set_error("%s: %s", src, strerror(errno));
        return -1;
    }
    if (S_ISLNK(st.st_mode)) {
        char t[4096];
        ssize_t n = readlink(src, t, sizeof t - 1);
        if (n < 0) return -1;
        t[n] = 0;
        return symlink(t, dst);
    }
    if (!S_ISDIR(st.st_mode)) return copy_file(src, dst);
    if (mkdir(dst, st.st_mode & 07777) && errno != EEXIST) {
        set_error("mkdir %s: %s", dst, strerror(errno));
        return -1;
    }
    DIR *d = opendir(src);
    if (!d) return -1;
    struct dirent *e;
    int rc = 0;
    while ((e = readdir(d))) {
        if (!strcmp(e->d_name, ".") || !strcmp(e->d_name, "..")) continue;
        int skip = 0;
        for (const char *const *ig = ignore; ig && *ig; ig++)
            if (glob_match(*ig, e->d_name)) skip = 1;
        if (skip) continue;
        char *a = path_join(src, e->d_name), *b = path_join(dst, e->d_name);
        if (copy_tree(a, b, ignore)) rc = -1;
        free(a);
        free(b);
    }
    closedir(d);
    return rc;
}

/* --- Prüfsummen ------------------------------------------------------------ */
static void hex(const unsigned char *h, size_t n, char *out) {
    static const char *x = "0123456789abcdef";
    for (size_t i = 0; i < n; i++) {
        out[2 * i] = x[h[i] >> 4];
        out[2 * i + 1] = x[h[i] & 15];
    }
    out[2 * n] = 0;
}
void sha256_hex(const void *data, size_t len, char out[65]) {
    unsigned char h[crypto_hash_sha256_BYTES];
    crypto_hash_sha256(h, data, len);
    hex(h, sizeof h, out);
}
int sha256_file(const char *path, char out[65]) {
    int fd = open(path, O_RDONLY | O_CLOEXEC);
    if (fd < 0) {
        set_error("%s: %s", path, strerror(errno));
        return -1;
    }
    crypto_hash_sha256_state st;
    crypto_hash_sha256_init(&st);
    unsigned char buf[1 << 16];
    ssize_t r;
    while ((r = read(fd, buf, sizeof buf)) > 0) crypto_hash_sha256_update(&st, buf, (unsigned long long)r);
    close(fd);
    unsigned char h[crypto_hash_sha256_BYTES];
    crypto_hash_sha256_final(&st, h);
    hex(h, sizeof h, out);
    return r < 0 ? -1 : 0;
}

/* --- Prozesse -------------------------------------------------------------- */
int run_cmd(char *const argv[], const char *cwd, char *const envp[]) {
    fflush(stdout);
    fflush(stderr);
    pid_t pid = fork();
    if (pid < 0) return -1;
    if (pid == 0) {
        if (cwd && chdir(cwd)) _exit(127);
        if (envp) execvpe(argv[0], argv, envp);
        else execvp(argv[0], argv);
        _exit(127);
    }
    int st;
    while (waitpid(pid, &st, 0) < 0)
        if (errno != EINTR) return -1;
    return WIFEXITED(st) ? WEXITSTATUS(st) : 128 + WTERMSIG(st);
}
int run_capture(char *const argv[], char **out) {
    int p[2];
    if (pipe(p)) return -1;
    fflush(stdout);
    pid_t pid = fork();
    if (pid < 0) return -1;
    if (pid == 0) {
        dup2(p[1], 1);
        int dn = open("/dev/null", O_WRONLY);
        if (dn >= 0) dup2(dn, 2);
        close(p[0]);
        close(p[1]);
        execvp(argv[0], argv);
        _exit(127);
    }
    close(p[1]);
    sbuf b = {0};
    sb_init(&b);
    char buf[4096];
    ssize_t r;
    while ((r = read(p[0], buf, sizeof buf)) > 0) sb_addn(&b, buf, (size_t)r);
    close(p[0]);
    int st;
    waitpid(pid, &st, 0);
    *out = sb_take(&b);
    return WIFEXITED(st) ? WEXITSTATUS(st) : -1;
}
int which(const char *prog, const char *path_env) {
    if (strchr(prog, '/')) return access(prog, X_OK) == 0;
    char *pe = xstrdup(path_env ? path_env : (getenv("PATH") ? getenv("PATH") : "/usr/bin:/bin"));
    int found = 0;
    for (char *tok = strtok(pe, ":"); tok && !found; tok = strtok(NULL, ":")) {
        char *p = path_join(tok, prog);
        found = access(p, X_OK) == 0;
        free(p);
    }
    free(pe);
    return found;
}

const char *col(const char *code) {
    static char buf[16][32];
    static int i;
    if (!g_tty) return "";
    i = (i + 1) % 16;
    snprintf(buf[i], sizeof buf[i], "\033[%sm", code);
    return buf[i];
}
const char *col_end(void) { return g_tty ? "\033[0m" : ""; }

void init_utf8_locale(void) {
    if (setlocale(LC_CTYPE, "") && !strcmp(nl_langinfo(CODESET), "UTF-8")) return;
    const char *cand[] = {"C.UTF-8", "C.utf8", "en_US.UTF-8", NULL};
    for (const char **c = cand; *c; c++)
        if (setlocale(LC_CTYPE, *c)) return;
}
