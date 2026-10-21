/* syp – der symfonOS-Paketmanager */
#define _GNU_SOURCE
#include <errno.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#include "build.h"
#include "core.h"
#include "solver.h"
#include "txn.h"

int cmd_buildall(int argc, char **argv, const char *root);

typedef struct {
    const char *root;
    int yes, dry, json, quiet;
    config cfg;
    database db;
    int db_open;
    keyring kr;
    char *cmdline;
    /* Transaktionsoptionen */
    strvec overwrite;
    int allow_unsigned, trust_scripts, no_scripts;
} ctx;

static database *DB(ctx *c) {
    if (!c->db_open) {
        char *p = cfg_path(&c->cfg, "var/lib/syp/syp.db");
        if (db_open(p, &c->db)) die("%s", last_error());
        free(p);
        c->db_open = 1;
    }
    return &c->db;
}

static void need_root(ctx *c) {
    if (cfg_is_root(&c->cfg) && geteuid() != 0) die("Diese Aktion benötigt Root-Rechte (sudo / pkexec)");
}

static int confirm(ctx *c, const char *q) {
    if (c->yes) return 1;
    printf("%s [J/n] ", q);
    fflush(stdout);
    char buf[64];
    if (!fgets(buf, sizeof buf, stdin)) return 0;
    char *s = str_trim(buf);
    str_lower(s);
    return !*s || !strcmp(s, "j") || !strcmp(s, "ja") || !strcmp(s, "y") || !strcmp(s, "yes");
}

static void free_pkgs(ptrvec *v) {
    for (size_t i = 0; i < v->n; i++) pkg_free(v->v[i]);
    free(v->v);
    v->v = NULL;
    v->n = 0;
}
static void free_installed(hmap *m) {
    HM_FOREACH(m, i) pkg_free(m->vals[i]);
    hm_free(m);
}

static jval *plan_json(plan *p, hmap *inst) {
    jval *o = jobj(), *ins = jarr();
    for (size_t i = 0; i < p->install.n; i++) {
        pkg *k = p->install.v[i], *old = hm_get(inst, k->name);
        jval *e = jobj();
        jset(e, "name", jstr(k->name));
        jset(e, "version", jstr(k->version));
        jset(e, "repo", jstr(k->repo ? k->repo : ""));
        jset(e, "old", old ? jstr(old->version) : jnull());
        jset(e, "size", jint(k->size));
        jpush(ins, e);
    }
    jset(o, "install", ins);
    jset(o, "remove", jarr_from_sv(&p->remove));
    return o;
}

static void show_plan(plan *p, hmap *inst) {
    if (p->remove.n) {
        printf("%sZu entfernen (%zu):%s", col("1"), p->remove.n, col_end());
        for (size_t i = 0; i < p->remove.n; i++) printf(" %s", p->remove.v[i]);
        printf("\n");
    }
    if (p->install.n) {
        printf("%sZu installieren/aktualisieren (%zu):%s\n", col("1"), p->install.n, col_end());
        long long total = 0;
        for (size_t i = 0; i < p->install.n; i++) {
            pkg *k = p->install.v[i], *old = hm_get(inst, k->name);
            if (old) printf("  %s/%s  %s -> %s\n", k->repo, k->name, old->version, k->version);
            else printf("  %s/%s  %s\n", k->repo, k->name, k->version);
            if (!k->local_path) total += k->size;
        }
        if (total) printf("Download: %.1f MB\n", (double)total / 1e6);
    }
}

static int run_plan(ctx *c, plan *p, hmap *inst) {
    if (c->json && c->dry) {
        jval *j = plan_json(p, inst);
        char *s = json_dump(j, 0, 0);
        puts(s);
        free(s);
        json_free(j);
        return 0;
    }
    if (plan_empty(p)) {
        printf(" Nichts zu tun.\n");
        return 0;
    }
    show_plan(p, inst);
    if (c->dry || !confirm(c, "Fortfahren?")) return 0;
    need_root(c);
    txn t;
    txn_init(&t, &c->cfg, DB(c), &c->kr);
    for (size_t i = 0; i < c->overwrite.n; i++) sv_push(&t.overwrite, c->overwrite.v[i]);
    t.allow_unsigned = c->allow_unsigned;
    t.trust_scripts = c->trust_scripts;
    t.no_scripts = t.no_scripts || c->no_scripts;
    long long tx = txn_execute(&t, p, c->cmdline);
    sv_free(&t.changed);
    sv_free(&t.overwrite);
    if (tx < 0) die("%s", last_error());
    printf("%s:: Transaktion %lld abgeschlossen%s\n", col("32;1"), tx, col_end());
    return 0;
}

