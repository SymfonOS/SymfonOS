/* crypto.c – Signaturen für Pakete und Repository-Indizes */
#define _GNU_SOURCE
#include "crypto.h"

#include <dirent.h>
#include <stdlib.h>
#include <string.h>

int crypto_init(void) {
    if (sodium_init() < 0) {
        set_error("libsodium konnte nicht initialisiert werden");
        return -1;
    }
    return 0;
}

static void make_id(const unsigned char *pk, char out[17]) {
    char h[65];
    sha256_hex(pk, crypto_sign_PUBLICKEYBYTES, h);
    memcpy(out, h, 16);
    out[16] = 0;
}

static char *b64(const unsigned char *d, size_t n) {
    size_t len = sodium_base64_ENCODED_LEN(n, sodium_base64_VARIANT_ORIGINAL);
    char *s = xmalloc(len);
    sodium_bin2base64(s, len, d, n, sodium_base64_VARIANT_ORIGINAL);
    return s;
}
static int unb64(const char *s, unsigned char *out, size_t want) {
    size_t n = 0;
    if (sodium_base642bin(out, want, s, strlen(s), " \n\r\t", &n, NULL, sodium_base64_VARIANT_ORIGINAL) ||
        n != want)
        return -1;
    return 0;
}

/* Liest "magic\nschlüssel: wert\n..." */
static int read_kv(const char *path, const char *magic, hmap *out) {
    char *buf;
    if (read_file(path, &buf, NULL)) return -1;
    hm_init(out);
    char *save = NULL;
    int first = 1, ok = 0;
    for (char *line = strtok_r(buf, "\n", &save); line; line = strtok_r(NULL, "\n", &save)) {
        if (first) {
            ok = strcmp(str_trim(line), magic) == 0;
            first = 0;
            continue;
        }
        char *c = strchr(line, ':');
        if (!c) continue;
        *c = 0;
        hm_set(out, str_trim(line), xstrdup(str_trim(c + 1)));
    }
    free(buf);
    if (!ok) {
        set_error("%s: kein gültiges Schlüsselformat (%s)", path, magic);
        return -1;
    }
    return 0;
}
static void kv_free(hmap *m) {
    HM_FOREACH(m, i) free(m->vals[i]);
    hm_free(m);
}

int seckey_generate(seckey *k, const char *comment) {
    crypto_sign_keypair(k->pk, k->sk);
    make_id(k->pk, k->id);
    k->comment = xstrdup(comment ? comment : "");
    return 0;
}

int seckey_load(const char *path, seckey *k) {
    hmap m;
    if (read_kv(path, "syp-secret-key v1", &m)) return -1;
    const char *seed64 = hm_get(&m, "seed");
    unsigned char seed[crypto_sign_SEEDBYTES];
    if (!seed64 || unb64(seed64, seed, sizeof seed)) {
        kv_free(&m);
        set_error("%s: ungültiger geheimer Schlüssel", path);
        return -1;
    }
    crypto_sign_seed_keypair(k->pk, k->sk, seed);
    sodium_memzero(seed, sizeof seed);
    make_id(k->pk, k->id);
    const char *c = hm_get(&m, "comment");
    k->comment = xstrdup(c ? c : "");
    kv_free(&m);
    return 0;
}

int seckey_save(const seckey *k, const char *secret_path, const char *public_path) {
    unsigned char seed[crypto_sign_SEEDBYTES];
    crypto_sign_ed25519_sk_to_seed(seed, k->sk);
    char *s64 = b64(seed, sizeof seed), *p64 = b64(k->pk, sizeof k->pk);
    sodium_memzero(seed, sizeof seed);
    char *sec = xasprintf("syp-secret-key v1\nid: %s\nseed: %s\ncomment: %s\n", k->id, s64, k->comment);
    char *pub = xasprintf("syp-public-key v1\nid: %s\nkey: %s\ncomment: %s\n", k->id, p64, k->comment);
    char *d = path_dirname(secret_path);
    mkdir_p(d, 0700);
    free(d);
    int rc = write_file(secret_path, sec, strlen(sec), 0600) || write_file(public_path, pub, strlen(pub), 0644);
    sodium_memzero(sec, strlen(sec));
    free(sec);
    free(pub);
    free(s64);
    free(p64);
    return rc ? -1 : 0;
}

