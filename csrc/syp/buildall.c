/* buildall.c – baut einen ganzen Rezeptbaum abhängigkeitsgerecht in ein Repository.
 *
 *   syp buildall REZEPTE --repo REPO [-k SCHLÜSSEL] [--root BAUROOT] [--max-minutes N]
 *                [--only a,b] [--nocheck] [--update-sums] [--name NAME] [--arch ARCH]
 *                [--keep-going] [-n]
 *
 * - Rezepte: REZEPTE/<name>/SYPBUILD.toml
 * - Reihenfolge: topologisch über depends + makedepends; Zyklen werden gemeldet und alphabetisch gebrochen.
 * - Bereits im Pool vorhandene Paketversionen werden übersprungen -> fortsetzbar (CI in Etappen).
 * - Mit --root wird in einer Chroot-Bauumgebung gebaut: der Host lädt Quellen, entpackt sie in die
 *   Wurzel und führt prepare/build/check/package per chroot aus (syp muss dort nicht vorhanden sein).
 *   Jedes gebaute Paket wird sofort in die Wurzel installiert (LFS-Prinzip; --no-install schaltet ab),
 *   unverwaltete Dateien der temporären Werkzeuge werden dabei überschrieben. Signiert wird auf dem Host.
 * - --max-minutes: sauber beenden, wenn das Zeitbudget erschöpft ist (Exit-Code 3 = es gibt noch Arbeit).
 */
#define _GNU_SOURCE
#include <dirent.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mount.h>
#include <time.h>
#include <unistd.h>

#include "build.h"
#include "core.h"
#include "pkg.h"

typedef struct {
    char *dir, *file, *name, *version, *pkgfile;
    jval *r;
    strvec deps;    /* Namen (erste auflösbare Alternative) */
    strvec atoms;   /* vollständige Ausdrücke für die Installation in der Bauumgebung */
    int state;      /* 0 neu, 1 in Arbeit, 2 fertig */
    int idx;
} recipe;

static int cmp_recipe(const void *a, const void *b) {
    return strcmp((*(recipe *const *)a)->name, (*(recipe *const *)b)->name);
}

static void add_deps(recipe *r, jval *arr) {
    for (size_t i = 0; arr && i < arr->n; i++) {
        if (arr->items[i]->t != J_STR) continue;
        sv_push(&r->atoms, arr->items[i]->s);
    }
}

static void topo(recipe *r, hmap *prov, ptrvec *out, strvec *cycles) {
    if (r->state == 2) return;
    if (r->state == 1) {
        if (!sv_contains(cycles, r->name)) sv_push(cycles, r->name);
        return;
    }
    r->state = 1;
    for (size_t i = 0; i < r->atoms.n; i++) {
        dep d;
        if (dep_parse(r->atoms.v[i], &d)) continue;
        for (size_t a = 0; a < d.n; a++) {
            recipe *q = hm_get(prov, d.alt[a].name);
            if (q && q != r) {
                topo(q, prov, out, cycles);
                if (!sv_contains(&r->deps, q->name)) sv_push(&r->deps, q->name);
                break;
            }
        }
        dep_free(&d);
    }
    r->state = 2;
    pv_push(out, r);
}

