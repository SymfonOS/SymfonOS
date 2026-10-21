/* crypto.h – Ed25519-Schlüssel (libsodium), kompatibel zum Dateiformat "syp-*-key v1" */
#ifndef SYP_CRYPTO_H
#define SYP_CRYPTO_H

#include <sodium.h>

#include "json.h"

typedef struct {
    char id[17];
    unsigned char pk[crypto_sign_PUBLICKEYBYTES];
    char *comment;
} pubkey;

typedef struct {
    char id[17];
    unsigned char pk[crypto_sign_PUBLICKEYBYTES];
    unsigned char sk[crypto_sign_SECRETKEYBYTES];
    char *comment;
} seckey;

typedef struct {
    pubkey *keys;
    size_t n;
} keyring;

int crypto_init(void);
int seckey_generate(seckey *k, const char *comment);
int seckey_load(const char *path, seckey *k);
int seckey_save(const seckey *k, const char *secret_path, const char *public_path);
int pubkey_load(const char *path, pubkey *k);
void keyring_load_dir(const char *dir, keyring *kr);
jval *sign_data(const seckey *k, const void *data, size_t len);       /* {"key_id","alg","sig"} */
const pubkey *verify_data(const keyring *kr, const void *data, size_t len, const jval *sig); /* NULL + set_error */
int sign_file(const seckey *k, const char *path);                     /* schreibt path.sig */
const pubkey *verify_file(const keyring *kr, const char *path);

#endif
