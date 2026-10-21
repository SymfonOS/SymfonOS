/* syp-convert – Pakete aus Debian/Ubuntu, Fedora und Arch nach .syp umwandeln */
#define _GNU_SOURCE
#include <libgen.h>
#include <limits.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "convert.h"

#ifndef SYP_DATADIR
#define SYP_DATADIR "/usr/share/syp"
#endif

static char *find_base_map(void) {
    const char *env = getenv("SYP_CONVERT_MAP");
    if (env && *env) return xstrdup(env);
    char exe[PATH_MAX];
    ssize_t n = readlink("/proc/self/exe", exe, sizeof exe - 1);
    if (n > 0) {
        exe[n] = 0;
        char *dir = dirname(exe);
        const char *rel[] = {"../share/syp/convert-map.toml", "../../system/usr/share/syp/convert-map.toml"};
        for (int i = 0; i < 2; i++) {
            char *p = path_join(dir, rel[i]);
            if (path_exists(p)) return p;
            free(p);
        }
    }
    return xstrdup(SYP_DATADIR "/convert-map.toml");
}

static void usage(void) {
    puts("syp-convert – Pakete aus Debian/Ubuntu (.deb), Fedora (.rpm) und Arch (.pkg.tar.*) nach .syp\n\n"
         "Aufruf: syp-convert PAKET... [-o AUSGABE] [-k SCHLÜSSEL] [-r ROOT] [--offline] [--map DATEI]\n"
         "        [--license SPDX] [--name NAME] [--keep-versions] [--mark-scripts-reviewed] [--strict] [--json]\n\n"
         "  -r ROOT                  System, dessen Repos/DB für das Mapping genutzt werden (Standard /)\n"
         "  --offline                Kein Abgleich mit dem Repo-Index (Ergebnis ungeprüft)\n"
         "  --keep-versions          Versionsbedingungen fremder Distros unverändert übernehmen\n"
         "  --mark-scripts-reviewed  Skripte als geprüft markieren (nur für vertrauenswürdige Quellen)\n"
         "  --strict                 Fehlende Abhängigkeiten = Fehler (Exit 2)");
}

int main(int argc, char **argv) {
    init_utf8_locale();
    g_tty = isatty(1);
    if (crypto_init()) die("%s", last_error());
    strvec pkgs = {0};
    const char *out = ".", *key = NULL, *root = "/", *extra = NULL;
    int offline = 0, strict = 0, json = 0;
    conv_opts o = {0};
    for (int i = 1; i < argc; i++) {
        const char *a = argv[i];
        int more = i + 1 < argc;
        if ((!strcmp(a, "-o") || !strcmp(a, "--out")) && more) out = argv[++i];
        else if ((!strcmp(a, "-k") || !strcmp(a, "--key")) && more) key = argv[++i];
        else if ((!strcmp(a, "-r") || !strcmp(a, "--root")) && more) root = argv[++i];
        else if (!strcmp(a, "--map") && more) extra = argv[++i];
        else if (!strcmp(a, "--license") && more) o.license = argv[++i];
        else if (!strcmp(a, "--name") && more) o.name = argv[++i];
        else if (!strcmp(a, "--offline")) offline = 1;
        else if (!strcmp(a, "--strict")) strict = 1;
        else if (!strcmp(a, "--json")) json = 1;
        else if (!strcmp(a, "--keep-versions")) o.keep_versions = 1;
        else if (!strcmp(a, "--mark-scripts-reviewed")) o.mark_reviewed = 1;
        else if (!strcmp(a, "-h") || !strcmp(a, "--help")) {
            usage();
            return 0;
        } else if (a[0] == '-') die("Unbekannte Option: %s", a);
        else sv_push(&pkgs, a);
    }
    if (!pkgs.n) {
        usage();
        return 2;
    }
    config cfg;
    if (config_load(root, &cfg)) die("%s", last_error());
    ptrvec av = {0};
    if (!offline) repo_load_synced(&cfg, &av);
    database db, *dbp = NULL;
    char *dbpath = cfg_path(&cfg, "var/lib/syp/syp.db");
    if (path_exists(dbpath) && db_open(dbpath, &db) == 0) dbp = &db;
    free(dbpath);
    char *ex = extra ? xstrdup(extra) : cfg_path(&cfg, "etc/syp/convert-map.toml");
    char *base = find_base_map();
    mapper *m = mapper_new(base, ex, &av, dbp);
    free(base);
    free(ex);
    seckey *k = NULL;
    if (key) {
        k = xcalloc(1, sizeof *k);
        if (seckey_load(key, k)) die("%s", last_error());
    }
    int rc = 0;
    jval *all = jarr();
    strvec missing = {0};
    for (size_t i = 0; i < pkgs.n; i++) {
        report *rep = NULL;
        char *res = convert_package(pkgs.v[i], out, m, k, &o, &rep);
        if (!res) {
            fprintf(stderr, "Fehler bei %s: %s\n", pkgs.v[i], last_error());
            rc = 1;
            continue;
        }
        free(res);
        if (!report_ok(rep) && strict && !rc) rc = 2;
        jval *ur = jgeta(rep->r, "unresolved");
        for (size_t u = 0; u < ur->n; u++)
            if (!sv_contains(&missing, jgets(ur->items[u], "dep", ""))) sv_push(&missing, jgets(ur->items[u], "dep", ""));
        if (!json) {
            char *t = report_text(rep);
            printf("%s\n\n", t);
            free(t);
        }
        jpush(all, jcopy(rep->r));
        report_free(rep);
    }
    if (json) {
        char *s = json_dump(all, 0, 2);
        puts(s);
        free(s);
    } else if (missing.n) {
        sv_sort(&missing);
        puts("Fehlende Pakete (bitte bereitstellen oder in convert-map.toml zuordnen):");
        for (size_t i = 0; i < missing.n; i++) printf("  - %s\n", missing.v[i]);
    }
    json_free(all);
    sv_free(&missing);
    mapper_free(m);
    for (size_t i = 0; i < av.n; i++) pkg_free(av.v[i]);
    free(av.v);
    if (dbp) db_close(dbp);
    free(k);
    sv_free(&pkgs);
    return rc;
}
