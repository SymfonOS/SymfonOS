/* txn.c – Herunterladen, Prüfen, Konfliktcheck, atomares Anwenden, Historie, Rollback */
#define _GNU_SOURCE
#include "txn.h"

#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <stdlib.h>
#include <string.h>
#include <sys/file.h>
#include <sys/stat.h>
#include <sys/vfs.h>
#include <time.h>
#include <unistd.h>

#include "toml.h"

#ifndef SYP_DATADIR
#define SYP_DATADIR "/usr/share/syp"
#endif
#define BTRFS_MAGIC 0x9123683E

void txn_init(txn *t, config *c, database *d, keyring *k) {
    memset(t, 0, sizeof *t);
    t->cfg = c;
    t->db = d;
    t->kr = k;
    t->no_scripts = !c->run_scripts;
}

static void logm(txn *t, const char *fmt, ...) __attribute__((format(printf, 2, 3)));
static void logm(txn *t, const char *fmt, ...) {
    if (t->quiet) return;
    va_list ap;
    va_start(ap, fmt);
    vprintf(fmt, ap);
    va_end(ap);
    putchar('\n');
    fflush(stdout);
}

/* --- Snapshots ------------------------------------------------------------------ */
int snapshot_available(config *c) {
    if (!c->snapshots || !cfg_is_root(c)) return 0;
    struct statfs fs;
    if (statfs("/", &fs) || (unsigned long)fs.f_type != BTRFS_MAGIC) return 0;
    return which("snapper", NULL) || !strcmp(c->snapshot_backend, "btrfs");
}

char *snapshot_create(config *c, const char *kind, const char *desc, const char *pre) {
    if (!snapshot_available(c)) return NULL;
    if (!strcmp(c->snapshot_backend, "snapper") && which("snapper", NULL)) {
        char *argv[16] = {"snapper", "-c", "root", "create", "--type", (char *)kind, "--print-number",
                          "--cleanup-algorithm", "number", "--description", (char *)desc, "--userdata",
                          (char *)(!strcmp(kind, "pre") ? "important=yes" : "important=no"), NULL, NULL, NULL};
        if (!strcmp(kind, "post") && pre) {
            argv[13] = "--pre-number";
            argv[14] = (char *)pre;
        }
        char *out;
        if (run_capture(argv, &out) != 0) {
            free(out);
            return NULL;
        }
        char *s = xstrdup(str_trim(out));
        free(out);
        if (!*s) {
            free(s);
            return NULL;
        }
        return s;
    }
    char *name = xasprintf("/.snapshots/syp-%ld-%s", (long)time(NULL), kind);
    char *argv[] = {"btrfs", "subvolume", "snapshot", "-r", "/", name, NULL};
    if (run_cmd(argv, NULL, NULL) != 0) {
        free(name);
        return NULL;
    }
    return name;
}

/* --- Trigger --------------------------------------------------------------------- */
static void load_trigger_dir(const char *dir, hmap *out) {
    struct dirent **list;
    int n = scandir(dir, &list, NULL, alphasort);
    for (int i = 0; i < n; i++) {
        if (str_ends(list[i]->d_name, ".toml")) {
            char *p = path_join(dir, list[i]->d_name);
            jval *t = toml_load(p);
            if (t) {
                jval *old = hm_get(out, list[i]->d_name);
                json_free(old);
                hm_set(out, list[i]->d_name, t); /* /etc überschreibt gleichnamige */
            }
            free(p);
        }
        free(list[i]);
    }
    if (n >= 0) free(list);
}

