/* harden.c – Härtungsprüfung gebauter ELF-Dateien */
#define _GNU_SOURCE
#include "harden.h"

#include <dirent.h>
#include <elf.h>
#include <fcntl.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>

/* Bereiche ohne normale Benutzerprogramme (Kernel, Firmware, Bootloader-Module, Objektdateien) */
static int exempt(const char *rel) {
    static const char *pre[] = {"usr/lib/modules/", "usr/lib/firmware/", "usr/lib/grub/", "usr/share/", "boot/",
                                "usr/lib/debug/", NULL};
    for (const char **p = pre; *p; p++)
        if (str_starts(rel, *p)) return 1;
    return str_ends(rel, ".o") || str_ends(rel, ".a") || str_ends(rel, ".ko") || str_ends(rel, ".ko.zst") ||
           str_ends(rel, ".efi");
}

typedef struct {
    int is_elf, is_dyn, has_interp, has_dynamic, relro, now, execstack, textrel, badrpath, ssp, fortify, has_text;
} elfinfo;

static int bad_rpath(const char *rp) {
    /* leere Einträge, ".", relative oder Build-Pfade erlauben Bibliotheks-Injektion */
    char *c = xstrdup(rp), *save = NULL;
    int bad = 0;
    if (!*rp) bad = 1;
    for (char *t = strtok_r(c, ":", &save); t && !bad; t = strtok_r(NULL, ":", &save))
        if (!*t || (t[0] != '/' && !str_starts(t, "$ORIGIN") && !str_starts(t, "${ORIGIN}")) || strstr(t, "/tmp/") ||
            strstr(t, "/var/tmp/") || strstr(t, "/build/"))
            bad = 1;
    free(c);
    return bad;
}

