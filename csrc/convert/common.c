/* common.c – Zwischenformat, Versionen, Entpacken, ELF, Pfad-Umzug, Skripte, Paketbau */
#define _GNU_SOURCE
#include <archive.h>
#include <archive_entry.h>
#include <dirent.h>
#include <elf.h>
#include <errno.h>
#include <fcntl.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>

#include "convert.h"
#include "pkg.h"

/* --- Abhängigkeitslisten ------------------------------------------------------- */
void dl_push_dep(deplist *l, dep d) {
    l->v = xrealloc(l->v, (l->n + 1) * sizeof(dep));
    l->v[l->n++] = d;
}
void dl_push(deplist *l, const char *name, depop op, const char *version) {
    dep d;
    d.n = 1;
    d.alt = xcalloc(1, sizeof(atom));
    d.alt[0].name = xstrdup(name);
    d.alt[0].op = op;
    d.alt[0].version = version ? xstrdup(version) : NULL;
    dl_push_dep(l, d);
}
void dl_free(deplist *l) {
    for (size_t i = 0; i < l->n; i++) dep_free(&l->v[i]);
    free(l->v);
    l->v = NULL;
    l->n = 0;
}
char *deps_to_str(const dep *d) { return dep_str(d); }

void conv_init(converted *c, const char *fmt) {
    memset(c, 0, sizeof *c);
    c->fmt = fmt;
    c->scripts = jobj();
}
void conv_free(converted *c) {
    char **s[] = {&c->name, &c->version, &c->arch, &c->tree, &c->summary, &c->description, &c->license,
                  &c->url, &c->packager, &c->source_version};
    for (size_t i = 0; i < sizeof s / sizeof *s; i++) free(*s[i]);
    dl_free(&c->depends);
    dl_free(&c->optdepends);
    dl_free(&c->conflicts);
    sv_free(&c->provides);
    sv_free(&c->replaces);
    sv_free(&c->backup);
    sv_free(&c->warnings);
    json_free(c->scripts);
}

/* --- Versionen / Namen ------------------------------------------------------------- */
char *norm_version(const char *in) {
    char *v = xstrdup(in), *s = str_trim(v), *epoch = NULL;
    char *colon = strchr(s, ':');
    if (colon && colon > s) {
        int dig = 1;
        for (char *p = s; p < colon; p++)
            if (*p < '0' || *p > '9') dig = 0;
        if (dig) {
            epoch = xstrndup(s, (size_t)(colon - s));
            s = colon + 1;
        }
    }
    char *rel = NULL, *dash = strrchr(s, '-');
    if (dash) {
        rel = xstrdup(dash + 1);
        *dash = 0;
    }
    sbuf b = {0};
    sb_init(&b);
    for (char *p = s; *p; p++) {
        if (*p == '-') sb_addc(&b, '_');
        else if (*p == '~') {
            char n = p[1];
            if (!((n >= 'a' && n <= 'z') || (n >= 'A' && n <= 'Z'))) sb_add(&b, "pre");
        } else if (*p == '^') sb_add(&b, ".0.");
        else sb_addc(&b, *p);
    }
    char *ver = sb_take(&b);
    if (rel)
        for (char *p = rel; *p; p++)
            if (strchr("~^:-", *p)) *p = '.';
    char *out = xasprintf("%s%s%s%s%s", epoch ? epoch : "", epoch ? ":" : "", ver, rel && *rel ? "-" : "",
                          rel && *rel ? rel : "");
    free(epoch);
    free(rel);
    free(ver);
    free(v);
    return out;
}

char *norm_name(const char *n) {
    char *s = xstrdup(n);
    str_lower(s);
    for (char *p = s; *p; p++)
        if (!((*p >= 'a' && *p <= 'z') || (*p >= '0' && *p <= '9') || strchr("@._+-", *p))) *p = '-';
    return s;
}

char *wrap_script(const char *body, const char *args) {
    size_t n = strlen(body);
    while (n && (body[n - 1] == '\n' || body[n - 1] == ' ')) n--;
    if (strncmp(body, "#!", 2)) return xasprintf("set -- %s\n%.*s\n", args, (int)n, body);
    return xasprintf("f=$(mktemp)\ntrap 'rm -f \"$f\"' EXIT\ncat >\"$f\" <<'__SYP_SCRIPT_EOF__'\n%.*s\n"
                     "__SYP_SCRIPT_EOF__\nchmod 700 \"$f\"\n\"$f\" %s\n",
                     (int)n, body, args);
}