int pubkey_load(const char *path, pubkey *k) {
    hmap m;
    if (read_kv(path, "syp-public-key v1", &m)) return -1;
    const char *key64 = hm_get(&m, "key"), *id = hm_get(&m, "id");
    if (!key64 || unb64(key64, k->pk, sizeof k->pk)) {
        kv_free(&m);
        set_error("%s: ungültiger öffentlicher Schlüssel", path);
        return -1;
    }
    make_id(k->pk, k->id);
    if (id && *id && strcmp(id, k->id)) {
        kv_free(&m);
        set_error("Schlüssel-ID passt nicht zum Schlüssel: %s", path);
        return -1;
    }
    const char *c = hm_get(&m, "comment");
    k->comment = xstrdup(c ? c : "");
    kv_free(&m);
    return 0;
}

static int cmpname(const void *a, const void *b) {
    return strcmp((*(struct dirent *const *)a)->d_name, (*(struct dirent *const *)b)->d_name);
}

void keyring_load_dir(const char *dir, keyring *kr) {
    kr->keys = NULL;
    kr->n = 0;
    struct dirent **list;
    int n = scandir(dir, &list, NULL, NULL);
    if (n < 0) return;
    qsort(list, (size_t)n, sizeof *list, cmpname);
    for (int i = 0; i < n; i++) {
        if (str_ends(list[i]->d_name, ".pub")) {
            char *p = path_join(dir, list[i]->d_name);
            pubkey k;
            if (pubkey_load(p, &k) == 0) {
                kr->keys = xrealloc(kr->keys, (kr->n + 1) * sizeof(pubkey));
                kr->keys[kr->n++] = k;
            } else {
                fprintf(stderr, "Warnung: %s\n", last_error());
            }
            free(p);
        }
        free(list[i]);
    }
    free(list);
}

jval *sign_data(const seckey *k, const void *data, size_t len) {
    unsigned char sig[crypto_sign_BYTES];
    crypto_sign_detached(sig, NULL, data, len, k->sk);
    jval *o = jobj();
    jset(o, "key_id", jstr(k->id));
    jset(o, "alg", jstr("ed25519"));
    jset(o, "sig", jstr_own(b64(sig, sizeof sig)));
    return o;
}

const pubkey *verify_data(const keyring *kr, const void *data, size_t len, const jval *sig) {
    const char *kid = jgets(sig, "key_id", NULL), *s64 = jgets(sig, "sig", NULL);
    if (!kid) {
        set_error("Nicht signiert");
        return NULL;
    }
    for (size_t i = 0; i < kr->n; i++) {
        if (strcmp(kr->keys[i].id, kid)) continue;
        unsigned char raw[crypto_sign_BYTES];
        if (!s64 || unb64(s64, raw, sizeof raw) ||
            crypto_sign_verify_detached(raw, data, len, kr->keys[i].pk)) {
            set_error("Signatur ungültig");
            return NULL;
        }
        return &kr->keys[i];
    }
    set_error("Unbekannter Signaturschlüssel '%s'", kid);
    return NULL;
}

int sign_file(const seckey *k, const char *path) {
    char *data;
    size_t len;
    if (read_file(path, &data, &len)) return -1;
    jval *s = sign_data(k, data, len);
    char *js = json_dump(s, 0, 0);
    char *sp = xasprintf("%s.sig", path);
    int rc = write_file(sp, js, strlen(js), 0644);
    free(data);
    free(js);
    free(sp);
    json_free(s);
    return rc;
}

const pubkey *verify_file(const keyring *kr, const char *path) {
    char *sp = xasprintf("%s.sig", path), *sigtxt, *data;
    size_t slen, dlen;
    if (read_file(sp, &sigtxt, &slen)) {
        set_error("Signatur fehlt: %s", sp);
        free(sp);
        return NULL;
    }
    free(sp);
    jval *sig = json_parse(sigtxt, slen);
    free(sigtxt);
    if (!sig) return NULL;
    if (read_file(path, &data, &dlen)) {
        json_free(sig);
        return NULL;
    }
    const pubkey *k = verify_data(kr, data, dlen, sig);
    free(data);
    json_free(sig);
    return k;
}