static void inspect(const char *path, elfinfo *e) {
    memset(e, 0, sizeof *e);
    int fd = open(path, O_RDONLY | O_CLOEXEC);
    if (fd < 0) return;
    struct stat st;
    if (fstat(fd, &st) || st.st_size < (off_t)sizeof(Elf64_Ehdr)) {
        close(fd);
        return;
    }
    size_t sz = (size_t)st.st_size;
    unsigned char *m = mmap(NULL, sz, PROT_READ, MAP_PRIVATE, fd, 0);
    close(fd);
    if (m == MAP_FAILED) return;
    if (memcmp(m, ELFMAG, SELFMAG) || m[EI_CLASS] != ELFCLASS64 || m[EI_DATA] != ELFDATA2LSB) goto out;
    Elf64_Ehdr *h = (Elf64_Ehdr *)m;
    if (h->e_type != ET_EXEC && h->e_type != ET_DYN) goto out;
    e->is_elf = 1;
    e->is_dyn = h->e_type == ET_DYN;
    Elf64_Off dyn_off = 0;
    Elf64_Xword dyn_sz = 0;
    for (int i = 0; i < h->e_phnum; i++) {
        size_t o = h->e_phoff + (size_t)i * h->e_phentsize;
        if (o + sizeof(Elf64_Phdr) > sz) goto out;
        Elf64_Phdr *p = (Elf64_Phdr *)(m + o);
        if (p->p_type == PT_INTERP) e->has_interp = 1;
        if (p->p_type == PT_GNU_RELRO) e->relro = 1;
        if (p->p_type == PT_GNU_STACK && (p->p_flags & PF_X)) e->execstack = 1;
        if (p->p_type == PT_DYNAMIC) {
            dyn_off = p->p_offset;
            dyn_sz = p->p_filesz;
        }
        if (p->p_type == PT_LOAD && (p->p_flags & PF_X)) e->has_text = 1;
    }
    if (dyn_sz && dyn_off + dyn_sz <= sz) {
        e->has_dynamic = 1;
        Elf64_Addr strtab = 0;
        Elf64_Xword rpath = (Elf64_Xword)-1, runpath = (Elf64_Xword)-1;
        for (Elf64_Dyn *d = (Elf64_Dyn *)(m + dyn_off); (unsigned char *)(d + 1) <= m + dyn_off + dyn_sz; d++) {
            if (d->d_tag == DT_NULL) break;
            if (d->d_tag == DT_BIND_NOW) e->now = 1;
            if (d->d_tag == DT_FLAGS && (d->d_un.d_val & DF_BIND_NOW)) e->now = 1;
            if (d->d_tag == DT_FLAGS && (d->d_un.d_val & DF_TEXTREL)) e->textrel = 1;
            if (d->d_tag == DT_FLAGS_1 && (d->d_un.d_val & DF_1_NOW)) e->now = 1;
            if (d->d_tag == DT_TEXTREL) e->textrel = 1;
            if (d->d_tag == DT_STRTAB) strtab = d->d_un.d_ptr;
            if (d->d_tag == DT_RPATH) rpath = d->d_un.d_val;
            if (d->d_tag == DT_RUNPATH) runpath = d->d_un.d_val;
        }
        /* Stringtabelle über PT_LOAD in Dateioffset umrechnen */
        size_t stroff = 0;
        for (int i = 0; strtab && i < h->e_phnum; i++) {
            Elf64_Phdr *p = (Elf64_Phdr *)(m + h->e_phoff + (size_t)i * h->e_phentsize);
            if (p->p_type == PT_LOAD && p->p_vaddr <= strtab && strtab < p->p_vaddr + p->p_filesz)
                stroff = p->p_offset + (strtab - p->p_vaddr);
        }
        Elf64_Xword rp[2] = {rpath, runpath};
        for (int k = 0; k < 2 && stroff; k++)
            if (rp[k] != (Elf64_Xword)-1 && stroff + rp[k] < sz && bad_rpath((const char *)m + stroff + rp[k]))
                e->badrpath = 1;
    }
    /* Symbole: __stack_chk_fail (Stack-Protector), *_chk (FORTIFY_SOURCE) */
    if (h->e_shoff && h->e_shoff + (size_t)h->e_shnum * sizeof(Elf64_Shdr) <= sz) {
        Elf64_Shdr *sh = (Elf64_Shdr *)(m + h->e_shoff);
        for (int i = 0; i < h->e_shnum; i++) {
            if (sh[i].sh_type != SHT_DYNSYM || sh[i].sh_link >= h->e_shnum) continue;
            Elf64_Shdr *strs = &sh[sh[i].sh_link];
            if (sh[i].sh_offset + sh[i].sh_size > sz || strs->sh_offset + strs->sh_size > sz) continue;
            Elf64_Sym *sym = (Elf64_Sym *)(m + sh[i].sh_offset);
            size_t n = sh[i].sh_size / sizeof(Elf64_Sym);
            for (size_t k = 0; k < n; k++) {
                if (sym[k].st_name >= strs->sh_size) continue;
                const char *nm = (const char *)m + strs->sh_offset + sym[k].st_name;
                if (!strcmp(nm, "__stack_chk_fail")) e->ssp = 1;
                size_t l = strnlen(nm, 128);
                if (l > 6 && !strncmp(nm, "__", 2) && !strcmp(nm + l - 4, "_chk")) e->fortify = 1;
            }
        }
    }
out:
    munmap(m, sz);
}

