#include "eid_crypto.h"
#include "aes.h"
#include "uECC.h"
#include <string.h>

#define K_EXPONENT 10
#define SECP160R1_ORDER_LEN 21
#define SECP160R1_KEY_LEN   20

/* SECP160R1 curve order (big-endian, 21 bytes)
 * n = 0x0100000000000000000001F4C8F927AED3CA752257 */
static const uint8_t SECP160R1_ORDER[SECP160R1_ORDER_LEN] = {
    0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x01, 0xF4, 0xC8, 0xF9, 0x27, 0xAE,
    0xD3, 0xCA, 0x75, 0x22, 0x57
};

/*
 * Big-endian modular reduction: result = num mod modulus
 * Uses bit-by-bit long division (schoolbook method).
 * Only called once per slot at boot — performance irrelevant.
 */
static void bignum_mod(const uint8_t *num, size_t num_len,
                       const uint8_t *mod, size_t mod_len,
                       uint8_t *result, size_t res_len)
{
    uint8_t r[22];
    const size_t r_len = mod_len + 1;
    memset(r, 0, r_len);

    for (size_t bit = 0; bit < num_len * 8; bit++) {
        for (size_t j = 0; j < r_len - 1; j++) {
            r[j] = (r[j] << 1) | (r[j + 1] >> 7);
        }
        r[r_len - 1] <<= 1;

        size_t byte_idx = bit / 8;
        int bit_idx = 7 - (bit % 8);
        if (num[byte_idx] & (1 << bit_idx)) {
            r[r_len - 1] |= 1;
        }

        int ge;
        if (r[0] != 0) {
            ge = 1;
        } else {
            ge = (memcmp(r + 1, mod, mod_len) >= 0) ? 1 : 0;
        }

        if (ge) {
            int borrow = 0;
            for (int j = (int)r_len - 1; j >= 1; j--) {
                int diff = (int)r[j] - (int)mod[j - 1] - borrow;
                if (diff < 0) {
                    diff += 256;
                    borrow = 1;
                } else {
                    borrow = 0;
                }
                r[j] = (uint8_t)diff;
            }
            r[0] = 0;
        }
    }

    memset(result, 0, res_len);
    if (res_len >= mod_len) {
        memcpy(result + res_len - mod_len, r + 1, mod_len);
    } else {
        memcpy(result, r + 1 + (mod_len - res_len), res_len);
    }
}


int generate_eid(const uint8_t eik[EIK_LEN],
                 uint32_t timestamp,
                 uint8_t eid_out[EID_LEN])
{
    /* Step 1: Mask timestamp */
    uint32_t ts_masked = timestamp & ~((1U << K_EXPONENT) - 1);
    uint8_t ts_bytes[4] = {
        (ts_masked >> 24) & 0xFF,
        (ts_masked >> 16) & 0xFF,
        (ts_masked >>  8) & 0xFF,
        (ts_masked      ) & 0xFF
    };

    /* Step 2: Build 32-byte data block */
    uint8_t data[32];
    memset(data, 0xFF, 11);
    data[11] = K_EXPONENT;
    memcpy(data + 12, ts_bytes, 4);
    memset(data + 16, 0x00, 11);
    data[27] = K_EXPONENT;
    memcpy(data + 28, ts_bytes, 4);

    /* Step 3: AES-256-ECB encrypt */
    struct AES_ctx aes_ctx;
    AES_init_ctx(&aes_ctx, eik);
    AES_ECB_encrypt(&aes_ctx, data);
    AES_ECB_encrypt(&aes_ctx, data + 16);

    /* Step 4: r = r' mod SECP160R1.order */
    uint8_t r_scalar[SECP160R1_ORDER_LEN];
    bignum_mod(data, 32,
               SECP160R1_ORDER, SECP160R1_ORDER_LEN,
               r_scalar, SECP160R1_ORDER_LEN);

    /* Step 5: R = r * G */
    uint8_t pubkey[2 * SECP160R1_KEY_LEN];
    if (uECC_compute_public_key(r_scalar, pubkey, uECC_secp160r1()) != 1) {
        return -1;
    }

    /* Step 6: EID = R.x */
    memcpy(eid_out, pubkey, EID_LEN);
    return 0;
}
