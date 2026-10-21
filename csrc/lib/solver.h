/* solver.h – Abhängigkeitsauflösung per Backtracking */
#ifndef SYP_SOLVER_H
#define SYP_SOLVER_H

#include "pkg.h"

typedef struct {
    ptrvec install; /* pkg* (aus available/lokal; nicht besitzend) */
    strvec remove;
    hmap reasons;   /* name -> "explicit" | "dep" (statische Strings) */
} plan;

typedef struct {
    ptrvec available;   /* pkg* */
    hmap *installed;    /* name -> pkg* */
    hmap by_provide;    /* name -> ptrvec* aller verfügbaren Anbieter */
    hmap inst_prov;     /* name -> ptrvec* installierter Anbieter */
    strvec problems;
    long steps;
} solver;

void solver_init(solver *s, ptrvec *available, hmap *installed);
void solver_free(solver *s);
void plan_init(plan *p);
void plan_free(plan *p);
int plan_empty(const plan *p);

/* -1 bei Fehler; Probleme in s->problems */
int solve_install(solver *s, char **requests, size_t nreq, ptrvec *local, plan *out);
int solve_upgrade(solver *s, plan *out);
int solve_remove(solver *s, char **names, size_t n, int cascade, plan *out);
void solve_orphans(solver *s, strvec *out);
/* verletzte Abhängigkeiten der Installation (für doctor) */
void solver_broken_installed(solver *s, strvec *out);

#endif
