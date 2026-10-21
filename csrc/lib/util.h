/* util.h – Hilfsfunktionen für syp: Speicher, Strings, Vektoren, Hashmap, Dateien, Prozesse */
#ifndef SYP_UTIL_H
#define SYP_UTIL_H

#include <stdarg.h>
#include <stddef.h>
#include <stdio.h>

#define SYP_VERSION "1.0.0"

void *xmalloc(size_t n);
void *xcalloc(size_t n, size_t m);
void *xrealloc(void *p, size_t n);
char *xstrdup(const char *s);
char *xstrndup(const char *s, size_t n);
char *xasprintf(const char *fmt, ...) __attribute__((format(printf, 1, 2)));

void die(const char *fmt, ...) __attribute__((noreturn, format(printf, 1, 2)));

/* Fehlerkanal: Funktionen geben -1 zurück und setzen eine Meldung */
void set_error(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
const char *last_error(void);

/* Dynamischer String */
typedef struct {
    char *s;
    size_t len, cap;
} sbuf;
void sb_init(sbuf *b);
void sb_add(sbuf *b, const char *s);
void sb_addn(sbuf *b, const char *s, size_t n);
void sb_addc(sbuf *b, char c);
void sb_addf(sbuf *b, const char *fmt, ...) __attribute__((format(printf, 2, 3)));
char *sb_take(sbuf *b);

/* String-Vektor */
typedef struct {
    char **v;
    size_t n, cap;
} strvec;
void sv_push(strvec *sv, const char *s);
void sv_push_own(strvec *sv, char *s);
int sv_contains(const strvec *sv, const char *s);
void sv_free(strvec *sv);
void sv_sort(strvec *sv);

/* Zeiger-Vektor */
typedef struct {
    void **v;
    size_t n, cap;
} ptrvec;
void pv_push(ptrvec *pv, void *p);

/* Hashmap string -> void* (offene Adressierung) */
typedef struct {
    char **keys;
    void **vals;
    size_t cap, n;
} hmap;
void hm_init(hmap *m);
void *hm_get(const hmap *m, const char *k);
void hm_set(hmap *m, const char *k, void *v); /* v == NULL entfernt nicht, setzt NULL */
void hm_del(hmap *m, const char *k);
void hm_free(hmap *m);
#define HM_FOREACH(m, i) for (size_t i = 0; i < (m)->cap; i++) if ((m)->keys[i] && (m)->keys[i] != HM_TOMB)
extern char HM_TOMB[];

int str_starts(const char *s, const char *p);
int str_ends(const char *s, const char *suffix);
char *str_trim(char *s);
void str_lower(char *s);
char *str_replace(const char *s, const char *from, const char *to);
int glob_match(const char *pattern, const char *s); /* fnmatch ohne FNM_PATHNAME */

/* Dateien */
int read_file(const char *path, char **out, size_t *len);
int write_file(const char *path, const void *data, size_t len, int mode);
int mkdir_p(const char *path, int mode);
int rm_rf(const char *path);
int copy_file(const char *src, const char *dst);
int path_exists(const char *p);     /* auch defekte Symlinks */
int is_dir(const char *p);          /* folgt Symlinks */
char *path_join(const char *a, const char *b);
char *path_dirname(const char *p);
const char *path_basename(const char *p);
char *make_tempdir(const char *prefix);
int copy_tree(const char *src, const char *dst, const char *const *ignore);

/* Prüfsummen (libsodium) */
void sha256_hex(const void *data, size_t len, char out[65]);
int sha256_file(const char *path, char out[65]);

/* Prozesse */
int run_cmd(char *const argv[], const char *cwd, char *const envp[]);   /* Exit-Code oder -1 */
int run_capture(char *const argv[], char **out);                        /* stdout einsammeln */
int which(const char *prog, const char *path_env);

/* Ausgabe */
extern int g_tty;
const char *col(const char *code);
const char *col_end(void);

#endif