void run_triggers(config *c, strvec *changed, int quiet) {
    hmap trig = {0};
    hm_init(&trig);
    char *d1 = cfg_path(c, "usr/share/syp/triggers"), *d2 = cfg_path(c, "etc/syp/triggers");
    load_trigger_dir(SYP_DATADIR "/triggers", &trig);
    load_trigger_dir(d1, &trig);
    load_trigger_dir(d2, &trig);
    free(d1);
    free(d2);
    strvec ids = {0};
    HM_FOREACH(&trig, i) sv_push(&ids, trig.keys[i]);
    sv_sort(&ids);
    char *binpath = cfg_path(c, "usr/bin");
    for (size_t k = 0; k < ids.n; k++) {
        jval *t = hm_get(&trig, ids.v[k]);
        jval *paths = jgeta(t, "paths"), *ex = jgeta(t, "exec");
        if (!paths || !ex || !ex->n) continue;
        int hit = 0;
        for (size_t i = 0; i < changed->n && !hit; i++)
            for (size_t j = 0; j < paths->n && !hit; j++)
                if (glob_match(paths->items[j]->s, changed->v[i])) hit = 1;
        if (!hit || !which(ex->items[0]->s, binpath)) continue;
        if (!quiet) printf(":: Trigger: %s\n", jgets(t, "description", ids.v[k]));
        size_t extra = cfg_is_root(c) ? 0 : 2;
        char **argv = xcalloc(ex->n + extra + 1, sizeof(char *));
        if (extra) {
            argv[0] = "chroot";
            argv[1] = c->root;
        }
        for (size_t i = 0; i < ex->n; i++) argv[extra + i] = ex->items[i]->s;
        if (extra && geteuid() != 0) {
            free(argv);
            continue;
        }
        int rc = run_cmd(argv, NULL, NULL);
        if (rc && !quiet) printf("   Warnung: Trigger %s fehlgeschlagen (%d)\n", ids.v[k], rc);
        free(argv);
    }
    free(binpath);
    HM_FOREACH(&trig, i) json_free(trig.vals[i]);
    hm_free(&trig);
    sv_free(&ids);
}

/* --- Skripte ----------------------------------------------------------------------- */
static int run_script(txn *t, const jval *info, const char *hook, const char *newv, const char *oldv) {
    const char *body = jgets(jget(info, "scripts"), hook, NULL);
    if (!body || !*body || t->no_scripts) return 0;
    const jval *origin = jget(info, "origin");
    const char *fmt = jgets(origin, "format", "native");
    if (strcmp(fmt, "native") && !jgetb(origin, "scripts_reviewed", 0) && !t->trust_scripts) {
        logm(t, "   Hinweis: %s von %s (konvertiert, ungeprüft) übersprungen; --trust-scripts zum Ausführen",
             hook, jgets(info, "name", ""));
        return 0;
    }
    char *argv[8];
    int i = 0;
    if (!cfg_is_root(t->cfg)) {
        char *sh = cfg_path(t->cfg, "bin/sh");
        int ok = geteuid() == 0 && path_exists(sh);
        free(sh);
        if (!ok) {
            logm(t, "   Hinweis: %s von %s in --root ohne chroot übersprungen", hook, jgets(info, "name", ""));
            return 0;
        }
        argv[i++] = "chroot";
        argv[i++] = t->cfg->root;
    }
    argv[i++] = "sh";
    argv[i++] = "-ec";
    argv[i++] = (char *)body;
    argv[i++] = (char *)hook;
    argv[i] = NULL;
    char *e1 = xasprintf("SYP_NEW_VERSION=%s", newv ? newv : ""), *e2 = xasprintf("SYP_OLD_VERSION=%s", oldv ? oldv : "");
    char *envp[] = {"PATH=/usr/bin:/usr/sbin:/bin:/sbin", e1, e2, NULL};
    logm(t, "   %s: %s", hook, jgets(info, "name", ""));
    int rc = run_cmd(argv, NULL, envp);
    free(e1);
    free(e2);
    if (rc && str_starts(hook, "pre_")) {
        set_error("%s von %s fehlgeschlagen (%d)", hook, jgets(info, "name", ""), rc);
        return -1;
    }
    if (rc) logm(t, "   Warnung: %s von %s beendet mit %d", hook, jgets(info, "name", ""), rc);
    return 0;
}

