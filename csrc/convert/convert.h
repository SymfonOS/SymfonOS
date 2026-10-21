/* convert.h – syp-convert: Fremdpakete (.deb, .rpm, .pkg.tar.*) nach .syp */
#ifndef SYP_CONVERT_H
#define SYP_CONVERT_H

#include "core.h"
#include "json.h"
#include "version.h"

typedef struct {
    dep *v;
    size_t n;
} deplist;
void dl_push(deplist *l, const char *name, depop op, const char *version); /* eine Alternative */
void dl_push_dep(deplist *l, dep d);
void dl_free(deplist *l);
char *deps_to_str(const dep *d);

typedef struct {
    const char *fmt; /* deb | rpm | arch */
    char *name, *version, *arch, *tree;
    char *summary, *description, *license, *url, *packager, *source_version;
    deplist depends, optdepends, conflicts;
    strvec provides, replaces, backup, warnings;
    jval *scripts; /* Objekt */
} converted;

void conv_init(converted *c, const char *fmt);
void conv_free(converted *c);

/* Parser (workdir leer, wird befüllt) */
int parse_deb(const char *path, const char *workdir, converted *c);
int parse_rpm(const char *path, const char *workdir, converted *c);
int parse_arch(const char *path, const char *workdir, converted *c);

/* Gemeinsames */
char *norm_version(const char *v);
char *norm_name(const char *n);
char *wrap_script(const char *body, const char *args);
int extract_archive_mem(const char *data, size_t len, const char *dest, strvec *warnings, const char *const *skip);
void elf_dynamic(const char *path, strvec *needed, char **soname);

/* Mapping */
typedef struct mapper mapper;
mapper *mapper_new(const char *base_map, const char *extra_map, ptrvec *available, database *db);
void mapper_free(mapper *m);

typedef struct {
    jval *r; /* Bericht als JSON-Objekt */
} report;
report *report_new(const char *pkg, const char *fmt);
void report_free(report *r);
void report_warn(report *r, const char *msg);
char *report_text(const report *r);
int report_ok(const report *r);

strvec map_deps(mapper *m, const char *fmt, const deplist *deps, report *rep, int optional, int keep_versions);
strvec map_sonames(mapper *m, const strvec *sonames, report *rep);

/* Gesamtablauf */
typedef struct {
    int mark_reviewed, keep_versions;
    const char *license, *name;
} conv_opts;
char *convert_package(const char *path, const char *outdir, mapper *m, const seckey *signer, const conv_opts *o,
                      report **rep_out);

#endif
