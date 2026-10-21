/* pkg.h – Paketmetadaten und das .syp-Dateiformat
 *
 * Ein .syp ist ein unkomprimiertes tar mit genau diesen Einträgen (feste Reihenfolge):
 *   .SYPINFO.json   Metadaten          .FILES.json   Dateiliste mit sha256
 *   data.tar.zst    Nutzdaten          .SIGNATURE.json  Ed25519 über den Digest der drei
 */
#ifndef SYP_PKG_H
#define SYP_PKG_H

#include "crypto.h"
#include "json.h"
#include "version.h"

#define SYP_FORMAT 1

typedef struct pkg {
    jval *info;                  /* besitzt */
    const char *name, *version;  /* zeigen in info */
    dep *deps;
    size_t ndeps;
    atom *conflicts;
    size_t nconf;
    char **prov_name, **prov_ver; /* inkl. name=version */
    size_t nprov;
    /* Repository/Datei */
    char *repo, *file, *sha256, *local_path;
    long long size;
    int priority;
    /* installiert */
    int installed;
    char *reason;
    long long installed_at;
} pkg;

int info_validate(const jval *info);                 /* -1 + set_error */
jval *info_new(const char *name, const char *version);
void info_defaults(jval *info);                       /* fehlende Felder ergänzen */
pkg *pkg_new(jval *info);                             /* übernimmt info; NULL bei Fehler */
void pkg_free(pkg *p);
int pkg_satisfies(const pkg *p, const atom *a);
int pkg_conflicts(const pkg *a, const pkg *b);
char *pkg_filename(const jval *info);
strvec info_list(const jval *info, const char *key);  /* Kopie einer String-Liste */

/* .syp lesen */
typedef struct {
    char *path;
    jval *info, *files, *sig;
    char *info_b, *files_b, *data_b;
    size_t info_n, files_n, data_n;
} sypfile;

int syp_open(const char *path, sypfile *f);
int syp_open_meta(const char *path, sypfile *f); /* ohne Nutzdaten (Index, Anzeige) */
int syp_resign(const char *path, const seckey *k); /* Signatur ersetzen */
void syp_close(sypfile *f);
const pubkey *syp_verify(const sypfile *f, const keyring *kr);
int syp_extract(const sypfile *f, const char *dest);

/* .syp schreiben */
jval *scan_tree(const char *tree);   /* Dateiliste */
char *syp_build(const char *tree, jval *info, const char *outdir, const seckey *signer); /* Pfad */

#endif
