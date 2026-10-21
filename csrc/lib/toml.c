/* toml.c – kompakter TOML-Parser für Konfigurationen, Rezepte, Trigger und Mapping-Tabellen */
#define _GNU_SOURCE
#include "toml.h"

#include <ctype.h>
#include <stdlib.h>
#include <string.h>

typedef struct {
    const char *p, *e;
    int line;
} tp;

#define FAIL(...)                                                \
    do {                                                         \
        char *m_ = xasprintf(__VA_ARGS__);                       \
        set_error("TOML Zeile %d: %s", s->line, m_);             \
        free(m_);                                                \
        return NULL;                                             \
    } while (0)

static void skip_ws(tp *s) {
    while (s->p < s->e && (*s->p == ' ' || *s->p == '\t')) s->p++;
}
static void skip_ws_nl(tp *s) {
    for (;;) {
        skip_ws(s);
        if (s->p < s->e && *s->p == '#')
            while (s->p < s->e && *s->p != '\n') s->p++;
        if (s->p < s->e && (*s->p == '\n' || *s->p == '\r')) {
            if (*s->p == '\n') s->line++;
            s->p++;
            continue;
        }
        break;
    }
}
static int eol(tp *s) {
    skip_ws(s);
    if (s->p < s->e && *s->p == '#')
        while (s->p < s->e && *s->p != '\n') s->p++;
    if (s->p < s->e && *s->p == '\r') s->p++;
    if (s->p >= s->e) return 1;
    if (*s->p == '\n') {
        s->p++;
        s->line++;
        return 1;
    }
    return 0;
}