static int solver_fail(solver *s) {
    for (size_t i = 0; i < s->problems.n; i++)
        fprintf(stderr, "%sFehler:%s %s\n", col("31;1"), col_end(), s->problems.v[i]);
    return 1;
}

/* --- Befehle ----------------------------------------------------------------------- */
static int c_sync(ctx *c) {
    need_root(c);
    if (repo_sync(&c->cfg, &c->kr, c->quiet)) die("%s", last_error());
    ptrvec av = {0};
    repo_load_synced(&c->cfg, &av);
    printf(":: %zu Pakete in %zu Repositories\n", av.n, c->cfg.nrepos);
    free_pkgs(&av);
    return 0;
}

static int c_install(ctx *c, int argc, char **argv, int as_dep) {
    ptrvec av = {0}, local = {0};
    repo_load_synced(&c->cfg, &av);
    strvec reqs = {0};
    for (int i = 0; i < argc; i++) {
        if (str_ends(argv[i], ".syp") && path_exists(argv[i])) {
            pkg *p = local_package(argv[i]);
            if (!p) die("%s", last_error());
            pv_push(&local, p);
        } else {
            sv_push(&reqs, argv[i]);
        }
    }
    hmap inst;
    db_installed(DB(c), &inst);
    solver s;
    solver_init(&s, &av, &inst);
    plan p;
    plan_init(&p);
    int rc = solve_install(&s, reqs.v, reqs.n, &local, &p) ? solver_fail(&s) : 0;
    if (!rc && as_dep) HM_FOREACH(&p.reasons, i) p.reasons.vals[i] = "dep";
    if (!rc) rc = run_plan(c, &p, &inst);
    plan_free(&p);
    solver_free(&s);
    free_installed(&inst);
    free_pkgs(&av);
    free_pkgs(&local);
    sv_free(&reqs);
    return rc;
}

static int c_remove(ctx *c, int argc, char **argv, int cascade) {
    hmap inst;
    db_installed(DB(c), &inst);
    solver s;
    solver_init(&s, NULL, &inst);
    plan p;
    plan_init(&p);
    int rc = solve_remove(&s, argv, (size_t)argc, cascade, &p) ? solver_fail(&s) : run_plan(c, &p, &inst);
    plan_free(&p);
    solver_free(&s);
    free_installed(&inst);
    return rc;
}

static int c_upgrade(ctx *c, int nosync) {
    if (!nosync) c_sync(c);
    ptrvec av = {0};
    repo_load_synced(&c->cfg, &av);
    hmap inst;
    db_installed(DB(c), &inst);
    solver s;
    solver_init(&s, &av, &inst);
    plan p;
    plan_init(&p);
    int rc = solve_upgrade(&s, &p) ? solver_fail(&s) : run_plan(c, &p, &inst);
    plan_free(&p);
    solver_free(&s);
    free_installed(&inst);
    free_pkgs(&av);
    return rc;
}

static int c_autoremove(ctx *c) {
    hmap inst;
    db_installed(DB(c), &inst);
    solver s;
    solver_init(&s, NULL, &inst);
    plan p;
    plan_init(&p);
    solve_orphans(&s, &p.remove);
    int rc = run_plan(c, &p, &inst);
    plan_free(&p);
    solver_free(&s);
    free_installed(&inst);
    return rc;
}

static jval *pkg_summary_json(const pkg *p, const pkg *inst) {
    jval *o = jobj();
    jset(o, "name", jstr(p->name));
    jset(o, "version", jstr(p->version));
    jset(o, "summary", jstr(jgets(p->info, "summary", "")));
    jset(o, "description", jstr(jgets(p->info, "description", "")));
    jset(o, "license", jstr(jgets(p->info, "license", "")));
    jset(o, "url", jstr(jgets(p->info, "url", "")));
    jval *cat = jgeta(p->info, "categories");
    jset(o, "categories", cat ? jcopy(cat) : jarr());
    jset(o, "origin", jstr(jgets(jget(p->info, "origin"), "format", "native")));
    jset(o, "repo", jstr(p->repo ? p->repo : ""));
    jset(o, "size", jint(p->size));
    jset(o, "installed", inst ? jstr(inst->version) : jnull());
    return o;
}