/* --- Entpacken ---------------------------------------------------------------------- */
int extract_archive_mem(const char *data, size_t len, const char *dest, strvec *warnings, const char *const *skip) {
    struct archive *a = archive_read_new(), *w = archive_write_disk_new();
    archive_read_support_filter_all(a);
    archive_read_support_format_tar(a);
    archive_read_support_format_cpio(a);
    archive_write_disk_set_options(w, ARCHIVE_EXTRACT_PERM | ARCHIVE_EXTRACT_SECURE_NODOTDOT |
                                          ARCHIVE_EXTRACT_SECURE_SYMLINKS);
    if (archive_read_open_memory(a, data, len) != ARCHIVE_OK) {
        set_error("Archiv unlesbar: %s", archive_error_string(a));
        archive_read_free(a);
        archive_write_free(w);
        return -1;
    }
    struct archive_entry *e;
    int r, rc = 0;
    mkdir_p(dest, 0755);
    while ((r = archive_read_next_header(a, &e)) == ARCHIVE_OK) {
        const char *name = archive_entry_pathname(e);
        while (!strncmp(name, "./", 2)) name += 2;
        while (*name == '/') name++;
        int sk = !*name;
        for (const char *const *s = skip; s && *s && !sk; s++)
            if (!strcmp(name, *s)) sk = 1;
        mode_t t = archive_entry_filetype(e);
        if (!sk && t != AE_IFREG && t != AE_IFDIR && t != AE_IFLNK) {
            if (warnings) sv_push_own(warnings, xasprintf("Gerätedatei/FIFO übersprungen: /%s", name));
            sk = 1;
        }
        if (sk) {
            archive_read_data_skip(a);
            continue;
        }
        mode_t perm = archive_entry_perm(e);
        if (perm & 06000 && warnings) sv_push_own(warnings, xasprintf("setuid/setgid entfernt: /%s", name));
        archive_entry_set_perm(e, perm & ~(mode_t)(S_ISUID | S_ISGID | S_ISVTX | S_IWGRP | S_IWOTH));
        char *full = path_join(dest, name);
        archive_entry_set_pathname(e, full);
        const char *hl = archive_entry_hardlink(e);
        if (hl) {
            while (!strncmp(hl, "./", 2)) hl += 2;
            char *h = path_join(dest, hl);
            archive_entry_set_hardlink(e, h);
            free(h);
        }
        free(full);
        if (archive_write_header(w, e) != ARCHIVE_OK) {
            set_error("Entpacken: %s", archive_error_string(w));
            rc = -1;
            break;
        }
        const void *buf;
        size_t sz;
        la_int64_t off;
        while (archive_read_data_block(a, &buf, &sz, &off) == ARCHIVE_OK) archive_write_data_block(w, buf, sz, off);
        archive_write_finish_entry(w);
    }
    if (!rc && r != ARCHIVE_EOF) {
        set_error("Archiv beschädigt: %s", archive_error_string(a));
        rc = -1;
    }
    archive_read_free(a);
    archive_write_free(w);
    return rc;
}

