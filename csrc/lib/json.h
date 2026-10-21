/* json.h – JSON-Werte, Parser, kanonische Ausgabe (sortierte Schlüssel, kompakt, UTF-8) */
#ifndef SYP_JSON_H
#define SYP_JSON_H

#include <stddef.h>

#include "util.h"

typedef enum { J_NULL, J_BOOL, J_INT, J_NUM, J_STR, J_ARR, J_OBJ } jtype;

typedef struct jval {
    jtype t;
    int b;
    long long i;
    double d;
    char *s;
    struct jval **items; /* Array-Elemente bzw. Objekt-Werte */
    char **keys;         /* nur Objekte */
    size_t n, cap;
} jval;

jval *json_parse(const char *text, size_t len); /* NULL + set_error bei Fehler */
char *json_dump(const jval *v, int canonical, int indent);
void json_free(jval *v);

jval *jnull(void);
jval *jbool(int b);
jval *jint(long long i);
jval *jstr(const char *s);
jval *jstr_own(char *s);
jval *jarr(void);
jval *jobj(void);
jval *jcopy(const jval *v);

void jpush(jval *arr, jval *v);
void jset(jval *obj, const char *k, jval *v); /* ersetzt vorhandenen Schlüssel */
jval *jget(const jval *obj, const char *k);
const char *jgets(const jval *obj, const char *k, const char *def);
long long jgeti(const jval *obj, const char *k, long long def);
int jgetb(const jval *obj, const char *k, int def);
jval *jgeta(const jval *obj, const char *k); /* Array oder NULL */
void jarr_strings(const jval *arr, strvec *out);
jval *jarr_from_sv(const strvec *sv);

#endif