static int c_search(ctx *c, int argc, char **argv) {
    ptrvec av = {0};
    repo_load_synced(&c->cfg, &av);
    hmap inst, newest = {0};
    db_installed(DB(c), &inst);
    hm_init(&newest);
    for (size_t i = 0; i < av.n; i++) {
        pkg *p = av.v[i], *cur = hm_get(&newest, p->name);
        if (!cur || vercmp(p->version, cur->version) > 0) hm_set(&newest, p->name, p);
    }
    strvec names = {0};
    HM_FOREACH(&newest, i) {
        pkg *p = newest.vals[i];
        jval *cat = jgeta(p->info, "categories");
        sbuf hay = {0};
        sb_init(&hay);
        sb_addf(&hay, "%s %s", p->name, jgets(p->info, "summary", ""));
        for (size_t k = 0; cat && k < cat->n; k++) sb_addf(&hay, " %s", cat->items[k]->s);
        str_lower(hay.s);
        int ok = 1;
        for (int k = 0; k < argc && ok; k++) {
            char *t = xstrdup(argv[k]);
            str_lower(t);
            if (!strstr(hay.s, t)) ok = 0;
            free(t);
        }
        free(hay.s);
        if (ok) sv_push(&names, p->name);
    }
    sv_sort(&names);
    jval *arr = jarr();
    for (size_t i = 0; i < names.n; i++) {
        pkg *p = hm_get(&newest, names.v[i]), *ip = hm_get(&inst, p->name);
        if (c->json) jpush(arr, pkg_summary_json(p, ip));
        else
            printf("%s%s%s/%s%s%s %s%s%s%s%s%s\n    %s\n", col("35;1"), p->repo, col_end(), col("1"), p->name, col_end(),
                   col("32"), p->version, col_end(), ip ? col("36") : "", ip ? " [installiert]" : "",
                   ip ? col_end() : "", jgets(p->info, "summary", ""));
    }
    if (c->json) {
        char *s = json_dump(arr, 0, 0);
        puts(s);
        free(s);
    }
    json_free(arr);
    sv_free(&names);
    hm_free(&newest);
    free_installed(&inst);
    free_pkgs(&av);
    return 0;
}

static void print_list(const char *label, jval *a) {
    sbuf b = {0};
    sb_init(&b);
    for (size_t i = 0; a && i < a->n; i++) sb_addf(&b, "%s%s", i ? ", " : "", a->items[i]->s);
    printf("%s%-14s%s: %s\n", col("1"), label, col_end(), b.len ? b.s : "–");
    free(b.s);
}

static int c_info(ctx *c, int argc, char **argv) {
    ptrvec av = {0};
    repo_load_synced(&c->cfg, &av);
    hmap inst;
    db_installed(DB(c), &inst);
    int rc = 0;
    for (int i = 0; i < argc; i++) {
        pkg *p = NULL, *own = NULL;
        const char *where = NULL;
        if (str_ends(argv[i], ".syp") && path_exists(argv[i])) {
            own = p = local_package(argv[i]);
            if (!p) die("%s", last_error());
            where = "Datei";
        } else if ((p = hm_get(&inst, argv[i]))) {
            where = "installiert";
        } else {
            for (size_t k = 0; k < av.n; k++) {
                pkg *a = av.v[k];
                if (!strcmp(a->name, argv[i]) && (!p || vercmp(a->version, p->version) > 0)) p = a;
            }
            where = p ? p->repo : NULL;
        }
        if (!p) {
            fprintf(stderr, "%sFehler:%s Paket nicht gefunden: %s\n", col("31;1"), col_end(), argv[i]);
            rc = 1;
            continue;
        }
        if (c->json) {
            char *s = json_dump(p->info, 0, 2);
            puts(s);
            free(s);
        } else {
            jval *I = p->info;
            printf("%s%-14s%s: %s\n", col("1"), "Name", col_end(), p->name);
            printf("%s%-14s%s: %s\n", col("1"), "Version", col_end(), p->version);
            printf("%s%-14s%s: %s\n", col("1"), "Quelle", col_end(), where);
            printf("%s%-14s%s: %s\n", col("1"), "Beschreibung", col_end(), jgets(I, "summary", ""));
            printf("%s%-14s%s: %s\n", col("1"), "Lizenz", col_end(), jgets(I, "license", ""));
            printf("%s%-14s%s: %s\n", col("1"), "URL", col_end(), jgets(I, "url", ""));
            printf("%s%-14s%s: %s\n", col("1"), "Architektur", col_end(), jgets(I, "arch", ""));
            print_list("Hängt ab von", jgeta(I, "depends"));
            print_list("Optional", jgeta(I, "optdepends"));
            print_list("Stellt bereit", jgeta(I, "provides"));
            print_list("Konflikte", jgeta(I, "conflicts"));
            printf("%s%-14s%s: %.1f MB\n", col("1"), "Größe", col_end(), (double)jgeti(I, "installed_size", 0) / 1e6);
            char *o = json_dump(jget(I, "origin"), 0, 0);
            printf("%s%-14s%s: %s\n", col("1"), "Herkunft", col_end(), o);
            free(o);
            time_t bd = (time_t)jgeti(I, "build_date", 0);
            char tb[32];
            strftime(tb, sizeof tb, "%Y-%m-%d %H:%M", localtime(&bd));
            printf("%s%-14s%s: %s\n", col("1"), "Gebaut", col_end(), tb);
            if (p->installed)
                printf("%s%-14s%s: %s\n", col("1"), "Grund", col_end(),
                       p->reason && !strcmp(p->reason, "explicit") ? "explizit" : "Abhängigkeit");
            printf("\n");
        }
        pkg_free(own);
    }
    free_installed(&inst);
    free_pkgs(&av);
    return rc;
}

