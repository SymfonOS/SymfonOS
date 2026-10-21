/* harden.h – Prüft gebaute ELF-Dateien auf Härtungsmerkmale (PIE, RELRO/BIND_NOW, NX-Stack,
 * TEXTREL, unsichere RPATHs, Stack-Protector/FORTIFY-Hinweise). Vergleichbar mit Fedoras annocheck. */
#ifndef SYP_HARDEN_H
#define SYP_HARDEN_H

#include "json.h"

typedef struct {
    int elf, nopie, norelro, nonow, execstack, textrel, badrpath, nossp, nofortify;
    strvec problems; /* "Pfad: Befund" */
} harden_report;

/* Durchsucht DESTDIR. mode: "strict" (Fehler bei kritischen Befunden), "warn", "off" */
int harden_check(const char *destdir, const char *mode, harden_report *r);
jval *harden_json(const harden_report *r);
void harden_free(harden_report *r);

#endif