/* --- Dateien ------------------------------------------------------------------------ */
static char *rp(txn *t, const char *rel) { return cfg_path(t->cfg, rel); }

static int hash_regular(const char *p, char out[65]) {
    struct stat st;
    if (lstat(p, &st) || !S_ISREG(st.st_mode)) return -1;
    return sha256_file(p, out);
}

static int place(txn *t, const char *src, const char *dst, const jval *e) {
    const char *type = jgets(e, "type", "file");
    struct stat st;
    if (!strcmp(type, "dir")) {
        if (!is_dir(dst)) {
            if (mkdir_p(dst, 0755)) return -1;
            chmod(dst, (mode_t)jgeti(e, "mode", 0755));
        }
        return 0;
    }
    char *dir = path_dirname(dst);
    mkdir_p(dir, 0755);
    if (lstat(dst, &st) == 0 && S_ISDIR(st.st_mode)) {
        set_error("Verzeichnis im Weg: /%s", jgets(e, "path", ""));
        free(dir);
        return -1;
    }
    char *tmp = xasprintf("%s/.%s.syp-tmp", dir, path_basename(dst));
    free(dir);
    unlink(tmp);
    int rc = 0;
    if (!strcmp(type, "link")) {
        rc = symlink(jgets(e, "target", ""), tmp);
    } else if (rename(src, tmp)) {
        rc = copy_file(src, tmp); /* z. B. EXDEV über Btrfs-Subvolumes */
        if (!rc && geteuid() == 0 && lstat(src, &st) == 0 && chown(tmp, st.st_uid, st.st_gid)) rc = -1;
    }
    if (!rc && rename(tmp, dst)) rc = -1;
    if (rc) {
        set_error("/%s: %s", jgets(e, "path", ""), strerror(errno));
        unlink(tmp);
    } else {
        sv_push(&t->changed, jgets(e, "path", ""));
    }
    free(tmp);
    return rc;
}

static int in_list(const jval *arr, const char *s) {
    for (size_t i = 0; arr && i < arr->n; i++) {
        const char *x = arr->items[i]->s;
        while (*x == '/') x++;
        if (!strcmp(x, s)) return 1;
    }
    return 0;
}

/* keep: Pfade, die bleiben (neue Version) */
static void remove_files(txn *t, const char *name, hmap *keep, const jval *info) {
    jval *files = db_files_of(t->db, name);
    jval *backup = jgeta(info, "backup");
    for (size_t k = files->n; k-- > 0;) {
        jval *f = files->items[k];
        const char *path = jgets(f, "path", "");
        if (keep && hm_get(keep, path)) continue;
        strvec own = {0};
        db_owners(t->db, path, &own);
        int other = 0;
        for (size_t i = 0; i < own.n; i++)
            if (strcmp(own.v[i], name)) other = 1;
        sv_free(&own);
        if (other) continue;
        char *target = rp(t, path);
        if (!strcmp(jgets(f, "type", ""), "dir")) {
            rmdir(target);
        } else if (path_exists(target)) {
            char h[65];
            if (in_list(backup, path) && (hash_regular(target, h) || strcmp(h, jgets(f, "sha256", "")))) {
                char *save = xasprintf("%s.sypsave", target);
                rename(target, save);
                logm(t, "   /%s geändert -> gesichert als .sypsave", path);
                free(save);
            } else {
                unlink(target);
                sv_push(&t->changed, path);
            }
        }
        free(target);
    }
    json_free(files);
}

static int do_remove(txn *t, hmap *inst, const char *name) {
    pkg *p = hm_get(inst, name);
    if (!p) return 0;
    if (run_script(t, p->info, "pre_remove", NULL, p->version)) return -1;
    logm(t, "-> Entferne %s %s", name, p->version);
    remove_files(t, name, NULL, p->info);
    db_delete(t->db, name);
    run_script(t, p->info, "post_remove", NULL, p->version);
    return 0;
}