static int c_list(ctx *c, int explicit_only) {
    hmap inst;
    db_installed(DB(c), &inst);
    strvec names = {0};
    HM_FOREACH(&inst, i) sv_push(&names, inst.keys[i]);
    sv_sort(&names);
    jval *arr = jarr();
    for (size_t i = 0; i < names.n; i++) {
        pkg *p = hm_get(&inst, names.v[i]);
        if (explicit_only && (!p->reason || strcmp(p->reason, "explicit"))) continue;
        if (c->json) {
            jval *e = jcopy(p->info);
            jset(e, "reason", jstr(p->reason ? p->reason : "explicit"));
            jpush(arr, e);
        } else {
            printf("%s %s\n", p->name, p->version);
        }
    }
    if (c->json) {
        char *s = json_dump(arr, 0, 0);
        puts(s);
        free(s);
    }
    json_free(arr);
    sv_free(&names);
    free_installed(&inst);
    return 0;
}

static int c_files(ctx *c, const char *name) {
    jval *f = db_files_of(DB(c), name);
    for (size_t i = 0; i < f->n; i++) printf("/%s\n", jgets(f->items[i], "path", ""));
    json_free(f);
    return 0;
}

static int c_owns(ctx *c, int argc, char **argv) {
    int rc = 0;
    for (int i = 0; i < argc; i++) {
        char *abs = argv[i][0] == '/' ? xstrdup(argv[i]) : NULL;
        if (!abs) {
            char cwd[4096];
            abs = xasprintf("%s/%s", getcwd(cwd, sizeof cwd) ? cwd : ".", argv[i]);
        }
        const char *rel = abs;
        size_t rl = strlen(c->cfg.root);
        if (!cfg_is_root(&c->cfg) && !strncmp(rel, c->cfg.root, rl) && rel[rl] == '/') rel += rl;
        while (*rel == '/') rel++;
        strvec own = {0};
        db_owners(DB(c), rel, &own);
        if (own.n) {
            printf("/%s gehört zu", rel);
            for (size_t k = 0; k < own.n; k++) printf("%s %s", k ? "," : "", own.v[k]);
            printf("\n");
        } else {
            printf("/%s gehört zu keinem Paket\n", rel);
            rc = 1;
        }
        sv_free(&own);
        free(abs);
    }
    return rc;
}

static int c_verify(ctx *c, int argc, char **argv) {
    hmap inst;
    db_installed(DB(c), &inst);
    strvec names = {0};
    if (argc)
        for (int i = 0; i < argc; i++) sv_push(&names, argv[i]);
    else
        HM_FOREACH(&inst, i) sv_push(&names, inst.keys[i]);
    sv_sort(&names);
    int bad = 0;
    for (size_t i = 0; i < names.n; i++) {
        pkg *p = hm_get(&inst, names.v[i]);
        if (!p) continue;
        jval *backup = jgeta(p->info, "backup");
        jval *f = db_files_of(DB(c), p->name);
        for (size_t k = 0; k < f->n; k++) {
            const char *path = jgets(f->items[k], "path", "");
            char *full = cfg_path(&c->cfg, path);
            if (!path_exists(full)) {
                printf("%s: fehlt /%s\n", p->name, path);
                bad++;
            } else if (!strcmp(jgets(f->items[k], "type", ""), "file")) {
                int isb = 0;
                for (size_t b = 0; backup && b < backup->n; b++) {
                    const char *x = backup->items[b]->s;
                    while (*x == '/') x++;
                    if (!strcmp(x, path)) isb = 1;
                }
                char h[65];
                if (!isb && (sha256_file(full, h) || strcmp(h, jgets(f->items[k], "sha256", "")))) {
                    printf("%s: verändert /%s\n", p->name, path);
                    bad++;
                }
            }
            free(full);
        }
        json_free(f);
    }
    if (bad) printf(":: %d Abweichungen\n", bad);
    else printf(":: Alle Dateien intakt\n");
    sv_free(&names);
    free_installed(&inst);
    return bad ? 1 : 0;
}