/* --- ELF ---------------------------------------------------------------------------- */
void elf_dynamic(const char *path, strvec *needed, char **soname) {
    *soname = NULL;
    int fd = open(path, O_RDONLY | O_CLOEXEC);
    if (fd < 0) return;
    struct stat st;
    if (fstat(fd, &st) || st.st_size < 64) {
        close(fd);
        return;
    }
    unsigned char *m = mmap(NULL, (size_t)st.st_size, PROT_READ, MAP_PRIVATE, fd, 0);
    close(fd);
    if (m == MAP_FAILED) return;
    size_t sz = (size_t)st.st_size;
    if (memcmp(m, ELFMAG, SELFMAG) || m[EI_DATA] != ELFDATA2LSB) goto out;
#define CHK(off, len) ((size_t)(off) + (size_t)(len) <= sz)
    uint64_t loads_va[64], loads_off[64], loads_sz[64];
    int nl = 0;
    uint64_t dyn_off = 0, dyn_sz = 0;
    int is64 = m[EI_CLASS] == ELFCLASS64;
    if (is64) {
        Elf64_Ehdr *h = (Elf64_Ehdr *)m;
        for (int i = 0; i < h->e_phnum; i++) {
            uint64_t o = h->e_phoff + (uint64_t)i * h->e_phentsize;
            if (!CHK(o, sizeof(Elf64_Phdr))) goto out;
            Elf64_Phdr *p = (Elf64_Phdr *)(m + o);
            if (p->p_type == PT_LOAD && nl < 64) {
                loads_va[nl] = p->p_vaddr;
                loads_off[nl] = p->p_offset;
                loads_sz[nl++] = p->p_filesz;
            } else if (p->p_type == PT_DYNAMIC) {
                dyn_off = p->p_offset;
                dyn_sz = p->p_filesz;
            }
        }
    } else {
        Elf32_Ehdr *h = (Elf32_Ehdr *)m;
        for (int i = 0; i < h->e_phnum; i++) {
            uint64_t o = h->e_phoff + (uint64_t)i * h->e_phentsize;
            if (!CHK(o, sizeof(Elf32_Phdr))) goto out;
            Elf32_Phdr *p = (Elf32_Phdr *)(m + o);
            if (p->p_type == PT_LOAD && nl < 64) {
                loads_va[nl] = p->p_vaddr;
                loads_off[nl] = p->p_offset;
                loads_sz[nl++] = p->p_filesz;
            } else if (p->p_type == PT_DYNAMIC) {
                dyn_off = p->p_offset;
                dyn_sz = p->p_filesz;
            }
        }
    }
    if (!dyn_sz || !CHK(dyn_off, dyn_sz)) goto out;
    uint64_t strtab = 0;
    int have_str = 0;
    size_t esz = is64 ? sizeof(Elf64_Dyn) : sizeof(Elf32_Dyn);
    for (uint64_t o = dyn_off; o + esz <= dyn_off + dyn_sz; o += esz) {
        int64_t tag = is64 ? ((Elf64_Dyn *)(m + o))->d_tag : ((Elf32_Dyn *)(m + o))->d_tag;
        uint64_t val = is64 ? ((Elf64_Dyn *)(m + o))->d_un.d_val : ((Elf32_Dyn *)(m + o))->d_un.d_val;
        if (tag == DT_NULL) break;
        if (tag == DT_STRTAB) {
            strtab = val;
            have_str = 1;
        }
    }
    if (!have_str) goto out;
    uint64_t stroff = 0;
    int found = 0;
    for (int i = 0; i < nl; i++)
        if (loads_va[i] <= strtab && strtab < loads_va[i] + loads_sz[i]) {
            stroff = loads_off[i] + strtab - loads_va[i];
            found = 1;
        }
    if (!found) goto out;
    for (uint64_t o = dyn_off; o + esz <= dyn_off + dyn_sz; o += esz) {
        int64_t tag = is64 ? ((Elf64_Dyn *)(m + o))->d_tag : ((Elf32_Dyn *)(m + o))->d_tag;
        uint64_t val = is64 ? ((Elf64_Dyn *)(m + o))->d_un.d_val : ((Elf32_Dyn *)(m + o))->d_un.d_val;
        if (tag == DT_NULL) break;
        if (tag != DT_NEEDED && tag != DT_SONAME) continue;
        uint64_t so = stroff + val;
        if (so >= sz) continue;
        const char *s = (const char *)m + so;
        size_t maxl = sz - so, l = strnlen(s, maxl);
        if (l == maxl) continue;
        if (tag == DT_NEEDED) sv_push(needed, s);
        else if (!*soname) *soname = xstrdup(s);
    }
out:
    munmap(m, sz);
}

/* --- Pfade, Audit, Kategorien -------------------------------------------------------- */
static const char *RELOCATE[][2] = {{"bin", "usr/bin"},
                                    {"sbin", "usr/bin"},
                                    {"usr/sbin", "usr/bin"},
                                    {"lib64", "usr/lib"},
                                    {"lib/x86_64-linux-gnu", "usr/lib"},
                                    {"lib", "usr/lib"},
                                    {"usr/lib64", "usr/lib"},
                                    {"usr/lib/x86_64-linux-gnu", "usr/lib"},
                                    {"usr/lib/aarch64-linux-gnu", "usr/lib"}};

