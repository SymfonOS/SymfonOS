/* harden.h – Prüft gebaute ELF-Dateien auf Härtungsmerkmale (PIE, RELRO/BIND_NOW, NX-Stack,
 * TEXTREL, unsichere RPATHs, Stack-Protector/FORTIFY-Hinweise) und Dateirechte (für alle beschreibbar,
 * setuid/setgid). Vergleichbar mit Fedoras annocheck und rpmlint. */
#ifndef SYP_HARDEN_H
#define SYP_HARDEN_H

#include "json.h"

typedef struct {
    int elf, nopie, norelro, nonow, execstack, textrel, badrpath, nossp, nofortify, worldwrite, suid;
    strvec problems; /* "Pfad: Befund" */
    strvec suids;    /* setuid-/setgid-Dateien (Übersicht, kein Fehler) */
} harden_report;

/* Durchsucht DESTDIR. mode: "strict" (Fehler bei kritischen Befunden), "warn", "off" */
int harden_check(const char *destdir, const char *mode, harden_report *r);
jval *harden_json(const harden_report *r);
void harden_free(harden_report *r);

#endif