static int do_install(txn *t, hmap *inst, sypfile *f, const char *reason) {
    const char *name = jgets(f->info, "name", ""), *ver = jgets(f->info, "version", "");
    pkg *old = hm_get(inst, name);
    const char *hook = old ? "upgrade" : "install";
    if (old) logm(t, "-> Aktualisiere %s %s -> %s", name, old->version, ver);
    else logm(t, "-> Installiere %s %s", name, ver);
    char *rel = xasprintf("var/cache/syp/staging/%s", name), *staging = rp(t, rel);
    free(rel);
    rm_rf(staging);
    if (syp_extract(f, staging)) {
        free(staging);
        return -1;
    }
    char *pre = xasprintf("pre_%s", hook), *post = xasprintf("post_%s", hook);
    int rc = run_script(t, f->info, pre, ver, old ? old->version : NULL);
    hmap oldhash = {0};
    hm_init(&oldhash);
    jval *oldfiles = old ? db_files_of(t->db, name) : NULL;
    for (size_t i = 0; oldfiles && i < oldfiles->n; i++) {
        const char *h = jgets(oldfiles->items[i], "sha256", NULL);
        hm_set(&oldhash, jgets(oldfiles->items[i], "path", ""), (void *)(h ? h : ""));
    }
    jval *backup = jgeta(f->info, "backup");
    hmap keep = {0};
    hm_init(&keep);
    for (size_t i = 0; !rc && i < f->files->n; i++) {
        jval *e = f->files->items[i];
        const char *path = jgets(e, "path", "");
        hm_set(&keep, path, (void *)1);
        char *src = path_join(staging, path), *dst = rp(t, path);
        struct stat st;
        if (in_list(backup, path) && lstat(dst, &st) == 0 && S_ISREG(st.st_mode)) {
            char cur[65];
            sha256_file(dst, cur);
            const char *orig = hm_get(&oldhash, path), *nw = jgets(e, "sha256", "");
            if (!strcmp(cur, nw)) {
                free(src);
                free(dst);
                continue;
            }
            if (orig && *orig && strcmp(cur, orig)) {
                char *n = xasprintf("%s.sypnew", dst);
                rc = place(t, src, n, e);
                logm(t, "   /%s lokal geändert -> neue Version als .sypnew", path);
                free(n);
                free(src);
                free(dst);
                continue;
            }
            if (!orig) {
                char *sv = xasprintf("%s.sypsave", dst);
                copy_file(dst, sv);
                free(sv);
            }
        }
        rc = place(t, src, dst, e);
        free(src);
        free(dst);
    }
    if (!rc && old) remove_files(t, name, &keep, old->info);
    if (!rc) {
        jval *dbfiles = jarr();
        for (size_t i = 0; i < f->files->n; i++) {
            jval *e = f->files->items[i], *x = jobj();
            jset(x, "path", jstr(jgets(e, "path", "")));
            jset(x, "type", jstr(jgets(e, "type", "file")));
            const char *h = jgets(e, "sha256", NULL);
            jset(x, "sha256", h ? jstr(h) : jnull());
            jpush(dbfiles, x);
        }
        rc = db_put(t->db, f->info, dbfiles, old && old->reason ? old->reason : reason);
        json_free(dbfiles);
    }
    rm_rf(staging);
    if (!rc) run_script(t, f->info, post, ver, old ? old->version : NULL);
    json_free(oldfiles);
    hm_free(&oldhash);
    hm_free(&keep);
    free(pre);
    free(post);
    free(staging);
    return rc;
}

