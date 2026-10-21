/* json.c – kleiner, strenger JSON-Parser und Serialisierer */
#define _GNU_SOURCE
#include "json.h"

#include <ctype.h>
#include <math.h>
#include <stdlib.h>
#include <string.h>

static jval *jnew(jtype t) {
    jval *v = xcalloc(1, sizeof *v);
    v->t = t;
    return v;
}
jval *jnull(void) { return jnew(J_NULL); }
jval *jbool(int b) {
    jval *v = jnew(J_BOOL);
    v->b = !!b;
    return v;
}
jval *jint(long long i) {
    jval *v = jnew(J_INT);
    v->i = i;
    return v;
}
jval *jstr(const char *s) { return jstr_own(xstrdup(s ? s : "")); }
jval *jstr_own(char *s) {
    jval *v = jnew(J_STR);
    v->s = s;
    return v;
}
jval *jarr(void) { return jnew(J_ARR); }
jval *jobj(void) { return jnew(J_OBJ); }

static void grow(jval *v) {
    if (v->n == v->cap) {
        v->cap = v->cap ? v->cap * 2 : 8;
        v->items = xrealloc(v->items, v->cap * sizeof(jval *));
        if (v->t == J_OBJ) v->keys = xrealloc(v->keys, v->cap * sizeof(char *));
    }
}
void jpush(jval *arr, jval *v) {
    grow(arr);
    arr->items[arr->n++] = v;
}
void jset(jval *obj, const char *k, jval *v) {
    for (size_t i = 0; i < obj->n; i++)
        if (strcmp(obj->keys[i], k) == 0) {
            json_free(obj->items[i]);
            obj->items[i] = v;
            return;
        }
    grow(obj);
    obj->keys[obj->n] = xstrdup(k);
    obj->items[obj->n++] = v;
}
jval *jget(const jval *obj, const char *k) {
    if (!obj || obj->t != J_OBJ) return NULL;
    for (size_t i = 0; i < obj->n; i++)
        if (strcmp(obj->keys[i], k) == 0) return obj->items[i];
    return NULL;
}
const char *jgets(const jval *obj, const char *k, const char *def) {
    jval *v = jget(obj, k);
    return v && v->t == J_STR ? v->s : def;
}
long long jgeti(const jval *obj, const char *k, long long def) {
    jval *v = jget(obj, k);
    if (!v) return def;
    if (v->t == J_INT) return v->i;
    if (v->t == J_NUM) return (long long)v->d;
    return def;
}
int jgetb(const jval *obj, const char *k, int def) {
    jval *v = jget(obj, k);
    return v && v->t == J_BOOL ? v->b : def;
}
jval *jgeta(const jval *obj, const char *k) {
    jval *v = jget(obj, k);
    return v && v->t == J_ARR ? v : NULL;
}
void jarr_strings(const jval *arr, strvec *out) {
    if (!arr || arr->t != J_ARR) return;
    for (size_t i = 0; i < arr->n; i++)
        if (arr->items[i]->t == J_STR) sv_push(out, arr->items[i]->s);
}
jval *jarr_from_sv(const strvec *sv) {
    jval *a = jarr();
    for (size_t i = 0; i < sv->n; i++) jpush(a, jstr(sv->v[i]));
    return a;
}
jval *jcopy(const jval *v) {
    if (!v) return NULL;
    jval *c = jnew(v->t);
    c->b = v->b;
    c->i = v->i;
    c->d = v->d;
    if (v->s) c->s = xstrdup(v->s);
    for (size_t i = 0; i < v->n; i++) {
        if (v->t == J_OBJ) jset(c, v->keys[i], jcopy(v->items[i]));
        else jpush(c, jcopy(v->items[i]));
    }
    return c;
}
void json_free(jval *v) {
    if (!v) return;
    for (size_t i = 0; i < v->n; i++) {
        json_free(v->items[i]);
        if (v->t == J_OBJ) free(v->keys[i]);
    }
    free(v->items);
    free(v->keys);
    free(v->s);
    free(v);
}

/* --- Parser --------------------------------------------------------------- */
typedef struct {
    const char *p, *e;
    int depth;
} ps;

static void ws(ps *s) {
    while (s->p < s->e && (*s->p == ' ' || *s->p == '\t' || *s->p == '\n' || *s->p == '\r')) s->p++;
}
static jval *pval(ps *s);