static int c_history(ctx *c, int limit) {
    jval *h = db_history(DB(c), limit);
    if (c->json) {
        char *s = json_dump(h, 0, 0);
        puts(s);
        free(s);
        json_free(h);
        return 0;
    }
    for (size_t i = h->n; i-- > 0;) {
        jval *t = h->items[i], *b = jget(t, "before"), *a = jget(t, "after");
        time_t ts = (time_t)jgeti(t, "ts", 0);
        char tb[32];
        strftime(tb, sizeof tb, "%Y-%m-%d %H:%M", localtime(&ts));
        const char *snap = jgets(t, "snapshot_pre", NULL);
        printf("%s%4lld%s  %s  %-7s %s%s%s\n", col("1"), jgeti(t, "id", 0), col_end(), tb, jgets(t, "status", ""),
               jgets(t, "cmdline", ""), snap ? " snapshot=" : "", snap ? snap : "");
        if (!a || a->t != J_OBJ) continue;
        strvec add = {0}, rem = {0}, chg = {0};
        for (size_t k = 0; k < a->n; k++) {
            const char *bv = jgets(b, a->keys[k], NULL);
            if (!bv) sv_push(&add, a->keys[k]);
            else if (strcmp(bv, a->items[k]->s)) sv_push(&chg, a->keys[k]);
        }
        for (size_t k = 0; k < b->n; k++)
            if (!jget(a, b->keys[k])) sv_push(&rem, b->keys[k]);
        strvec *lists[] = {&add, &rem, &chg};
        const char *labels[] = {"+", "-", "~"};
        for (int l = 0; l < 3; l++) {
            if (!lists[l]->n) continue;
            sv_sort(lists[l]);
            printf("        %s", labels[l]);
            for (size_t k = 0; k < lists[l]->n && k < 12; k++) printf(" %s", lists[l]->v[k]);
            printf("%s\n", lists[l]->n > 12 ? " …" : "");
            sv_free(lists[l]);
        }
    }
    json_free(h);
    return 0;
}

static int c_rollback(ctx *c, long long tx_id, int system) {
    if (system) {
        if (!snapshot_available(&c->cfg)) die("Keine Btrfs/snapper-Snapshots verfügbar");
        jval *h = db_history(DB(c), 1000);
        const char *snap = NULL;
        for (size_t i = 0; i < h->n && !snap; i++) {
            jval *t = h->items[i];
            if ((!tx_id || jgeti(t, "id", 0) == tx_id) && jgets(t, "snapshot_pre", NULL)) snap = jgets(t, "snapshot_pre", NULL);
        }
        if (!snap) die("Kein Snapshot zu dieser Transaktion");
        printf("System wird auf Snapshot %s zurückgesetzt (Neustart nötig).\n", snap);
        if (confirm(c, "Fortfahren?")) {
            need_root(c);
            execlp("snapper", "snapper", "--ambit", "classic", "rollback", snap, (char *)NULL);
            die("snapper: %s", strerror(errno));
        }
        json_free(h);
        return 0;
    }
    if (!tx_id) {
        jval *h = db_history(DB(c), 1000);
        for (size_t i = 0; i < h->n && !tx_id; i++)
            if (!strcmp(jgets(h->items[i], "status", ""), "done")) tx_id = jgeti(h->items[i], "id", 0);
        json_free(h);
        if (!tx_id) die("Keine Transaktion zum Zurücksetzen");
    }
    printf(":: Stelle Zustand vor Transaktion %lld wieder her\n", tx_id);
    ptrvec av = {0}, owned = {0};
    repo_load_synced(&c->cfg, &av);
    hmap inst;
    db_installed(DB(c), &inst);
    txn t;
    txn_init(&t, &c->cfg, DB(c), &c->kr);
    plan p;
    plan_init(&p);
    if (rollback_plan(&t, tx_id, &av, &owned, &p)) die("%s", last_error());
    int rc = run_plan(c, &p, &inst);
    plan_free(&p);
    free_installed(&inst);
    free_pkgs(&av);
    free_pkgs(&owned);
    return rc;
}