static void utf8(sbuf *b, unsigned long cp) {
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

static char *pstring(tp *s) {
    sbuf b = {0};
    sb_init(&b);
    if (s->e - s->p >= 3 && (!strncmp(s->p, "\"\"\"", 3) || !strncmp(s->p, "'''", 3))) {
        char q = *s->p;
        int lit = q == '\'';
        s->p += 3;
        if (s->p < s->e && *s->p == '\n') {
            s->p++;
            s->line++;
        }
        while (s->p < s->e) {
            if (s->e - s->p >= 3 && s->p[0] == q && s->p[1] == q && s->p[2] == q) {
                s->p += 3;
                while (s->p < s->e && *s->p == q) sb_addc(&b, *s->p++); /* bis zu 2 zusätzliche Quotes */
                return sb_take(&b);
            }
            char c = *s->p++;
            if (c == '\n') s->line++;
            if (!lit && c == '\\') {
                char e = s->p < s->e ? *s->p++ : 0;
                switch (e) {
                case 'n': sb_addc(&b, '\n'); break;
                case 't': sb_addc(&b, '\t'); break;
                case 'r': sb_addc(&b, '\r'); break;
                case '"': sb_addc(&b, '"'); break;
                case '\\': sb_addc(&b, '\\'); break;
                case '\n':
                case ' ':
                case '\r':
                    while (s->p < s->e && isspace((unsigned char)*s->p)) {
                        if (*s->p == '\n') s->line++;
                        s->p++;
                    }
                    break;
                default: sb_addc(&b, '\\'); sb_addc(&b, e);
                }
            } else {
                sb_addc(&b, c);
            }
        }
        free(b.s);
        set_error("TOML Zeile %d: mehrzeiliger String nicht beendet", s->line);
        return NULL;
    }
    char q = *s->p++;
    while (s->p < s->e && *s->p != q && *s->p != '\n') {
        char c = *s->p++;
        if (q == '"' && c == '\\') {
            char e = s->p < s->e ? *s->p++ : 0;
            switch (e) {
            case 'n': sb_addc(&b, '\n'); break;
            case 't': sb_addc(&b, '\t'); break;
            case 'r': sb_addc(&b, '\r'); break;
            case 'b': sb_addc(&b, '\b'); break;
            case 'f': sb_addc(&b, '\f'); break;
            case '"': sb_addc(&b, '"'); break;
            case '\\': sb_addc(&b, '\\'); break;
            case 'u':
            case 'U': {
                int n = e == 'u' ? 4 : 8;
                if (s->e - s->p < n) goto bad;
                char *h = xstrndup(s->p, (size_t)n);
                utf8(&b, strtoul(h, NULL, 16));
                free(h);
                s->p += n;
                break;
            }
            default: goto bad;
            }
        } else {
            sb_addc(&b, c);
        }
    }
    if (s->p >= s->e || *s->p != q) goto bad;
    s->p++;
    return sb_take(&b);
bad:
    free(b.s);
    set_error("TOML Zeile %d: ungültiger String", s->line);
    return NULL;
}

static char *pkey(tp *s) {
    skip_ws(s);
    if (s->p < s->e && (*s->p == '"' || *s->p == '\'')) return pstring(s);
    const char *st = s->p;
    while (s->p < s->e && (isalnum((unsigned char)*s->p) || *s->p == '_' || *s->p == '-')) s->p++;
    if (s->p == st) {
        set_error("TOML Zeile %d: Schlüssel erwartet", s->line);
        return NULL;
    }
    return xstrndup(st, (size_t)(s->p - st));
}

/* gepunkteter Schlüssel -> Liste */
static int pkeypath(tp *s, strvec *out) {
    for (;;) {
        char *k = pkey(s);
        if (!k) return -1;
        sv_push_own(out, k);
        skip_ws(s);
        if (s->p < s->e && *s->p == '.') {
            s->p++;
            continue;
        }
        return 0;
    }
}

static jval *pvalue(tp *s);

static jval *parray(tp *s) {
    s->p++;
    jval *a = jarr();
    for (;;) {
        skip_ws_nl(s);
        if (s->p < s->e && *s->p == ']') {
            s->p++;
            return a;
        }
        jval *v = pvalue(s);
        if (!v) {
            json_free(a);
            return NULL;
        }
        jpush(a, v);
        skip_ws_nl(s);
        if (s->p < s->e && *s->p == ',') {
            s->p++;
            continue;
        }
        if (s->p < s->e && *s->p == ']') {
            s->p++;
            return a;
        }
        json_free(a);
        FAIL("',' oder ']' erwartet");
    }
}

static jval *descend(jval *root, strvec *path, size_t upto, int line);

static jval *pinline(tp *s) {
    s->p++;
    jval *o = jobj();
    skip_ws(s);
    if (s->p < s->e && *s->p == '}') {
        s->p++;
        return o;
    }
    for (;;) {
        strvec kp = {0};
        if (pkeypath(s, &kp)) {
            json_free(o);
            sv_free(&kp);
            return NULL;
        }
        skip_ws(s);
        if (s->p >= s->e || *s->p != '=') {
            json_free(o);
            sv_free(&kp);
            FAIL("'=' erwartet");
        }
        s->p++;
        skip_ws(s);
        jval *v = pvalue(s);
        if (!v) {
            json_free(o);
            sv_free(&kp);
            return NULL;
        }
        jval *t = descend(o, &kp, kp.n - 1, s->line);
        jset(t, kp.v[kp.n - 1], v);
        sv_free(&kp);
        skip_ws(s);
        if (s->p < s->e && *s->p == ',') {
            s->p++;
            skip_ws(s);
            continue;
        }
        if (s->p < s->e && *s->p == '}') {
            s->p++;
            return o;
        }
        json_free(o);
        FAIL("',' oder '}' erwartet");
    }
}

static jval *pvalue(tp *s) {
    skip_ws(s);
    if (s->p >= s->e) FAIL("Wert erwartet");
    char c = *s->p;
    if (c == '"' || c == '\'') {
        char *str = pstring(s);
        return str ? jstr_own(str) : NULL;
    }
    if (c == '[') return parray(s);
    if (c == '{') return pinline(s);
    if (!strncmp(s->p, "true", 4)) {
        s->p += 4;
        return jbool(1);
    }
    if (!strncmp(s->p, "false", 5)) {
        s->p += 5;
        return jbool(0);
    }
    const char *st = s->p;
    while (s->p < s->e && (isalnum((unsigned char)*s->p) || strchr("+-._:", *s->p))) s->p++;
    char *tok = xstrndup(st, (size_t)(s->p - st));
    char *clean = str_replace(tok, "_", "");
    free(tok);
    char *end;
    jval *v = NULL;
    if (!strncmp(clean, "0x", 2)) v = jint(strtoll(clean + 2, &end, 16));
    else if (!strncmp(clean, "0o", 2)) v = jint(strtoll(clean + 2, &end, 8));
    else if (!strncmp(clean, "0b", 2)) v = jint(strtoll(clean + 2, &end, 2));
    else {
        long long i = strtoll(clean, &end, 10);
        if (*end == 0 && *clean) v = jint(i);
        else {
            double d = strtod(clean, &end);
            if (*end == 0 && *clean) {
                v = jarr(); /* Platzhalter, wird ersetzt */
                v->t = J_NUM;
                v->d = d;
            } else {
                v = jstr(clean); /* Datum/Zeit als String */
            }
        }
    }
    free(clean);
    return v;
}

/* Navigiert zu root[path[0..upto)] und legt Tabellen an. Bei Arrays: letztes Element. */
static jval *descend(jval *root, strvec *path, size_t upto, int line) {
    (void)line;
    jval *cur = root;
    for (size_t i = 0; i < upto; i++) {
        jval *n = jget(cur, path->v[i]);
        if (!n) {
            n = jobj();
            jset(cur, path->v[i], n);
        } else if (n->t == J_ARR && n->n && n->items[n->n - 1]->t == J_OBJ) {
            n = n->items[n->n - 1];
        } else if (n->t != J_OBJ) {
            return cur;
        }
        cur = n;
    }
    return cur;
}

jval *toml_parse(const char *text, size_t len) {
    tp st = {text, text + len, 1}, *s = &st;
    jval *root = jobj(), *cur = root;
    for (;;) {
        skip_ws_nl(s);
        if (s->p >= s->e) return root;
        if (*s->p == '[') {
            int arr = s->e - s->p >= 2 && s->p[1] == '[';
            s->p += arr ? 2 : 1;
            strvec kp = {0};
            if (pkeypath(s, &kp)) {
                sv_free(&kp);
                json_free(root);
                return NULL;
            }
            skip_ws(s);
            if (s->p >= s->e || *s->p != ']' || (arr && (s->e - s->p < 2 || s->p[1] != ']'))) {
                sv_free(&kp);
                json_free(root);
                FAIL("']' erwartet");
            }
            s->p += arr ? 2 : 1;
            jval *parent = descend(root, &kp, kp.n - 1, s->line);
            const char *last = kp.v[kp.n - 1];
            if (arr) {
                jval *a = jget(parent, last);
                if (!a) {
                    a = jarr();
                    jset(parent, last, a);
                }
                if (a->t != J_ARR) {
                    sv_free(&kp);
                    json_free(root);
                    FAIL("%s ist kein Tabellen-Array", last);
                }
                cur = jobj();
                jpush(a, cur);
            } else {
                jval *t = jget(parent, last);
                if (!t) {
                    t = jobj();
                    jset(parent, last, t);
                }
                cur = t;
            }
            sv_free(&kp);
            if (!eol(s)) {
                json_free(root);
                FAIL("Zeilenende erwartet");
            }
            continue;
        }
        strvec kp = {0};
        if (pkeypath(s, &kp)) {
            sv_free(&kp);
            json_free(root);
            return NULL;
        }
        skip_ws(s);
        if (s->p >= s->e || *s->p != '=') {
            sv_free(&kp);
            json_free(root);
            FAIL("'=' erwartet");
        }
        s->p++;
        jval *v = pvalue(s);
        if (!v) {
            sv_free(&kp);
            json_free(root);
            return NULL;
        }
        jval *t = descend(cur, &kp, kp.n - 1, s->line);
        jset(t, kp.v[kp.n - 1], v);
        sv_free(&kp);
        if (!eol(s)) {
            json_free(root);
            FAIL("Zeilenende erwartet");
        }
    }
}

jval *toml_load(const char *path) {
    char *buf;
    size_t len;
    if (read_file(path, &buf, &len)) return NULL;
    jval *v = toml_parse(buf, len);
    if (!v) {
        char *m = xasprintf("%s: %s", path, last_error());
        set_error("%s", m);
        free(m);
    }
    free(buf);
    return v;
}