static void utf8(sbuf *b, unsigned cp) {
    if (cp < 0x80) sb_addc(b, (char)cp);
    else if (cp < 0x800) {
        sb_addc(b, (char)(0xC0 | (cp >> 6)));
        sb_addc(b, (char)(0x80 | (cp & 0x3F)));
    } else if (cp < 0x10000) {
        sb_addc(b, (char)(0xE0 | (cp >> 12)));
        sb_addc(b, (char)(0x80 | ((cp >> 6) & 0x3F)));
        sb_addc(b, (char)(0x80 | (cp & 0x3F)));
    } else {
        sb_addc(b, (char)(0xF0 | (cp >> 18)));
        sb_addc(b, (char)(0x80 | ((cp >> 12) & 0x3F)));
        sb_addc(b, (char)(0x80 | ((cp >> 6) & 0x3F)));
        sb_addc(b, (char)(0x80 | (cp & 0x3F)));
    }
}
static int hex4(const char *p, unsigned *out) {
    unsigned v = 0;
    for (int i = 0; i < 4; i++) {
        char c = p[i];
        v <<= 4;
        if (c >= '0' && c <= '9') v |= (unsigned)(c - '0');
        else if (c >= 'a' && c <= 'f') v |= (unsigned)(c - 'a' + 10);
        else if (c >= 'A' && c <= 'F') v |= (unsigned)(c - 'A' + 10);
        else return -1;
    }
    *out = v;
    return 0;
}
static char *pstr(ps *s) {
    if (s->p >= s->e || *s->p != '"') return NULL;
    s->p++;
    sbuf b = {0};
    sb_init(&b);
    while (s->p < s->e && *s->p != '"') {
        unsigned char c = (unsigned char)*s->p++;
        if (c == '\\') {
            if (s->p >= s->e) goto bad;
            char e = *s->p++;
            switch (e) {
            case '"': sb_addc(&b, '"'); break;
            case '\\': sb_addc(&b, '\\'); break;
            case '/': sb_addc(&b, '/'); break;
            case 'b': sb_addc(&b, '\b'); break;
            case 'f': sb_addc(&b, '\f'); break;
            case 'n': sb_addc(&b, '\n'); break;
            case 'r': sb_addc(&b, '\r'); break;
            case 't': sb_addc(&b, '\t'); break;
            case 'u': {
                unsigned cp;
                if (s->e - s->p < 4 || hex4(s->p, &cp)) goto bad;
                s->p += 4;
                if (cp >= 0xD800 && cp < 0xDC00 && s->e - s->p >= 6 && s->p[0] == '\\' && s->p[1] == 'u') {
                    unsigned lo;
                    if (!hex4(s->p + 2, &lo) && lo >= 0xDC00 && lo < 0xE000) {
                        cp = 0x10000 + ((cp - 0xD800) << 10) + (lo - 0xDC00);
                        s->p += 6;
                    }
                }
                utf8(&b, cp);
                break;
            }
            default: goto bad;
            }
        } else if (c < 0x20) {
            goto bad;
        } else {
            sb_addc(&b, (char)c);
        }
    }
    if (s->p >= s->e) goto bad;
    s->p++;
    return sb_take(&b);
bad:
    free(b.s);
    return NULL;
}
static jval *pval(ps *s) {
    ws(s);
    if (s->p >= s->e || ++s->depth > 200) return NULL;
    jval *v = NULL;
    char c = *s->p;
    if (c == '{') {
        s->p++;
        v = jobj();
        ws(s);
        if (s->p < s->e && *s->p == '}') {
            s->p++;
            goto ok;
        }
        for (;;) {
            ws(s);
            char *k = pstr(s);
            if (!k) goto bad;
            ws(s);
            if (s->p >= s->e || *s->p != ':') {
                free(k);
                goto bad;
            }
            s->p++;
            jval *x = pval(s);
            if (!x) {
                free(k);
                goto bad;
            }
            jset(v, k, x);
            free(k);
            ws(s);
            if (s->p < s->e && *s->p == ',') {
                s->p++;
                continue;
            }
            if (s->p < s->e && *s->p == '}') {
                s->p++;
                goto ok;
            }
            goto bad;
        }
    } else if (c == '[') {
        s->p++;
        v = jarr();
        ws(s);
        if (s->p < s->e && *s->p == ']') {
            s->p++;
            goto ok;
        }
        for (;;) {
            jval *x = pval(s);
            if (!x) goto bad;
            jpush(v, x);
            ws(s);
            if (s->p < s->e && *s->p == ',') {
                s->p++;
                continue;
            }
            if (s->p < s->e && *s->p == ']') {
                s->p++;
                goto ok;
            }
            goto bad;
        }
    } else if (c == '"') {
        char *str = pstr(s);
        if (!str) return NULL;
        v = jstr_own(str);
    } else if (!strncmp(s->p, "true", 4) && s->e - s->p >= 4) {
        s->p += 4;
        v = jbool(1);
    } else if (!strncmp(s->p, "false", 5) && s->e - s->p >= 5) {
        s->p += 5;
        v = jbool(0);
    } else if (!strncmp(s->p, "null", 4) && s->e - s->p >= 4) {
        s->p += 4;
        v = jnull();
    } else if (c == '-' || isdigit((unsigned char)c)) {
        const char *st = s->p;
        int isf = 0;
        if (*s->p == '-') s->p++;
        while (s->p < s->e && (isdigit((unsigned char)*s->p) || strchr(".eE+-", *s->p))) {
            if (strchr(".eE", *s->p)) isf = 1;
            s->p++;
        }
        char *num = xstrndup(st, (size_t)(s->p - st));
        if (isf) {
            v = jnew(J_NUM);
            v->d = strtod(num, NULL);
        } else {
            v = jint(strtoll(num, NULL, 10));
        }
        free(num);
    } else {
        return NULL;
    }
ok:
    s->depth--;
    return v;
bad:
    json_free(v);
    return NULL;
}
jval *json_parse(const char *text, size_t len) {
    ps s = {text, text + len, 0};
    jval *v = pval(&s);
    ws(&s);
    if (!v || s.p != s.e) {
        json_free(v);
        set_error("Ungültiges JSON (Position %ld)", (long)(s.p - text));
        return NULL;
    }
    return v;
}