static int c_doctor(ctx *c) {
    int problems = 0;
    jval *h = db_history(DB(c), 5);
    for (size_t i = 0; i < h->n; i++) {
        const char *st = jgets(h->items[i], "status", "");
        if (!strcmp(st, "running") || !strcmp(st, "failed")) {
            printf("! Transaktion %lld ist %s – `syp rollback %lld` empfohlen\n", jgeti(h->items[i], "id", 0), st,
                   jgeti(h->items[i], "id", 0));
            problems++;
        }
    }
    json_free(h);
    hmap inst;
    db_installed(DB(c), &inst);
    solver s;
    solver_init(&s, NULL, &inst);
    strvec br = {0};
    solver_broken_installed(&s, &br);
    for (size_t i = 0; i < br.n; i++) printf("! %s\n", br.v[i]);
    problems += (int)br.n;
    sv_free(&br);
    solver_free(&s);
    free_installed(&inst);
    if (!c->kr.n) {
        char *kd = cfg_path(&c->cfg, "etc/syp/keys");
        printf("! Keine vertrauenswürdigen Schlüssel in %s\n", kd);
        free(kd);
        problems++;
    }
    for (size_t i = 0; i < c->cfg.nrepos; i++) {
        char *rel = xasprintf("var/lib/syp/sync/%s.index.json.zst", c->cfg.repos[i].name), *f = cfg_path(&c->cfg, rel);
        struct stat st;
        if (stat(f, &st)) {
            printf("! Repo %s nie synchronisiert – `syp sync`\n", c->cfg.repos[i].name);
            problems++;
        } else if (time(NULL) - st.st_mtime > 14 * 86400) {
            printf("! Repo %s seit über 14 Tagen nicht synchronisiert\n", c->cfg.repos[i].name);
            problems++;
        }
        free(rel);
        free(f);
    }
    char *etc = cfg_path(&c->cfg, "etc"), *out = NULL;
    char *argv[] = {"find", etc, "-name", "*.sypnew", NULL};
    if (run_capture(argv, &out) == 0 && out && *out) {
        char *save = NULL;
        for (char *l = strtok_r(out, "\n", &save); l; l = strtok_r(NULL, "\n", &save))
            printf("! Neue Konfiguration wartet auf Abgleich: %s\n", l + strlen(c->cfg.root) - (cfg_is_root(&c->cfg) ? 1 : 0));
    }
    free(out);
    free(etc);
    if (problems) printf(":: %d Problem(e)\n", problems);
    else printf(":: Keine Probleme gefunden\n");
    return problems ? 1 : 0;
}

static int c_keygen(const char *out, const char *comment) {
    seckey k;
    seckey_generate(&k, comment);
    char *sp = xasprintf("%s.key", out), *pp = xasprintf("%s.pub", out);
    if (seckey_save(&k, sp, pp)) die("%s", last_error());
    printf(":: Schlüssel %s: %s (geheim), %s\n", k.id, sp, pp);
    free(sp);
    free(pp);
    return 0;
}

static seckey *load_key(const char *path) {
    if (!path) return NULL;
    seckey *k = xcalloc(1, sizeof *k);
    if (seckey_load(path, k)) die("%s", last_error());
    return k;
}

static int c_repo_add(int argc, char **argv, const char *name, const char *arch, const char *key) {
    if (argc < 1) die("Repo-Verzeichnis fehlt");
    seckey *k = load_key(key);
    for (int i = 1; i < argc; i++) {
        char *d = repo_add_to_pool(argv[0], argv[i]);
        if (!d) die("%s", last_error());
        printf("  + %s\n", d + strlen(argv[0]) + 1);
        free(d);
    }
    if (repo_build_index(argv[0], name, arch, k)) die("%s", last_error());
    printf(":: Index geschrieben: %s/index.json.zst%s\n", argv[0], k ? "" : " (UNSIGNIERT)");
    free(k);
    return 0;
}

static int c_pack(int argc, char **argv, const char *out, const char *key) {
    if (argc < 2) die("Aufruf: syp pack <verzeichnis> <info.json> [-o ausgabe] [-k schlüssel]");
    char *js;
    size_t n;
    if (read_file(argv[1], &js, &n)) die("%s", last_error());
    jval *info = json_parse(js, n);
    free(js);
    if (!info) die("%s: %s", argv[1], last_error());
    seckey *k = load_key(key);
    char *p = syp_build(argv[0], info, out ? out : ".", k);
    if (!p) die("%s", last_error());
    puts(p);
    free(p);
    free(k);
    json_free(info);
    return 0;
}