static int check_conflicts(txn *t, sypfile *files, size_t n, strvec *removing) {
    hmap claimed = {0};
    hm_init(&claimed);
    strvec problems = {0};
    for (size_t k = 0; k < n; k++) {
        const char *pname = jgets(files[k].info, "name", "");
        for (size_t i = 0; i < files[k].files->n; i++) {
            jval *e = files[k].files->items[i];
            if (!strcmp(jgets(e, "type", ""), "dir")) continue;
            const char *path = jgets(e, "path", "");
            const char *prev = hm_get(&claimed, path);
            if (prev) {
                sv_push_own(&problems, xasprintf("/%s: in %s und %s", path, prev, pname));
                continue;
            }
            hm_set(&claimed, path, (void *)pname);
            strvec own = {0};
            db_owners(t->db, path, &own);
            sbuf others = {0};
            sb_init(&others);
            for (size_t j = 0; j < own.n; j++)
                if (strcmp(own.v[j], pname) && !sv_contains(removing, own.v[j])) {
                    if (others.len) sb_add(&others, ", ");
                    sb_add(&others, own.v[j]);
                }
            if (others.len) {
                sv_push_own(&problems, xasprintf("/%s existiert bereits in %s", path, others.s));
            } else if (!own.n) {
                char *target = rp(t, path), *abs = xasprintf("/%s", path);
                int ow = 0;
                for (size_t j = 0; j < t->overwrite.n; j++)
                    if (glob_match(t->overwrite.v[j], abs)) ow = 1;
                if (path_exists(target) && !is_dir(target) && !ow)
                    sv_push_own(&problems, xasprintf("/%s existiert im Dateisystem (keinem Paket zugeordnet)", path));
                free(target);
                free(abs);
            }
            free(others.s);
            sv_free(&own);
        }
    }
    hm_free(&claimed);
    if (problems.n) {
        sbuf b = {0};
        sb_init(&b);
        sb_add(&b, "Dateikonflikte:");
        for (size_t i = 0; i < problems.n && i < 25; i++) sb_addf(&b, "\n  %s", problems.v[i]);
        set_error("%s", b.s);
        free(b.s);
        sv_free(&problems);
        return -1;
    }
    return 0;
}

/* Topologische Reihenfolge: Abhängigkeiten zuerst */
static void order_visit(sypfile *f, size_t n, size_t i, int *state, size_t *out, size_t *no, hmap *prov) {
    if (state[i]) return;
    state[i] = 1;
    jval *deps = jgeta(f[i].info, "depends");
    for (size_t k = 0; deps && k < deps->n; k++) {
        dep d;
        if (dep_parse(deps->items[k]->s, &d)) continue;
        for (size_t a = 0; a < d.n; a++) {
            void *j = hm_get(prov, d.alt[a].name);
            if (j && (size_t)j - 1 != i) {
                order_visit(f, n, (size_t)j - 1, state, out, no, prov);
                break;
            }
        }
        dep_free(&d);
    }
    state[i] = 2;
    out[(*no)++] = i;
}

static int lock_db(txn *t) {
    char *p = rp(t, "var/lib/syp/db.lck");
    char *d = path_dirname(p);
    mkdir_p(d, 0755);
    free(d);
    int fd = open(p, O_WRONLY | O_CREAT | O_CLOEXEC, 0644);
    free(p);
    if (fd < 0 || flock(fd, LOCK_EX | LOCK_NB)) {
        set_error("Eine andere syp-Instanz läuft bereits");
        if (fd >= 0) close(fd);
        return -1;
    }
    return fd;
}

