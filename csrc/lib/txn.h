/* txn.h – Transaktionen, Trigger, Snapshots, Rollback */
#ifndef SYP_TXN_H
#define SYP_TXN_H

#include "core.h"
#include "solver.h"

typedef struct {
    config *cfg;
    database *db;
    keyring *kr;
    strvec overwrite;
    int allow_unsigned, trust_scripts, no_scripts, quiet;
    strvec changed;
} txn;

void txn_init(txn *t, config *c, database *d, keyring *k);
long long txn_execute(txn *t, plan *p, const char *cmdline); /* tx-id, 0 = nichts, -1 Fehler */
int rollback_plan(txn *t, long long tx_id, ptrvec *available, ptrvec *owned, plan *out);
int clean_cache(config *c, database *d, int keep);
char *snapshot_create(config *c, const char *kind, const char *desc, const char *pre);
int snapshot_available(config *c);
void run_triggers(config *c, strvec *changed, int quiet);

#endif