static void move_tree(const char *src, const char *dst, const char *tree, strvec *warn) {
    struct dirent **list;
    int n = scandir(src, &list, NULL, alphasort);
    for (int i = 0; i < n; i++) {
        const char *d = list[i]->d_name;
        if (strcmp(d, ".") && strcmp(d, "..")) {
            char *s = path_join(src, d), *t = path_join(dst, d);
            struct stat st;
            lstat(s, &st);
            if (S_ISDIR(st.st_mode)) {
                move_tree(s, t, tree, warn);
            } else {
                mkdir_p(dst, 0755);
                if (path_exists(t)) {
                    sv_push_own(warn, xasprintf("Pfadkollision bei Umzug: /%s -> /%s", s + strlen(tree) + 1,
                                                t + strlen(tree) + 1));
                } else {
                    rename(s, t);
                }
            }
            free(s);
            free(t);
        }
        free(list[i]);
    }
    if (n >= 0) free(list);
}

static void relocate(const char *tree, strvec *warn) {
    for (size_t i = 0; i < sizeof RELOCATE / sizeof *RELOCATE; i++) {
        char *src = path_join(tree, RELOCATE[i][0]);
        struct stat st;
        if (lstat(src, &st) || S_ISLNK(st.st_mode) || !S_ISDIR(st.st_mode)) {
            free(src);
            continue;
        }
        char *dst = path_join(tree, RELOCATE[i][1]);
        move_tree(src, dst, tree, warn);
        rm_rf(src);
        sv_push_own(warn, xasprintf("Pfade verschoben: /%s/ -> /%s/", RELOCATE[i][0], RELOCATE[i][1]));
        free(src);
        free(dst);
    }
}

static void audit(const char *tree, strvec *warn) {
    static const char *ok[] = {"usr", "etc", "opt", "var", "srv", NULL};
    struct dirent **list;
    int n = scandir(tree, &list, NULL, alphasort);
    sbuf odd = {0};
    sb_init(&odd);
    for (int i = 0; i < n; i++) {
        const char *d = list[i]->d_name;
        if (strcmp(d, ".") && strcmp(d, "..")) {
            int good = 0;
            for (const char **k = ok; *k; k++)
                if (!strcmp(*k, d)) good = 1;
            if (!good) sb_addf(&odd, "%s%s", odd.len ? ", " : "", d);
        }
        free(list[i]);
    }
    if (n >= 0) free(list);
    if (odd.len) sv_push_own(warn, xasprintf("Ungewöhnliche Top-Level-Verzeichnisse: %s", odd.s));
    free(odd.s);
    char *opt = path_join(tree, "opt");
    if (path_exists(opt)) sv_push(warn, "Paket installiert nach /opt (typisch für Fremdsoftware)");
    free(opt);
}

static void scan_elf(const char *dir, strvec *needed, strvec *provided) {
    struct dirent **list;
    int n = scandir(dir, &list, NULL, alphasort);
    for (int i = 0; i < n; i++) {
        const char *d = list[i]->d_name;
        if (strcmp(d, ".") && strcmp(d, "..")) {
            char *p = path_join(dir, d);
            struct stat st;
            if (lstat(p, &st) == 0) {
                if (S_ISDIR(st.st_mode)) scan_elf(p, needed, provided);
                else if (S_ISREG(st.st_mode)) {
                    strvec nd = {0};
                    char *so = NULL;
                    elf_dynamic(p, &nd, &so);
                    for (size_t k = 0; k < nd.n; k++)
                        if (!sv_contains(needed, nd.v[k])) sv_push(needed, nd.v[k]);
                    sv_free(&nd);
                    if (so && !sv_contains(provided, so)) sv_push(provided, so);
                    free(so);
                } else if (S_ISLNK(st.st_mode) && strstr(d, ".so.") && !sv_contains(provided, d)) {
                    sv_push(provided, d);
                }
            }
            free(p);
        }
        free(list[i]);
    }
    if (n >= 0) free(list);
}

static jval *desktop_categories(const char *tree) {
    jval *cats = jarr();
    char *dir = path_join(tree, "usr/share/applications");
    struct dirent **list;
    int n = scandir(dir, &list, NULL, alphasort);
    for (int i = 0; i < n; i++) {
        if (str_ends(list[i]->d_name, ".desktop")) {
            char *p = path_join(dir, list[i]->d_name), *txt;
            if (read_file(p, &txt, NULL) == 0) {
                char *save = NULL;
                for (char *l = strtok_r(txt, "\n", &save); l; l = strtok_r(NULL, "\n", &save)) {
                    if (!str_starts(l, "Categories=")) continue;
                    char *s2 = NULL;
                    for (char *c = strtok_r(l + 11, ";", &s2); c; c = strtok_r(NULL, ";", &s2)) {
                        c = str_trim(c);
                        int dup = 0;
                        for (size_t k = 0; k < cats->n; k++)
                            if (!strcmp(cats->items[k]->s, c)) dup = 1;
                        if (*c && !dup) jpush(cats, jstr(c));
                    }
                }
                free(txt);
            }
            free(p);
        }
        free(list[i]);
    }
    if (n >= 0) free(list);
    free(dir);
    return cats;
}