static void walk(const char *root, const char *rel, harden_report *r) {
    char *dir = rel ? path_join(root, rel) : xstrdup(root);
    struct dirent **list;
    int n = scandir(dir, &list, NULL, alphasort);
    for (int i = 0; i < n; i++) {
        const char *d = list[i]->d_name;
        if (strcmp(d, ".") && strcmp(d, "..")) {
            char *r2 = rel ? xasprintf("%s/%s", rel, d) : xstrdup(d), *full = path_join(root, r2);
            struct stat st;
            if (lstat(full, &st) == 0) {
                /* Für alle beschreibbar: bei Verzeichnissen nur mit Sticky-Bit zulässig (wie /tmp) */
                if ((S_ISDIR(st.st_mode) || S_ISREG(st.st_mode)) && (st.st_mode & S_IWOTH) &&
                    !(S_ISDIR(st.st_mode) && (st.st_mode & S_ISVTX))) {
                    r->worldwrite++;
                    sv_push_own(&r->problems, xasprintf("/%s: für alle beschreibbar (Modus %04o)", r2,
                                                        (unsigned)(st.st_mode & 07777)));
                }
                if (S_ISREG(st.st_mode) && (st.st_mode & (S_ISUID | S_ISGID))) {
                    r->suid++;
                    sv_push_own(&r->suids, xasprintf("/%s (%s%s)", r2, (st.st_mode & S_ISUID) ? "setuid" : "",
                                                     (st.st_mode & S_ISGID) ? ((st.st_mode & S_ISUID) ? "+setgid" : "setgid") : ""));
                }
                if (S_ISDIR(st.st_mode)) walk(root, r2, r);
                else if (S_ISREG(st.st_mode) && !exempt(r2)) {
                    elfinfo e;
                    inspect(full, &e);
                    if (e.is_elf && e.has_text) {
                        r->elf++;
                        int exe = e.has_interp, lib = e.is_dyn && !e.has_interp;
                        if (!e.is_dyn && e.has_dynamic) {
                            r->nopie++;
                            sv_push_own(&r->problems, xasprintf("/%s: kein PIE (fest geladene Adresse)", r2));
                        }
                        if (e.has_dynamic && !e.relro) {
                            r->norelro++;
                            sv_push_own(&r->problems, xasprintf("/%s: kein RELRO", r2));
                        } else if (e.has_dynamic && !e.now && (exe || lib)) {
                            r->nonow++;
                            sv_push_own(&r->problems, xasprintf("/%s: nur teilweises RELRO (BIND_NOW fehlt)", r2));
                        }
                        if (e.execstack) {
                            r->execstack++;
                            sv_push_own(&r->problems, xasprintf("/%s: ausführbarer Stack", r2));
                        }
                        if (e.textrel) {
                            r->textrel++;
                            sv_push_own(&r->problems, xasprintf("/%s: Text-Relokationen", r2));
                        }
                        if (e.badrpath) {
                            r->badrpath++;
                            sv_push_own(&r->problems, xasprintf("/%s: unsicherer RPATH/RUNPATH", r2));
                        }
                        if (e.has_dynamic && !e.ssp) r->nossp++;
                        if (e.has_dynamic && !e.fortify) r->nofortify++;
                    }
                }
            }
            free(r2);
            free(full);
        }
        free(list[i]);
    }
    if (n >= 0) free(list);
    free(dir);
}

int harden_check(const char *destdir, const char *mode, harden_report *r) {
    memset(r, 0, sizeof *r);
    if (mode && !strcmp(mode, "off")) return 0;
    walk(destdir, NULL, r);
    int strict = !mode || !strcmp(mode, "strict");
    int critical = r->nopie + r->execstack + r->textrel + r->badrpath + r->norelro + r->worldwrite;
    if (r->elf)
        printf("==> Härtung: %d ELF-Dateien · ohne PIE %d · ohne RELRO %d · ohne BIND_NOW %d · ausf. Stack %d · "
               "TEXTREL %d · unsicherer RPATH %d\n",
               r->elf, r->nopie, r->norelro, r->nonow, r->execstack, r->textrel, r->badrpath);
    for (size_t i = 0; i < r->problems.n && i < 20; i++) printf("    ! %s\n", r->problems.v[i]);
    if (r->worldwrite) printf("==> Dateirechte: %d Einträge für alle beschreibbar\n", r->worldwrite);
    for (size_t i = 0; i < r->suids.n; i++) printf("==> Privilegiert: %s\n", r->suids.v[i]);
    if (strict && critical) {
        set_error("Härtungsprüfung fehlgeschlagen (%d kritische Befunde; Ausnahme nur per [options] hardening = "
                  "\"warn\" mit Begründung)",
                  critical);
        return -1;
    }
    return 0;
}

jval *harden_json(const harden_report *r) {
    jval *o = jobj();
    jset(o, "elf", jint(r->elf));
    jset(o, "no_pie", jint(r->nopie));
    jset(o, "no_relro", jint(r->norelro));
    jset(o, "no_bind_now", jint(r->nonow));
    jset(o, "exec_stack", jint(r->execstack));
    jset(o, "textrel", jint(r->textrel));
    jset(o, "bad_rpath", jint(r->badrpath));
    jset(o, "no_stack_protector", jint(r->nossp));
    jset(o, "no_fortify", jint(r->nofortify));
    jset(o, "world_writable", jint(r->worldwrite));
    jval *su = jarr();
    for (size_t i = 0; i < r->suids.n; i++) jpush(su, jstr(r->suids.v[i]));
    jset(o, "setuid", su);
    return o;
}

void harden_free(harden_report *r) {
    sv_free(&r->problems);
    sv_free(&r->suids);
}
