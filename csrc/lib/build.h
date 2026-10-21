/* build.h – Pakete aus SYPBUILD.toml-Rezepten bauen */
#ifndef SYP_BUILD_H
#define SYP_BUILD_H

#include "crypto.h"
#include "json.h"

typedef struct {
    int nocheck;
    int update_sums; /* fehlende sha256 ermitteln und ins Rezept schreiben */
    int keep_work;
    const char *srccache; /* Download-Cache für Quellen */
    const char *chroot;   /* bauen in dieser Wurzel (Host steuert, Schritte laufen per chroot) */
} build_opts;

/* Baut ein Rezept; gibt Pfad des .syp zurück (NULL + set_error) */
char *build_recipe(const char *recipe, const char *outdir, const seckey *signer, const build_opts *o);
jval *recipe_load(const char *recipe); /* geparstes TOML */
char *recipe_pkgfile(const jval *recipe); /* erwarteter Dateiname des Pakets */
int recipe_fill_sums(const char *recipe, const char *srccache);
int recipe_fetch(const char *recipe, const char *dir, int quiet); /* Quellen laden + prüfen */

#endif
