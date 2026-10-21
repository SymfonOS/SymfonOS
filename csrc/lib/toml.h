/* toml.h – TOML-Leser (Teilmenge: Tabellen, Tabellen-Arrays, Strings aller Art, Zahlen,
 * Booleans, Arrays, Inline-Tabellen, gepunktete Schlüssel). Ergebnis als jval-Objekt. */
#ifndef SYP_TOML_H
#define SYP_TOML_H

#include "json.h"

jval *toml_parse(const char *text, size_t len); /* NULL + set_error */
jval *toml_load(const char *path);

#endif