static const char *ARCH_MAP[][2] = {{"amd64", "x86_64"}, {"x86_64", "x86_64"}, {"arm64", "aarch64"},
                                    {"aarch64", "aarch64"}, {"all", "any"},       {"noarch", "any"},
                                    {"any", "any"},         {"i386", "i686"},     {"i686", "i686"}};

static char *dep_base(const char *s) {
    size_t n = strcspn(s, "|><=");
    char *b = xstrndup(s, n);
    char *t = xstrdup(str_trim(b));
    free(b);
    return t;
}

char *convert_package(const char *path, const char *outdir, mapper *m, const seckey *signer, const conv_opts *o,
                      report **rep_out) {
    const char *bn = path_basename(path);
    int (*parser)(const char *, const char *, converted *) = NULL;
    const char *fmt = NULL;
    char head[8] = {0};
    FILE *f = fopen(path, "rb");
    if (!f) {
        set_error("%s: %s", path, strerror(errno));
        return NULL;
    }
    size_t hn = fread(head, 1, 8, f);
    fclose(f);
    if (str_ends(bn, ".deb") || (hn == 8 && !memcmp(head, "!<arch>\n", 8))) parser = parse_deb, fmt = "deb";
    else if (str_ends(bn, ".rpm") || (hn >= 4 && !memcmp(head, "\xed\xab\xee\xdb", 4))) parser = parse_rpm, fmt = "rpm";
    else if (strstr(bn, ".pkg.tar")) parser = parse_arch, fmt = "arch";
    else {
        set_error("Unbekanntes Paketformat: %s", bn);
        return NULL;
    }
    char *work = make_tempdir("syp-convert-");
    converted c;
    conv_init(&c, fmt);
    char *out = NULL;
    if (parser(path, work, &c)) goto done;
    if (o->license && *o->license) {
        free(c.license);
        c.license = xstrdup(o->license);
    }
    if (o->name && *o->name) {
        free(c.name);
        c.name = xstrdup(o->name);
    }
    for (size_t i = 0; i < sizeof ARCH_MAP / sizeof *ARCH_MAP; i++)
        if (c.arch && !strcmp(c.arch, ARCH_MAP[i][0])) {
            free(c.arch);
            c.arch = xstrdup(ARCH_MAP[i][1]);
            break;
        }
    relocate(c.tree, &c.warnings);
    audit(c.tree, &c.warnings);
    strvec needed = {0}, provided = {0};
    scan_elf(c.tree, &needed, &provided);
    report *rep = report_new(c.name, fmt);
    for (size_t i = 0; i < c.warnings.n; i++) report_warn(rep, c.warnings.v[i]);
    strvec provides = {0};
    for (size_t i = 0; i < c.provides.n; i++)
        if (!sv_contains(&provides, c.provides.v[i])) sv_push(&provides, c.provides.v[i]);
    sv_sort(&provided);
    for (size_t i = 0; i < provided.n; i++) {
        char *s = xasprintf("so:%s", provided.v[i]);
        if (!sv_contains(&provides, s)) sv_push(&provides, s);
        free(s);
    }
    strvec deps = map_deps(m, fmt, &c.depends, rep, 0, o->keep_versions);
    strvec missing_so = {0};
    sv_sort(&needed);
    for (size_t i = 0; i < needed.n; i++)
        if (!sv_contains(&provided, needed.v[i])) sv_push(&missing_so, needed.v[i]);
    strvec sodeps = map_sonames(m, &missing_so, rep);
    for (size_t i = 0; i < sodeps.n; i++) sv_push(&deps, sodeps.v[i]);
    jval *dj = jarr();
    for (size_t i = 0; i < deps.n; i++) {
        char *b = dep_base(deps.v[i]);
        int dup = 0;
        for (size_t k = 0; k < dj->n; k++)
            if (!strcmp(dj->items[k]->s, deps.v[i])) dup = 1;
        if (!dup && strcmp(b, c.name)) jpush(dj, jstr(deps.v[i]));
        free(b);
    }
    strvec optd = map_deps(m, fmt, &c.optdepends, rep, 1, 0);
    strvec conf = map_deps(m, fmt, &c.conflicts, rep, 1, 1);
    jval *cj = jarr();
    for (size_t i = 0; i < conf.n; i++)
        if (!str_starts(conf.v[i], c.name)) jpush(cj, jstr(conf.v[i]));
    if (c.scripts->n) {
        sbuf names = {0};
        sb_init(&names);
        strvec keys = {0};
        for (size_t i = 0; i < c.scripts->n; i++) sv_push(&keys, c.scripts->keys[i]);
        sv_sort(&keys);
        for (size_t i = 0; i < keys.n; i++) sb_addf(&names, "%s%s", i ? ", " : "", keys.v[i]);
        sv_free(&keys);
        char *w = xasprintf("Paket enthält Skripte (%s) – vor Freigabe prüfen", names.s);
        report_warn(rep, w);
        free(w);
        free(names.s);
        static const char *helpers[] = {"debconf", "dpkg-maintscript-helper", "update-alternatives",
                                        "deb-systemd-helper", "rpm ", "dpkg ", NULL};
        for (const char **h = helpers; *h; h++)
            for (size_t i = 0; i < c.scripts->n; i++)
                if (strstr(c.scripts->items[i]->s, *h)) {
                    char *hn2 = xstrdup(*h), *t = str_trim(hn2);
                    char *wm = xasprintf("Skript nutzt distro-spezifisches Werkzeug: %s", t);
                    report_warn(rep, wm);
                    free(wm);
                    free(hn2);
                    break;
                }
    }
    jval *info = jobj();
    jset(info, "name", jstr(c.name));
    jset(info, "version", jstr(c.version));
    jset(info, "arch", jstr(c.arch ? c.arch : "x86_64"));
    jset(info, "summary", jstr(c.summary ? c.summary : ""));
    jset(info, "description", jstr(c.description ? c.description : ""));
    jset(info, "license", jstr(c.license && *c.license ? c.license : "unknown"));
    jset(info, "url", jstr(c.url ? c.url : ""));
    jset(info, "packager", jstr(c.packager ? c.packager : ""));
    jset(info, "depends", dj);
    jset(info, "optdepends", jarr_from_sv(&optd));
    jset(info, "provides", jarr_from_sv(&provides));
    jset(info, "conflicts", cj);
    jset(info, "replaces", jarr_from_sv(&c.replaces));
    jval *bk = jarr();
    for (size_t i = 0; i < c.backup.n; i++) {
        const char *b = c.backup.v[i];
        while (*b == '/') b++;
        char *p = path_join(c.tree, b);
        struct stat st;
        if (lstat(p, &st) == 0 && S_ISREG(st.st_mode)) jpush(bk, jstr(b));
        free(p);
    }
    jset(info, "backup", bk);
    jset(info, "categories", desktop_categories(c.tree));
    jset(info, "scripts", jcopy(c.scripts));
    jval *origin = jobj();
    jset(origin, "format", jstr(fmt));
    jset(origin, "source_version", jstr(c.source_version ? c.source_version : ""));
    jset(origin, "scripts_reviewed", jbool(o->mark_reviewed || !c.scripts->n));
    jval *unres = jarr(), *ur = jgeta(rep->r, "unresolved");
    for (size_t i = 0; ur && i < ur->n; i++) jpush(unres, jstr(jgets(ur->items[i], "dep", "")));
    jset(origin, "unresolved", unres);
    jset(info, "origin", origin);
    if (!strcmp(jgets(info, "license", "unknown"), "unknown")) report_warn(rep, "Lizenz unbekannt – bitte manuell setzen (--license)");
    out = syp_build(c.tree, info, outdir, signer);
    if (out) {
        jset(rep->r, "output", jstr(out));
        jset(rep->r, "depends", jcopy(dj));
        *rep_out = rep;
    } else {
        report_free(rep);
    }
    json_free(info);
    sv_free(&needed);
    sv_free(&provided);
    sv_free(&provides);
    sv_free(&deps);
    sv_free(&missing_so);
    sv_free(&sodeps);
    sv_free(&optd);
    sv_free(&conf);
done:
    conv_free(&c);
    rm_rf(work);
    free(work);
    return out;
}