/* --- Ausgabe -------------------------------------------------------------- */
static void dstr(sbuf *b, const char *s) {
    sb_addc(b, '"');
    for (const unsigned char *p = (const unsigned char *)s; *p; p++) {
        switch (*p) {
        case '"': sb_add(b, "\\\""); break;
        case '\\': sb_add(b, "\\\\"); break;
        case '\n': sb_add(b, "\\n"); break;
        case '\r': sb_add(b, "\\r"); break;
        case '\t': sb_add(b, "\\t"); break;
        case '\b': sb_add(b, "\\b"); break;
        case '\f': sb_add(b, "\\f"); break;
        default:
            if (*p < 0x20) sb_addf(b, "\\u%04x", *p);
            else sb_addc(b, (char)*p);
        }
    }
    sb_addc(b, '"');
}
static const jval *g_sort_obj;
static int cmpidx(const void *a, const void *b) {
    return strcmp(g_sort_obj->keys[*(const size_t *)a], g_sort_obj->keys[*(const size_t *)b]);
}
static void nl(sbuf *b, int indent, int level) {
    if (!indent) return;
    sb_addc(b, '\n');
    for (int i = 0; i < indent * level; i++) sb_addc(b, ' ');
}
static void dval(sbuf *b, const jval *v, int canon, int indent, int level) {
    switch (v->t) {
    case J_NULL: sb_add(b, "null"); break;
    case J_BOOL: sb_add(b, v->b ? "true" : "false"); break;
    case J_INT: sb_addf(b, "%lld", v->i); break;
    case J_NUM:
        if (isfinite(v->d)) sb_addf(b, "%.17g", v->d);
        else sb_add(b, "null");
        break;
    case J_STR: dstr(b, v->s); break;
    case J_ARR:
        sb_addc(b, '[');
        for (size_t i = 0; i < v->n; i++) {
            if (i) sb_add(b, indent ? "," : (canon ? "," : ", "));
            nl(b, indent, level + 1);
            dval(b, v->items[i], canon, indent, level + 1);
        }
        if (v->n) nl(b, indent, level);
        sb_addc(b, ']');
        break;
    case J_OBJ: {
        size_t *idx = xmalloc((v->n + 1) * sizeof(size_t));
        for (size_t i = 0; i < v->n; i++) idx[i] = i;
        if (canon && v->n > 1) {
            g_sort_obj = v;
            qsort(idx, v->n, sizeof(size_t), cmpidx);
        }
        sb_addc(b, '{');
        for (size_t k = 0; k < v->n; k++) {
            size_t i = idx[k];
            if (k) sb_add(b, indent ? "," : (canon ? "," : ", "));
            nl(b, indent, level + 1);
            dstr(b, v->keys[i]);
            sb_add(b, canon && !indent ? ":" : ": ");
            dval(b, v->items[i], canon, indent, level + 1);
        }
        if (v->n) nl(b, indent, level);
        sb_addc(b, '}');
        free(idx);
        break;
    }
    }
}
char *json_dump(const jval *v, int canonical, int indent) {
    sbuf b = {0};
    sb_init(&b);
    dval(&b, v, canonical, indent, 0);
    return sb_take(&b);
}