long long txn_execute(txn *t, plan *p, const char *cmdline) {
    if (plan_empty(p)) {
        logm(t, " Nichts zu tun.");
        return 0;
    }
    if (t->db->readonly) {
        set_error("Keine Schreibrechte für die Paketdatenbank (Root-Rechte nötig)");
        return -1;
    }
    int lock = lock_db(t);
    if (lock < 0) return -1;
    long long result = -1;
    size_t n = p->install.n;
    sypfile *files = xcalloc(n + 1, sizeof(sypfile));
    size_t opened = 0;
    char *cache = rp(t, "var/cache/syp/pkg");
    for (size_t i = 0; i < n; i++) {
        pkg *rpk = p->install.v[i];
        char *path = repo_fetch_package(t->cfg, rpk);
        if (!path) goto out;
        if (rpk->local_path) {
            mkdir_p(cache, 0755);
            char *dst = path_join(cache, path_basename(path));
            if (strcmp(dst, path)) copy_file(path, dst); /* für Rollbacks */
            free(dst);
        }
        if (syp_open(path, &files[i])) {
            free(path);
            goto out;
        }
        opened++;
        free(path);
        if (strcmp(jgets(files[i].info, "name", ""), rpk->name) ||
            strcmp(jgets(files[i].info, "version", ""), rpk->version)) {
            set_error("%s: Metadaten passen nicht zum Index", rpk->name);
            goto out;
        }
        if (t->cfg->require_signatures && !t->allow_unsigned && !syp_verify(&files[i], t->kr)) goto out;
    }
    hmap prov = {0};
    hm_init(&prov);
    for (size_t i = 0; i < n; i++) {
        hm_set(&prov, jgets(files[i].info, "name", ""), (void *)(i + 1));
        jval *pr = jgeta(files[i].info, "provides");
        for (size_t k = 0; pr && k < pr->n; k++) {
            char *pn, *pvr;
            provide_parts(pr->items[k]->s, &pn, &pvr);
            if (!hm_get(&prov, pn)) hm_set(&prov, pn, (void *)(i + 1));
            free(pn);
            free(pvr);
        }
    }
    size_t *order = xcalloc(n + 1, sizeof(size_t)), no = 0;
    int *state = xcalloc(n + 1, sizeof(int));
    /* alphabetisch starten (deterministisch) – install ist bereits nach Namen sortiert */
    for (size_t i = 0; i < n; i++) order_visit(files, n, i, state, order, &no, &prov);
    free(state);
    hm_free(&prov);
    if (check_conflicts(t, files, n, &p->remove)) {
        free(order);
        goto out;
    }
    char *pre = snapshot_create(t->cfg, "pre", cmdline, NULL);
    long long tx = db_tx_begin(t->db, cmdline, pre);
    hmap inst;
    db_installed(t->db, &inst);
    int rc = 0;
    for (size_t i = 0; !rc && i < p->remove.n; i++) rc = do_remove(t, &inst, p->remove.v[i]);
    for (size_t k = 0; !rc && k < no; k++) {
        sypfile *f = &files[order[k]];
        const char *reason = hm_get(&p->reasons, jgets(f->info, "name", ""));
        rc = do_install(t, &inst, f, reason ? reason : "explicit");
    }
    if (!rc) run_triggers(t->cfg, &t->changed, t->quiet);
    if (rc) {
        db_tx_end(t->db, tx, "failed", NULL);
        char *m = xasprintf("Transaktion %lld fehlgeschlagen: %s. Zurücksetzen mit `syp rollback %lld`%s%s%s", tx,
                            last_error(), tx, pre ? " (Snapshot " : "", pre ? pre : "", pre ? ")" : "");
        set_error("%s", m);
        free(m);
    } else {
        char *post = snapshot_create(t->cfg, "post", cmdline, pre);
        db_tx_end(t->db, tx, "done", post);
        free(post);
        result = tx;
    }
    HM_FOREACH(&inst, i) pkg_free(inst.vals[i]);
    hm_free(&inst);
    free(pre);
    free(order);
out:
    for (size_t i = 0; i < opened; i++) syp_close(&files[i]);
    free(files);
    free(cache);
    close(lock);
    return result;
}

