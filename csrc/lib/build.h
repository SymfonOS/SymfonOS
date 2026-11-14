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

/* Revision der Bauumgebung (Vorlagen, Standardoptionen, Bauschritte). Wird erhöht, wenn sich der Inhalt
 * gebauter Pakete bei gleichem Rezept ändert; steht als origin.build_rev im Paket (fehlt: 1). buildall baut
 * Pakete mit kleinerer Revision neu, damit das Repo dem entspricht, was die Rezepte heute ergeben.
 *   2: Bibliotheksverzeichnis in Meson/Autotools vorgegeben (usr/lib), Bauschritte ohne Netz */
#define SYP_BUILD_REV 2

/* Baut ein Rezept; gibt Pfad des .syp zurück (NULL + set_error) */
char *build_recipe(const char *recipe, const char *outdir, const seckey *signer, const build_opts *o);
jval *recipe_load(const char *recipe); /* geparstes TOML */
char *recipe_pkgfile(const jval *recipe); /* erwarteter Dateiname des Pakets */
int recipe_fill_sums(const char *recipe, const char *srccache);
int recipe_fetch(const char *recipe, const char *dir, int quiet); /* Quellen laden + prüfen */

#endif