static void usage(void) {
    puts("syp " SYP_VERSION " – symfonOS-Paketmanager\n\n"
         "Aufruf: syp [-r ROOT] [-y] [-n] BEFEHL ...\n\n"
         "  sync                      Repository-Indizes aktualisieren\n"
         "  install|in PAKET...       Pakete oder .syp-Dateien installieren (--as-dep)\n"
         "  remove|rm PAKET...        Pakete entfernen (--cascade)\n"
         "  upgrade|up                System aktualisieren (--no-sync)\n"
         "  autoremove                Verwaiste Abhängigkeiten entfernen\n"
         "  search|s [BEGRIFF...]     Pakete suchen (--json)\n"
         "  info PAKET...             Paketdetails (--json)\n"
         "  list|ls                   Installierte Pakete (--explicit, --json)\n"
         "  files PAKET               Dateien eines Pakets\n"
         "  owns PFAD...              Zu welchem Paket gehört eine Datei?\n"
         "  verify [PAKET...]         Dateien gegen Prüfsummen prüfen\n"
         "  history                   Transaktionshistorie (--limit N, --json)\n"
         "  rollback [TX]             Transaktion zurücknehmen (--system: Btrfs-Snapshot)\n"
         "  clean                     Paket-Cache aufräumen (--keep N)\n"
         "  doctor                    Systemzustand prüfen\n"
         "  build [REZEPT]            Paket aus SYPBUILD.toml bauen (-o, -k, --nocheck, --update-sums)\n"
         "  buildall REZEPTE --repo R Alle Rezepte abhängigkeitsgerecht bauen (siehe --help)\n"
         "  pack DIR INFO.json        Verzeichnis als .syp packen (-o, -k)\n"
         "  keygen AUSGABE            Signaturschlüssel erzeugen (--comment)\n"
         "  repo-add REPO [PAKET...]  Pakete ins Repo aufnehmen, Index signieren (--name, --arch, -k)\n"
         "  vercmp A B                Versionen vergleichen (-1, 0, 1)\n\n"
         "Transaktionsoptionen: --overwrite GLOB, --allow-unsigned, --trust-scripts, --no-scripts");
}