/* --- Rollback ------------------------------------------------------------------------ */
static pkg *from_cache(config *c, const char *name, const char *ver) {
    char *cache = cfg_path(c, "var/cache/syp/pkg");
    struct dirent **list;
    int n = scandir(cache, &list, NULL, alphasort);
    pkg *found = NULL;
    size_t nl = strlen(name);
    for (int i = 0; i < n; i++) {
        const char *d = list[i]->d_name;
        if (!found && !strncmp(d, name, nl) && d[nl] == '-' && str_ends(d, ".syp")) {
            char *p = path_join(cache, d);
            pkg *lp = local_package(p);
            if (lp && !strcmp(lp->name, name) && !strcmp(lp->version, ver)) found = lp;
            else pkg_free(lp);
            free(p);
        }
        free(list[i]);
    }
    if (n >= 0) free(list);
    free(cache);
    return found;
}

int rollback_plan(txn *t, long long tx_id, ptrvec *available, ptrvec *owned, plan *out) {
    jval *tx = db_tx(t->db, tx_id);
    if (!tx) {
        set_error("Transaktion %lld unbekannt", tx_id);
        return -1;
    }
    jval *target = jget(tx, "before");
    hmap cur;
    db_installed(t->db, &cur);
    HM_FOREACH(&cur, i) if (!jget(target, cur.keys[i])) sv_push(&out->remove, cur.keys[i]);
    sv_sort(&out->remove);
    sbuf missing = {0};
    sb_init(&missing);
    for (size_t i = 0; i < target->n; i++) {
        const char *name = target->keys[i], *ver = target->items[i]->s;
        pkg *c = hm_get(&cur, name);
        if (c && !strcmp(c->version, ver)) continue;
        pkg *f = from_cache(t->cfg, name, ver);
        if (f) pv_push(owned, f);
        for (size_t k = 0; !f && available && k < available->n; k++) {
            pkg *a = available->v[k];
            if (!strcmp(a->name, name) && !strcmp(a->version, ver)) f = a;
        }
        if (!f) {
            sb_addf(&missing, "%s%s %s", missing.len ? ", " : "", name, ver);
            continue;
        }
        pv_push(&out->install, f);
        hm_set(&out->reasons, name, c && c->reason && !strcmp(c->reason, "explicit") ? "explicit" : "dep");
    }
    HM_FOREACH(&cur, i) pkg_free(cur.vals[i]);
    hm_free(&cur);
    json_free(tx);
    if (missing.len) {
        set_error("Für den Rollback fehlen Pakete (weder im Cache noch im Repo): %s. Alternative: `syp rollback "
                  "--system`.",
                  missing.s);
        free(missing.s);
        return -1;
    }
    free(missing.s);
    return 0;
}

typedef struct {
    pkg *p;
} cent;
static int cmp_cent(const void *a, const void *b) {
    const pkg *x = ((const cent *)a)->p, *y = ((const cent *)b)->p;
    int c = strcmp(x->name, y->name);
    return c ? c : vercmp(y->version, x->version);
}

int clean_cache(config *c, database *d, int keep) {
    char *cache = cfg_path(c, "var/cache/syp/pkg");
    struct dirent **list;
    int n = scandir(cache, &list, NULL, alphasort);
    cent *e = xcalloc((size_t)(n > 0 ? n : 1), sizeof *e);
    size_t ne = 0;
    for (int i = 0; i < n; i++) {
        if (str_ends(list[i]->d_name, ".syp")) {
            char *p = path_join(cache, list[i]->d_name);
            pkg *lp = local_package(p);
            if (lp) e[ne++].p = lp;
            else unlink(p);
            free(p);
        }
        free(list[i]);
    }
    if (n >= 0) free(list);
    qsort(e, ne, sizeof *e, cmp_cent);
    jval *state = db_state(d);
    int removed = 0, seen = 0;
    for (size_t i = 0; i < ne; i++) {
        seen = (i && !strcmp(e[i].p->name, e[i - 1].p->name)) ? seen + 1 : 0;
        const char *iv = jgets(state, e[i].p->name, NULL);
        if (seen >= keep && !(iv && !strcmp(iv, e[i].p->version))) {
            unlink(e[i].p->local_path);
            removed++;
        }
        pkg_free(e[i].p);
    }
    json_free(state);
    free(e);
    free(cache);
    return removed;
}