static int sh(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
static int sh(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    char *cmd = NULL;
    if (vasprintf(&cmd, fmt, ap) < 0) die("Kein Speicher");
    va_end(ap);
    char *argv[] = {"sh", "-c", cmd, NULL};
    int rc = run_cmd(argv, NULL, NULL);
    free(cmd);
    return rc;
}

/* Pfad dieses Programms: buildall ruft sich selbst für sync/install auf, unabhängig vom PATH
 * (in der CI und im Bootstrap liegt syp nicht in einem Standardverzeichnis). */
static const char *self_exe(void) {
    static char p[4096];
    if (!p[0]) {
        ssize_t n = readlink("/proc/self/exe", p, sizeof p - 1);
        if (n <= 0) snprintf(p, sizeof p, "syp");
        else p[n] = 0;
    }
    return p;
}

/* Bauumgebung vorbereiten: syp-Konfiguration mit lokalem Repo, Schlüssel, Mounts */
static int prepare_root(const char *broot, const char *repo, const char *pubkey_path, const char *rname) {
    char *etc = path_join(broot, "etc/syp"), *keys = path_join(broot, "etc/syp/keys");
    mkdir_p(keys, 0755);
    char *conf = xasprintf("arch = \"x86_64\"\n[[repo]]\nname = \"%s\"\nurl = \"file://%s\"\npriority = 1\n"
                           "[snapshots]\nenabled = false\n[security]\nrequire_signatures = %s\n",
                           rname, repo, pubkey_path ? "true" : "false");
    char *cp = path_join(etc, "syp.toml");
    int rc = write_file(cp, conf, strlen(conf), 0644);
    if (!rc && pubkey_path) {
        char *dst = path_join(keys, "build.pub");
        rc = copy_file(pubkey_path, dst);
        free(dst);
    }
    free(cp);
    free(conf);
    free(etc);
    free(keys);
    const char *dirs[] = {"dev", "proc", "sys", "run", "tmp", "var/tmp/syp-build", NULL};
    for (const char **d = dirs; *d; d++) {
        char *p = path_join(broot, *d);
        mkdir_p(p, 0755);
        free(p);
    }
    rc |= sh("mountpoint -q '%s/dev' || mount --bind /dev '%s/dev'", broot, broot);
    rc |= sh("mountpoint -q '%s/proc' || mount -t proc proc '%s/proc'", broot, broot);
    rc |= sh("mountpoint -q '%s/sys' || mount -t sysfs sys '%s/sys'", broot, broot);
    rc |= sh("mountpoint -q '%s/run' || mount -t tmpfs tmpfs '%s/run'", broot, broot);
    return rc ? -1 : 0;
}

static void release_root(const char *broot) {
    sh("for m in run sys proc dev; do mountpoint -q '%s/'$m && umount -l '%s/'$m; done; true", broot, broot);
}

static char *build_in_root(recipe *r, const char *broot, const build_opts *o) {
    /* Abhängigkeiten aus dem lokalen Repo in die Bauumgebung (meist schon vorhanden) */
    if (r->atoms.n) {
        sbuf cmd = {0};
        sb_init(&cmd);
        sb_addf(&cmd, "'%s' -q -r '%s' sync >/dev/null && '%s' -r '%s' -y install --as-dep --overwrite '/*'", self_exe(),
                broot, self_exe(), broot);
        for (size_t i = 0; i < r->atoms.n; i++) sb_addf(&cmd, " '%s'", r->atoms.v[i]);
        sb_add(&cmd, " >/dev/null");
        int rc = sh("%s", cmd.s);
        free(cmd.s);
        if (rc) {
            set_error("Bauabhängigkeiten von %s nicht installierbar", r->name);
            return NULL;
        }
    }
    build_opts bo = *o;
    bo.chroot = broot;
    char *outdir = path_join(broot, "var/tmp/syp-out");
    mkdir_p(outdir, 0755);
    char *res = build_recipe(r->file, outdir, NULL, &bo);
    free(outdir);
    return res;
}

int cmd_buildall(int argc, char **argv, const char *root) {
    (void)root;
    const char *rdir = NULL, *repo = NULL, *key = NULL, *broot = NULL, *only = NULL, *rname = "symfonos";
    const char *arch = "x86_64";
    int maxmin = 0, nocheck = 0, update_sums = 0, keep_going = 0, dry = 0, install_built = 1;
    for (int i = 0; i < argc; i++) {
        const char *a = argv[i];
        int more = i + 1 < argc;
        if (!strcmp(a, "--repo") && more) repo = argv[++i];
        else if ((!strcmp(a, "-k") || !strcmp(a, "--key")) && more) key = argv[++i];
        else if (!strcmp(a, "--root") && more) broot = argv[++i];
        else if (!strcmp(a, "--only") && more) only = argv[++i];
        else if (!strcmp(a, "--name") && more) rname = argv[++i];
        else if (!strcmp(a, "--arch") && more) arch = argv[++i];
        else if (!strcmp(a, "--max-minutes") && more) maxmin = atoi(argv[++i]);
        else if (!strcmp(a, "--nocheck")) nocheck = 1;
        else if (!strcmp(a, "--update-sums")) update_sums = 1;
        else if (!strcmp(a, "--keep-going")) keep_going = 1;
        else if (!strcmp(a, "--no-install")) install_built = 0;
        else if (!strcmp(a, "-n") || !strcmp(a, "--dry-run")) dry = 1;
        else if (a[0] == '-') die("Unbekannte Option für buildall: %s", a);
        else rdir = a;
    }
    if (!rdir || !repo) die("Aufruf: syp buildall REZEPTE --repo REPO [...]");
    char *repo_abs = realpath(repo, NULL);
    if (!repo_abs) {
        mkdir_p(repo, 0755);
        repo_abs = realpath(repo, NULL);
    }
    seckey *k = NULL;
    char *pubpath = NULL;
    if (key) {
        k = xcalloc(1, sizeof *k);
        if (seckey_load(key, k)) die("%s", last_error());
        pubpath = xasprintf("%s/.buildall-%s.pub", repo_abs, k->id);
        char *tmpsec = xasprintf("%s/.buildall.tmp", repo_abs);
        seckey_save(k, tmpsec, pubpath);
        unlink(tmpsec);
        free(tmpsec);
    }
    /* Rezepte laden */
    ptrvec recipes = {0};
    hmap prov = {0};
    hm_init(&prov);
    struct dirent **list;
    int n = scandir(rdir, &list, NULL, alphasort);
    if (n < 0) die("%s: nicht lesbar", rdir);
    for (int i = 0; i < n; i++) {
        char *d = path_join(rdir, list[i]->d_name), *f = path_join(d, "SYPBUILD.toml");
        if (list[i]->d_name[0] != '.' && path_exists(f)) {
            jval *r = recipe_load(f);
            if (!r) die("%s", last_error());
            recipe *x = xcalloc(1, sizeof *x);
            x->dir = realpath(d, NULL);
            x->file = realpath(f, NULL);
            x->r = r;
            jval *pk = jget(r, "package");
            x->name = xstrdup(jgets(pk, "name", list[i]->d_name));
            x->version = xstrdup(jgets(pk, "version", "0"));
            x->pkgfile = recipe_pkgfile(r);
            add_deps(x, jgeta(pk, "depends"));
            add_deps(x, jgeta(pk, "makedepends"));
            pv_push(&recipes, x);
            hm_set(&prov, x->name, x);
            jval *pr = jgeta(pk, "provides");
            for (size_t j = 0; pr && j < pr->n; j++) {
                char *pn, *pvv;
                provide_parts(pr->items[j]->s, &pn, &pvv);
                if (!hm_get(&prov, pn)) hm_set(&prov, pn, x);
                free(pn);
                free(pvv);
            }
        }
        free(d);
        free(f);
        free(list[i]);
    }
    free(list);
    qsort(recipes.v, recipes.n, sizeof(void *), cmp_recipe);
    ptrvec order = {0};
    strvec cycles = {0};
    for (size_t i = 0; i < recipes.n; i++) topo(recipes.v[i], &prov, &order, &cycles);
    if (cycles.n) {
        printf("Warnung: Abhängigkeitszyklen über:");
        for (size_t i = 0; i < cycles.n; i++) printf(" %s", cycles.v[i]);
        printf(" (alphabetisch gebrochen)\n");
    }
    /* --only: Ziele plus deren Abhängigkeiten */
    hmap wanted = {0};
    hm_init(&wanted);
    if (only) {
        char *o = xstrdup(only), *save = NULL;
        ptrvec stack = {0};
        for (char *t = strtok_r(o, ",", &save); t; t = strtok_r(NULL, ",", &save)) {
            recipe *x = hm_get(&prov, t);
            if (!x) die("Kein Rezept für %s", t);
            pv_push(&stack, x);
        }
        while (stack.n) {
            recipe *x = stack.v[--stack.n];
            if (hm_get(&wanted, x->name)) continue;
            hm_set(&wanted, x->name, x);
            for (size_t j = 0; j < x->deps.n; j++) pv_push(&stack, hm_get(&prov, x->deps.v[j]));
        }
        free(stack.v);
        free(o);
    }
    printf(":: %zu Rezepte, Bauumgebung: %s\n", recipes.n, broot ? broot : "Host");
    time_t start = time(NULL);
    int built = 0, skipped = 0, failed = 0, pending = 0;
    strvec failures = {0};
    hmap failed_names = {0};
    hm_init(&failed_names);
    if (broot && !dry) {
        if (geteuid() != 0) die("--root benötigt Root-Rechte (chroot, mount)");
        if (!path_exists(repo_abs)) mkdir_p(repo_abs, 0755);
        char *ip = path_join(repo_abs, "index.json.zst");
        if (!path_exists(ip)) repo_build_index(repo_abs, rname, arch, k);
        free(ip);
        char *br = realpath(broot, NULL);
        if (!br || prepare_root(br, repo_abs, pubpath, rname)) die("Bauumgebung %s nicht nutzbar", broot);
        broot = br;
    }
    build_opts bo = {nocheck, update_sums, 0, getenv("SYP_SRCCACHE"), NULL};
    char *pool = path_join(repo_abs, "pool");
    for (size_t i = 0; i < order.n; i++) {
        recipe *r = order.v[i];
        if (only && !hm_get(&wanted, r->name)) continue;
        char letter[2] = {r->name[0], 0};
        char *pd = path_join(pool, letter), *target = path_join(pd, r->pkgfile);
        int blocked = 0;
        for (size_t j = 0; j < r->deps.n; j++)
            if (hm_get(&failed_names, r->deps.v[j])) blocked = 1;
        /* Vorhandenes Paket mit fremdem Schlüssel signiert (Schlüsselwechsel, kompromittierter
         * Schlüssel): verwerfen und neu bauen, statt es weiterzuverwenden */
        if (path_exists(target) && k) {
            sypfile sf;
            if (syp_open_meta(target, &sf) == 0) {
                const char *kid = sf.sig ? jgets(sf.sig, "key_id", "") : "";
                if (strcmp(kid, k->id)) {
                    printf("   %s: mit fremdem Schlüssel %s signiert – wird neu gebaut\n", r->pkgfile, *kid ? kid : "?");
                    unlink(target);
                }
                syp_close(&sf);
            }
        }
        if (path_exists(target)) {
            skipped++;
            /* Gebaut, aber (z. B. nach einem Abbruch) nicht in der Bauumgebung installiert: nachholen */
            if (broot && install_built &&
                sh("'%s' -q -r '%s' list 2>/dev/null | awk -v n='%s' '$1==n{f=1} END{exit !f}'", self_exe(), broot,
                   r->name) &&
                sh("'%s' -q -r '%s' sync >/dev/null && '%s' -r '%s' -y install --overwrite '/*' '%s' >/dev/null",
                   self_exe(), broot, self_exe(), broot, r->name)) {
                printf("!! %s: vorhanden, aber nicht in die Bauumgebung installierbar\n", r->name);
                hm_set(&failed_names, r->name, (void *)1);
                failed++;
                skipped--;
            }
        } else if (blocked) {
            printf("-- %s %s übersprungen (Abhängigkeit fehlgeschlagen)\n", r->name, r->version);
            hm_set(&failed_names, r->name, (void *)1);
            failed++;
        } else if (maxmin && time(NULL) - start > maxmin * 60) {
            pending++;
        } else if (dry) {
            printf("   würde bauen: %s %s\n", r->name, r->version);
            pending++;
        } else {
            printf("%s==> [%zu/%zu] %s %s%s\n", col("1;34"), i + 1, order.n, r->name, r->version, col_end());
            fflush(stdout);
            char *res = broot ? build_in_root(r, broot, &bo) : build_recipe(r->file, pd, NULL, &bo);
            if (res && k && syp_resign(res, k)) {
                free(res);
                res = NULL;
            }
            if (res && broot) {
                mkdir_p(pd, 0755);
                if (rename(res, target) && copy_file(res, target)) {
                    free(res);
                    res = NULL;
                }
            }
            if (res && repo_index_add(repo_abs, rname, arch, k, target)) {
                free(res);
                res = NULL;
            }
            if (res && broot && install_built &&
                sh("'%s' -q -r '%s' sync >/dev/null && '%s' -r '%s' -y install --overwrite '/*' '%s' >/dev/null", self_exe(),
                   broot, self_exe(), broot, r->name)) {
                set_error("Installation von %s in die Bauumgebung fehlgeschlagen", r->name);
                free(res);
                res = NULL;
            }
            if (res) {
                built++;
                free(res);
            } else {
                failed++;
                hm_set(&failed_names, r->name, (void *)1);
                sv_push_own(&failures, xasprintf("%s: %s", r->name, last_error()));
                fprintf(stderr, "%s!! %s: %s%s\n", col("31;1"), r->name, last_error(), col_end());
                if (!keep_going) {
                    free(pd);
                    free(target);
                    break;
                }
            }
        }
        free(pd);
        free(target);
    }
    free(pool);
    if (broot && !dry) release_root(broot);
    if (pubpath) {
        unlink(pubpath);
        free(pubpath);
    }
    printf(":: gebaut %d, vorhanden %d, fehlgeschlagen %d, offen %d (%ld min)\n", built, skipped, failed, pending,
           (long)(time(NULL) - start) / 60);
    for (size_t i = 0; i < failures.n; i++) printf("   ✗ %s\n", failures.v[i]);
    if (failed) return 1;
    return pending && !dry ? 3 : 0;
}
