/* version.h – Versionsvergleich (rpm/pacman-kompatibel) und Abhängigkeitsausdrücke */
#ifndef SYP_VERSION_H
#define SYP_VERSION_H

#include "util.h"

int rpmvercmp(const char *a, const char *b);
int vercmp(const char *a, const char *b); /* epoch:ver-rel; rel nur wenn beide eine haben */

typedef enum { OP_NONE, OP_EQ, OP_GE, OP_LE, OP_LT, OP_GT } depop;

typedef struct {
    char *name;
    depop op;
    char *version;
} atom;

typedef struct {
    atom *alt;
    size_t n;
} dep;

int atom_parse(const char *s, atom *out); /* -1 bei Syntaxfehler */
int dep_parse(const char *s, dep *out);
void dep_free(dep *d);
char *dep_str(const dep *d);
char *atom_str(const atom *a);
int atom_matches_version(const atom *a, const char *version); /* version NULL = unversioniert */
void provide_parts(const char *s, char **name, char **version);
const char *op_str(depop op);

#endif
