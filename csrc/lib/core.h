/* core.h – Konfiguration, Repositories, Datenbank */
#ifndef SYP_CORE_H
#define SYP_CORE_H

#include <sqlite3.h>

#include "pkg.h"

/* --- Konfiguration: <root>/etc/syp/syp.toml ----------------------------------- */
typedef struct {
    char *name, *url;
    int priority, enabled;
} repoconf;

typedef struct {
    char *root;
    char *arch;
    repoconf *repos;
    size_t nrepos;
    int snapshots;
    char *snapshot_backend;
    int keep_cache, run_scripts, require_signatures;
} config;

int config_load(const char *root, config *c);
char *cfg_path(const config *c, const char *rel); /* root + rel */
int cfg_is_root(const config *c);                 /* root == "/" */

/* --- Repositories -------------------------------------------------------------- */
int fetch_url(const char *url, const char *dest);   /* http(s)://, file://, Pfad */
int repo_sync(const config *c, const keyring *kr, int quiet);
long long index_expiry(void);
void repo_load_synced(const config *c, ptrvec *out); /* pkg* */
char *repo_fetch_package(const config *c, const pkg *p); /* Pfad im Cache */
int repo_build_index(const char *repo_dir, const char *name, const char *arch, const seckey *signer);
/* Index eines lokalen Repos laden; mit pubkey_path nur bei gültiger Signatur dieses Schlüssels */
jval *repo_show_index(const char *repo_dir, const char *pubkey_path);
char *repo_add_to_pool(const char *repo_dir, const char *pkg_path);
int repo_index_add(const char *repo_dir, const char *name, const char *arch, const seckey *signer,
                   const char *pkgfile);
pkg *local_package(const char *path); /* NULL + set_error */

/* zstd-Hilfen */
int zstd_compress_buf(const char *in, size_t n, int level, char **out, size_t *outn);
int zstd_decompress_buf(const char *in, size_t n, char **out, size_t *outn);

/* --- Datenbank ------------------------------------------------------------------- */
typedef struct {
    sqlite3 *db;
    int readonly;
} database;

int db_open(const char *path, database *d);
void db_close(database *d);
void db_installed(database *d, hmap *out);           /* name -> pkg* (installed=1) */
jval *db_state(database *d);                          /* {name: version} */
jval *db_files_of(database *d, const char *name);     /* [{path,type,sha256}] */
void db_owners(database *d, const char *path, strvec *out);
int db_put(database *d, const jval *info, const jval *files, const char *reason);
int db_delete(database *d, const char *name);
long long db_tx_begin(database *d, const char *cmdline, const char *snap_pre);
void db_tx_end(database *d, long long tx, const char *status, const char *snap_post);
jval *db_history(database *d, int limit);              /* neueste zuerst */
jval *db_tx(database *d, long long id);

#endif
