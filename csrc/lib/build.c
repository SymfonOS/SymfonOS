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
#include "harden.h"
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
    char *copy = xstrdup(text), *next = copy;
    int pending = 0, done = 0;
    /* zeilenweise inklusive Leerzeilen (strtok würde sie verschlucken und das Rezept umformatieren) */
    while (*next) {
        char *line = next, *nl = strchr(next, '\n');
        if (nl) {
            *nl = 0;
            next = nl + 1;
        } else {
            next = line + strlen(line);
        }
        char *own = xstrdup(line), *tt = str_trim(own); /* str_trim verschiebt den Anfang: own freigeben */
        if (pending) {
            if (str_starts(tt, "sha256")) {
                sb_addf(&out, "sha256 = \"%s\"\n", sum);
                pending = 0;
                done = 1;
                free(own);
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
        free(own);
    }
    if (pending) sb_addf(&out, "sha256 = \"%s\"\n", sum);
    free(copy);
    free(text);
    int rc = write_file(recipe, out.s, out.len, 0644);
    free(out.s);
    return rc;
}

/* Ausweichquellen für große Projektserver. Unbedenklich, weil jede Quelle über die sha256 im
 * Rezept geprüft wird: ein Spiegel kann nichts unterschieben, nur ausfallen. */
static const char *MIRRORS[][4] = {
    {"https://ftp.gnu.org/gnu/", "https://ftpmirror.gnu.org/gnu/", "https://mirrors.kernel.org/gnu/",
     "https://ftp.fau.de/gnu/"},
    {"https://www.kernel.org/pub/", "https://mirrors.edge.kernel.org/pub/", "https://cdn.kernel.org/pub/", NULL},
    {"https://download.savannah.gnu.org/releases/", "https://download-mirror.savannah.gnu.org/releases/",
     "https://mirrors.kernel.org/nongnu/", "https://ftp.fau.de/nongnu/"},
    {"https://download.savannah.nongnu.org/releases/", "https://download-mirror.savannah.gnu.org/releases/",
     "https://mirrors.kernel.org/nongnu/", "https://ftp.fau.de/nongnu/"},
    {"https://www.cpan.org/", "https://cpan.metacpan.org/", NULL, NULL},
    {"https://data.iana.org/time-zones/", "https://ftp.iana.org/tz/", NULL, NULL},
    /* KDE und Qt verschieben ältere Versionen ins Archiv */
    {"https://download.kde.org/stable/", "https://download.kde.org/Attic/", "https://mirrors.mit.edu/kde/stable/",
     "https://ftp.fau.de/kde/stable/"},
    {"https://download.qt.io/official_releases/", "https://download.qt.io/archive/",
     "https://qt-mirror.dannhauer.de/archive/", NULL},
    {"https://www.x.org/pub/", "https://xorg.freedesktop.org/archive/", "https://ftp.fau.de/x.org/pub/", NULL},
    {"https://download.gnome.org/sources/", "https://ftp.gnome.org/pub/GNOME/sources/",
     "https://ftp.fau.de/gnome/sources/", NULL},
};

static char *fetch_source(const char *url, const char *fname, const char *srccache) {
    char *dest = path_join(srccache, fname);
    if (path_exists(dest)) return dest;
    /* Fortschritt auf stderr: stdout von "syp fetch" ist maschinenlesbar (ein Pfad pro Zeile) */
    fprintf(stderr, "  Lade %s\n", url);
    if (fetch_url(url, dest) == 0) return dest;
    char *first = xstrdup(last_error());
    for (size_t m = 0; m < sizeof MIRRORS / sizeof *MIRRORS; m++) {
        if (!str_starts(url, MIRRORS[m][0])) continue;
        for (int k = 1; k < 4 && MIRRORS[m][k]; k++) {
            char *alt = xasprintf("%s%s", MIRRORS[m][k], url + strlen(MIRRORS[m][0]));
            fprintf(stderr, "  Ausweichquelle %s\n", alt);
            int ok = fetch_url(alt, dest) == 0;
            free(alt);
            if (ok) {
                free(first);
                return dest;
            }
        }
    }
    set_error("%s", first);
    free(first);
    free(dest);
    return NULL;
}

/* Quelle laden und gegen die sha256 des Rezepts prüfen. Eine Datei im Cache mit falscher Summe
 * (abgebrochener Download, gleichnamige andere Version) wird verworfen und einmal neu geladen. */
static char *fetch_verified(const char *url, const char *fname, const char *srccache, const char *sum) {
    char *cached = path_join(srccache, fname);
    int was_cached = path_exists(cached);
    free(cached);
    for (int attempt = 0; attempt < 2; attempt++) {
        char *f = fetch_source(url, fname, srccache), h[65] = "";
        if (!f) return NULL;
        if (sha256_file(f, h) == 0 && !strcmp(h, sum)) return f;
        unlink(f);
        free(f);
        if (!was_cached || attempt) {
            set_error("sha256 falsch: %s (erwartet %s, erhalten %s)", url, sum, h);
            return NULL;
        }
        fprintf(stderr, "  Prüfsumme der Datei im Cache passt nicht – lade %s neu\n", fname);
    }
    return NULL;
}

int recipe_fetch(const char *recipe, const char *dir, int quiet) {
    jval *r = recipe_load(recipe);
    if (!r) return -1;
    mkdir_p(dir, 0755);
    jval *src = jgeta(r, "source");
    int rc = 0;
    for (size_t i = 0; src && i < src->n && !rc; i++) {
        const char *url = jgets(src->items[i], "url", NULL), *sum = jgets(src->items[i], "sha256", "");
        if (!url) continue;
        const char *fname = jgets(src->items[i], "filename", path_basename(url));
        if (!*sum || !strcmp(sum, "TODO")) {
            set_error("%s: keine sha256 im Rezept (syp build --update-sums)", url);
            rc = -1;
            break;
        }
        char *f = fetch_verified(url, fname, dir, sum);
        if (!f) {
            rc = -1;
            break;
        }
        if (!quiet) puts(f);
        free(f);
    }
    json_free(r);
    return rc;
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

/* chroot liegt in sbin, das nicht in jedem PATH steht (Dienste, normale Benutzer mit sudo -E) */
static const char *chroot_bin(void) {
    static const char *cand[] = {"/usr/sbin/chroot", "/usr/bin/chroot", "/sbin/chroot", "/bin/chroot", NULL};
    for (const char **c = cand; *c; c++)
        if (access(*c, X_OK) == 0) return *c;
    return "chroot";
}

static int is_elf(const char *p) {
    FILE *f = fopen(p, "rb");
    if (!f) return 0;
    char m[4] = {0};
    size_t n = fread(m, 1, 4, f);
    fclose(f);
    return n == 4 && !memcmp(m, "\x7f" "ELF", 4);
}

/* Paketinhalt an das Dateisystem-Layout anpassen (wie Fedora/Arch): /bin, /sbin, /lib, /lib64 sind
 * Symlinks nach /usr, /var/run und /var/lock zeigen nach /run. Rezepte, die dorthin installieren,
 * würden sonst mit dem Paket filesystem kollidieren. Laufzeitverzeichnisse legt systemd-tmpfiles an. */
static int usr_merge(const char *dest) {
    char *argv[] = {"sh", "-c",
                    "cd \"$1\" || exit 1\n"
                    "for d in bin sbin lib lib64; do\n"
                    "  [ -d \"$d\" ] && [ ! -L \"$d\" ] || continue\n"
                    "  t=usr/$d; [ \"$d\" = lib64 ] && t=usr/lib\n"
                    "  mkdir -p \"$t\" && cp -a \"$d/.\" \"$t/\" && rm -rf \"$d\" || exit 1\n"
                    "  echo \"    /$d -> /$t verschoben (usr-merge)\"\n"
                    "done\n"
                    /* /usr/lib64 (Meson, Nettle u. a. wählen es, wenn es existiert): pkg-config sucht nur in /usr/lib */
                    "if [ -d usr/lib64 ] && [ ! -L usr/lib64 ]; then\n"
                    "  mkdir -p usr/lib && cp -a usr/lib64/. usr/lib/ && rm -rf usr/lib64 || exit 1\n"
                    "  echo \"    /usr/lib64 -> /usr/lib verschoben\"\n"
                    "  grep -rlZ --include='*.pc' --include='*.cmake' /usr/lib64 usr/lib usr/share 2>/dev/null | xargs -0r sed -i 's#/usr/lib64#/usr/lib#g'\n"
                    "fi\n"
                    "for d in var/run var/lock; do\n"
                    "  if [ -d \"$d\" ] && [ ! -L \"$d\" ]; then rm -rf \"$d\"; echo \"    /$d entfernt (Laufzeitverzeichnis)\"; fi\n"
                    "done\n",
                    "usr-merge", (char *)dest, NULL};
    if (run_cmd(argv, NULL, NULL)) {
        set_error("usr-merge im Paketinhalt fehlgeschlagen");
        return -1;
    }
    return 0;
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

/* Standard-Schritte je Build-Style; Rezepte überschreiben einzelne Schritte oder ergänzen
 * configure_flags/make_flags/install_flags sowie pre_configure/post_install. */
static char *style_step(const jval *b, const char *step) {
    const char *style = jgets(b, "style", NULL);
    if (!style) return NULL;
    const char *cf = jgets(b, "configure_flags", ""), *mf = jgets(b, "make_flags", ""),
               *inf = jgets(b, "install_flags", ""), *pre = jgets(b, "pre_configure", ""),
               *post = jgets(b, "post_install", "");
    int run_check = jgetb(b, "run_check", 0);
    if (!strcmp(step, "prepare")) return *pre ? xasprintf("cd \"$S\"\n%s", pre) : NULL;
    if (!strcmp(style, "autotools")) {
        if (!strcmp(step, "build"))
            return xasprintf("cd \"$S\"\n./configure --prefix=/usr --libdir=/usr/lib --sysconfdir=/etc --localstatedir=/var "
                             "--disable-static %s\nmake %s",
                             cf, mf);
        if (!strcmp(step, "check")) return run_check ? xstrdup("cd \"$S\"\nmake check") : NULL;
        if (!strcmp(step, "package"))
            return xasprintf("cd \"$S\"\nmake DESTDIR=\"$DESTDIR\" install %s\n%s", inf, post);
    } else if (!strcmp(style, "make")) {
        if (!strcmp(step, "build")) return xasprintf("cd \"$S\"\nmake %s", mf);
        if (!strcmp(step, "check")) return run_check ? xstrdup("cd \"$S\"\nmake check") : NULL;
        if (!strcmp(step, "package"))
            return xasprintf("cd \"$S\"\nmake DESTDIR=\"$DESTDIR\" PREFIX=/usr install %s\n%s", inf, post);
    } else if (!strcmp(style, "meson")) {
        if (!strcmp(step, "build"))
            return xasprintf("meson setup \"$S/_build\" \"$S\" --prefix=/usr --libdir=lib --libexecdir=lib --sbindir=bin "
                             "--buildtype=release -Dwrap_mode=nodownload -Db_pie=true %s\nninja -C \"$S/_build\" %s",
                             cf, mf);
        if (!strcmp(step, "check")) return run_check ? xstrdup("meson test -C \"$S/_build\" --print-errorlogs") : NULL;
        if (!strcmp(step, "package"))
            return xasprintf("DESTDIR=\"$DESTDIR\" ninja -C \"$S/_build\" install %s\n%s", inf, post);
    } else if (!strcmp(style, "cmake")) {
        if (!strcmp(step, "build"))
            /* Ninja nur, wenn vorhanden (nicht jedes Rezept führt es als Bauabhängigkeit);
             * CMake 4 lehnt cmake_minimum_required < 3.5 ab – ältere Projekte trotzdem zulassen */
            return xasprintf("gen=Ninja; command -v ninja >/dev/null || gen='Unix Makefiles'\n"
                             "cmake -S \"$S\" -B \"$S/_build\" -G \"$gen\" -DCMAKE_INSTALL_PREFIX=/usr "
                             "-DCMAKE_INSTALL_LIBDIR=lib -DCMAKE_BUILD_TYPE=Release -DBUILD_TESTING=OFF "
                             "-DCMAKE_POLICY_VERSION_MINIMUM=3.5 %s\n"
                             "cmake --build \"$S/_build\" %s",
                             cf, mf);
        if (!strcmp(step, "check")) return run_check ? xstrdup("ctest --test-dir \"$S/_build\" --output-on-failure") : NULL;
        if (!strcmp(step, "package"))
            return xasprintf("DESTDIR=\"$DESTDIR\" cmake --install \"$S/_build\" %s\n%s", inf, post);
    } else if (!strcmp(style, "python")) {
        if (!strcmp(step, "build"))
            return xasprintf("cd \"$S\"\npip3 wheel -w dist --no-cache-dir --no-build-isolation --no-deps %s .", cf);
        if (!strcmp(step, "package"))
            return xasprintf("cd \"$S\"\npip3 install --root=\"$DESTDIR\" --no-index --no-deps --no-user "
                             "--no-compile dist/*.whl %s\n%s",
                             inf, post);
    } else if (!strcmp(style, "perl")) {
        if (!strcmp(step, "build")) return xasprintf("cd \"$S\"\nperl Makefile.PL INSTALLDIRS=vendor %s\nmake %s", cf, mf);
        if (!strcmp(step, "package"))
            /* perllocal.pod und .packlist sind lokale Protokolle von CPAN, kein Paketinhalt (Dateikonflikte) */
            return xasprintf("cd \"$S\"\nmake DESTDIR=\"$DESTDIR\" install %s\n"
                             "find \"$DESTDIR\" \\( -name perllocal.pod -o -name .packlist \\) -delete\n%s",
                             inf, post);
    } else if (!strcmp(style, "meta")) {
        if (!strcmp(step, "package")) return xstrdup("mkdir -p \"$DESTDIR/usr/share/symfonos/meta\"\n"
                                                     "echo \"$PKGNAME\" > \"$DESTDIR/usr/share/symfonos/meta/$PKGNAME\"");
    }
    return NULL;
}

/* Quellbaum: [build] dir = "..." oder das einzige Verzeichnis in SRCDIR */
static char *source_dir(const char *src, const jval *b) {
    const char *d = jgets(b, "dir", NULL);
    if (d) return path_join(src, d);
    struct dirent **list;
    int n = scandir(src, &list, NULL, alphasort);
    char *only = NULL;
    int dirs = 0, other = 0;
    for (int i = 0; i < n; i++) {
        const char *x = list[i]->d_name;
        if (strcmp(x, ".") && strcmp(x, "..")) {
            char *p = path_join(src, x);
            if (is_dir(p)) {
                dirs++;
                free(only);
                only = p;
                p = NULL;
            } else {
                other++;
            }
            free(p);
        }
        free(list[i]);
    }
    if (n >= 0) free(list);
    /* Genau ein Verzeichnis = entpackte Hauptquelle; daneben liegende Dateien (Patches,
     * Konfiguration, pax_global_header aus GitHub-Archiven) ändern daran nichts */
    (void)other;
    if (dirs == 1) return only;
    free(only);
    return xstrdup(src);
}

static char **build_env(const char *src, const char *sdir, const char *dest, const char *rdir, const jval *pk,
                        const jval *opts, int isolated) {
    strvec env = {0};
    extern char **environ;
    if (isolated) {
        /* Chroot: keine Host-Umgebung durchreichen */
        sv_push(&env, "HOME=/root");
        sv_push(&env, "TERM=xterm");
        sv_push(&env, "LC_ALL=C"); /* immer vorhanden, auch vor glibc-Locales; reproduzierbar */
        sv_push(&env, "PATH=/usr/bin:/usr/sbin");
        const char *sde = getenv("SOURCE_DATE_EPOCH");
        if (sde) sv_push_own(&env, xasprintf("SOURCE_DATE_EPOCH=%s", sde));
    } else {
        for (char **e = environ; *e; e++)
            if (!str_starts(*e, "SRCDIR=") && !str_starts(*e, "DESTDIR=") && !str_starts(*e, "CFLAGS=") &&
                !str_starts(*e, "CXXFLAGS=") && !str_starts(*e, "LDFLAGS=") && !str_starts(*e, "MAKEFLAGS="))
                sv_push(&env, *e);
    }
    const char *ver = jgets(pk, "version", "0");
    char *v = xstrdup(ver), *dash = strrchr(v, '-'), *colon = strchr(v, ':');
    char *rel = dash ? xstrdup(dash + 1) : xstrdup("1");
    if (dash) *dash = 0;
    long nproc = sysconf(_SC_NPROCESSORS_ONLN);
    const char *cf = jgets(opts, "cflags", DEF_CFLAGS), *lf = jgets(opts, "ldflags", DEF_LDFLAGS);
    sv_push_own(&env, xasprintf("SRCDIR=%s", src));
    sv_push_own(&env, xasprintf("S=%s", sdir));
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
    /* [options] env = ["NAME=wert", …]: zusätzliche Umgebung für alle Bauschritte (zuletzt, gewinnt) */
    jval *extra = jgeta(opts, "env");
    for (size_t i = 0; extra && i < extra->n; i++)
        if (extra->items[i]->t == J_STR && strchr(extra->items[i]->s, '=')) sv_push(&env, extra->items[i]->s);
    free(v);
    free(rel);
    sv_push_own(&env, NULL);
    return env.v;
}

static char *default_srccache(const build_opts *o) {
    if (o->srccache && *o->srccache) return xstrdup(o->srccache);
    if (geteuid() == 0) return xstrdup("/var/cache/syp/sources");
    const char *x = getenv("XDG_CACHE_HOME"), *h = getenv("HOME");
    if (x && *x) return xasprintf("%s/syp/sources", x);
    return xasprintf("%s/.cache/syp/sources", h && *h ? h : "/tmp");
}

char *build_recipe(const char *recipe, const char *outdir, const seckey *signer, const build_opts *o) {
    char *rpath = realpath(recipe, NULL);
    if (!rpath) {
        set_error("%s: %s", recipe, strerror(errno));
        return NULL;
    }
    char *cache = default_srccache(o);
    if (o->update_sums && recipe_fill_sums(rpath, cache)) {
        free(rpath);
        free(cache);
        return NULL;
    }
    jval *r = recipe_load(rpath);
    if (!r) {
        free(rpath);
        free(cache);
        return NULL;
    }
    char *rdir = path_dirname(rpath);
    jval *pk = jget(r, "package");
    if (!pk) {
        set_error("%s: [package] fehlt", recipe);
        json_free(r);
        free(rdir);
        free(rpath);
        free(cache);
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
    char *work;
    if (o->chroot) {
        char *base = path_join(o->chroot, "var/tmp/syp-build");
        mkdir_p(base, 0755);
        work = xasprintf("%s/%s-XXXXXX", base, jgets(pk, "name", "x"));
        free(base);
        if (!mkdtemp(work)) die("mkdtemp %s", work);
    } else {
        work = make_tempdir("syp-build-");
    }
    char *src = path_join(work, "src"), *dest = path_join(work, "pkg");
    mkdir_p(src, 0755);
    mkdir_p(dest, 0755);
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
            char *f = fetch_verified(url, fname, cache, sum);
            if (!f) {
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
            /* "@/pfad" = relativ zur Wurzel des Quell-Repositorys (SYP_SOURCE_ROOT oder nächstes .git) */
            char *base = xstrdup(rdir);
            if (str_starts(path, "@/")) {
                free(base);
                base = NULL;
                const char *env = getenv("SYP_SOURCE_ROOT");
                if (env && *env) base = realpath(env, NULL);
                for (char *d = xstrdup(rdir); !base && strcmp(d, "/");) {
                    char *g = path_join(d, ".git");
                    if (path_exists(g)) base = xstrdup(d);
                    free(g);
                    char *up = path_dirname(d);
                    free(d);
                    d = up;
                    if (base || !strcmp(d, "/")) {
                        free(d);
                        break;
                    }
                }
                if (!base) {
                    set_error("Quell-Repository für %s nicht gefunden (SYP_SOURCE_ROOT setzen)", path);
                    fail = 1;
                    break;
                }
                path += 2;
            }
            char *p = path_join(base, path), *rp = realpath(p, NULL);
            free(p);
            if (!rp || (strcmp(rp, base) && !(str_starts(rp, base) && rp[strlen(base)] == '/'))) {
                free(base);
                set_error("Quellpfad außerhalb des Rezepts: %s", path);
                free(rp);
                fail = 1;
                break;
            }
            free(base);
            const char *dn = jgets(s, "dest", path_basename(rp));
            char *d = path_join(src, dn);
            static const char *ign[] = {".git", "out", "work", "__pycache__", "*.syp", ".pytest_cache", "*.o",
                                        "*.a", NULL};
            if (copy_tree(rp, d, ign)) fail = 1;
            free(d);
            free(rp);
        }
    }
    if (!fail) {
        size_t rl = o->chroot ? strlen(o->chroot) : 0;
        while (rl > 1 && o->chroot[rl - 1] == '/') rl--;
        const char *csrc = o->chroot ? src + rl : src, *cdest = o->chroot ? dest + rl : dest;
        char *crdir = NULL;
        if (o->chroot) {
            /* Rezeptverzeichnis in die Wurzel spiegeln, damit $RECIPEDIR (Patches) erreichbar ist */
            char *rd = path_join(work, "recipe");
            static const char *ig[] = {".git", NULL};
            copy_tree(rdir, rd, ig);
            crdir = xstrdup(rd + rl);
            free(rd);
        }
        char *sdir = source_dir(src, jget(r, "build"));
        const char *csdir = o->chroot ? sdir + rl : sdir;
        char **env = build_env(csrc, csdir, cdest, crdir ? crdir : rdir, pk, jget(r, "options"), o->chroot != NULL);
        const char *steps[] = {"prepare", "build", "check", "package", NULL};
        jval *b = jget(r, "build");
        for (const char **st = steps; *st && !fail; st++) {
            if (!strcmp(*st, "check") && o->nocheck) continue;
            char *gen = NULL;
            const char *script = jgets(b, *st, NULL);
            if (!script) script = gen = style_step(b, *st);
            if (!script) continue;
            printf("==> %s()\n", *st);
            fflush(stdout);
            int rc;
            if (o->chroot) {
                size_t ne = 0;
                while (env[ne]) ne++;
                char **argv = xcalloc(ne + 10, sizeof(char *));
                size_t k = 0;
                argv[k++] = (char *)chroot_bin();
                argv[k++] = (char *)o->chroot;
                argv[k++] = "/usr/bin/env";
                argv[k++] = "-i";
                for (size_t e = 0; e < ne; e++) argv[k++] = env[e];
                char *wrapped = xasprintf("cd \"$SRCDIR\"\n%s", script);
                argv[k++] = "/bin/bash";
                argv[k++] = "-euo";
                argv[k++] = "pipefail";
                argv[k++] = "-c";
                argv[k++] = wrapped;
                argv[k] = NULL;
                rc = run_cmd(argv, NULL, NULL);
                free(wrapped);
                free(argv);
            } else {
                char *argv[] = {"bash", "-euo", "pipefail", "-c", (char *)script, NULL};
                rc = run_cmd(argv, src, env);
            }
            free(gen);
            if (rc) {
                set_error("%s() fehlgeschlagen (%d)", *st, rc);
                fail = 1;
            }
        }
        for (char **e = env; *e; e++) free(*e);
        free(env);
        free(crdir);
        free(sdir);
    }
    if (!fail) {
        jval *opts = jget(r, "options");
        if (usr_merge(dest)) fail = 1;
        post_process(dest, jgetb(opts, "strip", 1));
        harden_report hr;
        const char *mode = jgets(opts, "hardening", "strict");
        if (strcmp(mode, "strict") && !jgets(opts, "hardening_reason", NULL))
            printf("    Hinweis: hardening = \"%s\" ohne hardening_reason\n", mode);
        if (harden_check(dest, mode, &hr)) {
            fail = 1;
        } else {
            jval *origin = jget(info, "origin");
            jval *hj = harden_json(&hr);
            jset(hj, "mode", jstr(mode));
            if (jgets(opts, "hardening_reason", NULL)) jset(hj, "reason", jstr(jgets(opts, "hardening_reason", "")));
            jset(origin, "hardening", hj);
            out = syp_build(dest, info, outdir, signer);
        }
        harden_free(&hr);
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
    free(cache);
    return out;
}