int main(int argc, char **argv) {
    g_tty = isatty(1);
    if (crypto_init()) die("%s", last_error());
    ctx c = {0};
    c.root = getenv("SYP_ROOT") ? getenv("SYP_ROOT") : "/";
    int i = 1;
    for (; i < argc && argv[i][0] == '-'; i++) {
        const char *a = argv[i];
        if ((!strcmp(a, "-r") || !strcmp(a, "--root")) && i + 1 < argc) c.root = argv[++i];
        else if (str_starts(a, "--root=")) c.root = a + 7;
        else if (!strcmp(a, "-y") || !strcmp(a, "--yes")) c.yes = 1;
        else if (!strcmp(a, "-n") || !strcmp(a, "--dry-run")) c.dry = 1;
        else if (!strcmp(a, "-q") || !strcmp(a, "--quiet")) c.quiet = 1;
        else if (!strcmp(a, "--version")) {
            puts("syp " SYP_VERSION);
            return 0;
        } else if (!strcmp(a, "-h") || !strcmp(a, "--help")) {
            usage();
            return 0;
        } else die("Unbekannte Option: %s", a);
    }
    if (i >= argc) {
        usage();
        return 2;
    }
    const char *cmd = argv[i++];
    if (!strcmp(cmd, "buildall")) return cmd_buildall(argc - i, argv + i, c.root);
    if (!strcmp(cmd, "vercmp")) {
        if (argc - i != 2) die("Aufruf: syp vercmp A B");
        printf("%d\n", vercmp(argv[i], argv[i + 1]));
        return 0;
    }
    /* Befehlsoptionen einsammeln */
    strvec pos = {0};
    int as_dep = 0, cascade = 0, nosync = 0, explicit_only = 0, nocheck = 0, update_sums = 0, keep_work = 0, system = 0;
    int limit = 30, keep = -1;
    const char *out = NULL, *key = NULL, *comment = "", *rname = "symfonos", *arch = "x86_64";
    for (; i < argc; i++) {
        const char *a = argv[i];
        int more = i + 1 < argc;
        if (!strcmp(a, "--json")) c.json = 1;
        else if (!strcmp(a, "--as-dep")) as_dep = 1;
        else if (!strcmp(a, "--cascade")) cascade = 1;
        else if (!strcmp(a, "--no-sync")) nosync = 1;
        else if (!strcmp(a, "--explicit")) explicit_only = 1;
        else if (!strcmp(a, "--nocheck")) nocheck = 1;
        else if (!strcmp(a, "--update-sums")) update_sums = 1;
        else if (!strcmp(a, "--keep-work")) keep_work = 1;
        else if (!strcmp(a, "--system")) system = 1;
        else if (!strcmp(a, "--allow-unsigned")) c.allow_unsigned = 1;
        else if (!strcmp(a, "--trust-scripts")) c.trust_scripts = 1;
        else if (!strcmp(a, "--no-scripts")) c.no_scripts = 1;
        else if (!strcmp(a, "-y") || !strcmp(a, "--yes")) c.yes = 1;
        else if (!strcmp(a, "-n") || !strcmp(a, "--dry-run")) c.dry = 1;
        else if (!strcmp(a, "--overwrite") && more) sv_push(&c.overwrite, argv[++i]);
        else if (!strcmp(a, "--limit") && more) limit = atoi(argv[++i]);
        else if (!strcmp(a, "--keep") && more) keep = atoi(argv[++i]);
        else if ((!strcmp(a, "-o") || !strcmp(a, "--out")) && more) out = argv[++i];
        else if ((!strcmp(a, "-k") || !strcmp(a, "--key")) && more) key = argv[++i];
        else if (!strcmp(a, "--comment") && more) comment = argv[++i];
        else if (!strcmp(a, "--name") && more) rname = argv[++i];
        else if (!strcmp(a, "--arch") && more) arch = argv[++i];
        else if (a[0] == '-' && a[1] && strcmp(a, "-")) die("Unbekannte Option für %s: %s", cmd, a);
        else sv_push(&pos, a);
    }
    sbuf cl = {0};
    sb_init(&cl);
    sb_add(&cl, "syp");
    for (int k = 1; k < argc; k++) sb_addf(&cl, strchr(argv[k], ' ') ? " '%s'" : " %s", argv[k]);
    c.cmdline = sb_take(&cl);

    /* Befehle ohne Systemkontext */
    if (!strcmp(cmd, "keygen")) {
        if (!pos.n) die("Aufruf: syp keygen AUSGABE");
        return c_keygen(pos.v[0], comment);
    }
    if (!strcmp(cmd, "repo-add")) return c_repo_add((int)pos.n, pos.v, rname, arch, key);
    if (!strcmp(cmd, "pack")) return c_pack((int)pos.n, pos.v, out, key);
    if (!strcmp(cmd, "build")) {
        build_opts o = {nocheck, update_sums, keep_work, getenv("SYP_SRCCACHE")};
        seckey *k = load_key(key);
        char *p = build_recipe(pos.n ? pos.v[0] : "SYPBUILD.toml", out ? out : ".", k, &o);
        if (!p) die("%s", last_error());
        printf("%s:: Gebaut: %s%s%s\n", col("32;1"), p, col_end(), k ? "" : " (unsigniert)");
        free(p);
        free(k);
        return 0;
    }

    if (config_load(c.root, &c.cfg)) die("%s", last_error());
    char *kd = cfg_path(&c.cfg, "etc/syp/keys");
    keyring_load_dir(kd, &c.kr);
    free(kd);
    int rc;
    if (!strcmp(cmd, "sync")) rc = c_sync(&c);
    else if (!strcmp(cmd, "install") || !strcmp(cmd, "in")) {
        if (!pos.n) die("Keine Pakete angegeben");
        rc = c_install(&c, (int)pos.n, pos.v, as_dep);
    } else if (!strcmp(cmd, "remove") || !strcmp(cmd, "rm")) {
        if (!pos.n) die("Keine Pakete angegeben");
        rc = c_remove(&c, (int)pos.n, pos.v, cascade);
    } else if (!strcmp(cmd, "upgrade") || !strcmp(cmd, "up")) rc = c_upgrade(&c, nosync);
    else if (!strcmp(cmd, "autoremove")) rc = c_autoremove(&c);
    else if (!strcmp(cmd, "search") || !strcmp(cmd, "s")) rc = c_search(&c, (int)pos.n, pos.v);
    else if (!strcmp(cmd, "info")) rc = c_info(&c, (int)pos.n, pos.v);
    else if (!strcmp(cmd, "list") || !strcmp(cmd, "ls")) rc = c_list(&c, explicit_only);
    else if (!strcmp(cmd, "files")) {
        if (!pos.n) die("Paketname fehlt");
        rc = c_files(&c, pos.v[0]);
    } else if (!strcmp(cmd, "owns")) rc = c_owns(&c, (int)pos.n, pos.v);
    else if (!strcmp(cmd, "verify")) rc = c_verify(&c, (int)pos.n, pos.v);
    else if (!strcmp(cmd, "history")) rc = c_history(&c, limit);
    else if (!strcmp(cmd, "rollback")) rc = c_rollback(&c, pos.n ? atoll(pos.v[0]) : 0, system);
    else if (!strcmp(cmd, "clean")) {
        need_root(&c);
        int n = clean_cache(&c.cfg, DB(&c), keep >= 0 ? keep : c.cfg.keep_cache);
        printf(":: %d alte Pakete aus dem Cache entfernt\n", n);
        rc = 0;
    } else if (!strcmp(cmd, "doctor")) rc = c_doctor(&c);
    else die("Unbekannter Befehl: %s (syp --help)", cmd);
    if (c.db_open) db_close(&c.db);
    sv_free(&pos);
    free(c.cmdline);
    return rc;
}
